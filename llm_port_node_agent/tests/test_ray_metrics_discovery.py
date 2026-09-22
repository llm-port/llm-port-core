"""Where the agent gets the cluster's Prometheus endpoints from.

It used to derive them: walk ``cluster-status``'s node records and read each
one's ``metrics_export_port``.  That yields exactly one endpoint per node, and
Ray runs more exporters than that.  On the DGX pair the derivation produced
two targets where Ray itself published four -- the missing two being the
autoscaler (cluster capacity, pending resources) and the dashboard/component
exporter (per-component memory).  Neither has a node record to be derived from,
so no amount of care in the derivation would have found them.

Ray already writes the authoritative list, keeps it current as nodes join and
leave, and writes it in Prometheus' own file_sd format.  So the agent reads it
instead, and keeps the derivation only as a fallback for a cluster whose file
is not there yet.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from llm_port_node_agent.ray.container import (
    DEFAULT_CONTAINER_NAME,
    RayContainerRuntime,
)
from llm_port_node_agent.runtimes import ContainerRuntimeError

_DISCOVERY_PATH = "/tmp/ray/prom_metrics_service_discovery.json"

# Exactly what the head node published during the two-node run: one group,
# four endpoints, the head carrying three of them.
_LIVE_FILE = json.dumps(
    [
        {
            "labels": {"job": "ray"},
            "targets": [
                "10.88.10.71:44239",
                "10.88.10.49:37617",
                "10.88.10.49:44217",
                "10.88.10.49:44227",
            ],
        }
    ]
)


class _Runtime:
    """Container handler that answers ``cat`` and the helper verbs separately."""

    def __init__(
        self,
        *,
        discovery: str | None = None,
        discovery_rc: int = 0,
        cluster_status: dict[str, Any] | None = None,
        raise_on_exec: bool = False,
    ) -> None:
        self._discovery = discovery
        self._discovery_rc = discovery_rc
        self._cluster_status = cluster_status
        self._raise = raise_on_exec
        self.reads: list[list[str]] = []

    @property
    def name(self) -> str:
        return "docker"

    async def exists(self, name: str) -> bool:
        return True

    async def inspect(self, name: str, *, format_: str | None = None, timeout_sec: float = 10):
        return {"State": {"Running": True}}

    async def exec_(
        self,
        name: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        stdin: str | None = None,
        timeout_sec: float = 120,
        raise_on_error: bool = True,
    ) -> tuple[int, str, str]:
        self.reads.append(list(command))
        if self._raise:
            raise ContainerRuntimeError("container went away mid-read")
        if command[:1] == ["cat"]:
            if self._discovery is None:
                return 1, "", f"cat: {_DISCOVERY_PATH}: No such file or directory"
            return self._discovery_rc, self._discovery, ""
        return 0, json.dumps(self._cluster_status or {"alive": False}), ""


def _runtime(**kwargs: Any) -> tuple[RayContainerRuntime, _Runtime]:
    handler = _Runtime(**kwargs)
    return RayContainerRuntime(runtime=handler), handler


# ── the published list ───────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_every_exporter_is_returned_not_only_the_per_node_ones() -> None:
    """The regression, in the numbers that exposed it: four, not two."""
    runtime, _handler = _runtime(discovery=_LIVE_FILE)

    result = await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME)

    assert result["enabled"] is True
    assert [(t["address"], t["port"]) for t in result["targets"]] == [
        ("10.88.10.71", 44239),
        ("10.88.10.49", 37617),
        ("10.88.10.49", 44217),
        ("10.88.10.49", 44227),
    ]


@pytest.mark.anyio()
async def test_several_exporters_on_one_host_all_survive() -> None:
    """The head runs three.  Keying anything by host would collapse them."""
    runtime, _handler = _runtime(discovery=_LIVE_FILE)

    targets = (await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME))["targets"]
    head = [t for t in targets if t["address"] == "10.88.10.49"]
    assert len(head) == 3
    assert len({t["port"] for t in head}) == 3


@pytest.mark.anyio()
async def test_targets_carry_a_dialable_url() -> None:
    runtime, _handler = _runtime(discovery=_LIVE_FILE)

    first = (await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME))["targets"][0]
    assert first["url"] == "http://10.88.10.71:44239/metrics"


@pytest.mark.anyio()
async def test_the_file_is_read_from_rays_own_path() -> None:
    """Pinned because Ray owns this path; a typo would silently fall back."""
    runtime, handler = _runtime(discovery=_LIVE_FILE)

    await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME)
    assert handler.reads[0] == ["cat", _DISCOVERY_PATH]


@pytest.mark.anyio()
async def test_an_ipv6_style_entry_splits_on_the_last_colon() -> None:
    runtime, _handler = _runtime(
        discovery=json.dumps([{"labels": {}, "targets": ["[::1]:8080"]}])
    )

    targets = (await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME))["targets"]
    assert targets == [
        {
            "node_id": None,
            "address": "[::1]",
            "port": 8080,
            "url": "http://[::1]:8080/metrics",
        }
    ]


# ── falling back rather than going blind ─────────────────────────────────


@pytest.mark.anyio()
async def test_no_file_falls_back_to_the_node_derivation() -> None:
    """An older Ray, or a session whose file has not appeared yet.

    Two targets is worse than four, and much better than none.
    """
    runtime, handler = _runtime(
        discovery=None,
        cluster_status={
            "alive": True,
            "nodes": [
                {"node_ip": "10.88.10.49", "metrics_export_port": 37617, "alive": True},
                {"node_ip": "10.88.10.71", "metrics_export_port": 44239, "alive": True},
            ],
        },
    )

    result = await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME)

    assert result["enabled"] is True
    assert len(result["targets"]) == 2
    assert handler.reads[0][0] == "cat"
    assert handler.reads[1][0] == "llm-port-ray-runtime"


@pytest.mark.anyio()
@pytest.mark.parametrize(
    "body",
    [
        "",
        "not json at all",
        json.dumps({"targets": ["10.0.0.1:9000"]}),  # an object, not a list
        json.dumps([{"labels": {}, "targets": ["no-port-here"]}]),
        json.dumps([{"labels": {}, "targets": [":9000"]}]),
        json.dumps(["a bare string, not a group"]),
    ],
)
async def test_an_unusable_file_falls_back_instead_of_raising(body: str) -> None:
    """Whatever is in that file, a metrics read must not break cluster-status.

    ``get_metrics_targets`` runs inside the status probe the backend polls, so
    an exception here would take the whole cluster view down over monitoring.
    """
    runtime, _handler = _runtime(
        discovery=body,
        cluster_status={
            "alive": True,
            "nodes": [{"node_ip": "10.88.10.49", "metrics_export_port": 37617}],
        },
    )

    result = await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME)
    assert [t["port"] for t in result["targets"]] == [37617]


@pytest.mark.anyio()
async def test_a_container_that_went_away_reports_no_targets() -> None:
    runtime, _handler = _runtime(discovery=_LIVE_FILE, raise_on_exec=True)

    result = await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME)
    assert result == {"enabled": False, "targets": []}


@pytest.mark.anyio()
async def test_a_dead_cluster_with_no_file_is_reported_disabled() -> None:
    runtime, _handler = _runtime(discovery=None, cluster_status={"alive": False})

    assert await runtime.get_metrics_targets(DEFAULT_CONTAINER_NAME) == {
        "enabled": False,
        "targets": [],
    }
