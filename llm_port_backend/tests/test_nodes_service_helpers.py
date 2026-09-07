"""Unit tests for the pure/static helpers in ``NodeControlService``.

Covers:
* ``_parse_bearer_token`` — Authorization header parsing / auth errors.
* ``serialize_node`` / ``serialize_command`` / ``serialize_profile`` —
  dict rendering incl. the *stale → offline* safety net.
* ``_rewrite_endpoint_host`` — hostname substitution for agent-reported
  endpoints, driven by an in-memory async DAO mock (no database).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from llm_port_backend.db.models.node_control import NodeHealthStatus
from llm_port_backend.services.nodes.service import NodeControlService


def _make_service(dao: object) -> NodeControlService:
    return NodeControlService(
        dao,  # type: ignore[arg-type]
        pepper="pep",
        enrollment_ttl_minutes=10,
        default_command_timeout_sec=300,
    )


# ──────────────────────────────────────────────────────────────────────────────
# _parse_bearer_token
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_bearer_ok() -> None:
    assert NodeControlService._parse_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"
    # extra whitespace is stripped
    assert NodeControlService._parse_bearer_token("Bearer   tok  ") == "tok"


def test_parse_bearer_missing_header() -> None:
    with pytest.raises(PermissionError, match="Missing Authorization header"):
        NodeControlService._parse_bearer_token(None)
    with pytest.raises(PermissionError, match="Missing Authorization header"):
        NodeControlService._parse_bearer_token("")


def test_parse_bearer_wrong_scheme() -> None:
    with pytest.raises(PermissionError, match="Invalid Authorization header"):
        NodeControlService._parse_bearer_token("Basic abc")
    # "Bearer" without the trailing space is not a valid "Bearer <token>"
    with pytest.raises(PermissionError, match="Invalid Authorization header"):
        NodeControlService._parse_bearer_token("Bearer")


def test_parse_bearer_empty_token() -> None:
    with pytest.raises(PermissionError, match="Missing bearer token"):
        NodeControlService._parse_bearer_token("Bearer    ")


# ──────────────────────────────────────────────────────────────────────────────
# serialize_node
# ──────────────────────────────────────────────────────────────────────────────


def _node(**overrides: object) -> SimpleNamespace:
    now = datetime.now(tz=UTC)
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "agent_id": "agent-1",
        "host": "10.0.0.5",
        "status": NodeHealthStatus.HEALTHY,
        "version": "1.2.3",
        "labels_json": {"env": "dev"},
        "capabilities_json": {"cpu": 8},
        "maintenance_mode": False,
        "draining": False,
        "scheduler_eligible": True,
        "profile_id": None,
        "last_seen": now,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_serialize_node_renders_fields_and_coerces_ids() -> None:
    node = _node(profile_id=uuid.uuid4())
    out = NodeControlService.serialize_node(node)  # type: ignore[arg-type]
    assert out["id"] is not None and isinstance(out["id"], str)
    assert out["agent_id"] == "agent-1"
    assert out["host"] == "10.0.0.5"
    assert out["status"] is NodeHealthStatus.HEALTHY
    assert out["labels"] == {"env": "dev"}
    assert out["capabilities"] == {"cpu": 8}
    assert out["profile_id"] is not None and isinstance(out["profile_id"], str)
    assert out["last_seen"] is not None and out["created_at"] is not None


def test_serialize_node_profile_none_stays_none() -> None:
    out = NodeControlService.serialize_node(_node(profile_id=None))  # type: ignore[arg-type]
    assert out["profile_id"] is None


def test_serialize_node_stale_last_seen_overrides_to_offline() -> None:
    stale = datetime.now(tz=UTC) - timedelta(minutes=5)  # > 2 min threshold
    out = NodeControlService.serialize_node(_node(status=NodeHealthStatus.HEALTHY, last_seen=stale))  # type: ignore[arg-type]
    assert out["status"] is NodeHealthStatus.OFFLINE


def test_serialize_node_recent_last_seen_keeps_status() -> None:
    recent = datetime.now(tz=UTC)
    out = NodeControlService.serialize_node(_node(status=NodeHealthStatus.HEALTHY, last_seen=recent))  # type: ignore[arg-type]
    assert out["status"] is NodeHealthStatus.HEALTHY


def test_serialize_node_never_seen_is_not_overridden() -> None:
    out = NodeControlService.serialize_node(_node(last_seen=None))  # type: ignore[arg-type]
    assert out["last_seen"] is None
    assert out["status"] is NodeHealthStatus.HEALTHY  # no last_seen → no override


@pytest.mark.parametrize("status", [NodeHealthStatus.OFFLINE, NodeHealthStatus.MAINTENANCE])
def test_serialize_node_offline_and_maintenance_never_overridden(status: NodeHealthStatus) -> None:
    stale = datetime.now(tz=UTC) - timedelta(minutes=9)
    out = NodeControlService.serialize_node(_node(status=status, last_seen=stale))  # type: ignore[arg-type]
    assert out["status"] is status


# ──────────────────────────────────────────────────────────────────────────────
# serialize_command
# ──────────────────────────────────────────────────────────────────────────────


def _command(**overrides: object) -> SimpleNamespace:
    now = datetime.now(tz=UTC)
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "node_id": uuid.uuid4(),
        "command_type": "deploy_workload",
        "status": "pending",
        "correlation_id": None,
        "idempotency_key": None,
        "payload_json": {"runtime_id": str(uuid.uuid4())},
        "result_json": None,
        "timeout_sec": 300,
        "error_code": None,
        "error_message": None,
        "issued_at": now,
        "dispatched_at": now,
        "acked_at": None,
        "started_at": None,
        "completed_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_serialize_command_renders_payload_and_optional_times() -> None:
    runtime_id = str(uuid.uuid4())
    command = _command(payload_json={"runtime_id": runtime_id}, result_json={"ok": True})
    out = NodeControlService.serialize_command(command)  # type: ignore[arg-type]
    assert isinstance(out["id"], str) and isinstance(out["node_id"], str)
    assert out["command_type"] == "deploy_workload"
    assert out["payload"] == {"runtime_id": runtime_id}
    assert out["result"] == {"ok": True}
    assert out["dispatched_at"] is not None
    assert out["acked_at"] is None
    assert out["started_at"] is None
    assert out["completed_at"] is None


# ──────────────────────────────────────────────────────────────────────────────
# serialize_profile
# ──────────────────────────────────────────────────────────────────────────────


def _profile(**overrides: object) -> SimpleNamespace:
    now = datetime.now(tz=UTC)
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "nvidia-a100",
        "description": "A100 profile",
        "is_default": True,
        "runtime_config": {"memory": "16g"},
        "gpu_config": {"devices": "all"},
        "storage_config": None,
        "network_config": None,
        "logging_config": None,
        "security_config": None,
        "update_config": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_serialize_profile_passes_config_through() -> None:
    out = NodeControlService.serialize_profile(_profile())  # type: ignore[arg-type]
    assert isinstance(out["id"], str)
    assert out["name"] == "nvidia-a100"
    assert out["runtime_config"] == {"memory": "16g"}
    assert out["gpu_config"] == {"devices": "all"}
    assert out["storage_config"] is None
    assert out["created_at"] is not None


# ──────────────────────────────────────────────────────────────────────────────
# _rewrite_endpoint_host (async)
# ──────────────────────────────────────────────────────────────────────────────


class _AsyncNodeDAO:
    def __init__(self, node: SimpleNamespace | None) -> None:
        self._node = node

    async def get_node_by_id(self, node_id: object) -> SimpleNamespace | None:
        return self._node


async def test_rewrite_endpoint_host_replaces_host_and_keeps_port() -> None:
    node = SimpleNamespace(host="10.0.0.9")
    svc = _make_service(_AsyncNodeDAO(node))
    out = await svc._rewrite_endpoint_host("http://agent-ip:8123/v1", node_id=uuid.uuid4())
    assert out == "http://10.0.0.9:8123/v1"


async def test_rewrite_endpoint_host_without_port() -> None:
    node = SimpleNamespace(host="10.0.0.9")
    svc = _make_service(_AsyncNodeDAO(node))
    out = await svc._rewrite_endpoint_host("http://agent-ip/health", node_id=uuid.uuid4())
    assert out == "http://10.0.0.9/health"


async def test_rewrite_endpoint_host_no_hostname_returns_unchanged() -> None:
    svc = _make_service(_AsyncNodeDAO(SimpleNamespace(host="10.0.0.9")))
    out = await svc._rewrite_endpoint_host("not-a-url", node_id=uuid.uuid4())
    assert out == "not-a-url"


async def test_rewrite_endpoint_host_missing_node_returns_unchanged() -> None:
    svc = _make_service(_AsyncNodeDAO(None))
    out = await svc._rewrite_endpoint_host("http://agent-ip:8123/v1", node_id=uuid.uuid4())
    assert out == "http://agent-ip:8123/v1"


async def test_rewrite_endpoint_host_node_without_host_returns_unchanged() -> None:
    svc = _make_service(_AsyncNodeDAO(SimpleNamespace(host=None)))
    out = await svc._rewrite_endpoint_host("http://agent-ip:8123/v1", node_id=uuid.uuid4())
    assert out == "http://agent-ip:8123/v1"
