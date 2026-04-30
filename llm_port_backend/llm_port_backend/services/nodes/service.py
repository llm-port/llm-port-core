"""Backend-authoritative node control service."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

log = logging.getLogger(__name__)

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.llm import LLMModel, LLMProvider, LLMRuntime, ModelStatus, RuntimeStatus
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    InfraNodeCredential,
    InfraNodeProfile,
    InfraNodeSession,
    NodeCommandStatus,
    NodeCommandType,
    NodeHealthStatus,
)
from llm_port_backend.services.llm.gateway_sync import GatewaySyncService
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
        await self._dao.mark_enrollment_token_used(token_row, node_id=node.id)
        await self._dao.sync_legacy_infra_agent(node=node)

        return {
            "node_id": str(node.id),
            "agent_id": node.agent_id,
            "credential": f"{credential_id}.{secret}",
            "status": node.status,
            "host": node.host,
        }

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
                node.status = NodeHealthStatus.OFFLINE
                await self._demote_node_runtimes(node_id=node.id)

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
        updated = await self._dao.update_node_heartbeat(
            node,
            status=status,
            capabilities_json=capabilities,
            version=version,
        )
        await self._dao.sync_legacy_infra_agent(node=updated)
        return self.serialize_node(updated)

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
        rows = await self._dao.list_nodes()
        return [self.serialize_node(item) for item in rows]

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
            elif command.command_type == NodeCommandType.STOP_WORKLOAD.value:
                runtime.status = RuntimeStatus.STOPPED
                if self._gateway_sync is not None:
                    await self._gateway_sync.set_instance_health(runtime_id=runtime.id, health_status="unhealthy")
            elif command.command_type == NodeCommandType.REMOVE_WORKLOAD.value:
                runtime.status = RuntimeStatus.STOPPED
                if self._gateway_sync is not None:
                    await self._gateway_sync.unpublish_runtime(runtime_id=runtime.id, alias=runtime.name)
        else:
            runtime.status = RuntimeStatus.ERROR
            runtime.status_message = str(payload.get("error_message") or payload.get("error_code") or "Command failed")

        await self._dao.upsert_workload_assignment(
            runtime_id=runtime.id,
            node_id=command.node_id,
            desired_state=runtime.desired_state,
            actual_state=runtime.status.value,
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
