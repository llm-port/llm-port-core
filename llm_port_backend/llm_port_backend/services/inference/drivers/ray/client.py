"""Abstracts Ray cluster inspection queries via node agent."""

import asyncio
import logging
import uuid
from typing import Any

from llm_port_backend.db.models.node_control import (
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.inference.drivers.ray.schemas import RayClusterStatus
from llm_port_backend.services.nodes.service import NodeControlService

log = logging.getLogger(__name__)

# How often (seconds) to poll a dispatched command for completion, and the
# total budget (seconds) before a probe is abandoned rather than blocking the
# reconcile loop indefinitely.
_POLL_INTERVAL_SEC = 1.0
_PROBE_BUDGET_SEC = 90.0

_SUCCESS = NodeCommandStatus.SUCCEEDED.value
_TERMINAL = {
    NodeCommandStatus.FAILED.value,
    NodeCommandStatus.CANCELED.value,
    NodeCommandStatus.TIMED_OUT.value,
}


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

    async def probe_cluster(
        self, *, head_node_id: str | uuid.UUID, issued_by: uuid.UUID | None = None,
    ) -> RayClusterStatus:
        """Dispatch GET_RAY_STATUS to *head_node_id* and await its result."""
        node_id = head_node_id if isinstance(head_node_id, uuid.UUID) else uuid.UUID(str(head_node_id))
        command = await self._nodes.issue_command(
            node_id=node_id,
            command_type=NodeCommandType.GET_RAY_STATUS.value,
            payload={},  # no secrets in the payload
            issued_by=issued_by,
            correlation_id=None,
            timeout_sec=None,
            idempotency_key=f"inference-env:probe:{node_id}:{uuid.uuid4().hex[:12]}",
        )

        deadline = asyncio.get_event_loop().time() + _PROBE_BUDGET_SEC
        while True:
            current = await self._nodes.get_command(command_id=command.id)
            if current is not None:
                if current.status == _SUCCESS:
                    return _parse_cluster_status(current.result_json)
                if current.status in _TERMINAL:
                    log.warning(
                        "Ray probe command %s ended in state %s (%s: %s)",
                        command.id, current.status, current.error_code, current.error_message,
                    )
                    return RayClusterStatus(alive=False)

            if asyncio.get_event_loop().time() > deadline:
                log.warning("Ray probe command %s did not finish in %.0fs; reporting not alive.", command.id, _PROBE_BUDGET_SEC)
                return RayClusterStatus(alive=False)
            await asyncio.sleep(_POLL_INTERVAL_SEC)

