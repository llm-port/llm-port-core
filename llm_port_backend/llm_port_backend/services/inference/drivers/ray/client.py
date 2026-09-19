"""Abstracts Ray cluster inspection queries via node agent."""

import asyncio
import logging
import uuid
from typing import Any

from pydantic import BaseModel

from llm_port_backend.db.models.node_control import (
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.inference.drivers.ray.schemas import RayClusterStatus
from llm_port_backend.services.nodes.service import NodeControlService

log = logging.getLogger(__name__)

# How often (seconds) to poll a dispatched command for completion, and the
# total budget applied to *probes* (status observation) before they are
# abandoned rather than blocking the reconcile loop indefinitely.
_POLL_INTERVAL_SEC = 1.0
_PROBE_BUDGET_SEC = 90.0

# Budget for *mutating* Serve lifecycle commands (RUN_SERVE_APP /
# DELETE_SERVE_APP).  build_openai_app + serve.run do GCS round-trips and can
# take longer than a probe, so they get a larger (but still bounded) budget.
_LIFECYCLE_BUDGET_SEC = 300.0

_SUCCESS = NodeCommandStatus.SUCCEEDED.value
_TERMINAL = {
    NodeCommandStatus.FAILED.value,
    NodeCommandStatus.CANCELED.value,
    NodeCommandStatus.TIMED_OUT.value,
}


class RayCommandError(Exception):
    """A mutating Ray command reached a terminal failure state.

    Carries the agent-reported ``error_code`` / ``error_message`` so the
    deployment orchestrator can surface the real cause (e.g.
    "build_openai_app failed: ...") in the deployment phase message.
    """

    def __init__(
        self,
        *,
        command_type: str,
        node_id: uuid.UUID | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        self.command_type = command_type
        self.node_id = node_id
        self.error_code = error_code
        detail = error_message or error_code or "unknown agent error"
        super().__init__(f"{command_type} to {node_id if node_id else 'node'} failed: {detail}")
        self.detail = detail


def _parse_cluster_status(result: dict[str, Any] | None) -> RayClusterStatus:
    """Best-effort parse of an agent GET_RAY_STATUS result.

    A missing/unexpected payload is treated as "cluster not observed"
    (alive=False) rather than raising, so a flaky or half-deployed node can
    never wedge the reconcile loop.
    """
    result = result or {}
    return RayClusterStatus(
        alive=bool(result.get("alive", False)),
        version=result.get("version"),
        num_nodes=int(result.get("num_nodes", 0) or 0),
        nodes=list(result.get("nodes") or []),
        total_gpus=float(result.get("total_gpus", 0.0) or 0.0),
        available_gpus=float(result.get("available_gpus", result.get("total_gpus", 0.0)) or 0.0),
        cluster_address=result.get("cluster_address"),
        total_cpus=float(result.get("total_cpus", 0.0) or 0.0),
        head_address=result.get("head_address"),
        capabilities=dict(result.get("capabilities") or {}),
        serve=result.get("serve"),
        metrics=result.get("metrics"),
        state=result.get("state"),
    )


class RayServeStatus(BaseModel):
    """Parsed result of a GET_RAY_SERVE_STATUS command (additive tier)."""

    alive: bool = False
    available: bool = False
    active: bool = False
    apps: dict[str, Any] = {}
    detail: str | None = None


def _parse_serve_status(result: dict[str, Any] | None) -> RayServeStatus:
    """Best-effort parse of an agent GET_RAY_SERVE_STATUS result."""
    result = result or {}
    serve = result.get("serve") if isinstance(result.get("serve"), dict) else {}
    apps = serve.get("apps")
    return RayServeStatus(
        alive=bool(result.get("alive", False)),
        available=bool(serve.get("available", False)),
        active=bool(serve.get("active", False)),
        apps=dict(apps) if isinstance(apps, dict) else {},
        detail=serve.get("detail"),
    )


class RayClusterClient:
    """Queries Ray cluster state through a node's agent.

    Issues a ``GET_RAY_STATUS`` control-plane command to the (head) node and
    polls until the command reaches a terminal state, then parses the result
    into a :class:`RayClusterStatus`.  The command carries *no* secrets — the
    agent fetches the cluster auth token out-of-band.
    """

    def __init__(self, control_service: NodeControlService) -> None:
        self._nodes = control_service

    async def _dispatch_until_terminal(
        self,
        *,
        node_id: str | uuid.UUID,
        command_type: str,
        payload: dict[str, Any] | None = None,
        idem_prefix: str = "inference-deployment:apply",
        issued_by: uuid.UUID | None = None,
        timeout_sec: int | None = None,
    ) -> Any:
        """Dispatch a strictly-observed command, polling until terminal.

        Unlike :meth:`_dispatch_and_poll` (the probe path, where failure means
        "not observed"), a terminal failure here raises
        :class:`RayCommandError` carrying the agent's error detail — the
        mutation (RUN/DELETE_SERVE_APP) must be observed.  Node-not-found
        from :meth:`issue_command` is mapped to the same error type.  There is
        no wall-clock budget: the node command's own ``timeout_sec`` (enforced
        by the reaper) bounds its lifetime; this loop only observes.
        """
        node_id = node_id if isinstance(node_id, uuid.UUID) else uuid.UUID(str(node_id))
        try:
            command = await self._nodes.issue_command(
                node_id=node_id,
                command_type=command_type,
                payload=payload or {},  # no secrets in the payload
                issued_by=issued_by,
                correlation_id=None,
                timeout_sec=timeout_sec,
                idempotency_key=f"{idem_prefix}:{node_id}:{uuid.uuid4().hex[:12]}",
            )
        except ValueError as exc:
            raise RayCommandError(
                command_type=command_type,
                node_id=node_id,
                error_code="node_not_found",
                error_message=str(exc),
            ) from exc

        while True:
            current = await self._nodes.get_command(command_id=command.id)
            if current is not None:
                if current.status == _SUCCESS:
                    return current
                if current.status in _TERMINAL:
                    raise RayCommandError(
                        command_type=command_type,
                        node_id=node_id,
                        error_code=current.error_code,
                        error_message=current.error_message,
                    )
            await asyncio.sleep(_POLL_INTERVAL_SEC)

    async def _dispatch_and_poll(
        self,
        *,
        node_id: str | uuid.UUID,
        command_type: str,
        payload: dict[str, Any] | None = None,
        idem_prefix: str = "inference-env:probe",
        issued_by: uuid.UUID | None = None,
        timeout_sec: int | None = None,
    ) -> dict[str, Any] | None:
        """Dispatch *command_type* to *node_id* and poll until terminal.

        Returns the command's ``result_json`` on success, else ``None``
        (terminal failure or polling budget exhausted — callers interpret
        that as "not observed").
        """
        node_id = node_id if isinstance(node_id, uuid.UUID) else uuid.UUID(str(node_id))
        try:
            command = await self._nodes.issue_command(
                node_id=node_id,
                command_type=command_type,
                payload=payload or {},  # no secrets in the payload
                issued_by=issued_by,
                correlation_id=None,
                timeout_sec=timeout_sec,
                idempotency_key=f"{idem_prefix}:{node_id}:{uuid.uuid4().hex[:12]}",
            )
        except ValueError:
            log.warning("%s dispatch: node %s not found", command_type, node_id)
            return None

        deadline = asyncio.get_event_loop().time() + _PROBE_BUDGET_SEC
        while True:
            current = await self._nodes.get_command(command_id=command.id)
            if current is not None:
                if current.status == _SUCCESS:
                    return current.result_json
                if current.status in _TERMINAL:
                    log.warning(
                        "%s command %s ended in state %s (%s: %s)",
                        command_type, command.id, current.status,
                        current.error_code, current.error_message,
                    )
                    return None

            if asyncio.get_event_loop().time() > deadline:
                log.warning("%s command %s did not finish in %.0fs", command_type, command.id, _PROBE_BUDGET_SEC)
                return None
            await asyncio.sleep(_POLL_INTERVAL_SEC)

    async def probe_cluster(
        self, *, head_node_id: str | uuid.UUID, issued_by: uuid.UUID | None = None,
    ) -> RayClusterStatus:
        """Dispatch GET_RAY_STATUS to *head_node_id* and await its result."""
        result = await self._dispatch_and_poll(
            node_id=head_node_id,
            command_type=NodeCommandType.GET_RAY_STATUS.value,
            payload={},  # no secrets in the payload
            issued_by=issued_by,
        )
        return _parse_cluster_status(result)

    async def probe_serve(
        self, *, head_node_id: str | uuid.UUID, issued_by: uuid.UUID | None = None,
    ) -> RayServeStatus:
        """Dispatch GET_RAY_SERVE_STATUS to *head_node_id* and await its result.

        Additive Serve tier: a failed/absent command means "Serve not
        observed" (``alive=False``), never a raised error.
        """
        result = await self._dispatch_and_poll(
            node_id=head_node_id,
            command_type=NodeCommandType.GET_RAY_SERVE_STATUS.value,
            payload={},
            idem_prefix="inference-env:serve-probe",
            issued_by=issued_by,
        )
        return _parse_serve_status(result)

    # ------------------------------------------------------------------
    # Serve application lifecycle (Phase 3, strict)
    # ------------------------------------------------------------------

    async def run_serve_app(
        self,
        *,
        head_node_id: str | uuid.UUID,
        app_name: str,
        llm_serving_args: dict[str, Any],
        issued_by: uuid.UUID | None = None,
        idem_prefix: str = "inference-deployment:run",
    ) -> dict[str, Any]:
        """Deploy or update a named LLM Serve app on the head node.

        Strict: raises :class:`RayCommandError` when the agent reports a
        terminal failure (build error, serve.run rejection, ...), so the
        orchestrator can persist a FAILED phase with the real cause instead
        of waiting for a readiness probe that can never converge.

        ``idem_prefix`` scopes the idempotency key (with the node id folded in
        below); the orchestrator passes a per-(deployment, generation) prefix
        so a spec change re-keys and re-dispatches while a same-generation
        re-run is a cheap dedupe/retired-key no-op.
        """
        row = await self._dispatch_until_terminal(
            node_id=head_node_id,
            command_type=NodeCommandType.RUN_SERVE_APP.value,
            payload={"app_name": app_name, "llm_serving_args": llm_serving_args},
            idem_prefix=idem_prefix,
            issued_by=issued_by,
            timeout_sec=int(_LIFECYCLE_BUDGET_SEC),
        )
        return dict(row.result_json or {})

    async def delete_serve_app(
        self,
        *,
        head_node_id: str | uuid.UUID,
        app_name: str,
        issued_by: uuid.UUID | None = None,
        idem_prefix: str = "inference-deployment:delete",
    ) -> dict[str, Any]:
        """Delete a named Serve app on the head node (strict)."""
        row = await self._dispatch_until_terminal(
            node_id=head_node_id,
            command_type=NodeCommandType.DELETE_SERVE_APP.value,
            payload={"app_name": app_name},
            idem_prefix=idem_prefix,
            issued_by=issued_by,
            timeout_sec=int(_LIFECYCLE_BUDGET_SEC),
        )
        return dict(row.result_json or {})

