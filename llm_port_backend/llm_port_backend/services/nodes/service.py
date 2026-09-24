"""Backend-authoritative node control service."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

log = logging.getLogger(__name__)

from llm_port_backend.db.dao.inference_dao import ModelAvailabilityDAO
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.inference import ModelAvailabilityStatus
from llm_port_backend.db.models.llm import LLMModel, LLMProvider, LLMRuntime, ModelStatus, RuntimeStatus
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    InfraNodeCredential,
    InfraNodeJoinRequest,
    InfraNodeProfile,
    InfraNodeSession,
    JoinRequestStatus,
    NodeCommandStatus,
    NodeCommandType,
    NodeHealthStatus,
)
from llm_port_backend.services.llm.gateway_sync import GatewaySyncService
from llm_port_backend.services.llm.monitoring import (
    deprovision_for_runtime,
    provision_for_runtime,
)
from llm_port_backend.services.nodes.wakeup import get_command_notifier
from llm_port_backend.services.nodes.auth import constant_time_equal, hash_with_pepper, random_secret


class NodeControlService:
    """Implements onboarding, command lifecycle, and scheduler selection."""

    def __init__(
        self,
        dao: NodeControlDAO,
        *,
        pepper: str,
        enrollment_ttl_minutes: int,
        default_command_timeout_sec: int,
        gateway_sync: GatewaySyncService | None = None,
    ) -> None:
        self._dao = dao
        self._pepper = pepper
        self._enrollment_ttl_minutes = enrollment_ttl_minutes
        self._default_command_timeout_sec = default_command_timeout_sec
        self._gateway_sync = gateway_sync

    # In-flight commands on an OFFLINE node get this grace period past
    # their timeout before the reaper marks them TIMED_OUT.  A reconnect
    # within the grace window re-dispatches the command first (hello_ack
    # path) so a node that was briefly unreachable loses no work.
    _REAPER_OFFLINE_GRACE_SEC = 600

    # How long a command on a *reachable* node may make no progress at all
    # before it is declared dead.
    #
    # Derived from the command's own budget rather than one number for all of
    # them. A flat 1800s had to sit above the slowest legitimate gap between
    # progress events -- a multi-GB image push -- and every short command
    # inherited that patience: a 300s `run_serve_app` that died on arrival
    # held its deployment for half an hour, and the row went on reporting the
    # phase before it the whole time. Scaling with the budget keeps the
    # transfer safe and lets a short command be declared dead on a timescale
    # somebody is still watching.
    #
    # The floor is what stops a brief hiccup -- a slow event write, a
    # reconnect -- from cancelling real work.
    _REAPER_MIN_SILENCE_SEC = 300

    # A session that has not heartbeated in this long is not connected,
    # whatever its row says.  Well above the agent's heartbeat interval so a
    # brief stall never closes a live session.
    _SESSION_STALE_SEC = 300

    @staticmethod
    def _parse_bearer_token(header_value: str | None) -> str:
        if not header_value:
            raise PermissionError("Missing Authorization header.")
        if not header_value.startswith("Bearer "):
            raise PermissionError("Invalid Authorization header.")
        token = header_value[len("Bearer ") :].strip()
        if not token:
            raise PermissionError("Missing bearer token.")
        return token

    def _hash(self, value: str) -> str:
        return hash_with_pepper(value, pepper=self._pepper)

    async def create_enrollment_token(
        self,
        *,
        issued_by: uuid.UUID | None,
        note: str | None,
    ) -> dict[str, Any]:
        plain = random_secret(24)
        token_hash = self._hash(plain)
        expires_at = datetime.now(tz=UTC) + timedelta(minutes=self._enrollment_ttl_minutes)
        row = await self._dao.create_enrollment_token(
            token_hash=token_hash,
            expires_at=expires_at,
            issued_by=issued_by,
            note=note,
        )
        return {
            "id": str(row.id),
            "token": plain,
            "expires_at": expires_at.isoformat(),
            "note": row.note,
        }

    async def enroll_node(
        self,
        *,
        enrollment_token: str,
        agent_id: str,
        host: str,
        capabilities: dict[str, Any],
        version: str | None,
    ) -> dict[str, Any]:
        token_hash = self._hash(enrollment_token)
        token_row = await self._dao.get_usable_enrollment_token(token_hash=token_hash)
        if token_row is None:
            raise PermissionError("Enrollment token is invalid or expired.")

        node, payload = await self._provision_node(
            agent_id=agent_id, host=host, capabilities=capabilities, version=version
        )
        await self._dao.mark_enrollment_token_used(token_row, node_id=node.id)
        return payload

    async def _provision_node(
        self,
        *,
        agent_id: str,
        host: str,
        capabilities: dict[str, Any],
        version: str | None,
    ) -> tuple[InfraNode, dict[str, Any]]:
        """Upsert the node and mint it a credential.

        Shared by both ways in: a token the operator carried to the machine,
        and an approval the operator clicked in the browser.  The two differ
        only in how the human authorised it, never in what the machine ends up
        holding, so this is deliberately the single place a node credential is
        created.
        """
        node = await self._dao.get_node_by_agent_id(agent_id)
        if node is None:
            node = await self._dao.create_node(
                agent_id=agent_id,
                host=host,
                version=version,
                capabilities_json=capabilities,
            )
        else:
            node.host = host
            node.version = version
            node.capabilities_json = capabilities
            node.status = "healthy"
            node.last_seen = datetime.now(tz=UTC)

        credential_id = uuid.uuid4()
        secret = random_secret(32)
        await self._dao.create_credential(
            node_id=node.id,
            credential_id=credential_id,
            secret_hash=self._hash(secret),
        )
        await self._dao.sync_legacy_infra_agent(node=node)

        return node, {
            "node_id": str(node.id),
            "agent_id": node.agent_id,
            "credential": f"{credential_id}.{secret}",
            "status": node.status,
            "host": node.host,
        }

    # -- join requests: the machine asks, a human approves ----------
    #
    # The token direction assumes the operator can paste.  When they cannot --
    # standing at the box, or connected from a different workstation -- a
    # 32-character token has to be retyped, and that is the worst moment in
    # onboarding.  Here the machine asks instead, and nothing long is typed.

    #: Unambiguous when read off a screen: no O/0, I/1, S/5 or U/V pairs.
    _CODE_ALPHABET = "ACDEFGHJKLMNPQRTWXY34679"
    _CODE_LENGTH = 6
    #: Long enough to walk to another room, short enough that an abandoned
    #: request does not sit in the operator's queue all day.
    _JOIN_REQUEST_TTL_MINUTES = 15
    #: A queue nobody can read is a queue nobody can approve from, so the cap
    #: is about keeping the list legible as much as it is about abuse.
    _MAX_PENDING_JOIN_REQUESTS = 50
    _MAX_PENDING_PER_SOURCE = 3

    @classmethod
    def _format_code(cls, raw: str) -> str:
        """``K7M-3QP`` -- grouped, because that is how a person reads it across."""
        half = len(raw) // 2
        return f"{raw[:half]}-{raw[half:]}"

    async def _mint_join_code(self) -> str:
        live = await self._dao.live_join_codes()
        for _ in range(20):
            raw = "".join(secrets.choice(self._CODE_ALPHABET) for _ in range(self._CODE_LENGTH))
            code = self._format_code(raw)
            if code not in live:
                return code
        raise RuntimeError("Could not allocate a join code; too many are live.")

    async def request_join(
        self,
        *,
        agent_id: str,
        host: str,
        capabilities: dict[str, Any],
        version: str | None,
        source_ip: str | None,
    ) -> dict[str, Any]:
        """Queue a machine for approval.  Unauthenticated by necessity.

        Returns the code to show on the machine and the secret only that
        machine holds.  Asking again from a machine that is already waiting
        returns *nothing new*: a second row would give the operator two codes
        for one box and no way to tell which to approve.

        Raises:
            PermissionError: the queue is full, or this source already has
                more live requests than it should.
        """
        existing = await self._dao.find_pending_join_request_by_agent(agent_id)
        if existing is not None:
            # The caller cannot prove it is the original requester, so it does
            # not get that request's poll secret back.  It gets the code, which
            # is all the human needs.
            return {
                "id": str(existing.id),
                "code": existing.code,
                "poll_secret": None,
                "expires_at": existing.expires_at.isoformat(),
                "already_pending": True,
            }

        if await self._dao.count_pending_join_requests() >= self._MAX_PENDING_JOIN_REQUESTS:
            raise PermissionError("Too many machines are already waiting for approval.")
        if source_ip is not None:
            per_source = await self._dao.count_pending_join_requests(source_ip=source_ip)
            if per_source >= self._MAX_PENDING_PER_SOURCE:
                raise PermissionError("Too many pending requests from this address.")

        poll_secret = random_secret(32)
        row = await self._dao.create_join_request(
            code=await self._mint_join_code(),
            poll_secret_hash=self._hash(poll_secret),
            agent_id=agent_id,
            host=host,
            source_ip=source_ip,
            version=version,
            capabilities=capabilities,
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=self._JOIN_REQUEST_TTL_MINUTES),
        )
        return {
            "id": str(row.id),
            "code": row.code,
            "poll_secret": poll_secret,
            "expires_at": row.expires_at.isoformat(),
            "already_pending": False,
        }

    async def list_pending_join_requests(self) -> list[InfraNodeJoinRequest]:
        return await self._dao.list_pending_join_requests()

    @staticmethod
    def _join_request_is_live(row: InfraNodeJoinRequest) -> bool:
        return row.status == JoinRequestStatus.PENDING and row.expires_at > datetime.now(tz=UTC)

    async def decide_join_request(
        self,
        *,
        request_id: uuid.UUID,
        approve: bool,
        decided_by: uuid.UUID | None,
        message: str | None = None,
    ) -> InfraNodeJoinRequest:
        """Approve or reject, as an authenticated administrator.

        Approval does not hand the credential out here.  It marks the request
        approved and lets the waiting agent collect it once, which keeps the
        secret on the only path that can prove it is the requester.

        Raises:
            LookupError: no such request.
            PermissionError: the request is no longer waiting.
        """
        row = await self._dao.get_join_request(request_id)
        if row is None:
            raise LookupError("Join request not found.")
        if not self._join_request_is_live(row):
            raise PermissionError("This request is no longer waiting for a decision.")

        row.status = JoinRequestStatus.APPROVED if approve else JoinRequestStatus.REJECTED
        row.decided_at = datetime.now(tz=UTC)
        row.decided_by = decided_by
        row.message = message
        await self._dao.session.flush()
        return row

    async def collect_join_result(
        self, *, request_id: uuid.UUID, poll_secret: str
    ) -> dict[str, Any]:
        """What the waiting agent asks, repeatedly, until it gets an answer.

        The credential is minted on the first successful collect and the
        request is spent, so a replay returns nothing.

        Raises:
            LookupError: no such request.
            PermissionError: the poll secret does not match -- which is what
                stops someone who guessed a code from collecting a credential.
        """
        row = await self._dao.get_join_request(request_id)
        if row is None:
            raise LookupError("Join request not found.")
        if not constant_time_equal(row.poll_secret_hash, self._hash(poll_secret)):
            raise PermissionError("Join request secret mismatch.")

        if row.status == JoinRequestStatus.REJECTED:
            return {"status": "rejected", "message": row.message}
        if row.status == JoinRequestStatus.CLAIMED:
            # Already spent.  Saying so beats returning "pending" forever.
            return {"status": "claimed", "message": "This request was already used."}
        if row.status == JoinRequestStatus.PENDING:
            if row.expires_at <= datetime.now(tz=UTC):
                return {"status": "expired", "message": "The request timed out."}
            return {
                "status": "pending",
                "code": row.code,
                "expires_at": row.expires_at.isoformat(),
            }

        node, payload = await self._provision_node(
            agent_id=row.agent_id,
            host=row.host,
            capabilities=row.capabilities_json or {},
            version=row.version,
        )
        row.status = JoinRequestStatus.CLAIMED
        row.node_id = node.id
        await self._dao.session.flush()
        # ``payload`` carries the node's health under "status"; the agent is
        # asking about the *request*, so the join outcome wins that key.
        return {**payload, "status": "approved"}

    async def authenticate_agent(self, *, authorization: str | None) -> tuple[InfraNode, InfraNodeCredential]:
        token = self._parse_bearer_token(authorization)
        parts = token.split(".", 1)
        if len(parts) != 2:
            raise PermissionError("Invalid credential format.")
        credential_id_raw, secret = parts
        try:
            credential_id = uuid.UUID(credential_id_raw)
        except ValueError as exc:
            raise PermissionError("Invalid credential id.") from exc

        credential = await self._dao.get_active_credential(credential_id)
        if credential is None:
            raise PermissionError("Credential is invalid or revoked.")
        if not constant_time_equal(credential.secret_hash, self._hash(secret)):
            raise PermissionError("Credential secret mismatch.")
        node = await self._dao.get_node_by_id(credential.node_id)
        if node is None:
            raise PermissionError("Credential node no longer exists.")
        return node, credential

    async def rotate_credential(self, *, authorization: str | None) -> dict[str, Any]:
        node, credential = await self.authenticate_agent(authorization=authorization)
        await self._dao.revoke_credential(credential)
        credential_id = uuid.uuid4()
        secret = random_secret(32)
        await self._dao.create_credential(
            node_id=node.id,
            credential_id=credential_id,
            secret_hash=self._hash(secret),
        )
        return {
            "node_id": str(node.id),
            "credential": f"{credential_id}.{secret}",
        }

    async def create_stream_session(self, *, node: InfraNode, credential: InfraNodeCredential) -> InfraNodeSession:
        return await self._dao.create_session(node_id=node.id, credential_id=credential.id)

    async def close_stream_session(self, *, session: InfraNodeSession) -> None:
        await self._dao.close_session(session)
        # Mark the node offline when its only stream disconnects.
        node = await self._dao.get_node_by_id(session.node_id)
        if node is not None:
            # Check if the node has any other active sessions.
            active = await self._dao.count_active_sessions(node_id=node.id)
            if active == 0:
                await self._node_lost_its_last_stream(node)

    async def _node_lost_its_last_stream(self, node: InfraNode) -> None:
        """Everything that follows from a machine having no agent connected.

        One place for it, because there are two ways to find out: the clean
        websocket teardown, and the stale-session reaper for an agent that
        died without one. Only the clean path used to do any of this, so a
        killed agent -- or one cut off by a backend restart -- left its
        machine reading "healthy" indefinitely, with nothing running on it.
        """
        node.status = NodeHealthStatus.OFFLINE
        await self._demote_node_runtimes(node_id=node.id)
        await self._fail_commands_lost_with_the_stream(node_id=node.id)
        await self._recheck_member_clusters(node_id=node.id)

    async def _recheck_member_clusters(self, *, node_id: uuid.UUID) -> int:
        """Queue every running cluster this machine belongs to for a fresh look.

        The reconciler only revisits a cluster when something about it
        changes. A machine vanishing is not a change to the cluster row, so a
        cluster whose only machine had been offline since the night before
        still read "ready" -- with Prometheus still scraping it. A machine
        leaving, or coming back, is exactly when its clusters need looking at.
        """
        from llm_port_backend.db.models.inference import (  # noqa: PLC0415
            InferenceEnvironment,
            InferenceEnvironmentNode,
        )
        from llm_port_backend.services.inference.service import (  # noqa: PLC0415
            _queue_for_reconcile,
        )

        try:
            rows = await self._dao.session.execute(
                select(InferenceEnvironment)
                .join(
                    InferenceEnvironmentNode,
                    InferenceEnvironmentNode.environment_id == InferenceEnvironment.id,
                )
                .where(
                    InferenceEnvironmentNode.node_id == node_id,
                    InferenceEnvironment.desired_state == "running",
                ),
            )
            clusters = list(rows.scalars().unique())
        except Exception:  # pragma: no cover - defensive
            log.exception("Could not list the clusters of node %s", node_id)
            return 0
        for cluster in clusters:
            _queue_for_reconcile(cluster)
        if clusters:
            from llm_port_backend.services.inference.wakeup import (  # noqa: PLC0415
                wake_reconciler_after_commit,
            )

            wake_reconciler_after_commit(self._dao.session)
        return len(clusters)

    async def _fail_commands_lost_with_the_stream(self, *, node_id: uuid.UUID) -> int:
        """Fail commands that were in flight on a stream that has just closed.

        A command runs inside the agent that holds the socket. When the socket
        goes, so does the work: the agent does not resume anything on
        reconnect, so the result frame is never coming.

        The reaper alone is not enough here. It waits for a command to go
        silent past its own timeout, which is right for a long transfer that
        is still making progress, but the node is usually back within seconds
        -- so it is never offline long enough for the offline path either, and
        the command sits in ``running`` for the whole silence budget with
        nothing to read. Pulling a runtime image saturates the link, times out
        the keepalive, and drops the very stream carrying the command that
        started it; the cluster then shows "preparing" and explains nothing.

        Losing the connection is proof the command died, so say so at once
        rather than inferring it from silence several minutes later.
        """
        try:
            commands = await self._dao.list_inflight_commands(node_id=node_id)
        except Exception:  # pragma: no cover - defensive
            log.exception("Could not list in-flight commands for node %s", node_id)
            return 0

        failed = 0
        for command in commands:
            try:
                await self._dao.set_command_status(
                    command,
                    status=NodeCommandStatus.FAILED,
                    error_code="node_stream_lost",
                    error_message=(
                        "The node's connection closed while this command was "
                        "running, so it did not finish. Nothing is left running "
                        "on the node; it is safe to try again."
                    ),
                )
                await self._dao.append_command_event(
                    command_id=command.id,
                    phase="failed",
                    message="Node stream closed while the command was in flight.",
                    payload_json=None,
                )
                await self._apply_runtime_side_effect(
                    command=command,
                    success=False,
                    payload={
                        "success": False,
                        "error_code": "node_stream_lost",
                        "error_message": "The node's connection closed mid-command.",
                    },
                )
                failed += 1
            except Exception:  # pragma: no cover - one bad row must not stop the rest
                log.exception("Could not fail in-flight command %s", command.id)

        if failed:
            log.warning(
                "Failed %d in-flight command(s) on node %s after its stream closed",
                failed,
                node_id,
            )
        return failed

    async def update_stream_offset(self, *, session: InfraNodeSession, offset: int) -> bool:
        if offset <= session.last_rx_offset:
            return False
        await self._dao.update_session_offset(session, offset=offset)
        return True

    async def _demote_node_runtimes(self, *, node_id: uuid.UUID) -> None:
        """Mark all active runtimes on a now-offline node as ERROR."""
        result = await self._dao.session.execute(
            select(LLMRuntime).where(
                LLMRuntime.assigned_node_id == node_id,
                LLMRuntime.status.in_([
                    RuntimeStatus.RUNNING,
                    RuntimeStatus.STARTING,
                    RuntimeStatus.CREATING,
                ]),
            ),
        )
        runtimes = list(result.scalars().all())
        for runtime in runtimes:
            prev = runtime.status
            runtime.status = RuntimeStatus.ERROR
            runtime.status_message = "Node offline"
            if self._gateway_sync is not None:
                await self._gateway_sync.set_instance_health(
                    runtime_id=runtime.id, health_status="unhealthy",
                )
            await self._dao.upsert_workload_assignment(
                runtime_id=runtime.id,
                node_id=node_id,
                desired_state=runtime.desired_state,
                actual_state=RuntimeStatus.ERROR.value,
            )
            log.info(
                "Runtime %s demoted %s → error (node %s offline)",
                runtime.id, prev.value, node_id,
            )

    async def heartbeat_node(
        self,
        *,
        node: InfraNode,
        status: str,
        capabilities: dict[str, Any] | None = None,
        version: str | None = None,
        host: str | None = None,
    ) -> dict[str, Any]:
        if host and host != node.host:
            node.host = host
        if capabilities is not None:
            capabilities = self._merge_projected_capabilities(node, capabilities)
        was_offline = node.status == NodeHealthStatus.OFFLINE
        updated = await self._dao.update_node_heartbeat(
            node,
            status=status,
            capabilities_json=capabilities,
            version=version,
        )
        await self._dao.sync_legacy_infra_agent(node=updated)
        if was_offline and updated.status != NodeHealthStatus.OFFLINE:
            # Back: its clusters were marked as missing it; look again now
            # rather than whenever something else happens to change.
            await self._recheck_member_clusters(node_id=updated.id)
        return self.serialize_node(updated)

    # Tier-2-only keys: the raw per-interface detail stays in the snapshot
    # table and is deliberately not projected onto the node row.
    _TIER2_NETWORK_KEYS = frozenset({"all_interfaces"})

    #: Capability keys written by the *inventory* projection, not by the
    #: heartbeat.  The heartbeat replaces ``capabilities_json`` wholesale, so
    #: anything projected here has to be carried across or it is destroyed on
    #: the next tick.
    _PROJECTED_CAPABILITY_KEYS = ("network",)

    @classmethod
    def _merge_projected_capabilities(
        cls, node: InfraNode, capabilities: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep inventory-projected capabilities across a heartbeat.

        A heartbeat reports *static* capabilities (hostname, arch, GPU count).
        The network summary the fabric planner needs comes from the inventory
        message instead, projected onto the same dict by
        :meth:`record_inventory`.  Replacing the dict therefore erased it
        every tick, and ``plan_fabric`` reported "no network facts reported"
        for a node that had just planned successfully -- the window between an
        inventory and the next heartbeat was the only time planning worked.
        """
        merged = dict(capabilities)
        existing = node.capabilities_json or {}
        for key in cls._PROJECTED_CAPABILITY_KEYS:
            if key not in merged and key in existing:
                merged[key] = existing[key]
        return merged

    @classmethod
    def _tier1_network_summary(cls, inventory: dict[str, Any]) -> dict[str, Any] | None:
        """Project the planner-relevant slice of an inventory's network block.

        The agent ships the normalized network summary inside the *inventory*
        message, which lands in ``InfraNodeInventorySnapshot`` (Tier 2).  The
        planner reads ``InfraNode.capabilities_json['network']`` (Tier 1), which
        is only ever written from the heartbeat's static capabilities — so
        without this projection the planner sees no fabrics at all on a real
        node.  This is the two-tier split from Phase3_upgrade.md section 8.
        """
        network = inventory.get("network")
        if not isinstance(network, dict):
            return None
        return {k: v for k, v in network.items() if k not in cls._TIER2_NETWORK_KEYS}

    async def record_inventory(
        self,
        *,
        node: InfraNode,
        inventory: dict[str, Any],
        utilization: dict[str, Any],
    ) -> None:
        await self._dao.upsert_inventory_snapshot(
            node_id=node.id,
            inventory_json=inventory,
            utilization_json=utilization,
        )
        tier1_network = self._tier1_network_summary(inventory)
        if tier1_network is None:
            return
        caps = dict(node.capabilities_json or {})
        if caps.get("network") == tier1_network:
            # Unchanged facts: do not rewrite the row.  Inventory ticks every
            # ~15s and a rewrite would churn ``updated_at`` for no reason.
            return
        caps["network"] = tier1_network
        node.capabilities_json = caps

    async def issue_command(
        self,
        *,
        node_id: uuid.UUID,
        command_type: str,
        payload: dict[str, Any],
        issued_by: uuid.UUID | None,
        correlation_id: str | None,
        timeout_sec: int | None,
        idempotency_key: str | None,
    ) -> InfraNodeCommand:
        node = await self._dao.get_node_by_id(node_id)
        if node is None:
            raise ValueError("Node not found.")
        key = idempotency_key or str(uuid.uuid4())
        existing = await self._dao.get_command_by_idempotency_key(node_id=node_id, idempotency_key=key)
        if existing is not None:
            # Only deduplicate against commands still in-flight.
            # Terminal states (failed/succeeded/canceled/timed_out) should
            # not block a retry — suffix the old key to free ours.
            terminal = {
                NodeCommandStatus.SUCCEEDED.value,
                NodeCommandStatus.FAILED.value,
                NodeCommandStatus.CANCELED.value,
                NodeCommandStatus.TIMED_OUT.value,
            }
            if existing.status not in terminal:
                return existing
            # Retire the old key so the new command can take it
            existing.idempotency_key = f"{key}::retired::{existing.id}"
        command = await self._dao.create_command(
            node_id=node_id,
            command_type=command_type,
            payload_json=payload,
            idempotency_key=key,
            issued_by=issued_by,
            correlation_id=correlation_id,
            timeout_sec=timeout_sec or self._default_command_timeout_sec,
        )
        # Wake the node's stream on commit.  Without this the command waits
        # for the agent to say something of its own accord -- measured at
        # 30-44s on the DGX pair for work that took under a tenth of a second.
        await get_command_notifier().notify(self._dao.session, node_id)
        return command

    async def list_commands_for_dispatch(self, *, node_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
        commands = await self._dao.list_pending_commands(node_id=node_id, limit=limit)
        items: list[dict[str, Any]] = []
        for command in commands:
            if command.status == NodeCommandStatus.QUEUED.value:
                await self._dao.set_command_status(command, status=NodeCommandStatus.DISPATCHED)
                await self._dao.append_command_event(
                    command_id=command.id,
                    phase="dispatched",
                    message="Command dispatched to stream session.",
                    payload_json=None,
                )
            items.append(self.serialize_command(command))
        return items

    async def list_dispatchable_commands(self, *, node_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
        """Pending commands plus overdue in-flight commands, for re-dispatch.

        In-flight = dispatched-but-unacked, or acked/running.  These are only
        re-sent once their timeout has elapsed (see ``_command_is_overdue``)
        so a healthy in-progress deploy is never re-triggered.  Safe to
        re-send because the agent deduplicates: an already-executed command
        is replayed from its persisted result store.
        """
        items = await self.list_commands_for_dispatch(node_id=node_id, limit=limit)
        try:
            inflight = await self._dao.list_inflight_commands(node_id=node_id, limit=limit)
        except Exception:  # pragma: no cover - defensive; DAO unavailable in some tests
            log.exception("list_inflight_commands failed for node %s", node_id)
            return items
        for command in inflight:
            if not self._command_is_overdue(command):
                continue
            log.info(
                "Re-dispatching overdue in-flight command %s (%s, status=%s)",
                command.id, command.command_type, command.status,
            )
            await self._dao.append_command_event(
                command_id=command.id,
                phase="re-dispatched",
                message="Command re-dispatched (still in flight past timeout).",
                payload_json=None,
            )
            item = self.serialize_command(command)
            # Mark as server-driven re-dispatch so a new agent skips only the
            # fresh-command age check (an overdue in-flight command is by
            # definition older than the 5-minute freshness bound and would
            # otherwise be rejected as "command_expired" before it ever runs).
            item["redispatch"] = True
            items.append(item)
        return items

    def _command_is_overdue(self, command: InfraNodeCommand) -> bool:
        """True when an in-flight command has outlived its timeout budget.

        Anchored to when the agent last made life (accepted or started the
        command), falling back to when it was dispatched/issued.
        """
        if command.status not in (
            NodeCommandStatus.DISPATCHED.value,
            NodeCommandStatus.ACKED.value,
            NodeCommandStatus.RUNNING.value,
        ):
            return False
        timeout = command.timeout_sec
        if not timeout or timeout <= 0:
            timeout = self._default_command_timeout_sec
        anchor: datetime | None = None
        if command.status == NodeCommandStatus.RUNNING.value:
            anchor = command.started_at or command.acked_at
            # A transfer in progress can legitimately run long (multi-GB
            # image push); give the agent up to 2x the stated timeout while
            # we are actively running.
            budget = timeout * 2
        else:
            anchor = command.acked_at or command.dispatched_at or command.issued_at
            budget = timeout
        if anchor is None:
            return False
        anchor = anchor if anchor.tzinfo else anchor.replace(tzinfo=UTC)
        return (datetime.now(tz=UTC) - anchor) > timedelta(seconds=budget)

    def _silence_budget(self, command: InfraNodeCommand) -> int:
        """How long *this* command may say nothing before it is declared dead.

        Its own timeout, floored: a command that streams progress never
        accumulates silence at all, so the window only has to outlast the
        longest gap between events that the command itself justifies.
        """
        timeout = command.timeout_sec
        if not timeout or timeout <= 0:
            timeout = self._default_command_timeout_sec
        return max(self._REAPER_MIN_SILENCE_SEC, int(timeout))

    async def reap_stale_commands(self) -> int:
        """Mark overdue in-flight commands TIMED_OUT when their node is offline.

        A command that is still DISPATCHED/ACKED/RUNNING with no node
        connection cannot complete — the agent's websocket is gone and the
        result frame will never arrive.  Without a reaper such commands (and
        the runtimes they drive) are stuck "in flight" forever, which is what
        left the 220 deploy dead with "container cannot be found".

        Only OFFLINE nodes are reaped: a command on a connected node past its
        timeout may be legitimately long (large image transfer), and the
        agent will still report its outcome.  A grace period lets a briefly
        unreachable node reconnect and re-dispatch before being timed out.
        """
        now = datetime.now(tz=UTC)
        try:
            commands = await self._dao.list_inflight_commands_all_nodes()
        except Exception:  # pragma: no cover - defensive
            log.exception("Stale command reaper: failed to list in-flight commands")
            return 0
        reaped = 0
        for command in commands:
            node = await self._dao.get_node_by_id(command.node_id)
            if node is None:
                continue
            if not self._command_is_overdue(command):
                continue

            offline = node.status == NodeHealthStatus.OFFLINE
            anchor = (
                command.started_at or command.acked_at or command.dispatched_at or command.issued_at
            )

            if offline:
                if anchor is not None and (now - anchor) < timedelta(
                    seconds=self._REAPER_OFFLINE_GRACE_SEC
                ):
                    continue
            elif not await self._command_has_gone_silent(command, anchor=anchor, now=now):
                # Healthy node, and the command is still making noise: leave
                # it alone.  A large image transfer is legitimately long.
                continue
            timeout = command.timeout_sec or self._default_command_timeout_sec
            try:
                await self._dao.set_command_status(
                    command,
                    status=NodeCommandStatus.TIMED_OUT,
                    error_code="command_timed_out",
                    error_message=(
                        f"Command timed out with no node connection (node offline, "
                        f"in flight {timeout}s + grace past its limit)."
                        if offline
                        else (
                            f"Command produced no progress for "
                            f"{self._silence_budget(command)}s past its {timeout}s limit; "
                            f"the node is reachable but nothing is answering for this command."
                        )
                    ),
                )
                await self._dao.append_command_event(
                    command_id=command.id,
                    phase="timed_out",
                    message="Command reaped: node offline with no result in flight.",
                    payload_json=None,
                )
                await self._apply_runtime_side_effect(
                    command=command, success=False,
                    payload={
                        "success": False,
                        "error_code": "command_timed_out",
                        "error_message": "Command timed out with no node connection (node offline).",
                    },
                )
                reaped += 1
                log.warning(
                    "Reaped stale command %s (%s) on offline node %s",
                    command.id, command.command_type, command.node_id,
                )
            except Exception:  # pragma: no cover - defensive
                log.exception("Failed to reap command %s", command.id)
        return reaped

    async def _command_has_gone_silent(
        self,
        command: InfraNodeCommand,
        *,
        anchor: datetime | None,
        now: datetime,
    ) -> bool:
        """Whether a command on a *reachable* node has stopped making progress.

        Node health is not evidence that a given command is alive.  Two agents
        on one machine, a dropped session, an agent that restarted mid-command
        -- in all of these the node heartbeats normally while a dispatched
        command is never going to be answered.  Reaping only OFFLINE nodes
        left those in flight forever, and the deployment waiting on them
        simply never moved.

        Progress is the discrimination that keeps a legitimately slow command
        safe: an 11GB image transfer streams events, and each one pushes the
        deadline out.  Silence for the whole window is what says nobody is
        coming back.
        """
        last_seen = anchor
        try:
            last_event = await self._dao.latest_command_event_at(command_id=command.id)
        except Exception:  # pragma: no cover - defensive
            log.exception("Could not read events for command %s", command.id)
            return False
        if last_event is not None and (last_seen is None or last_event > last_seen):
            last_seen = last_event
        if last_seen is None:
            return False
        return (now - last_seen) >= timedelta(seconds=self._silence_budget(command))

    async def close_stale_sessions(self) -> int:
        """End stream sessions that stopped heartbeating.

        Sessions are closed cleanly only when the websocket handler runs its
        teardown; a killed agent never gets there.  The rows then count as
        live forever, so a node appears to have more agents attached than it
        does -- which is exactly the state that makes a duplicate agent hard
        to notice.
        """
        try:
            # Which nodes are about to lose a session, before it is gone: the
            # rows carry the node id, and the count that comes back does not.
            stale = await self._dao.list_stale_sessions(
                silent_for=timedelta(seconds=self._SESSION_STALE_SEC)
            )
            affected = {session.node_id for session in stale}
            closed = await self._dao.close_stale_sessions(
                silent_for=timedelta(seconds=self._SESSION_STALE_SEC)
            )
        except Exception:  # pragma: no cover - defensive
            log.exception("Stale session reaper failed")
            return 0

        # A killed agent never runs the websocket teardown, so the clean path
        # in close_stream_session never fires for it -- and the commands it
        # was running stay in flight, on a node that looks perfectly healthy
        # once it restarts. Close that gap here, where we have just proved
        # the agent holding them is gone.
        for node_id in affected:
            try:
                if await self._dao.count_active_sessions(node_id=node_id) == 0:
                    node = await self._dao.get_node_by_id(node_id)
                    if node is not None:
                        await self._node_lost_its_last_stream(node)
            except Exception:  # pragma: no cover - one node must not stop the rest
                log.exception("Could not mark node %s disconnected", node_id)

        # And machines that read as up with no stream at all: their sessions
        # were closed some other way (or before this sweep existed), so the
        # pass above never sees them again.
        marked = await self._mark_silent_nodes_offline()

        if closed:
            log.info("Closed %d stale node stream session(s)", closed)
        # Counted in the return, because the caller commits only when this is
        # non-zero: a sweep that marked machines offline without closing a
        # session was rolled back every pass while logging that it had worked.
        return closed + marked

    async def _mark_silent_nodes_offline(self) -> int:
        """Machines not marked offline, silent past the stale window, with no stream."""
        from sqlalchemy import select as _select  # noqa: PLC0415

        cutoff = datetime.now(tz=UTC) - timedelta(seconds=self._SESSION_STALE_SEC)
        try:
            rows = (
                await self._dao.session.execute(
                    _select(InfraNode).where(
                        InfraNode.status != NodeHealthStatus.OFFLINE.value,
                        InfraNode.last_seen.is_not(None),
                        InfraNode.last_seen < cutoff,
                    )
                )
            ).scalars().all()
        except Exception:  # pragma: no cover - defensive
            log.exception("Could not list silent nodes")
            return 0
        marked = 0
        for node in rows:
            if await self._dao.count_active_sessions(node_id=node.id) > 0:
                continue
            await self._node_lost_its_last_stream(node)
            marked += 1
            log.warning(
                "Node %s (%s) marked offline: silent since %s with no agent connected",
                node.agent_id, node.host, node.last_seen,
            )
        return marked

    async def record_command_ack(
        self,
        *,
        node_id: uuid.UUID,
        command_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> None:
        command = await self._dao.get_command(command_id)
        if command is None or command.node_id != node_id:
            return
        await self._dao.set_command_status(command, status=NodeCommandStatus.ACKED)
        await self._dao.append_command_event(
            command_id=command.id,
            phase="acked",
            message=str(payload.get("message") or "Command acknowledged by agent."),
            payload_json=payload,
        )

    async def record_command_progress(
        self,
        *,
        node_id: uuid.UUID,
        command_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> None:
        command = await self._dao.get_command(command_id)
        if command is None or command.node_id != node_id:
            return
        await self._dao.set_command_status(command, status=NodeCommandStatus.RUNNING)
        await self._dao.append_command_event(
            command_id=command.id,
            phase="progress",
            message=str(payload.get("message") or "Progress event received."),
            payload_json=payload,
        )
        if command.command_type == NodeCommandType.SYNC_MODEL.value:
            await self._apply_model_sync_progress(command=command, payload=payload)

    async def record_command_result(
        self,
        *,
        node_id: uuid.UUID,
        command_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> None:
        command = await self._dao.get_command(command_id)
        if command is None or command.node_id != node_id:
            return
        success = bool(payload.get("success", False))
        status = NodeCommandStatus.SUCCEEDED if success else NodeCommandStatus.FAILED
        result_json = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if result_json.get("replayed") and command.status in {
            NodeCommandStatus.SUCCEEDED.value,
            NodeCommandStatus.FAILED.value,
        }:
            # Delivery is at-least-once: a command re-sent before its ack
            # lands reaches the agent twice, and the agent answers the second
            # from its result store. That answer is the first one minus what
            # the agent will not keep -- a takeover's cluster token -- so
            # taking it over the stored result lost the token (seen live on
            # the DGX pair). The first result stands, and its side effects
            # are not applied twice.
            await self._dao.append_command_event(
                command_id=command.id,
                phase="duplicate",
                message="Delivered twice; the agent replayed its result. The first result stands.",
                payload_json=None,
            )
            return
        if command.command_type == NodeCommandType.DESCRIBE_RAY_CLUSTER.value and "cluster_token" in result_json:
            # A takeover asks the machine for its cluster's token. Stored as it
            # arrived, it would sit in plain text in the command and its event;
            # it is sealed with the settings key before either is written.
            from llm_port_backend.services.inference.drivers.ray.secrets import seal_token  # noqa: PLC0415

            result_json = dict(result_json)
            result_json["cluster_token_sealed"] = seal_token(str(result_json.pop("cluster_token")))
            payload = {**payload, "result": result_json}
        await self._dao.set_command_status(
            command,
            status=status,
            result_json=result_json,
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
        )
        await self._dao.append_command_event(
            command_id=command.id,
            phase="result",
            message="Command succeeded." if success else "Command failed.",
            payload_json=payload,
        )
        await self._apply_runtime_side_effect(command=command, success=success, payload=payload)
        await self._apply_model_sync_side_effect(command=command, success=success, payload=payload)

    async def record_node_events(self, *, node_id: uuid.UUID, events: list[dict[str, Any]]) -> None:
        await self._dao.add_node_events(node_id=node_id, events=events)
        # React to workload health events emitted by the node agent's
        # HealthSupervisor so the backend reconciles runtime status and
        # gateway health when a container recovers or crashes.
        for event in events:
            event_type = str(event.get("event_type") or event.get("type") or "")
            if event_type in (
                "workload.health.running",
                "workload.health.stopped",
                "workload.health.missing",
                "workload.health.crash_loop",
                "workload.health.unhealthy",
            ):
                await self._reconcile_runtime_from_event(
                    node_id=node_id, event_type=event_type, event=event,
                )

    async def _reconcile_runtime_from_event(
        self,
        *,
        node_id: uuid.UUID,
        event_type: str,
        event: dict[str, Any],
    ) -> None:
        """Update runtime status + gateway health based on agent health events."""
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        runtime_id_raw = payload.get("runtime_id")
        if not runtime_id_raw:
            return
        try:
            runtime_id = uuid.UUID(str(runtime_id_raw))
        except ValueError:
            return

        result = await self._dao.session.execute(
            select(LLMRuntime).where(LLMRuntime.id == runtime_id),
        )
        runtime = result.scalar_one_or_none()
        if runtime is None:
            return

        if event_type == "workload.health.running":
            # Container has (re)started — promote runtime back to RUNNING.
            prev = runtime.status
            if prev in (RuntimeStatus.ERROR, RuntimeStatus.STARTING, RuntimeStatus.CREATING):
                # Guard: a container can report "running" while a deploy /
                # restart is still in flight (image transfer, model pull, a
                # crash-looping vLLM before its readiness probe passes).
                # Promoting from STARTING/CREATING in that window flips the
                # UI to a false "Running" the moment something dies. Promote
                # from a prior ERROR outright (self-heal after a failure);
                # while a provisioning command is still in flight, let the
                # command's own result / the reaper settle the status.
                if prev in (RuntimeStatus.STARTING, RuntimeStatus.CREATING) and await self._runtime_has_inflight_command(runtime):
                    log.info(
                        "Runtime %s health=running ignored (prev=%s): provisioning command still in flight",
                        runtime_id,
                        prev.value,
                    )
                    return
                runtime.status = RuntimeStatus.RUNNING
                runtime.status_message = None
                # Update endpoint if the agent reported one
                endpoint_url = payload.get("endpoint_url")
                if isinstance(endpoint_url, str) and endpoint_url.strip():
                    endpoint_url = await self._rewrite_endpoint_host(
                        endpoint_url.strip(), node_id=node_id,
                    )
                    runtime.endpoint_url = endpoint_url
                await self._publish_runtime_to_gateway(runtime=runtime)
                await self._promote_model_status(runtime)
                # Endpoint may have changed after a container restart —
                # the provision rebuild keeps targets/dashboards aligned.
                await provision_for_runtime(self._dao.session, runtime.id)
                log.info(
                    "Runtime %s reconciled %s → running from agent health event",
                    runtime_id,
                    prev.value,
                )
        elif event_type in ("workload.health.stopped", "workload.health.missing"):
            # Container stopped or disappeared
            if runtime.status == RuntimeStatus.RUNNING:
                exit_code = payload.get("exit_code", -1)
                container_status = payload.get("status", "stopped")
                runtime.status = RuntimeStatus.ERROR
                runtime.status_message = (
                    f"Container {container_status} (exit code {exit_code})"
                    if event_type == "workload.health.stopped"
                    else "Container not found on node"
                )
                if self._gateway_sync is not None:
                    await self._gateway_sync.set_instance_health(
                        runtime_id=runtime.id, health_status="unhealthy",
                    )
                log.warning(
                    "Runtime %s marked ERROR from agent health event: %s",
                    runtime_id,
                    runtime.status_message,
                )
        elif event_type == "workload.health.crash_loop":
            # Container is restarting repeatedly — mark as error
            restart_count = payload.get("restart_count", 0)
            runtime.status = RuntimeStatus.ERROR
            runtime.status_message = (
                f"Container crash-looping ({restart_count} restarts)"
            )
            if self._gateway_sync is not None:
                await self._gateway_sync.set_instance_health(
                    runtime_id=runtime.id, health_status="unhealthy",
                )
            log.warning(
                "Runtime %s marked ERROR — crash loop (%d restarts)",
                runtime_id,
                restart_count,
            )
        elif event_type == "workload.health.unhealthy":
            # Docker HEALTHCHECK reports unhealthy
            if runtime.status == RuntimeStatus.RUNNING:
                runtime.status_message = "Container health check failing"
                if self._gateway_sync is not None:
                    await self._gateway_sync.set_instance_health(
                        runtime_id=runtime.id, health_status="unhealthy",
                    )
                log.warning(
                    "Runtime %s health check unhealthy",
                    runtime_id,
                )

        await self._dao.upsert_workload_assignment(
            runtime_id=runtime.id,
            node_id=node_id,
            desired_state=runtime.desired_state,
            actual_state=runtime.status.value,
        )

    _RUNTIME_COMMAND_TYPES = {
        NodeCommandType.DEPLOY_WORKLOAD.value,
        NodeCommandType.START_WORKLOAD.value,
        NodeCommandType.RESTART_WORKLOAD.value,
        NodeCommandType.UPDATE_WORKLOAD.value,
    }

    async def _runtime_has_inflight_command(self, runtime: LLMRuntime) -> bool:
        """True when a provisioning command for this runtime is still open.

        The command's payload carries the runtime_id it operates on, so we
        scan the node's open (dispatched/acked/running) workload commands.
        """
        if not runtime.assigned_node_id:
            return False
        commands = await self._dao.list_inflight_commands(node_id=runtime.assigned_node_id)
        for command in commands:
            if command.command_type not in self._RUNTIME_COMMAND_TYPES:
                continue
            payload = command.payload_json or {}
            if payload.get("runtime_id") in (None, runtime.id, str(runtime.id)):
                return True
        return False

    async def set_node_maintenance(
        self,
        *,
        node_id: uuid.UUID,
        enabled: bool,
        reason: str | None,
        requested_by: uuid.UUID | None,
    ) -> dict[str, Any]:
        node = await self._dao.get_node_by_id(node_id)
        if node is None:
            raise ValueError("Node not found.")
        await self._dao.set_node_maintenance(
            node=node,
            enabled=enabled,
            reason=reason,
            requested_by=requested_by,
        )
        await self._dao.sync_legacy_infra_agent(node=node)
        return self.serialize_node(node)

    async def set_node_draining(self, *, node_id: uuid.UUID, enabled: bool) -> dict[str, Any]:
        node = await self._dao.get_node_by_id(node_id)
        if node is None:
            raise ValueError("Node not found.")
        await self._dao.set_node_draining(node=node, enabled=enabled)
        await self._dao.sync_legacy_infra_agent(node=node)
        return self.serialize_node(node)

    async def delete_node(self, *, node_id: uuid.UUID) -> bool:
        return await self._dao.delete_node(node_id=node_id)

    async def list_nodes(self) -> list[dict[str, Any]]:
        """The fleet, each machine carrying its latest utilization.

        The list used to omit it while ``get_node`` included it, so anything
        reading the fleet saw ``latest_utilization: null`` for every machine
        however recently it had reported.  The cluster topology reads exactly
        that field to size each node's allocation arc, so the arc never drew --
        it looked like a rendering bug and was a missing join.
        """
        rows = await self._dao.list_nodes()
        snapshots = await self._dao.latest_inventory_snapshots(
            node_ids=[row.id for row in rows]
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = self.serialize_node(row)
            snap = snapshots.get(row.id)
            payload["latest_utilization"] = snap.utilization_json if snap else None
            out.append(payload)
        return out

    async def get_node(self, *, node_id: uuid.UUID) -> dict[str, Any] | None:
        row = await self._dao.get_node_by_id(node_id)
        if row is None:
            return None
        snap = await self._dao.get_latest_inventory_snapshot(node_id=node_id)
        payload = self.serialize_node(row)
        payload["latest_inventory"] = snap.inventory_json if snap else None
        payload["latest_utilization"] = snap.utilization_json if snap else None
        return payload

    async def get_command_timeline(self, *, command_id: uuid.UUID) -> dict[str, Any] | None:
        command = await self._dao.get_command(command_id)
        if command is None:
            return None
        events = await self._dao.list_command_events(command_id=command_id)
        return {
            "command": self.serialize_command(command),
            "events": [
                {
                    "seq": item.seq,
                    "phase": item.phase,
                    "message": item.message,
                    "payload": item.payload_json,
                    "ts": item.created_at.isoformat(),
                }
                for item in events
            ],
        }

    async def get_command(self, *, command_id: uuid.UUID) -> InfraNodeCommand | None:
        """Return a command record by ID."""
        return await self._dao.get_command(command_id)

    async def get_node_record(self, *, node_id: uuid.UUID) -> InfraNode | None:
        """Return raw node row for internal orchestration paths."""
        return await self._dao.get_node_by_id(node_id)

    async def upsert_workload_assignment(
        self,
        *,
        runtime_id: uuid.UUID,
        node_id: uuid.UUID,
        desired_state: str,
        actual_state: str,
    ) -> None:
        """Persist runtime -> node assignment state."""
        await self._dao.upsert_workload_assignment(
            runtime_id=runtime_id,
            node_id=node_id,
            desired_state=desired_state,
            actual_state=actual_state,
        )

    async def schedule_node_for_runtime(
        self,
        *,
        provider: LLMProvider,
        runtime_name: str,
    ) -> tuple[InfraNode, dict[str, Any]]:
        nodes = await self._dao.list_nodes()
        if not nodes:
            raise ValueError("No eligible nodes are currently available.")

        requires_gpu = bool((provider.capabilities or {}).get("requires_gpu"))
        if provider.type.value in {"vllm", "tgi"}:
            requires_gpu = True

        candidates: list[dict[str, Any]] = []
        for node in nodes:
            snapshot = await self._dao.get_latest_inventory_snapshot(node_id=node.id)
            inventory = (snapshot.inventory_json if snapshot else {}) or {}
            utilization = (snapshot.utilization_json if snapshot else {}) or {}
            gpu_count = int(
                inventory.get("gpu_count")
                or inventory.get("gpu", {}).get("count")
                or node.capabilities_json.get("gpu_count", 0),
            )
            free_vram_bytes = int(
                utilization.get("gpu_free_vram_bytes")
                or utilization.get("gpu", {}).get("free_vram_bytes", 0),
            )
            rejected_reasons: list[str] = []
            score = 0.0
            if not node.scheduler_eligible:
                rejected_reasons.append("scheduler_ineligible")
            if node.maintenance_mode:
                rejected_reasons.append("maintenance_mode")
            if node.draining:
                rejected_reasons.append("draining")
            if node.status not in {"healthy", "degraded"}:
                rejected_reasons.append(f"status_{node.status}")
            if requires_gpu and gpu_count <= 0:
                rejected_reasons.append("gpu_required")

            if not rejected_reasons:
                score += 30.0 if node.status == "healthy" else 15.0
                score += min(float(gpu_count) * 10.0, 50.0)
                score += free_vram_bytes / (1024.0**3)
            candidates.append(
                {
                    "node_id": str(node.id),
                    "host": node.host,
                    "status": node.status,
                    "gpu_count": gpu_count,
                    "free_vram_bytes": free_vram_bytes,
                    "score": round(score, 3),
                    "rejected_reason": rejected_reasons[0] if rejected_reasons else None,
                    "rejected_reasons": rejected_reasons,
                }
            )

        eligible = [item for item in candidates if not item["rejected_reasons"]]
        if not eligible:
            raise ValueError("No eligible nodes satisfy runtime constraints.")
        selected = max(eligible, key=lambda item: (item["score"], item["host"]))
        selected_node = await self._dao.get_node_by_id(uuid.UUID(str(selected["node_id"])))
        if selected_node is None:
            raise ValueError("Selected node no longer exists.")

        explain = {
            "runtime_name": runtime_name,
            "requires_gpu": requires_gpu,
            "selected_node_id": str(selected_node.id),
            "candidates": candidates,
            "ts": datetime.now(tz=UTC).isoformat(),
        }
        return selected_node, explain

    async def _rewrite_endpoint_host(
        self, endpoint_url: str, *, node_id: uuid.UUID,
    ) -> str:
        """Replace the hostname in an agent-reported endpoint with the node's known host."""
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(endpoint_url)
        if not parsed.hostname:
            return endpoint_url
        node = await self._dao.get_node_by_id(node_id)
        if node is None or not node.host:
            return endpoint_url
        # Replace hostname, keep port and path
        new_netloc = node.host
        if parsed.port:
            new_netloc = f"{node.host}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=new_netloc))

    async def _apply_runtime_side_effect(
        self,
        *,
        command: InfraNodeCommand,
        success: bool,
        payload: dict[str, Any],
    ) -> None:
        runtime_id_raw = command.payload_json.get("runtime_id")
        if not runtime_id_raw:
            return
        try:
            runtime_id = uuid.UUID(str(runtime_id_raw))
        except ValueError:
            return

        runtime_res = await self._dao.session.execute(select(LLMRuntime).where(LLMRuntime.id == runtime_id))
        runtime = runtime_res.scalar_one_or_none()
        if runtime is None:
            return

        runtime.last_command_id = command.id
        runtime.execution_target = "node"
        runtime.assigned_node_id = command.node_id

        if success:
            runtime.status_message = None
            if command.command_type in {
                NodeCommandType.DEPLOY_WORKLOAD.value,
                NodeCommandType.START_WORKLOAD.value,
                NodeCommandType.RESTART_WORKLOAD.value,
            }:
                runtime.status = RuntimeStatus.RUNNING
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                endpoint_url = result.get("endpoint_url")
                if isinstance(endpoint_url, str) and endpoint_url.strip():
                    endpoint_url = await self._rewrite_endpoint_host(endpoint_url.strip(), node_id=command.node_id)
                    runtime.endpoint_url = endpoint_url
                    runtime.container_ref = f"node:{command.node_id}:{runtime.id}"
                    await self._publish_runtime_to_gateway(runtime=runtime)
                # Promote the linked model to AVAILABLE when it is still
                # in a transient state (e.g. the node downloaded it from
                # HuggingFace directly so the server-side record stayed
                # at "downloading" or was marked "failed" on restart).
                await self._promote_model_status(runtime)
                # (Re)provision per-runtime monitoring (no-op when the
                # feature flag is off or the provider isn't vLLM).
                await provision_for_runtime(self._dao.session, runtime.id)
            elif command.command_type == NodeCommandType.STOP_WORKLOAD.value:
                # NOTE: intentionally stopped runtimes KEEP their dashboard
                # + scrape target — history stays browsable and the target
                # simply reports up=0 until the next START re-provisions.
                runtime.status = RuntimeStatus.STOPPED
                if self._gateway_sync is not None:
                    await self._gateway_sync.set_instance_health(runtime_id=runtime.id, health_status="unhealthy")
            elif command.command_type == NodeCommandType.REMOVE_WORKLOAD.value:
                runtime.status = RuntimeStatus.STOPPED
                if self._gateway_sync is not None:
                    await self._gateway_sync.unpublish_runtime(runtime_id=runtime.id, alias=runtime.name)
                await deprovision_for_runtime(runtime.id)
        else:
            # Failure: surface the real cause, not a generic "Command failed".
            # New agents embed the container log tail in error_message
            # (readiness timeouts) so the UI can show the actual crash line
            # (e.g. vLLM swap-space overflow) instead of a retry button with
            # no clue as to why it failed.
            runtime.status = RuntimeStatus.ERROR
            error_message = (
                payload.get("error_message")
                or payload.get("error_code")
                or "Command failed"
            )
            runtime.status_message = str(error_message)[:2000]
            # The previously-reported endpoint can no longer be trusted.
            if command.command_type in {
                NodeCommandType.DEPLOY_WORKLOAD.value,
                NodeCommandType.START_WORKLOAD.value,
                NodeCommandType.RESTART_WORKLOAD.value,
                NodeCommandType.UPDATE_WORKLOAD.value,
            } and self._gateway_sync is not None:
                try:
                    await self._gateway_sync.set_instance_health(
                        runtime_id=runtime.id, health_status="unhealthy",
                    )
                except Exception:  # pragma: no cover - defensive
                    log.exception("Failed to mark runtime %s unhealthy after command failure", runtime.id)
            log.warning(
                "Runtime %s marked ERROR after %s failure: %s",
                runtime.id, command.command_type, runtime.status_message[:200],
            )

        await self._dao.upsert_workload_assignment(
            runtime_id=runtime.id,
            node_id=command.node_id,
            desired_state=runtime.desired_state,
            actual_state=runtime.status.value,
        )

    async def _resolve_model_id_for_command(
        self,
        command: InfraNodeCommand,
        payload: dict[str, Any] | None = None,
    ) -> uuid.UUID | None:
        cmd_payload = command.payload_json or {}
        model_sync = cmd_payload.get("model_sync") if isinstance(cmd_payload.get("model_sync"), dict) else {}
        result_json = payload.get("result") if payload and isinstance(payload.get("result"), dict) else {}

        raw_id = (
            cmd_payload.get("model_id")
            or model_sync.get("model_id")
            or result_json.get("model_id")
        )
        if raw_id:
            try:
                return uuid.UUID(str(raw_id))
            except (ValueError, AttributeError):
                pass

        hf_repo_id = (
            cmd_payload.get("hf_repo_id")
            or model_sync.get("hf_repo_id")
            or result_json.get("hf_repo_id")
        )
        if hf_repo_id:
            res = await self._dao.session.execute(
                select(LLMModel.id).where(LLMModel.hf_repo_id == str(hf_repo_id))
            )
            found_id = res.scalar_one_or_none()
            if found_id:
                return found_id

        return None

    async def _apply_model_sync_progress(
        self,
        *,
        command: InfraNodeCommand,
        payload: dict[str, Any],
    ) -> None:
        if command.command_type != NodeCommandType.SYNC_MODEL.value:
            return
        model_id = await self._resolve_model_id_for_command(command, payload)
        if model_id is None:
            return

        progress_raw = (
            payload.get("progress_pct")
            if payload.get("progress_pct") is not None
            else payload.get("progress")
        )
        try:
            progress_val = float(progress_raw) if progress_raw is not None else 0.0
        except (TypeError, ValueError):
            progress_val = 0.0

        message = payload.get("message")
        fields: dict[str, Any] = {"progress": progress_val}
        if message:
            fields["status_message"] = str(message)[:2000]

        dao = ModelAvailabilityDAO(self._dao.session)
        await dao.mark(
            model_id=model_id,
            node_id=command.node_id,
            status=ModelAvailabilityStatus.SYNCING,
            **fields,
        )

    async def _apply_model_sync_side_effect(
        self,
        *,
        command: InfraNodeCommand,
        success: bool,
        payload: dict[str, Any],
    ) -> None:
        if command.command_type != NodeCommandType.SYNC_MODEL.value:
            return
        model_id = await self._resolve_model_id_for_command(command, payload)
        if model_id is None:
            log.warning("Could not resolve model_id for SYNC_MODEL command %s", command.id)
            return

        dao = ModelAvailabilityDAO(self._dao.session)
        if success:
            result_json = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            cmd_payload = command.payload_json or {}
            model_sync = cmd_payload.get("model_sync") if isinstance(cmd_payload.get("model_sync"), dict) else {}

            root_path = result_json.get("root_path")
            revision = result_json.get("revision")
            manifest_sha256 = (
                result_json.get("manifest_sha256")
                or cmd_payload.get("manifest_sha256")
                or model_sync.get("manifest_sha256")
            )
            size_bytes = int(result_json.get("total_size") or 0)

            await dao.mark(
                model_id=model_id,
                node_id=command.node_id,
                status=ModelAvailabilityStatus.READY,
                root_path=str(root_path) if root_path else None,
                revision=str(revision) if revision else None,
                manifest_sha256=str(manifest_sha256) if manifest_sha256 else None,
                size_bytes=size_bytes,
                progress=100.0,
                status_message=None,
                ready_at=datetime.now(tz=UTC),
            )
        else:
            error_msg = (
                payload.get("error_message")
                or payload.get("error_code")
                or "Model sync failed on node"
            )
            await dao.mark(
                model_id=model_id,
                node_id=command.node_id,
                status=ModelAvailabilityStatus.FAILED,
                status_message=str(error_msg)[:2000],
            )

    async def _promote_model_status(self, runtime: LLMRuntime) -> None:
        """Promote the linked model to AVAILABLE if still in a transient state.

        When a node deploys a model from HuggingFace directly, the server-
        side model record may remain in DOWNLOADING (or get marked FAILED
        on restart).  Once the deploy command succeeds the model is clearly
        usable, so we promote it.
        """
        model_res = await self._dao.session.execute(
            select(LLMModel).where(LLMModel.id == runtime.model_id),
        )
        model = model_res.scalar_one_or_none()
        if model is None:
            return
        if model.status in (ModelStatus.DOWNLOADING, ModelStatus.FAILED):
            log.info(
                "Promoting model %s status %s → available (runtime deployed successfully)",
                model.id,
                model.status.value,
            )
            model.status = ModelStatus.AVAILABLE

    async def _publish_runtime_to_gateway(self, *, runtime: LLMRuntime) -> None:
        from llm_port_backend.services.llm.kinds import runtime_calls_tools, runtime_kind  # noqa: PLC0415

        if self._gateway_sync is None or not runtime.endpoint_url:
            return
        provider_res = await self._dao.session.execute(select(LLMProvider).where(LLMProvider.id == runtime.provider_id))
        provider = provider_res.scalar_one_or_none()
        if provider is None:
            return
        # Resolve the HF repo ID so LiteLLM sends the correct model name
        # to the engine (the alias may differ from what vLLM serves).
        litellm_model: str | None = None
        model_res = await self._dao.session.execute(select(LLMModel).where(LLMModel.id == runtime.model_id))
        model = model_res.scalar_one_or_none()
        if model and model.hf_repo_id:
            litellm_model = model.hf_repo_id
        await self._gateway_sync.publish_runtime(
            runtime_id=runtime.id,
            alias=runtime.name,
            base_url=runtime.endpoint_url,
            backend_provider_type=provider.type.value,
            is_remote=False,
            health_status="healthy",
            litellm_model=litellm_model,
            node_id=runtime.assigned_node_id,
            node_metadata={
                "execution_target": runtime.execution_target,
                "desired_state": runtime.desired_state,
            },
            capacity_hints={
                "node_id": str(runtime.assigned_node_id) if runtime.assigned_node_id else None,
            },
            task=runtime_kind(runtime),
            tools=runtime_calls_tools(runtime),
        )

    _STALE_THRESHOLD = timedelta(minutes=2)

    @staticmethod
    def serialize_node(node: InfraNode) -> dict[str, Any]:
        status = node.status
        # Safety net: if last_seen is stale, override to offline.
        if (
            status not in {NodeHealthStatus.OFFLINE, NodeHealthStatus.MAINTENANCE}
            and node.last_seen is not None
            and (datetime.now(tz=UTC) - node.last_seen) > NodeControlService._STALE_THRESHOLD
        ):
            status = NodeHealthStatus.OFFLINE
        return {
            "id": str(node.id),
            "agent_id": node.agent_id,
            "host": node.host,
            "status": status,
            "version": node.version,
            "labels": node.labels_json,
            "capabilities": node.capabilities_json,
            "maintenance_mode": node.maintenance_mode,
            "draining": node.draining,
            "scheduler_eligible": node.scheduler_eligible,
            "profile_id": str(node.profile_id) if node.profile_id else None,
            "last_seen": node.last_seen.isoformat() if node.last_seen else None,
            "created_at": node.created_at.isoformat(),
            "updated_at": node.updated_at.isoformat(),
        }

    @staticmethod
    def serialize_command(command: InfraNodeCommand) -> dict[str, Any]:
        return {
            "id": str(command.id),
            "node_id": str(command.node_id),
            "command_type": command.command_type,
            "status": command.status,
            "correlation_id": command.correlation_id,
            "idempotency_key": command.idempotency_key,
            "payload": command.payload_json,
            "result": command.result_json,
            "timeout_sec": command.timeout_sec,
            "error_code": command.error_code,
            "error_message": command.error_message,
            "issued_at": command.issued_at.isoformat(),
            "dispatched_at": command.dispatched_at.isoformat() if command.dispatched_at else None,
            "acked_at": command.acked_at.isoformat() if command.acked_at else None,
            "started_at": command.started_at.isoformat() if command.started_at else None,
            "completed_at": command.completed_at.isoformat() if command.completed_at else None,
        }

    @staticmethod
    def serialize_profile(profile: InfraNodeProfile) -> dict[str, Any]:
        return {
            "id": str(profile.id),
            "name": profile.name,
            "description": profile.description,
            "is_default": profile.is_default,
            "runtime_config": profile.runtime_config,
            "gpu_config": profile.gpu_config,
            "storage_config": profile.storage_config,
            "network_config": profile.network_config,
            "logging_config": profile.logging_config,
            "security_config": profile.security_config,
            "update_config": profile.update_config,
            "created_at": profile.created_at.isoformat(),
            "updated_at": profile.updated_at.isoformat(),
        }

    # ── profile CRUD ──────────────────────────────────────────

    async def create_profile(self, *, data: dict[str, Any]) -> dict[str, Any]:
        profile = await self._dao.create_profile(
            name=str(data.get("name", "")).strip(),
            description=data.get("description"),
            is_default=bool(data.get("is_default", False)),
            runtime_config=data.get("runtime_config"),
            gpu_config=data.get("gpu_config"),
            storage_config=data.get("storage_config"),
            network_config=data.get("network_config"),
            logging_config=data.get("logging_config"),
            security_config=data.get("security_config"),
            update_config=data.get("update_config"),
        )
        return self.serialize_profile(profile)

    async def get_profile(self, *, profile_id: uuid.UUID) -> dict[str, Any] | None:
        profile = await self._dao.get_profile(profile_id)
        if profile is None:
            return None
        return self.serialize_profile(profile)

    async def list_profiles(self) -> list[dict[str, Any]]:
        rows = await self._dao.list_profiles()
        return [self.serialize_profile(p) for p in rows]

    async def update_profile(self, *, profile_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any] | None:
        profile = await self._dao.get_profile(profile_id)
        if profile is None:
            return None
        updated = await self._dao.update_profile(profile, updates=data)
        return self.serialize_profile(updated)

    async def delete_profile(self, *, profile_id: uuid.UUID) -> bool:
        return await self._dao.delete_profile(profile_id)

    async def assign_profile_to_node(
        self,
        *,
        node_id: uuid.UUID,
        profile_id: uuid.UUID,
        issued_by: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Assign profile to node and issue sync command."""
        node = await self._dao.assign_profile(node_id=node_id, profile_id=profile_id)
        profile = await self._dao.get_profile(profile_id)
        if profile is not None:
            await self.issue_command(
                node_id=node_id,
                command_type=NodeCommandType.SYNC_NODE_PROFILE.value,
                payload=self.serialize_profile(profile),
                issued_by=issued_by,
                correlation_id=None,
                timeout_sec=30,
                idempotency_key=f"sync-profile-{node_id}-{profile_id}",
            )
        return self.serialize_node(node)

    async def unassign_profile_from_node(self, *, node_id: uuid.UUID) -> dict[str, Any]:
        """Remove profile from node."""
        node = await self._dao.unassign_profile(node_id=node_id)
        return self.serialize_node(node)

    async def get_node_profile(self, *, node_id: uuid.UUID) -> dict[str, Any] | None:
        """Return the profile assigned to a node, if any."""
        node = await self._dao.get_node_by_id(node_id)
        if node is None or node.profile_id is None:
            return None
        profile = await self._dao.get_profile(node.profile_id)
        if profile is None:
            return None
        return self.serialize_profile(profile)
