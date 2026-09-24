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
    SYNC_MODEL = "sync_model"
    FETCH_CONTAINER_LOGS = "fetch_container_logs"
    HOST_OP = "host_op"
    SYNC_NODE_PROFILE = "sync_node_profile"
    CHECK_SYSTEM_UPDATES = "check_system_updates"
    APPLY_SYSTEM_UPDATES = "apply_system_updates"

    # --- Ray environment lifecycle (Phase 2) ---
    ENSURE_RAY_RUNTIME = "ensure_ray_runtime"
    START_RAY_HEAD = "start_ray_head"
    JOIN_RAY_CLUSTER = "join_ray_cluster"
    LEAVE_RAY_CLUSTER = "leave_ray_cluster"
    STOP_RAY = "stop_ray"
    GET_RAY_STATUS = "get_ray_status"
    GET_RAY_SERVE_STATUS = "get_ray_serve_status"
    #: What the cluster this machine runs is serving, read from Ray, for a
    #: server taking it over (``ray/inspect.py``).
    DESCRIBE_RAY_CLUSTER = "describe_ray_cluster"

    # --- Ray Serve application lifecycle (Phase 3) ---
    RUN_SERVE_APP = "run_serve_app"
    DELETE_SERVE_APP = "delete_serve_app"

    # --- Fabric planning / active validation (Phase 4A) ---
    VALIDATE_FABRIC_LISTEN = "validate_fabric_listen"
    VALIDATE_FABRIC_CONNECT = "validate_fabric_connect"

    # --- Runtime bundle readiness (Phase 4B) ---
    ENSURE_RUNTIME_IMAGE = "ensure_runtime_image"
    SERVE_RUNTIME_IMAGE = "serve_runtime_image"


class CommandResult(TypedDict, total=False):
    """Normalized command result sent over stream."""

    success: bool
    result: dict[str, Any]
    error_code: str
    error_message: str
