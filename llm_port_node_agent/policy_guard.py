"""Local policy checks before executing backend commands."""

from __future__ import annotations

from llm_port_node_agent.models import NodeCommandType
from llm_port_node_agent.state_store import AgentState


class PolicyViolationError(RuntimeError):
    """Raised when command is blocked by local policy guard."""


class PolicyGuard:
    """Minimal local safety checks for maintenance and drain states."""

    _BLOCKED_DURING_MAINTENANCE = {
        NodeCommandType.DEPLOY_WORKLOAD.value,
        NodeCommandType.START_WORKLOAD.value,
        NodeCommandType.RESTART_WORKLOAD.value,
        NodeCommandType.UPDATE_WORKLOAD.value,
    }
    _BLOCKED_DURING_DRAIN = {
        NodeCommandType.DEPLOY_WORKLOAD.value,
        NodeCommandType.START_WORKLOAD.value,
        NodeCommandType.RESTART_WORKLOAD.value,
    }

    def __init__(self, *, image_allowlist: list[str] | None = None) -> None:
        self._image_allowlist = image_allowlist or []

    def validate(self, *, command_type: str, state: AgentState) -> None:
        """Raise when execution violates local guard constraints."""
        if state.maintenance_mode and command_type in self._BLOCKED_DURING_MAINTENANCE:
            raise PolicyViolationError(
                f"Command '{command_type}' blocked: node is in maintenance mode.",
            )
        if state.draining and command_type in self._BLOCKED_DURING_DRAIN:
            raise PolicyViolationError(
                f"Command '{command_type}' blocked: node is draining.",
            )

    def validate_image(self, image: str) -> None:
        """Raise when image is not in the configured allowlist."""
        if not self._image_allowlist:
            return
        for prefix in self._image_allowlist:
            if image == prefix or image.startswith(prefix):
                return
        raise PolicyViolationError(
            f"Image '{image}' not in allowlist.",
        )
