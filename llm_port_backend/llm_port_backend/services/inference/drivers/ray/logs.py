"""Ray log retrieval, normalized into the driver-neutral contract (Phase 6, WI-2).

Everything Ray-shaped stops here.  The agent answers `FETCH_CONTAINER_LOGS`
with whatever the container runtime emitted, and Serve reports replicas in its
own structures; this module turns both into :class:`LogPage` so the API and the
frontend never learn what is running underneath.

Transport is request/response over node commands rather than a stream: the
agent already fetches container logs that way for native runtimes, it is enough
to diagnose a deployment, and live tailing is not part of Phase 6.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from llm_port_backend.db.models.inference import InferenceDeployment
from llm_port_backend.db.models.node_control import NodeCommandType
from llm_port_backend.services.inference.observability import (
    LogLine,
    LogPage,
    LogSource,
)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway

log = logging.getLogger(__name__)

# One log fetch is interactive: an operator is waiting on it.
_LOG_COMMAND_TIMEOUT_SEC = 60
_LOG_WAIT_BUDGET_SEC = 30.0

_MAX_TAIL = 5000

# ``2026-09-20T17:26:29.252000Z  WARNING  something happened``  and the common
# bracketed variants.  Anything that does not match is kept verbatim as the
# message, which is the honest outcome for an unstructured line.
_TS_PATTERNS = (
    re.compile(
        r"^\[?(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?\s+"
        r"(?:\[?(?P<level>[A-Z]{4,8})\]?\s+)?(?P<message>.*)$"
    ),
    re.compile(
        r"^(?P<level>[A-Z]{4,8})\s+(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+(?P<message>.*)$"
    ),
)

_LEVELS = {"TRACE", "DEBUG", "INFO", "WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"}


def _parse_timestamp(raw: str) -> datetime | None:
    text = raw.strip().replace(",", ".")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_log_line(raw: str) -> LogLine:
    """Normalize one raw line into ``(ts, level, message)``.

    Best effort by design: an unparseable line keeps its full text as the
    message rather than being dropped, because the line an operator needs is
    often the one that does not match the format.
    """
    stripped = raw.rstrip("\r\n")
    for pattern in _TS_PATTERNS:
        match = pattern.match(stripped)
        if not match:
            continue
        level = (match.group("level") or "").upper() or None
        if level is not None and level not in _LEVELS:
            # A capitalized first word is not a level; keep the line intact.
            break
        return LogLine(
            ts=_parse_timestamp(match.group("ts")),
            level=level,
            message=match.group("message"),
        )
    return LogLine(ts=None, level=None, message=stripped)


def normalize_log_text(
    text: str,
    *,
    source: LogSource,
    node_id: str | None = None,
    replica_id: str | None = None,
    tail: int | None = None,
) -> LogPage:
    """Turn a raw log blob into a :class:`LogPage`."""
    raw_lines = [line for line in (text or "").splitlines() if line.strip()]
    truncated = False
    if tail is not None and len(raw_lines) > tail:
        raw_lines = raw_lines[-tail:]
        truncated = True
    return LogPage(
        source=source,
        node_id=node_id,
        replica_id=replica_id,
        lines=[parse_log_line(line) for line in raw_lines],
        truncated=truncated,
    )


def _result_text(result: dict[str, Any]) -> str:
    """Pull the log body out of an agent result, whatever key it used."""
    for key in ("logs", "output", "stdout", "text", "content"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
    return ""


class RayLogReader:
    """Reads deployment logs through node commands and normalizes them."""

    def __init__(self, gateway: "NodeCommandGateway") -> None:
        self._gateway = gateway

    async def read(
        self,
        deployment: InferenceDeployment,
        *,
        source: LogSource,
        head_node_id: uuid.UUID,
        app_name: str,
        node_id: str | None = None,
        replica_id: str | None = None,
        tail: int = 200,
        since: str | None = None,
        runtime_bundle: dict[str, Any] | None = None,
    ) -> LogPage:
        """Fetch one page of logs for *deployment*."""
        tail = max(1, min(int(tail or 200), _MAX_TAIL))
        target_node = uuid.UUID(node_id) if node_id else head_node_id

        payload: dict[str, Any] = {
            "tail": tail,
            # The agent keys container lookup off these; for the Ray path the
            # container is the runtime bundle's, not a per-model runtime.
            "runtime_id": str(deployment.id),
            "runtime_name": app_name,
        }
        if since:
            payload["since"] = since
        if runtime_bundle is not None:
            payload["runtime_bundle"] = runtime_bundle
        if source == LogSource.SERVE_REPLICA and replica_id:
            payload["replica_id"] = replica_id

        try:
            command = await self._gateway.issue(
                node_id=target_node,
                command_type=NodeCommandType.FETCH_CONTAINER_LOGS.value,
                payload=payload,
                # Always fresh: a log read must never replay an older page.
                idempotency_key=f"inference-logs:{deployment.id}:{uuid.uuid4().hex[:12]}",
                timeout_sec=_LOG_COMMAND_TIMEOUT_SEC,
            )
            final = await self._gateway.wait(command.id, budget_sec=_LOG_WAIT_BUDGET_SEC)
        except Exception as exc:  # noqa: BLE001 - a log read never fails a request
            log.warning("Log fetch for deployment %s failed: %s", deployment.id, exc)
            return LogPage(
                source=source,
                node_id=str(target_node),
                replica_id=replica_id,
                detail=f"could not reach the node: {exc}",
            )

        if final is None:
            return LogPage(
                source=source,
                node_id=str(target_node),
                replica_id=replica_id,
                detail=f"no response from the node within {_LOG_WAIT_BUDGET_SEC:.0f}s",
            )

        result = dict(final.result_json or {})
        text = _result_text(result)
        if not text:
            return LogPage(
                source=source,
                node_id=str(target_node),
                replica_id=replica_id,
                detail=result.get("error") or "the node returned no log output",
            )

        page = normalize_log_text(
            text, source=source, node_id=str(target_node), replica_id=replica_id, tail=tail,
        )
        return page
