"""Read-only aggregation service for gateway observability data.

All queries run against the gateway (``llm_api``) database via the
secondary ``llm_graph_trace_session_factory`` engine.  Queries use raw
SQL with ``text()`` and bind parameters — no ORM coupling.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Hard caps
MAX_QUERY_ROWS = 500
MAX_EXPORT_ROWS = 5000
MAX_RANGE_DAYS = 90


class ObservabilityService:
    """Aggregation queries against ``llm_gateway_request_log``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Summary ───────────────────────────────────────────────────

    async def get_summary(
        self,
        start: datetime,
        end: datetime,
    ) -> dict:
        """Aggregate totals and breakdowns for the given time range."""
        # Totals
        totals_q = text("""
            SELECT
                COUNT(*)                                AS total_requests,
                COALESCE(SUM(prompt_tokens), 0)         AS total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)     AS total_completion_tokens,
                COALESCE(SUM(total_tokens), 0)          AS total_tokens,
                SUM(estimated_total_cost)               AS estimated_total_cost,
                COALESCE(SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END), 0) AS error_count,
                AVG(latency_ms)                         AS avg_latency_ms
            FROM llm_gateway_request_log
            WHERE created_at >= :start AND created_at < :end
        """)
        result = await self._session.execute(totals_q, {"start": start, "end": end})
        totals = dict(result.mappings().one())

        # By provider
        provider_q = text("""
            SELECT
                COALESCE(CAST(provider_instance_id AS TEXT), 'unknown') AS provider_instance_id,
                COUNT(*)                  AS total_requests,
                COALESCE(SUM(total_tokens), 0)  AS total_tokens,
                SUM(estimated_total_cost) AS estimated_total_cost
            FROM llm_gateway_request_log
            WHERE created_at >= :start AND created_at < :end
            GROUP BY provider_instance_id
            ORDER BY total_requests DESC
            LIMIT 50
        """)
        prov_result = await self._session.execute(provider_q, {"start": start, "end": end})
        by_provider = [dict(r) for r in prov_result.mappings().all()]

        # By model
        model_q = text("""
            SELECT
                COALESCE(model_alias, 'unknown') AS model_alias,
                COUNT(*)                  AS total_requests,
                COALESCE(SUM(total_tokens), 0)  AS total_tokens,
                SUM(estimated_total_cost) AS estimated_total_cost
            FROM llm_gateway_request_log
            WHERE created_at >= :start AND created_at < :end
            GROUP BY model_alias
            ORDER BY total_requests DESC
            LIMIT 50
        """)
        model_result = await self._session.execute(model_q, {"start": start, "end": end})
        by_model = [dict(r) for r in model_result.mappings().all()]

        # Top users
        user_q = text("""
            SELECT
                user_id,
                COUNT(*)                  AS total_requests,
                COALESCE(SUM(total_tokens), 0)  AS total_tokens,
                SUM(estimated_total_cost) AS estimated_total_cost
            FROM llm_gateway_request_log
            WHERE created_at >= :start AND created_at < :end
            GROUP BY user_id
            ORDER BY total_requests DESC
            LIMIT 20
        """)
        user_result = await self._session.execute(user_q, {"start": start, "end": end})
        top_users = [dict(r) for r in user_result.mappings().all()]

        return {
            **totals,
            "by_provider": by_provider,
            "by_model": by_model,
            "top_users": top_users,
        }

    # ── Timeseries ────────────────────────────────────────────────

    async def get_timeseries(
        self,
        start: datetime,
        end: datetime,
        granularity: str = "day",
    ) -> list[dict]:
        """Return time-bucketed aggregates."""
        gran = "day" if granularity not in ("hour", "day", "week") else granularity
        q = text(f"""
            SELECT
                date_trunc(:gran, created_at)   AS bucket,
                COUNT(*)                        AS total_requests,
                COALESCE(SUM(total_tokens), 0)  AS total_tokens,
                SUM(estimated_total_cost)       AS estimated_total_cost,
                COALESCE(SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END), 0) AS error_count,
                AVG(latency_ms)                 AS avg_latency_ms
            FROM llm_gateway_request_log
            WHERE created_at >= :start AND created_at < :end
            GROUP BY 1
            ORDER BY 1
        """)
        result = await self._session.execute(
            q, {"gran": gran, "start": start, "end": end},
        )
        return [dict(r) for r in result.mappings().all()]

    # ── Performance ───────────────────────────────────────────────

    async def get_performance(
        self,
        start: datetime,
        end: datetime,
    ) -> dict:
        """Latency percentiles, throughput, error rate."""
        q = text("""
            SELECT
                PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
                PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_latency_ms,
                PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY ttft_ms)    AS p50_ttft_ms,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY ttft_ms)    AS p95_ttft_ms,
                PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY ttft_ms)    AS p99_ttft_ms,
                AVG(latency_ms)        AS avg_latency_ms,
                COUNT(*)               AS total_requests,
                COALESCE(SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END), 0) AS error_count
            FROM llm_gateway_request_log
            WHERE created_at >= :start AND created_at < :end
        """)
        result = await self._session.execute(q, {"start": start, "end": end})
        row = dict(result.mappings().one())
        total = row["total_requests"] or 0
        errors = row["error_count"] or 0
        row["error_rate"] = round(errors / total, 4) if total > 0 else None
        return row

    # ── Request list ──────────────────────────────────────────────

    async def get_requests(
        self,
        start: datetime,
        end: datetime,
        page: int = 1,
        limit: int = 50,
        model_alias: str | None = None,
        user_id: str | None = None,
        status_code: int | None = None,
    ) -> dict:
        """Paginated request list."""
        limit = min(limit, MAX_QUERY_ROWS)
        offset = (max(page, 1) - 1) * limit

        filters = "WHERE created_at >= :start AND created_at < :end"
        params: dict = {"start": start, "end": end, "limit": limit, "offset": offset}
        if model_alias:
            filters += " AND model_alias = :model_alias"
            params["model_alias"] = model_alias
        if user_id:
            filters += " AND user_id = :user_id"
            params["user_id"] = user_id
        if status_code is not None:
            filters += " AND status_code = :status_code"
            params["status_code"] = status_code

        count_q = text(f"SELECT COUNT(*) FROM llm_gateway_request_log {filters}")
        count_result = await self._session.execute(count_q, params)
        total = count_result.scalar_one()

        data_q = text(f"""
            SELECT
                id, request_id, trace_id, tenant_id, user_id,
                model_alias, CAST(provider_instance_id AS TEXT) AS provider_instance_id,
                endpoint, status_code, latency_ms, ttft_ms,
                prompt_tokens, completion_tokens, total_tokens, error_code,
                estimated_input_cost, estimated_output_cost, estimated_total_cost,
                currency, cost_estimate_status, cached_tokens, stream,
                session_id, finish_reason, retry_count,
                skills_used, rag_context,
                mcp_tool_call_count, mcp_tool_loop_iterations,
                created_at
            FROM llm_gateway_request_log
            {filters}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """)
        result = await self._session.execute(data_q, params)
        items = []
        for r in result.mappings().all():
            row = dict(r)
            row["id"] = str(row["id"])
            items.append(row)

        return {"items": items, "total": total, "page": page, "limit": limit}

    # ── Request detail ────────────────────────────────────────────

    async def get_request_detail(self, request_id: str) -> dict | None:
        """Single request by request_id."""
        q = text("""
            SELECT
                id, request_id, trace_id, tenant_id, user_id,
                model_alias, CAST(provider_instance_id AS TEXT) AS provider_instance_id,
                endpoint, status_code, latency_ms, ttft_ms,
                prompt_tokens, completion_tokens, total_tokens, error_code,
                estimated_input_cost, estimated_output_cost, estimated_total_cost,
                currency, price_catalog_id, cost_estimate_status,
                cached_tokens, stream,
                session_id, finish_reason, retry_count,
                skills_used, rag_context,
                mcp_tool_call_count, mcp_tool_loop_iterations,
                created_at
            FROM llm_gateway_request_log
            WHERE request_id = :request_id
            ORDER BY created_at DESC
            LIMIT 1
        """)
        result = await self._session.execute(q, {"request_id": request_id})
        row = result.mappings().first()
        if row is None:
            return None
        d = dict(row)
        d["id"] = str(d["id"])
        if d.get("price_catalog_id"):
            d["price_catalog_id"] = str(d["price_catalog_id"])
        return d

    async def get_request_by_trace_id(self, trace_id: str) -> dict | None:
        """Single request by trace_id (most recent if multiple)."""
        q = text("""
            SELECT
                id, request_id, trace_id, tenant_id, user_id,
                model_alias, CAST(provider_instance_id AS TEXT) AS provider_instance_id,
                endpoint, status_code, latency_ms, ttft_ms,
                prompt_tokens, completion_tokens, total_tokens, error_code,
                estimated_input_cost, estimated_output_cost, estimated_total_cost,
                currency, price_catalog_id, cost_estimate_status,
                cached_tokens, stream,
                session_id, finish_reason, retry_count,
                skills_used, rag_context,
                mcp_tool_call_count, mcp_tool_loop_iterations,
                created_at
            FROM llm_gateway_request_log
            WHERE trace_id = :trace_id
            ORDER BY created_at DESC
            LIMIT 1
        """)
        result = await self._session.execute(q, {"trace_id": trace_id})
        row = result.mappings().first()
        if row is None:
            return None
        d = dict(row)
        d["id"] = str(d["id"])
        if d.get("price_catalog_id"):
            d["price_catalog_id"] = str(d["price_catalog_id"])
        return d

    # ── Tool call detail ──────────────────────────────────────────

    async def get_tool_calls(self, request_id: str) -> list[dict]:
        """Return MCP tool call logs for a given request."""
        q = text("""
            SELECT
                id, request_id, iteration, tool_name, mcp_server,
                latency_ms, is_error, error_message, created_at
            FROM llm_tool_call_log
            WHERE request_id = :request_id
            ORDER BY iteration, created_at
        """)
        result = await self._session.execute(q, {"request_id": request_id})
        items = []
        for r in result.mappings().all():
            row = dict(r)
            row["id"] = str(row["id"])
            items.append(row)
        return items

    # ── Session cost ──────────────────────────────────────────────

    async def get_session_cost(self, session_id: str) -> dict | None:
        """Aggregate cost for a chat session (via trace_id = session_id)."""
        q = text("""
            SELECT
                COUNT(*)                          AS total_requests,
                COALESCE(SUM(total_tokens), 0)    AS total_tokens,
                SUM(estimated_total_cost)         AS estimated_total_cost
            FROM llm_gateway_request_log
            WHERE trace_id = :session_id
        """)
        result = await self._session.execute(q, {"session_id": session_id})
        row = dict(result.mappings().one())
        row["session_id"] = session_id
        return row

    # ── CSV export ────────────────────────────────────────────────

    async def export_csv(
        self,
        start: datetime,
        end: datetime,
    ) -> AsyncGenerator[str, None]:
        """Stream CSV rows for request log data (capped at 5K rows)."""
        q = text(f"""
            SELECT
                request_id, created_at, tenant_id, user_id,
                model_alias, CAST(provider_instance_id AS TEXT) AS provider_instance_id,
                endpoint, status_code, latency_ms, ttft_ms,
                prompt_tokens, completion_tokens, total_tokens,
                estimated_input_cost, estimated_output_cost, estimated_total_cost,
                currency, cost_estimate_status, stream, error_code
            FROM llm_gateway_request_log
            WHERE created_at >= :start AND created_at < :end
            ORDER BY created_at DESC
            LIMIT {MAX_EXPORT_ROWS}
        """)
        result = await self._session.execute(q, {"start": start, "end": end})
        columns = list(result.keys())

        # Header row
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()

        # Data rows
        for row in result.mappings():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([str(row[c]) if row[c] is not None else "" for c in columns])
            yield buf.getvalue()

    # ── Model names ───────────────────────────────────────────────

    async def get_model_names(self, query: str = "") -> list[str]:
        """Return distinct model names from active routing aliases, price catalog, and provider instances."""
        q = text("""
            SELECT DISTINCT name FROM (
                SELECT ma.alias AS name
                FROM llm_model_alias ma
                JOIN llm_pool_membership pm ON pm.model_alias = ma.alias
                WHERE ma.enabled = true AND pm.enabled = true
                UNION
                SELECT model AS name
                FROM price_catalog
                WHERE active = true
                UNION
                SELECT litellm_model AS name
                FROM llm_provider_instance
                WHERE litellm_model IS NOT NULL AND litellm_model != ''
            ) sub
            WHERE (:q = '' OR name ILIKE '%' || :q || '%')
            ORDER BY name
            LIMIT 50
        """)
        result = await self._session.execute(q, {"q": query})
        return [row[0] for row in result.fetchall()]

    # ── Provider names ────────────────────────────────────────────

    async def get_provider_names(self, query: str = "") -> list[str]:
        """Return distinct provider names from price catalog and provider instances."""
        q = text("""
            SELECT DISTINCT name FROM (
                SELECT provider AS name
                FROM price_catalog
                WHERE active = true AND provider IS NOT NULL
                UNION
                SELECT litellm_provider AS name
                FROM llm_provider_instance
                WHERE litellm_provider IS NOT NULL AND litellm_provider != ''
            ) sub
            WHERE (:q = '' OR name ILIKE '%' || :q || '%')
            ORDER BY name
            LIMIT 50
        """)
        result = await self._session.execute(q, {"q": query})
        return [row[0] for row in result.fetchall()]

    # ── Force-recalculate costs ───────────────────────────────────

    async def recalculate_costs(self) -> dict:
        """Recalculate cost estimates for all request-log rows using current price_catalog.

        Matches each row's ``model_alias`` against active ``price_catalog``
        entries (by model name).  Rows that match get updated costs; rows
        without a matching catalog entry are marked ``unavailable``.

        Returns a summary dict with counts of updated / unavailable rows.
        """
        # 1. Update rows that DO have a matching active price catalog entry.
        #    Pick the newest active entry per model (latest effective_from).
        update_matched = text("""
            WITH latest_price AS (
                SELECT DISTINCT ON (model)
                    id, model, input_price_per_1k, output_price_per_1k, currency
                FROM price_catalog
                WHERE active = true
                ORDER BY model, effective_from DESC
            )
            UPDATE llm_gateway_request_log r
            SET
                estimated_input_cost = CASE
                    WHEN r.prompt_tokens IS NOT NULL
                    THEN (COALESCE(r.prompt_tokens, 0) - COALESCE(r.cached_tokens, 0))
                         * p.input_price_per_1k / 1000.0
                    ELSE NULL
                END,
                estimated_output_cost = CASE
                    WHEN r.completion_tokens IS NOT NULL
                    THEN r.completion_tokens * p.output_price_per_1k / 1000.0
                    ELSE NULL
                END,
                estimated_total_cost = CASE
                    WHEN r.prompt_tokens IS NOT NULL OR r.completion_tokens IS NOT NULL
                    THEN COALESCE(
                        (COALESCE(r.prompt_tokens, 0) - COALESCE(r.cached_tokens, 0))
                            * p.input_price_per_1k / 1000.0, 0
                    ) + COALESCE(
                        r.completion_tokens * p.output_price_per_1k / 1000.0, 0
                    )
                    ELSE NULL
                END,
                currency = p.currency,
                price_catalog_id = p.id,
                cost_estimate_status = CASE
                    WHEN r.prompt_tokens IS NOT NULL AND r.completion_tokens IS NOT NULL
                        THEN 'complete'
                    WHEN r.prompt_tokens IS NOT NULL OR r.completion_tokens IS NOT NULL
                        THEN 'partial'
                    ELSE 'unavailable'
                END
            FROM latest_price p
            WHERE r.model_alias = p.model
        """)
        result_matched = await self._session.execute(update_matched)
        matched_count = result_matched.rowcount

        # 2. Mark rows without a matching price as unavailable.
        update_unmatched = text("""
            UPDATE llm_gateway_request_log r
            SET
                estimated_input_cost = NULL,
                estimated_output_cost = NULL,
                estimated_total_cost = NULL,
                price_catalog_id = NULL,
                cost_estimate_status = 'unavailable'
            WHERE r.model_alias NOT IN (
                SELECT model FROM price_catalog WHERE active = true
            )
            AND (r.cost_estimate_status IS DISTINCT FROM 'unavailable'
                 OR r.estimated_total_cost IS NOT NULL)
        """)
        result_unmatched = await self._session.execute(update_unmatched)
        unmatched_count = result_unmatched.rowcount

        await self._session.commit()

        log.info(
            "Cost recalculation complete: %d matched, %d unavailable",
            matched_count, unmatched_count,
        )
        return {
            "recalculated": matched_count,
            "unavailable": unmatched_count,
        }
