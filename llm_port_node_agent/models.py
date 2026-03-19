"""Shared model constants and wire contracts."""

from __future__ import annotations

import enum
from typing import Any, TypedDict


class NodeCommandType(enum.StrEnum):
    """Command types produced by backend control plane."""

    DEPLOY_WORKLOAD = "deploy_workload"
    START_WORKLOAD = "start_workload"
    STOP_WORKLOAD = "stop_workload"
    RESTART_WORKLOAD = "restart_workload"
    REMOVE_WORKLOAD = "remove_workload"
    UPDATE_WORKLOAD = "update_workload"
    REFRESH_INVENTORY = "refresh_inventory"
    SET_MAINTENANCE_MODE = "set_maintenance_mode"
    DRAIN_NODE = "drain_node"
    RESUME_NODE = "resume_node"
    COLLECT_DIAGNOSTICS = "collect_diagnostics"
    HOST_OP = "host_op"


class CommandResult(TypedDict, total=False):
    """Normalized command result sent over stream."""

    success: bool
    result: dict[str, Any]
    error_code: str
    error_message: str
