"""Runtime monitoring provisioner — vLLM /metrics → Prometheus → Grafana.

Centralized per-runtime observability for the shared-stack deployment:

* **Scrape targets** — ``prometheus/targets.json`` (file_sd) is rebuilt
  from the set of provisionable LLM runtimes.
* **Dashboards** — one per-runtime Grafana dashboard rendered from a
  versioned template (``llm_port_shared/grafana/dashboards/templates/
  vllm-runtime.json``) into the dashboard file-provider directory that
  Grafana already reads.  The file provider has
  ``disableDeletion: false``, so removing the file removes the
  dashboard automatically.
* **Stat cards** — :meth:`MonitoringProvisioner.stats` queries the
  local Prometheus HTTP API (see :data:`STAT_QUERIES`); metric names
  mirror the dashboards' stat panels so cards and dashboard agree.

Everything is gated by ``settings.llm_monitoring_enabled`` (off by
default).  All failures are log-and-swallow so monitoring never breaks
runtime lifecycle operations.

Lifecycle hooks (``services/nodes/service.py``, ``services/llm/service.py``):

* provision — DEPLOY/START/RESTART succeeds (endpoint known) and on
  ``workload.health.running`` recovery.  Idempotent, so safe to call on
  every state transition; this also re-syncs after endpoint changes.
* deprovision — REMOVE / delete paths only.  Intentionally stopped
  runtimes KEEP their dashboard + scrape target: history stays
  browsable in Grafana and the target simply reports ``up=0`` until the
  next START re-provisions.

Startup reconciliation: ``rebuild_all`` is called once from
``web/lifespan.py`` after boot and re-provisions every vLLM runtime
that has a known endpoint (any status).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.llm import (
    LLMModel,
    LLMProvider,
    LLMRuntime,
    ProviderType,
)
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)

# Placeholder tokens in the dashboard template (see the template file;
# ``__RUNTIME_NAME__`` occurs in every vLLM selector).
_TPL_UID = "__UID__"
_TPL_TITLE = "__TITLE__"
_TPL_RUNTIME_NAME = "__RUNTIME_NAME__"
_TPL_INSTANCE = "__INSTANCE__"

#: Debounce window for Prometheus file_sd reloads (batch rapid writes).
_RELOAD_DEBOUNCE_SEC = 2.5

#: Stat-card metric expressions keyed by API field name.  ``{rt}`` is
#: replaced with ``{runtime_name="<name>"}`` — the name is the same
#: value baked into the dashboard template, so cards match panels.
STAT_QUERIES: dict[str, str] = {
    "running_requests": "sum(vllm:num_requests_running{{rt}})",
    "waiting_requests": "sum(vllm:num_requests_waiting{{rt}})",
    "kv_cache_usage": "100 * avg(vllm:kv_cache_usage_perc{{rt}})",
    "prefix_cache_hit_rate": (
        '100 * sum(rate(vllm:prefix_cache_hits_total{{rt}}[5m])) '
        "/ clamp_min(sum(rate(vllm:prefix_cache_queries_total{{rt}}[5m])), 1)"
    ),
    "mtp_acceptance": (
        '100 * sum(rate(vllm:spec_decode_num_accepted_tokens_total{{rt}}[5m])) '
        "/ clamp_min(sum(rate(vllm:spec_decode_num_draft_tokens_total{{rt}}[5m])), 1)"
    ),
    "generation_tokens_per_sec": "sum(rate(vllm:generation_tokens_total{{rt}}[1m]))",
    "preemption_rate": "sum(rate(vllm:num_preemptions_total{{rt}}[5m]))",
}


def dashboard_uid_for(runtime_id: uuid.UUID | str) -> str:
    """Deterministic dashboard uid: ``vllm-rt-<hash8(runtime.id)>``."""
    return f"vllm-rt-{hashlib.sha256(str(runtime_id).encode()).hexdigest()[:8]}"


def is_monitored(provider: Any) -> bool:
    """True when the provider type exposes a scrapeable /metrics.

    vLLM exposes ``/metrics`` (``--enable-metrics``); other provider
    types (llama.cpp / TGI / cloud) are out of scope for v1.
    """
    return getattr(provider, "type", None) == ProviderType.VLLM or (
        str(getattr(provider, "type", "") or "").upper() == "VLLM"
    )


def endpoint_host_for_target(endpoint_url: str) -> tuple[str, int | None] | None:
    """Split an endpoint URL into (host, port) for a scrape target.

    Loopback endpoints (local-docker runtimes on the core host) are
    rewritten to ``host.docker.internal`` so the prometheus container
    (``extra_hosts: host-gateway``) can reach the host-published port.
    Returns ``None`` for unparseable URLs.
    """
    parsed = urllib.parse.urlparse(endpoint_url or "")
    if not parsed.hostname:
        return None
    host = parsed.hostname
    if host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
        host = "host.docker.internal"
    return host, parsed.port


def _atomic_write_json(path: Path, data: object) -> None:
    """Write JSON atomically (tmp file + replace) to avoid torn reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        Path(tmp_name).replace(path)
    except BaseException:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass
        raise


class MonitoringProvisioner:
    """Owns generated scrape targets + per-runtime dashboards.

    All public methods are safe to call repeatedly; every mutation
    ends in a debounced ``POST {prom_url}/-/reload``.
    """

    def __init__(
        self,
        *,
        prom_url: str = settings.prom_url,
        targets_file: str = settings.prom_targets_file,
        dashboard_dir: str = settings.prom_dashboard_dir,
        template_path: str = settings.dash_template,
        dashboard_folder: str = settings.dash_folder,
    ) -> None:
        self._prom_url = prom_url.rstrip("/")
        self._targets_file = Path(targets_file)
        self._dashboard_dir = Path(dashboard_dir)
        self._template_path = Path(template_path)
        self._dashboard_folder = dashboard_folder
        self._template_cache: str | None = None
        self._reload_lock = asyncio.Lock()
        self._reload_task: asyncio.Task[None] | None = None
        self._http_timeout = 10.0

    # ── template / rendering ───────────────────────────────────────────
    def _template(self) -> str:
        if self._template_cache is None:
            self._template_cache = self._template_path.read_text(encoding="utf-8")
        return self._template_cache

    def _render_dashboard(
        self,
        *,
        runtime_id: uuid.UUID,
        title: str,
        runtime_name: str,
        instance: str,
    ) -> tuple[Path, object]:
        rendered = (
            self._template()
            .replace(_TPL_UID, dashboard_uid_for(runtime_id))
            .replace(_TPL_TITLE, title)
            .replace(_TPL_RUNTIME_NAME, runtime_name)
            .replace(_TPL_INSTANCE, instance)
        )
        # Fail fast on a broken render before touching the filesystem.
        data = json.loads(rendered)
        return self.dashboard_path(runtime_id), data

    def dashboard_path(self, runtime_id: uuid.UUID | str) -> Path:
        return self._dashboard_dir / f"{dashboard_uid_for(runtime_id)}.json"

    @property
    def dashboard_folder(self) -> str:
        """Grafana folder that future API-based provisioning will use."""
        return self._dashboard_folder

    # ── identity helpers ───────────────────────────────────────────────
    @staticmethod
    def node_host_of(runtime: Any, provider: Any | None) -> str:
        """Human-readable node host for titles / labels.

        Preference: the provider's registered endpoint (remote nodes
        advertise themselves), then the runtime endpoint's hostname.
        """
        if provider is not None and getattr(provider, "endpoint_url", None):
            host = urllib.parse.urlparse(str(provider.endpoint_url)).hostname
            if host:
                return host
        if getattr(runtime, "endpoint_url", None):
            host = urllib.parse.urlparse(str(runtime.endpoint_url)).hostname
            if host:
                return host
        return "local"

    # ── provision ──────────────────────────────────────────────────────
    async def provision(self, session: AsyncSession, runtime_id: uuid.UUID | str) -> str | None:
        """Regenerate this runtime's scrape target + dashboard.

        Returns the public dashboard URL, or ``None`` when the runtime
        is not monitorable (non-vLLM / no endpoint / missing).
        """
        runtime_id = uuid.UUID(str(runtime_id))
        row = await self._load_rows_by_id(session, id_filter=LLMRuntime.id == runtime_id)
        if row is None:
            return None
        runtime, model, provider = row
        if not is_monitored(provider):
            return None
        endpoint_url = str(runtime.endpoint_url or "")
        if not endpoint_url.strip():
            log.info("monitoring: runtime %s has no endpoint yet — skip provision", runtime_id)
            return None
        target = endpoint_host_for_target(endpoint_url)
        if target is None:
            log.warning("monitoring: unparseable endpoint %r for %s", endpoint_url, runtime_id)
            return None

        host, port = target
        instance = f"{host}:{port}" if port else host
        node_host = self.node_host_of(runtime, provider)
        model_name = model.hf_repo_id or model.display_name or "unknown"
        provider_name = provider.name or "vLLM"
        title = f"{provider_name} · {model_name} ({node_host})"

        # 1) desired-state rebuild of targets.json (single upsert)
        entry = {
            "targets": [instance],
            "labels": {
                "job": "llm-runtimes",
                "runtime_id": str(runtime.id),
                "runtime_name": runtime.name,
                "provider_name": provider_name,
                "model_name": model_name,
                "node_host": node_host,
            },
        }
        await self._upsert_target(str(runtime.id), entry)

        # 2) dashboard
        path, data = self._render_dashboard(
            runtime_id=runtime.id,
            title=title,
            runtime_name=runtime.name,
            instance=instance,
        )
        _atomic_write_json(path, data)

        self._schedule_reload()
        log.info("monitoring: provisioned %s (%s → %s)", runtime.name, runtime_id, instance)
        return self.dashboard_url(runtime.id)

    # ── deprovision ────────────────────────────────────────────────────
    async def deprovision(self, runtime_id: uuid.UUID | str) -> None:
        """Remove this runtime's target + dashboard (idempotent)."""
        runtime_id = uuid.UUID(str(runtime_id))
        changed = False
        targets = self._read_targets()
        remaining = [t for t in targets if t.get("labels", {}).get("runtime_id") != str(runtime_id)]
        if len(remaining) != len(targets):
            _atomic_write_json(self._targets_file, remaining)
            changed = True
        dash = self.dashboard_path(runtime_id)
        if dash.exists():
            dash.unlink()
            changed = True
        if changed:
            self._schedule_reload()
            log.info("monitoring: deprovisioned %s", runtime_id)

    # ── full rebuild (startup catch-up) ────────────────────────────────
    async def rebuild_all(self, session: AsyncSession) -> int:
        """Re-provision every vLLM runtime that has a known endpoint.

        Status is intentionally NOT a filter: intentionally stopped
        runtimes keep their dashboard (history stays browsable) and
        their last-known endpoint (``up=0`` until the next start).
        Also drops stale target entries and orphaned dashboards.
        Returns the number of provisioned runtimes.
        """
        res = await session.execute(
            select(LLMRuntime, LLMModel, LLMProvider)
            .join(LLMModel, LLMModel.id == LLMRuntime.model_id)
            .join(LLMProvider, LLMProvider.id == LLMRuntime.provider_id),
        )
        entries: list[dict[str, Any]] = []
        provisioned_paths: set[str] = set()
        for runtime, model, provider in res.all():
            if not is_monitored(provider):
                continue
            endpoint_url = str(runtime.endpoint_url or "")
            target = endpoint_host_for_target(endpoint_url)
            if not endpoint_url.strip() or target is None:
                continue
            host, port = target
            instance = f"{host}:{port}" if port else host
            node_host = self.node_host_of(runtime, provider)
            model_name = model.hf_repo_id or model.display_name or "unknown"
            provider_name = provider.name or "vLLM"
            entries.append(
                {
                    "targets": [instance],
                    "labels": {
                        "job": "llm-runtimes",
                        "runtime_id": str(runtime.id),
                        "runtime_name": runtime.name,
                        "provider_name": provider_name,
                        "model_name": model_name,
                        "node_host": node_host,
                    },
                }
            )
            path, data = self._render_dashboard(
                runtime_id=runtime.id,
                title=f"{provider_name} · {model_name} ({node_host})",
                runtime_name=runtime.name,
                instance=instance,
            )
            _atomic_write_json(path, data)
            provisioned_paths.add(path.name)
        entries.sort(key=lambda t: str(t.get("labels", {}).get("runtime_name", "")))
        _atomic_write_json(self._targets_file, entries)

        if self._dashboard_dir.exists():
            for dash in self._dashboard_dir.glob("vllm-rt-*.json"):
                if dash.name not in provisioned_paths:
                    dash.unlink()

        self._schedule_reload()
        log.info("monitoring: rebuild_all → %d runtime(s)", len(entries))
        return len(entries)

    # ── stat cards ─────────────────────────────────────────────────────
    async def stats(self, runtime_id: uuid.UUID | str, runtime_name: str) -> dict[str, Any]:
        """Fetch stat-card values for one runtime (never raises).

        Returns ``{"enabled", "stale": bool, "dashboard_url": str|None,
        "stats": {key: float|None}}`` — ``stale`` is True when not a
        single expression produced a value (no data / query failure).
        """
        out: dict[str, Any] = {
            "enabled": True,
            "stale": True,
            "dashboard_url": self.dashboard_url(runtime_id),
            "stats": {key: None for key in STAT_QUERIES},
        }
        # vLLM runtime names are validated to [a-z0-9-]+ upstream; the
        # guard below is a final belt-and-braces check.
        if not runtime_name or '"' in runtime_name or "\x00" in runtime_name:
            return out
        rt = f'{{runtime_name="{runtime_name}"}}'
        exprs = [template.format(rt=rt) for template in STAT_QUERIES.values()]
        try:
            values = await asyncio.gather(*(self._prom_query(expr) for expr in exprs))
            for key, value in zip(STAT_QUERIES, values):
                out["stats"][key] = value
            if any(v is not None for v in values):
                out["stale"] = False
        except Exception:  # noqa: BLE001 - monitoring must not break the API
            log.warning("monitoring: stats query failed for %s", runtime_id, exc_info=True)
        return out

    async def _prom_query(self, expr: str) -> float | None:
        """Instant query (offloaded — urllib blocks up to the timeout)."""
        return await asyncio.to_thread(self._prom_query_sync, expr)

    def _prom_query_sync(self, expr: str) -> float | None:
        url = f"{self._prom_url}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._http_timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if payload.get("status") != "success":
            return None
        results = (payload.get("data") or {}).get("result") or []
        if not results:
            return None
        try:
            value = float(results[0].get("value", [None, "NaN"])[1])
        except (TypeError, ValueError):
            return None
        if value != value:  # NaN — not a valid JSON number
            return None
        return value

    # ── target file helpers ────────────────────────────────────────────
    def _read_targets(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self._targets_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [t for t in raw if isinstance(t, dict)]
        except (OSError, ValueError):
            pass
        return []

    async def _upsert_target(self, runtime_id: str, entry: dict[str, Any]) -> None:
        targets = [
            t for t in self._read_targets() if t.get("labels", {}).get("runtime_id") != runtime_id
        ]
        targets.append(entry)
        targets.sort(key=lambda t: str(t.get("labels", {}).get("runtime_name", "")))
        _atomic_write_json(self._targets_file, targets)

    # ── debounced reload ───────────────────────────────────────────────
    def _schedule_reload(self) -> None:
        loop = asyncio.get_running_loop()
        if self._reload_task is not None and not self._reload_task.done():
            self._reload_task.cancel()
        self._reload_task = loop.create_task(self._debounced_reload(), name="mon-prom-reload")

    async def _debounced_reload(self) -> None:
        try:
            await asyncio.sleep(_RELOAD_DEBOUNCE_SEC)
            async with self._reload_lock:
                await asyncio.to_thread(self._prom_reload)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    def _prom_reload(self) -> bool:
        try:
            req = urllib.request.Request(f"{self._prom_url}/-/reload", method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=self._http_timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError) as exc:
            log.debug("monitoring: prometheus not reachable yet: %s", exc)
            return False

    # ── public helpers ─────────────────────────────────────────────────
    def dashboard_url(self, runtime_id: uuid.UUID | str) -> str | None:
        """Public Grafana URL for a runtime dashboard."""
        base = str(getattr(settings, "grafana_url", None) or "").rstrip("/")
        if not base:
            # Same-origin nginx sub-path (shared stack layout).
            base = "/grafana"
        return f"{base}/d/{dashboard_uid_for(runtime_id)}/vllm-runtime?orgId=1"

    async def close(self) -> None:
        if self._reload_task is not None and not self._reload_task.done():
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            self._reload_task = None

    # ── shared loader ──────────────────────────────────────────────────
    async def _load_rows_by_id(
        self, session: AsyncSession, *, id_filter: Any,
    ) -> tuple[LLMRuntime, LLMModel, LLMProvider] | None:
        res = await session.execute(
            select(LLMRuntime, LLMModel, LLMProvider)
            .join(LLMModel, LLMModel.id == LLMRuntime.model_id)
            .join(LLMProvider, LLMProvider.id == LLMRuntime.provider_id)
            .where(id_filter)
        )
        row = res.one_or_none()
        if row is None:
            return None
        return (row[0], row[1], row[2])


# ── module singleton + lifecycle helpers ─────────────────────────────────────
_provisioner: MonitoringProvisioner | None = None


def get_monitoring_provisioner() -> MonitoringProvisioner | None:
    """Shared provisioner, or ``None`` when monitoring is disabled."""
    global _provisioner
    if not settings.llm_monitoring_enabled:
        return None
    if _provisioner is None:
        _provisioner = MonitoringProvisioner()
    return _provisioner


def set_monitoring_provisioner(p: MonitoringProvisioner | None) -> None:
    """Test seam / explicit construction site."""
    global _provisioner
    _provisioner = p


async def provision_for_runtime(session: AsyncSession, runtime_id: uuid.UUID | str) -> str | None:
    """Lifecycle-hook helper: provision (no-op when monitoring off).

    Never raises — lifecycle callers must not fail on monitoring.
    """
    p = get_monitoring_provisioner()
    if p is None:
        return None
    try:
        return await p.provision(session, runtime_id)
    except Exception:  # noqa: BLE001
        log.exception("monitoring: provision failed for %s", runtime_id)
        return None


async def deprovision_for_runtime(runtime_id: uuid.UUID | str) -> None:
    """Lifecycle-hook helper: deprovision (no-op when monitoring off)."""
    p = get_monitoring_provisioner()
    if p is None:
        return
    try:
        await p.deprovision(runtime_id)
    except Exception:  # noqa: BLE001
        log.exception("monitoring: deprovision failed for %s", runtime_id)


async def provision_all_on_startup(session: AsyncSession) -> int:
    """Startup reconciliation: re-provision every monitorable runtime.

    Called once from ``web/lifespan.py`` after boot.  Re-provisions all
    vLLM runtimes that have a known endpoint (any status) and drops
    stale target entries / orphaned dashboards — a desired-state catch-
    up for files removed by hand or a crashed write.  Never raises.
    """
    p = get_monitoring_provisioner()
    if p is None:
        return 0
    try:
        return await p.rebuild_all(session)
    except Exception:  # noqa: BLE001
        log.exception("monitoring: startup rebuild failed")
        return 0
