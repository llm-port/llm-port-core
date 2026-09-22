"""A page waiting on a node must not hold its connection for 90 seconds.

What this prevents, concretely: the cluster and deployment screens poll their
metrics every 10-15s, and those reads issue a command to a node and wait for
the answer. At the reconciler's 90s budget a 10s poll stacks nine requests,
and a browser opens about six connections per origin -- so the page starves
itself and every other tab with it. The symptom is a UI that freezes and only
recovers on reload, which is just the reload aborting the stalled requests.

The second thing here is a distinction that was collapsed: how long the agent
may take, and how long this caller is willing to wait, are different numbers.
Passing one as the other told the node it had 8 seconds to answer.
"""

from __future__ import annotations

import inspect

from llm_port_backend.services.inference.drivers.ray import client as ray_client
from llm_port_backend.services.inference.drivers.ray import driver as ray_driver


def test_the_interactive_budget_is_short_enough_to_poll_behind() -> None:
    # Must be well under the shortest poll interval the UI uses (10s), or
    # requests overlap no matter what the frontend does.
    assert ray_client._INTERACTIVE_PROBE_BUDGET_SEC <= 10.0
    assert ray_client._INTERACTIVE_PROBE_BUDGET_SEC < ray_client._PROBE_BUDGET_SEC


def test_the_reconciler_keeps_its_long_budget() -> None:
    """Background work can afford to wait; only the browser cannot."""
    assert ray_client._PROBE_BUDGET_SEC >= 60.0


def test_waiting_and_command_timeout_are_separate_parameters() -> None:
    """They were one, so an impatient reader cancelled the node's work."""
    params = inspect.signature(ray_client.RayClusterClient._dispatch_and_poll).parameters
    assert "timeout_sec" in params
    assert "wait_budget_sec" in params


def test_both_probes_accept_a_wait_budget() -> None:
    for probe in (
        ray_client.RayClusterClient.probe_cluster,
        ray_client.RayClusterClient.probe_serve,
    ):
        assert "budget_sec" in inspect.signature(probe).parameters, probe.__name__


def test_the_metrics_reads_use_the_short_budget() -> None:
    """These are the two a browser polls."""
    for fn in (
        ray_driver.RayDriver.deployment_metrics,
        ray_driver.RayDriver.environment_metrics,
    ):
        source = inspect.getsource(fn)
        assert "_INTERACTIVE_PROBE_BUDGET_SEC" in source, fn.__name__


def test_a_probe_that_does_not_answer_is_a_partial_not_an_error() -> None:
    """Metrics already know how to be honest about a gap.

    The budget only works because a timed-out probe degrades to "not
    observed" rather than failing the request -- otherwise shortening it
    would turn a slow screen into a broken one.
    """
    source = inspect.getsource(ray_driver.RayDriver.environment_metrics)
    assert "MetricsPartial" in source


# ── the deeper fix: a page reads, it does not interrogate ────────────────


def test_environment_metrics_prefers_the_stored_observation() -> None:
    """A probe takes ~20s on this hardware; a page polls every 10s.

    No budget reconciles those two numbers -- the request either outlives the
    interval and stacks, or it is cut short and the screen shows zeros. The
    reconciler already collects these exact figures every 30s, so the read is
    a read.
    """
    source = inspect.getsource(ray_driver.RayDriver.environment_metrics)
    stored_at = source.find('observed_status_json or {}).get("cluster")')
    probe_at = source.find("probe_cluster")
    assert stored_at != -1, "metrics no longer read the stored observation"
    assert probe_at != -1, "the never-reconciled case still needs one probe"
    assert stored_at < probe_at, "the stored observation must be tried first"


def test_stored_figures_are_labelled_as_not_live() -> None:
    """Fast and wrong-looking is worse than fast and explained.

    The numbers are real but they are as of the last reconcile, and a screen
    that implies otherwise is the same lie as showing a gap as a zero.
    """
    source = inspect.getsource(ray_driver.RayDriver.environment_metrics)
    assert "MetricsPartial" in source
    # Written for the operator: "as of the last cluster check" tells them what
    # they are looking at. The first version said "asking the node takes ~20s,
    # which a page cannot wait for" -- true, and an explanation of our
    # plumbing rather than of their data.
    assert "last cluster check" in source


def test_a_probe_that_times_out_is_not_reported_as_zeros() -> None:
    """``alive=False`` means both "it is down" and "we never heard back".

    Only the first is a fact. Copying the second into the metrics produced
    "0 of 0 accelerators" looking exactly like a measurement.
    """
    from llm_port_backend.services.inference.drivers.ray.client import (
        _parse_cluster_status,
    )

    assert _parse_cluster_status(None).observed is False
    assert _parse_cluster_status({"alive": False}).observed is True

    source = inspect.getsource(ray_driver.RayDriver.environment_metrics)
    assert "status.observed" in source
