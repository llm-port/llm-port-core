"""ClickHouse housekeeping: its own diagnostic logs.

``llm_port_shared/clickhouse/config.d/system-logs.xml`` turns off the system
log tables Langfuse never reads. Off, a table stops growing but keeps what it
holds -- 41 GB of ``trace_log`` on one install, which had filled its disk --
so an upgrade drops them. So too the numbered copies (``query_log_0``)
ClickHouse leaves behind whenever it changes a log table's schema.
"""

from __future__ import annotations

import re

from llmport.core.compose import ComposeContext, _run

#: The system log tables ``system-logs.xml`` turns off.
DISABLED_LOGS = (
    "trace_log",
    "text_log",
    "opentelemetry_span_log",
    "asynchronous_metric_log",
    "metric_log",
    "latency_log",
)

_DISABLED = re.compile(rf"^({'|'.join(DISABLED_LOGS)})(_\d+)?$")
_RENAMED = re.compile(r"^\w+_log_\d+$")


def stale_log_tables(names: list[str]) -> list[str]:
    """Of the ``system`` tables *names*, the logs that are off or left behind."""
    return [n for n in names if _DISABLED.match(n) or _RENAMED.match(n)]


def _query(ctx: ComposeContext, sql: str) -> tuple[int, str]:
    """Run *sql* in the ClickHouse container, as the user the container was made with."""
    cmd = ctx.base_cmd() + [
        "exec", "-T", "clickhouse", "sh", "-c",
        'clickhouse-client -u "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" -q "$1"',
        "sh", sql,
    ]
    result = _run(cmd, capture=True)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def drop_stale_logs(ctx: ComposeContext) -> list[str]:
    """Drop the diagnostic log tables ClickHouse no longer writes; the names dropped.

    Raises ``RuntimeError`` when ClickHouse cannot be asked -- the caller
    decides whether that stops it.
    """
    code, out = _query(ctx, "SELECT name FROM system.tables WHERE database = 'system'")
    if code != 0:
        raise RuntimeError(out.strip() or f"clickhouse-client exited with {code}")
    dropped = []
    for name in stale_log_tables(out.split()):
        code, out = _query(ctx, f"DROP TABLE IF EXISTS system.`{name}` SYNC")
        if code != 0:
            raise RuntimeError(f"system.{name}: {out.strip()}")
        dropped.append(name)
    return dropped
