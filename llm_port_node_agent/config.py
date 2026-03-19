"""Environment-driven runtime configuration."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(slots=True)
class AgentConfig:
    """Static process configuration resolved from env vars."""

    backend_url: str
    agent_id: str
    host: str
    advertise_host: str
    advertise_scheme: str
    enrollment_token: str | None
    state_path: Path
    heartbeat_interval_sec: int
    inventory_interval_sec: int
    reconnect_min_sec: float
    reconnect_max_sec: float
    request_timeout_sec: float
    verify_tls: bool
    log_level: str

    @classmethod
    def from_env(cls) -> AgentConfig:
        """Load config using LLM_PORT_NODE_AGENT_* environment vars."""
        hostname = socket.gethostname()
        host = os.getenv("LLM_PORT_NODE_AGENT_HOST", hostname)
        advertise_host = os.getenv("LLM_PORT_NODE_AGENT_ADVERTISE_HOST", host).strip()
        advertise_scheme = os.getenv("LLM_PORT_NODE_AGENT_ADVERTISE_SCHEME", "http").strip().lower() or "http"
        if advertise_scheme not in {"http", "https"}:
            advertise_scheme = "http"
        return cls(
            backend_url=os.getenv("LLM_PORT_NODE_AGENT_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/"),
            agent_id=os.getenv("LLM_PORT_NODE_AGENT_AGENT_ID", hostname),
            host=host,
            advertise_host=advertise_host or host,
            advertise_scheme=advertise_scheme,
            enrollment_token=os.getenv("LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN"),
            state_path=Path(
                os.getenv(
                    "LLM_PORT_NODE_AGENT_STATE_PATH",
                    "/var/lib/llm-port-node-agent/state.json",
                ),
            ),
            heartbeat_interval_sec=int(os.getenv("LLM_PORT_NODE_AGENT_HEARTBEAT_INTERVAL_SEC", "15")),
            inventory_interval_sec=int(os.getenv("LLM_PORT_NODE_AGENT_INVENTORY_INTERVAL_SEC", "60")),
            reconnect_min_sec=float(os.getenv("LLM_PORT_NODE_AGENT_RECONNECT_MIN_SEC", "2")),
            reconnect_max_sec=float(os.getenv("LLM_PORT_NODE_AGENT_RECONNECT_MAX_SEC", "30")),
            request_timeout_sec=float(os.getenv("LLM_PORT_NODE_AGENT_REQUEST_TIMEOUT_SEC", "20")),
            verify_tls=_env_bool("LLM_PORT_NODE_AGENT_VERIFY_TLS", True),
            log_level=os.getenv("LLM_PORT_NODE_AGENT_LOG_LEVEL", "INFO").upper(),
        )
