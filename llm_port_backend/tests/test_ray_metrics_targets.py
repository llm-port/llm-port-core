"""Ray's metrics endpoints, made discoverable by Prometheus.

Three things had to be true before a single panel could populate, and none of
them were:

  * **A reachable address.** Ray advertises each node by its *Ray* address,
    which on a fabric-bound cluster is the isolated 10.100.0.x link. Recorded
    verbatim, it produced targets nothing outside the fabric could dial -- and
    it looked for a while like the fabric made central scraping impossible.
    It does not: the metrics agent binds 0.0.0.0 and only *advertises* the
    fabric address, so the same port answers on the management address.
  * **Every node.** Each node's agent exports only its own metrics; the head
    knows nothing about a replica running on a worker. Scraping just the head
    would have shown zero engine metrics for a model that was serving.
  * **Labels the dashboard filters on.** The template was written for the
    legacy one-runtime-per-container world and selects on ``runtime_name``.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.drivers.ray.driver import RayDriver


class _Env:
    """Just the fields the translation reads."""

    def __init__(self, bindings: dict[str, Any]) -> None:
        self.observed_status_json = {"resolved_fabric": {"node_bindings": bindings}}


async def _node(session: AsyncSession, host: str) -> InfraNode:
    node = InfraNode(
        agent_id=f"agent-{uuid.uuid4().hex[:8]}",
        host=host,
        status="healthy",
        capabilities_json={},
    )
    session.add(node)
    await session.flush()
    return node


def _raw(*targets: tuple[str, int]) -> dict[str, Any]:
    return {
        "enabled": True,
        "targets": [
            {
                "address": address,
                "port": port,
                # Ray's own node id, which is *not* one of ours -- the reason
                # the first attempt at this translation silently did nothing.
                "node_id": uuid.uuid4().hex + uuid.uuid4().hex[:24],
                "url": f"http://{address}:{port}/metrics",
            }
            for address, port in targets
        ],
    }


@pytest.mark.anyio()
async def test_fabric_addresses_become_management_addresses(
    dbsession: AsyncSession,
) -> None:
    """The exact regression: a target nothing could reach."""
    head = await _node(dbsession, "10.88.10.71")
    worker = await _node(dbsession, "10.88.10.49")
    env = _Env(
        {
            str(head.id): {"ip": "10.100.0.2"},
            str(worker.id): {"ip": "10.100.0.1"},
        }
    )

    targets = await RayDriver()._scrape_targets(
        dbsession, _raw(("10.100.0.2", 40535), ("10.100.0.1", 44961)), env
    )

    assert [(t.address, t.port) for t in targets] == [
        ("10.88.10.71", 40535),
        ("10.88.10.49", 44961),
    ]
    assert all("10.100." not in t.url for t in targets)


@pytest.mark.anyio()
async def test_every_node_is_registered_not_only_the_head(
    dbsession: AsyncSession,
) -> None:
    """A replica's engine metrics live on the node it runs on, nowhere else."""
    head = await _node(dbsession, "10.88.10.71")
    worker = await _node(dbsession, "10.88.10.49")
    env = _Env({str(head.id): {"ip": "10.100.0.2"}, str(worker.id): {"ip": "10.100.0.1"}})

    targets = await RayDriver()._scrape_targets(
        dbsession, _raw(("10.100.0.2", 40535), ("10.100.0.1", 44961)), env
    )
    assert len(targets) == 2


@pytest.mark.anyio()
async def test_an_unmapped_address_is_kept_rather_than_dropped(
    dbsession: AsyncSession,
) -> None:
    """A node with no fabric binding still gets a target.

    Dropping it would mean a silently unmonitored machine, which is worse than
    a target that may not resolve: one is visible in Prometheus as down, the
    other is invisible everywhere.
    """
    targets = await RayDriver()._scrape_targets(dbsession, _raw(("10.0.0.7", 9999)), _Env({}))
    assert [(t.address, t.port) for t in targets] == [("10.0.0.7", 9999)]


@pytest.mark.anyio()
async def test_no_metrics_means_no_targets(dbsession: AsyncSession) -> None:
    assert await RayDriver()._scrape_targets(dbsession, None, _Env({})) == []
    assert await RayDriver()._scrape_targets(dbsession, {}, _Env({})) == []


# ── the targets file ─────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_sync_writes_entries_the_dashboard_can_select(tmp_path: Path) -> None:
    from llm_port_backend.services.llm.monitoring import MonitoringProvisioner

    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    prov = MonitoringProvisioner(targets_file=str(targets_file))

    env_id = uuid.uuid4()
    written = await prov.sync_ray_targets(
        environment_id=env_id,
        environment_name="dgx-pair",
        targets=[{"address": "10.88.10.71", "port": 40535}],
    )
    assert written == 1

    entries = json.loads(targets_file.read_text(encoding="utf-8"))
    assert entries[0]["targets"] == ["10.88.10.71:40535"]
    labels = entries[0]["labels"]
    assert labels["job"] == "ray"
    assert labels["environment_id"] == str(env_id)
    # The dashboard template filters on runtime_name; without it every panel
    # matches nothing and the dashboard looks broken rather than unwired.
    assert labels["runtime_name"] == "dgx-pair"


@pytest.mark.anyio()
async def test_syncing_replaces_this_cluster_and_leaves_others_alone(
    tmp_path: Path,
) -> None:
    from llm_port_backend.services.llm.monitoring import MonitoringProvisioner

    targets_file = tmp_path / "targets.json"
    # A legacy per-runtime entry, keyed differently on purpose.
    targets_file.write_text(
        json.dumps(
            [{"targets": ["192.168.1.50:5001"], "labels": {"runtime_id": "legacy"}}]
        ),
        encoding="utf-8",
    )
    prov = MonitoringProvisioner(targets_file=str(targets_file))
    env_id = uuid.uuid4()

    await prov.sync_ray_targets(
        environment_id=env_id,
        environment_name="dgx-pair",
        targets=[{"address": "10.88.10.71", "port": 40535}],
    )
    # A cluster that lost a node must lose its target, not keep a stale one.
    await prov.sync_ray_targets(
        environment_id=env_id,
        environment_name="dgx-pair",
        targets=[{"address": "10.88.10.49", "port": 44961}],
    )

    entries = json.loads(targets_file.read_text(encoding="utf-8"))
    ray_entries = [e for e in entries if e["labels"].get("environment_id") == str(env_id)]
    assert len(ray_entries) == 1
    assert ray_entries[0]["targets"] == ["10.88.10.49:44961"]
    # The legacy runtime entry is untouched.
    assert any(e["labels"].get("runtime_id") == "legacy" for e in entries)


# ── the dashboard ────────────────────────────────────────────────────────

_DASHBOARD = (
    Path(__file__).resolve().parents[2]
    / "llm_port_shared/grafana/dashboards/templates/vllm-runtime.json"
)


@pytest.mark.skipif(not _DASHBOARD.exists(), reason="dashboard template not present")
def test_dashboard_matches_either_metric_family() -> None:
    """One template serves both kinds of runtime, so it must name both.

    A standalone vLLM container publishes ``vllm:generation_tokens_total``.
    Ray Serve republishes the engine's metrics through ``ray.util.metrics``,
    which sanitises the name to ``ray_vllm_generation_tokens_total``. Porting
    the template to the Ray names fixed the Ray dashboards and silently blanked
    the ones rendered for local runtimes -- the same template renders both.

    A metric name cannot be alternated in the normal position, so every
    engine selector matches on ``__name__``.
    """
    text = _DASHBOARD.read_text(encoding="utf-8")
    assert '__name__=~' in text
    # No selector may name one family alone.
    assert not re.search(r"(?<!\|)ray_vllm_[a-z0-9_]+\{", text), (
        "a selector still names only Ray's family"
    )
    assert "vllm:generation_tokens_total|ray_vllm_generation_tokens_total" in text


@pytest.mark.skipif(not _DASHBOARD.exists(), reason="dashboard template not present")
def test_serve_metrics_are_not_given_a_vllm_alternative() -> None:
    """``ray_serve_*`` exists only under Serve; there is nothing to alternate."""
    text = _DASHBOARD.read_text(encoding="utf-8")
    assert "vllm:num_http_requests_total" not in text
    assert "ray_serve_num_http_requests_total" in text


@pytest.mark.skipif(not _DASHBOARD.exists(), reason="dashboard template not present")
def test_gauge_panels_survive_an_idle_cluster() -> None:
    """Ray expires a gauge that stops being updated.

    vLLM writes these only during engine iterations, so on an idle cluster the
    series is absent from the scrape entirely and an instant query shows
    "No data" for a perfectly healthy replica. Verified on hardware: present
    at ``num_requests_running 2.0`` during load, gone minutes later.
    """
    text = _DASHBOARD.read_text(encoding="utf-8")
    for gauge in (
        "num_requests_running",
        "num_requests_waiting",
        "kv_cache_usage_perc",
    ):
        # The file stores JSON, so the selector's quotes are escaped in it.
        needle = 'last_over_time({__name__=~\\"vllm:' + gauge + "|"
        assert needle in text, f"{gauge} will read No data when idle"


@pytest.mark.skipif(not _DASHBOARD.exists(), reason="dashboard template not present")
def test_no_panel_queries_vllms_own_http_server() -> None:
    """Under Ray Serve there is no vLLM HTTP server; Serve is the ingress."""
    text = _DASHBOARD.read_text(encoding="utf-8")
    assert "http_requests_total{handler=" not in text
    assert "ray_serve_num_http_requests_total" in text


@pytest.mark.skipif(not _DASHBOARD.exists(), reason="dashboard template not present")
def test_dashboard_is_still_valid_json() -> None:
    json.loads(_DASHBOARD.read_text(encoding="utf-8"))


@pytest.mark.skipif(not _DASHBOARD.exists(), reason="dashboard template not present")
def test_the_serve_panels_select_labels_that_exist() -> None:
    """The ingress panels, after a rename that was only half a port.

    Moving from vLLM's own HTTP server to Ray Serve's renamed the metric and
    left three selectors that do not survive the move. Each label was checked
    against the live cluster:

        instance    is always host:port -- "10.88.10.71:40535", never a
                    cluster name, so ``instance="<cluster>"`` matched nothing.
        status      does not exist; Serve's label is ``status_code`` and it
                    carries the code itself (200, 404), not a family ("2xx").
        route       is prefixed with the application's name, so the real one
                    is "/llmport-<deployment-id>/v1/chat/completions".

    The result parsed and ran and matched nothing, which is worse than an
    error: three panels showed a confident flat zero for a cluster that was
    serving traffic.
    """
    text = _DASHBOARD.read_text(encoding="utf-8")
    assert 'status=\\"2xx\\"' not in text, "Serve labels the code, not the family"
    assert 'status_code=~\\"2..\\"' in text
    # `instance` pins one exporter; a cluster has several, and the panel is
    # about the cluster.
    assert "ray_serve_num_http_requests_total" in text
    serve_selectors = [
        line
        for line in text.split("ray_serve_num_http_requests_total")[1:]
    ]
    for selector in serve_selectors:
        head = selector.split("}")[0]
        assert "instance=" not in head, "a cluster is more than one instance"
        assert "runtime_name" in head, "the panel must be scoped to this cluster"
        assert ".*/v1/chat/completions" in head, "Serve prefixes the app's route"


@pytest.mark.skipif(not _DASHBOARD.exists(), reason="dashboard template not present")
def test_no_panel_still_asks_for_a_label_ray_does_not_publish() -> None:
    """A guard against the next mechanical rename.

    Verified against the live cluster's own series: these are the label keys
    Ray actually publishes alongside the engine metrics.
    """
    text = _DASHBOARD.read_text(encoding="utf-8")
    # `handler` was vLLM's; Serve has `route`.
    assert "handler=" not in text


# ── the startup rebuild ──────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_backend_restart_does_not_wipe_a_clusters_targets(
    dbsession: AsyncSession, tmp_path: Path
) -> None:
    """``rebuild_all`` rebuilt the targets file from the runtime table alone.

    A Ray cluster has no ``LLMRuntime`` row, so every backend start silently
    deleted its scrape targets and Prometheus stopped collecting from it --
    with nothing anywhere to say that had happened. Observed on the live
    stack: the cluster's two targets were present, the backend reloaded, and
    they were gone.
    """
    from llm_port_backend.db.models.inference import (
        InferenceControlPlane,
        InferenceEnvironment,
    )
    from llm_port_backend.services.llm.monitoring import MonitoringProvisioner

    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    dash_dir = tmp_path / "dash"
    dash_dir.mkdir()
    prov = MonitoringProvisioner(
        targets_file=str(targets_file), dashboard_dir=str(dash_dir)
    )

    # A cluster that exists: the rebuild keeps only those.
    control_plane = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(control_plane)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=control_plane.id, name="dgx-pair", desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()
    env_id = env.id
    await prov.sync_ray_targets(
        environment_id=env_id,
        environment_name="dgx-pair",
        targets=[
            {"address": "10.88.10.71", "port": 40535},
            {"address": "10.88.10.49", "port": 44961},
        ],
    )
    await prov.rebuild_all(dbsession)

    entries = json.loads(targets_file.read_text(encoding="utf-8"))
    kept = [e for e in entries if e["labels"].get("environment_id") == str(env_id)]
    assert len(kept) == 2, "the cluster's targets did not survive the rebuild"


@pytest.mark.anyio()
async def test_a_backend_restart_does_not_delete_a_clusters_dashboard(
    dbsession: AsyncSession, tmp_path: Path
) -> None:
    """The orphan sweep deleted every dashboard the rebuild had not written.

    It cannot tell a cluster's dashboard from a runtime's by filename -- both
    are ``vllm-rt-<hash>.json`` -- so the cluster's was simply swept, and the
    link in the product led to a Grafana 404.
    """
    from llm_port_backend.db.models.inference import (
        InferenceControlPlane,
        InferenceEnvironment,
    )
    from llm_port_backend.services.llm.monitoring import (
        MonitoringProvisioner,
        dashboard_uid_for,
    )

    control_plane = InferenceControlPlane(
        name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray"
    )
    dbsession.add(control_plane)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=control_plane.id,
        name=f"dgx-{uuid.uuid4().hex[:6]}",
        desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    dash_dir = tmp_path / "dash"
    dash_dir.mkdir()
    prov = MonitoringProvisioner(
        targets_file=str(targets_file), dashboard_dir=str(dash_dir)
    )

    await prov.provision_environment(environment_id=env.id, environment_name=env.name)
    dashboard = dash_dir / f"{dashboard_uid_for(env.id)}.json"
    assert dashboard.exists()

    await prov.rebuild_all(dbsession)
    assert dashboard.exists(), "the rebuild swept the cluster's dashboard"


@pytest.mark.anyio()
async def test_a_clusters_dashboard_is_this_cluster(
    dbsession: AsyncSession, tmp_path: Path
) -> None:
    """Every panel selects on ``runtime_name``; a cluster's is its own name.

    The scrape targets carry the same value under the same label, so the
    cluster reads as "the runtime" and the template works unchanged.
    """
    from llm_port_backend.services.llm.monitoring import (
        MonitoringProvisioner,
        dashboard_uid_for,
    )

    dash_dir = tmp_path / "dash"
    dash_dir.mkdir()
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    prov = MonitoringProvisioner(
        targets_file=str(targets_file), dashboard_dir=str(dash_dir)
    )

    env_id = uuid.uuid4()
    url = await prov.provision_environment(
        environment_id=env_id, environment_name="dgx-pair"
    )

    rendered = (dash_dir / f"{dashboard_uid_for(env_id)}.json").read_text(
        encoding="utf-8"
    )
    # The name lands inside JSON string values, so it appears escaped.
    assert 'runtime_name=' in rendered
    assert "dgx-pair" in rendered
    assert "__RUNTIME_NAME__" not in rendered, "a placeholder survived the render"
    assert "__UID__" not in rendered
    json.loads(rendered)
    assert url and dashboard_uid_for(env_id) in url


# ── the stat cards in the providers-page expander ────────────────────────


def test_stat_cards_ask_for_both_metric_families() -> None:
    """One set of queries serves a Ray cluster and a local vLLM container.

    These feed the cards inside the providers table's row expander. Porting
    them to Ray's names fixed the cluster and blanked the container, and a
    blank card is indistinguishable from an idle engine -- so the break would
    have read as "this runtime is quiet", not "this query is wrong".
    """
    from llm_port_backend.services.llm.monitoring import STAT_QUERIES

    rendered = {
        key: template.format(rt='runtime_name="x"')
        for key, template in STAT_QUERIES.items()
    }
    for key, expr in rendered.items():
        assert "__name__=~" in expr, f"{key} names one family only"
        assert 'runtime_name="x"' in expr, f"{key} is not scoped to a runtime"
        # No selector may name a family without offering the other.
        assert not re.search(r"(?<![|\"])ray_vllm_[a-z0-9_]+\{", expr), key
        assert not re.search(r"(?<![|\"~])vllm:[a-z0-9_]+\{", expr), key


def test_stat_card_gauges_survive_an_idle_engine() -> None:
    """Same lazy-registration trap as the dashboard's gauges."""
    from llm_port_backend.services.llm.monitoring import STAT_QUERIES

    for key in ("running_requests", "waiting_requests", "kv_cache_usage"):
        assert "last_over_time(" in STAT_QUERIES[key], key
    # Counters need no wrapper; rate() already looks back.
    assert "last_over_time(" not in STAT_QUERIES["generation_tokens_per_sec"]


def test_every_stat_query_is_valid_promql_shape() -> None:
    """Braces have to survive ``str.format`` intact.

    The selectors carry their own braces, so the stored templates double them
    -- and getting that wrong raises ``KeyError`` deep inside ``format`` at
    request time rather than at import, where nobody would see it until a card
    was opened.
    """
    from llm_port_backend.services.llm.monitoring import STAT_QUERIES

    for key, template in STAT_QUERIES.items():
        expr = template.format(rt='runtime_name="x"')
        assert expr.count("{") == expr.count("}"), key
        assert "{rt}" not in expr, key


# ── metrics Ray cannot publish at all ────────────────────────────────────


def test_a_counter_that_has_never_fired_reads_as_zero() -> None:
    """Ray cannot publish a zero counter, so "never happened" has no series.

    ``ray.util.metrics.Counter.inc`` raises ``ValueError`` on a non-positive
    value, which is why vLLM's Ray wrapper guards with ``if value == 0:
    return`` -- and because Ray creates a metric lazily on first record, a
    counter whose delta is always zero is never created. Checked in the
    runtime image, not inferred.

    So a healthy cluster that has never preempted a request showed "No data"
    for preemptions, which claims we cannot see something we can.
    """
    from llm_port_backend.services.llm.monitoring import STAT_QUERIES

    expr = STAT_QUERIES["preemption_rate"].format(rt='runtime_name="x"')
    assert " or (" in expr, "an absent preemption counter still reads as no data"


def test_the_zero_is_conditioned_on_the_engine_being_alive() -> None:
    """``or vector(0)`` would turn a missing cluster into a healthy one.

    A name typed wrongly, a deleted deployment or a dead engine would all
    report zero preemptions and zero requests running -- which reads as a
    quiet, healthy cluster. The fallback is conditioned on a witness series
    from the same engine instead, so zero is only claimed when something else
    from that engine is being published.
    """
    from llm_port_backend.services.llm.monitoring import STAT_QUERIES

    for key in (
        "preemption_rate",
        "running_requests",
        "waiting_requests",
        "kv_cache_usage",
    ):
        expr = STAT_QUERIES[key].format(rt='runtime_name="x"')
        assert "or (vector(0))" not in expr, f"{key} claims zero unconditionally"
        assert "generation_tokens_total" in expr, f"{key} has no witness"
        assert 'runtime_name="x"' in expr.split(" or (")[1], (
            f"{key}'s witness is not scoped to this cluster"
        )


def test_config_gated_families_are_left_absent() -> None:
    """Speculative decoding is not declared unless it is configured.

    Zero would be a different lie: it would say "no tokens were accepted"
    about a feature that is not running. Absence is the honest answer, and
    the panel says "No data" for it.
    """
    from llm_port_backend.services.llm.monitoring import STAT_QUERIES

    expr = STAT_QUERIES["mtp_acceptance"].format(rt='runtime_name="x"')
    assert " or (" not in expr


@pytest.mark.skipif(not _DASHBOARD.exists(), reason="dashboard template not present")
def test_the_dashboard_makes_the_same_distinction() -> None:
    """Whatever the cards do, the dashboard must do -- they are read together."""
    text = _DASHBOARD.read_text(encoding="utf-8")

    # Counters and gauges that are absent only because nothing happened.
    for metric in (
        "num_preemptions_total",
        "num_requests_running",
        "kv_cache_usage_perc",
    ):
        for expr in _exprs_naming(text, metric):
            assert " or (" in expr, f"{metric} still reads as no data when idle"

    # Config-gated: absent because the feature is off.
    for expr in _exprs_naming(text, "spec_decode_num_drafts_total"):
        assert " or (" not in expr, "speculative decoding was given a false zero"


def _exprs_naming(text: str, metric: str) -> list[str]:
    """Every dashboard expression mentioning *metric*.

    Walks the parsed document rather than matching the raw text: the
    expressions contain escaped quotes, and a regex over them is a second
    thing to get right for no benefit.
    """
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            expr = node.get("expr")
            if isinstance(expr, str) and metric in expr:
                found.append(expr)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(text))
    return found


def test_kv_cache_block_metrics_are_asked_for() -> None:
    """vLLM declares these three only when asked, and they are worth asking for.

    Block lifetime, reuse gap and idle-before-evict are what say whether the
    cache is sized right for the traffic -- the difference between "the model
    is slow" and "the model keeps recomputing the same prefixes". Off by
    default in vLLM, so the histograms were never declared and the metrics
    could not be missing-but-recoverable; they did not exist.

    vLLM samples them at 1% by default, so the cardinality cost is bounded
    without us having to bound it.
    """
    from llm_port_backend.services.inference.drivers.ray.compiler import (
        _engine_kwargs,
    )

    kwargs = _engine_kwargs(
        engine_config={}, tensor_parallel_size=None, pipeline_parallel_size=None
    )
    assert kwargs["kv_cache_metrics"] is True


def test_an_operator_can_still_turn_them_off() -> None:
    """Every default in the compiler is a default, not a policy."""
    from llm_port_backend.services.inference.drivers.ray.compiler import (
        _engine_kwargs,
    )

    kwargs = _engine_kwargs(
        engine_config={"kv_cache_metrics": False},
        tensor_parallel_size=None,
        pipeline_parallel_size=None,
    )
    assert kwargs["kv_cache_metrics"] is False
