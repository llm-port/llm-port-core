"""Node command dispatcher."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.models import NodeCommandType
from llm_port_node_agent.policy_guard import PolicyGuard, PolicyViolationError
from llm_port_node_agent.runtime_manager import RuntimeManager, RuntimeManagerError
from llm_port_node_agent.state_store import StateStore
from llm_port_node_agent import system_updater

ProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]

log = logging.getLogger(__name__)


class CommandDispatcher:
    """Dispatch backend commands to local handlers."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        runtime_manager: RuntimeManager,
        policy_guard: PolicyGuard,
        events: EventBuffer,
        on_refresh_inventory: Callable[[], None] | None = None,
    ) -> None:
        self._state = state_store
        self._runtime = runtime_manager
        self._guard = policy_guard
        self._events = events
        self._on_refresh_inventory = on_refresh_inventory

    async def handle(self, command: dict[str, Any], emit_progress: ProgressEmitter) -> dict[str, Any]:
        """Execute command and return normalized result payload."""
        command_id = str(command.get("id") or "").strip()
        command_type = str(command.get("command_type") or "").strip().lower()
        payload = command.get("payload")
        payload = payload if isinstance(payload, dict) else {}

        if not command_id:
            return {
                "success": False,
                "error_code": "invalid_command",
                "error_message": "Missing command id.",
            }

        cached = self._state.get_command_result(command_id)
        if cached is not None:
            replayed = dict(cached)
            replayed.setdefault("result", {})
            replayed["result"]["replayed"] = True
            return replayed

        try:
            self._guard.validate(command_type=command_type, state=self._state.state)
            await emit_progress({"phase": "dispatched", "message": f"Executing {command_type}"})
            result = await self._execute(command_type=command_type, payload=payload, emit_progress=emit_progress)
            normalized = {"success": True, "result": result}
        except PolicyViolationError as exc:
            normalized = {
                "success": False,
                "error_code": "policy_violation",
                "error_message": str(exc),
                "result": {},
            }
        except RuntimeManagerError as exc:
            normalized = {
                "success": False,
                "error_code": "runtime_error",
                "error_message": str(exc),
                "result": {},
            }
        except Exception as exc:
            normalized = {
                "success": False,
                "error_code": "internal_error",
                "error_message": str(exc),
                "result": {},
            }

        try:
            self._state.remember_command_result(command_id, normalized)
        except OSError:
            log.warning("Failed to persist command result for %s (filesystem error)", command_id)
        self._events.add(
            event_type="command.finished",
            severity="info" if normalized.get("success") else "error",
            payload={"command_id": command_id, "command_type": command_type, **normalized},
            correlation_id=command_id,
        )
        return normalized

    async def _execute(
        self, *, command_type: str, payload: dict[str, Any], emit_progress: ProgressEmitter,
    ) -> dict[str, Any]:
        if command_type in {
            NodeCommandType.DEPLOY_WORKLOAD.value,
            NodeCommandType.UPDATE_WORKLOAD.value,
            NodeCommandType.START_WORKLOAD.value,
            NodeCommandType.RESTART_WORKLOAD.value,
        }:
            image = self._resolve_image(payload)
            if image:
                self._guard.validate_image(image)
        if command_type == NodeCommandType.DEPLOY_WORKLOAD.value:
            return await self._runtime.deploy_workload(payload, emit_progress=emit_progress)
        if command_type == NodeCommandType.START_WORKLOAD.value:
            return await self._runtime.start_workload(payload, emit_progress=emit_progress)
        if command_type == NodeCommandType.STOP_WORKLOAD.value:
            return await self._runtime.stop_workload(payload)
        if command_type == NodeCommandType.RESTART_WORKLOAD.value:
            return await self._runtime.restart_workload(payload, emit_progress=emit_progress)
        if command_type == NodeCommandType.REMOVE_WORKLOAD.value:
            return await self._runtime.remove_workload(payload)
        if command_type == NodeCommandType.UPDATE_WORKLOAD.value:
            return await self._runtime.update_workload(payload, emit_progress=emit_progress)
        if command_type == NodeCommandType.REFRESH_INVENTORY.value:
            if self._on_refresh_inventory:
                self._on_refresh_inventory()
            return {"refresh_requested": True}
        if command_type == NodeCommandType.COLLECT_DIAGNOSTICS.value:
            return await self._runtime.collect_diagnostics()
        if command_type == NodeCommandType.SYNC_MODEL.value:
            return await self._runtime.sync_model(payload)
        if command_type == NodeCommandType.FETCH_CONTAINER_LOGS.value:
            return await self._runtime.fetch_container_logs(payload)
        if command_type == NodeCommandType.SET_MAINTENANCE_MODE.value:
            enabled = bool(payload.get("enabled", True))
            self._state.state.maintenance_mode = enabled
            self._state.save()
            return {"maintenance_mode": enabled}
        if command_type == NodeCommandType.DRAIN_NODE.value:
            self._state.state.draining = True
            self._state.save()
            return {"draining": True}
        if command_type == NodeCommandType.RESUME_NODE.value:
            self._state.state.draining = False
            self._state.save()
            return {"draining": False}
        if command_type == NodeCommandType.HOST_OP.value:
            return {
                "accepted": False,
                "reason": "host_op not enabled by default on agent.",
            }
        if command_type == NodeCommandType.SYNC_NODE_PROFILE.value:
            profile = payload.get("profile")
            self._state.state.profile = profile if isinstance(profile, dict) else None
            self._state.save()
            log.info("Node profile synced: %s", profile.get("name") if isinstance(profile, dict) else None)
            return {"synced": True}
        if command_type == NodeCommandType.CHECK_SYSTEM_UPDATES.value:
            profile = self._state.state.profile or {}
            return await system_updater.check_updates(
                emit_progress, update_config=profile.get("update_config"),
            )
        if command_type == NodeCommandType.APPLY_SYSTEM_UPDATES.value:
            profile = self._state.state.profile or {}
            ucfg = dict(profile.get("update_config") or {})
            scope = str(payload.get("scope", "all"))
            if "reboot_policy" in payload:
                ucfg["reboot_policy"] = payload["reboot_policy"]
            return await system_updater.apply_updates(
                emit_progress, scope=scope, update_config=ucfg,
            )
        raise RuntimeManagerError(f"Unsupported command type: {command_type}")

    @staticmethod
    def _resolve_image(payload: dict[str, Any]) -> str:
        """Extract image string from deploy/update payload."""
        image = str(payload.get("image") or "").strip()
        if image:
            return image
        provider_config = payload.get("provider_config")
        if isinstance(provider_config, dict):
            return str(provider_config.get("image") or "").strip()
        return ""
