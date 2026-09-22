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

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
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

#: How old a completed log fetch may be and still be worth showing at once.
#: Above this the caller waits for a fresh one rather than presenting a stale
#: page as current.
_RECENT_LOG_MAX_AGE_SEC = 120.0

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

    async def _recent_log_result(
        self, node_id: uuid.UUID, deployment_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """The newest completed log fetch for this deployment, if one is fresh.

        Bounded by age so a panel never shows a page from a previous session
        as though it were current; past that the caller waits for a real one.
        """
        try:
            commands = await self._gateway.list_recent(
                node_id=node_id,
                command_type=NodeCommandType.FETCH_CONTAINER_LOGS.value,
                limit=12,
            )
        except Exception:  # noqa: BLE001 - a log read never fails a request
            return None
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=_RECENT_LOG_MAX_AGE_SEC)
        for command in commands:
            if command.status != "succeeded" or not command.result_json:
                continue
            completed = command.completed_at
            if completed is None:
                continue
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=UTC)
            if completed < cutoff:
                break
            if str((command.payload_json or {}).get("runtime_id")) == str(deployment_id):
                return dict(command.result_json)
        return None

    async def _read_from_loki(
        self,
        deployment: InferenceDeployment,
        *,
        source: LogSource,
        app_name: str,
        replica_id: str | None,
        tail: int,
    ) -> LogPage | None:
        """One page from Loki, or ``None`` to let the caller fall back.

        ``None`` rather than an empty page on every failure path, deliberately:
        an empty page is an answer ("this deployment logged nothing") and
        would stop the node fallback being tried. Only a successful query with
        lines counts as an answer here.
        """
        selector = f'{{job="ray-serve", app="{_escape_label(app_name)}"}}'
        if replica_id:
            selector = (
                f'{{job="ray-serve", app="{_escape_label(app_name)}", '
                f'replica="{_escape_label(replica_id)}"}}'
            )

        try:
            from llm_port_backend.web.api.logs.views import (  # noqa: PLC0415
                LokiUpstreamError,
                _request_loki_json,
            )
        except Exception:  # noqa: BLE001 - no Loki proxy compiled in
            return None

        now = datetime.now(tz=UTC)
        params = {
            "query": selector,
            # A page, not a history. The panel shows a tail; anything older
            # is a Loki query the operator can widen themselves.
            "start": str(int((now - timedelta(hours=6)).timestamp() * 1_000_000_000)),
            "end": str(int(now.timestamp() * 1_000_000_000)),
            "limit": str(tail),
            "direction": "BACKWARD",
        }
        try:
            payload = await _request_loki_json(
                "/loki/api/v1/query_range", params=params, timeout=5.0
            )
        except Exception:  # noqa: BLE001 - Loki being down is not an error here
            log.debug("Loki log read failed for %s", deployment.id, exc_info=True)
            return None

        lines = _lines_from_loki(payload)
        if not lines:
            return None

        return LogPage(
            source=source,
            node_id=None,
            replica_id=replica_id,
            # Oldest first: Loki answers newest-first for a BACKWARD query,
            # and a log panel reads downwards.
            lines=list(reversed(lines))[-tail:],
            detail=None,
        )

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

        # Loki first: the agent ships these lines continuously, so they are
        # already there and the answer costs one query instead of a round
        # trip to a node that may be busy. Falls through on anything -- an
        # empty result included -- so a node whose bundle predates the
        # session-directory mount still works.
        page = await self._read_from_loki(
            deployment,
            source=source,
            app_name=app_name,
            replica_id=replica_id,
            tail=tail,
        )
        if page is not None:
            return page

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
            # Name the container explicitly.  Under the Ray driver there is no
            # per-deployment container: every replica runs inside the runtime
            # bundle's one.  Without this the agent derives
            # "llm-port-<app_name>", finds nothing, and every log read returns
            # "Container ... does not exist on this node" -- a successful
            # fetch of the wrong thing.
            bundle_container = runtime_bundle.get("name")
            if isinstance(bundle_container, str) and bundle_container.strip():
                payload["container_name"] = bundle_container.strip()
        if source == LogSource.SERVE_REPLICA and replica_id:
            payload["replica_id"] = replica_id

        # Serve the most recent fetch that finished, and start the next one.
        #
        # This was once load-bearing for a different reason: a queued command
        # was only delivered when the agent next spoke, so a fetch doing ~0.1s
        # of work took 30-45s end to end and the request gave up at 30s,
        # reporting "no response from the node" for a fetch that had in fact
        # succeeded.  The backend now wakes the node's stream as soon as a
        # command is queued and the round trip is ~0.5s, measured on the DGX
        # pair.
        #
        # It stays because it is the right shape regardless: a polling log
        # view does not need *this* request to carry the newest page, it needs
        # a page now and a newer one on the next poll.  That still holds when
        # the node is genuinely busy or briefly unreachable.
        recent = await self._recent_log_result(target_node, deployment.id)

        try:
            command = await self._gateway.issue(
                node_id=target_node,
                command_type=NodeCommandType.FETCH_CONTAINER_LOGS.value,
                payload=payload,
                # Always fresh: a log read must never replay an older page.
                idempotency_key=f"inference-logs:{deployment.id}:{uuid.uuid4().hex[:12]}",
                timeout_sec=_LOG_COMMAND_TIMEOUT_SEC,
            )
            # Only wait if there is nothing to show yet.  With a page in
            # hand the fetch just started becomes the next poll's answer.
            final = (
                None
                if recent is not None
                else await self._gateway.wait(command.id, budget_sec=_LOG_WAIT_BUDGET_SEC)
            )
        except Exception as exc:  # noqa: BLE001 - a log read never fails a request
            log.warning("Log fetch for deployment %s failed: %s", deployment.id, exc)
            return LogPage(
                source=source,
                node_id=str(target_node),
                replica_id=replica_id,
                detail=f"could not reach the node: {exc}",
            )

        if final is None and recent is None:
            return LogPage(
                source=source,
                node_id=str(target_node),
                replica_id=replica_id,
                detail=(
                    f"the node has not answered within {_LOG_WAIT_BUDGET_SEC:.0f}s; "
                    "the fetch is still running and the next refresh should show it"
                ),
            )

        result = dict((final.result_json if final is not None else recent) or {})
        text = _result_text(result)
        if not text:
            return LogPage(
                source=source,
                node_id=str(target_node),
                replica_id=replica_id,
                detail=(
                    result.get("error")
                    or (
                        # The runtime container idles on `sleep infinity` and
                        # Ray writes to files inside it, so `docker logs` on
                        # it is legitimately empty.  Saying "no output" alone
                        # reads as a fault; the replica's own log file is
                        # where this deployment actually writes.
                        "the runtime container produced no console output — Ray "
                        "writes per-replica logs to files inside it, which this "
                        "reader does not yet collect"
                        if runtime_bundle is not None
                        else "the node returned no log output"
                    )
                ),
            )

        page = normalize_log_text(
            text, source=source, node_id=str(target_node), replica_id=replica_id, tail=tail,
        )
        return page


def _escape_label(value: str) -> str:
    """Make a value safe inside a LogQL double-quoted matcher.

    These come from our own records (an app name, a Ray replica id), but a
    stray quote would turn a selector into a syntax error at best.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _lines_from_loki(payload: dict[str, Any]) -> list[LogLine]:
    """Normalise a Loki ``query_range`` body into log lines.

    Each entry is ``[<nanoseconds as a string>, <line>]``. The line is JSON
    when it came from a Serve replica -- the compiler asks for JSON encoding
    -- so the level and message are read from fields rather than parsed out
    of formatted text. Anything else ships as-is rather than being dropped.
    """
    result = ((payload or {}).get("data") or {}).get("result") or []
    out: list[LogLine] = []
    for stream in result:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") or {}
        for value in stream.get("values") or []:
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            raw_ts, raw_line = value[0], value[1]
            try:
                ts = datetime.fromtimestamp(int(raw_ts) / 1_000_000_000, tz=UTC)
            except (ValueError, TypeError, OverflowError):
                ts = datetime.now(tz=UTC)

            level = str(labels.get("level") or "").upper() or None
            message = str(raw_line)
            stripped = message.lstrip()
            if stripped.startswith("{"):
                try:
                    doc = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    doc = None
                if isinstance(doc, dict):
                    message = str(doc.get("message") or message)
                    level = str(doc.get("levelname") or level or "").upper() or None
            out.append(LogLine(ts=ts, level=level, message=message))
    out.sort(key=lambda line: line.ts)
    return out
