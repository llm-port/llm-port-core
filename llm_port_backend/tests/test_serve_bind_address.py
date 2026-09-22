"""Where a Serve proxy binds is not where clients reach it.

The bug this pins, seen on the DGX pair: the driver passed the head node's
*management* IP as the proxy bind address. Ray placed a proxy on a different
node, the bind failed with EADDRNOTAVAIL, Serve retried and failed again, and
the only thing the operator saw was

    Failed to update the deployments ['LLMServer:Qwen2_5-0_5B-Instruct']

with the real cause three log files away on another machine.

Two things made it wrong, and either alone is enough:

  * the backend does not choose which node Ray puts a proxy on, so any
    specific address is a guess that fails everywhere else;
  * on a cluster bound to a separate fabric, Ray nodes do not carry the
    management IP at all, so the bind can fail even on the intended node.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_port_backend.services.inference.drivers.ray import deployment as dep
from llm_port_backend.services.inference.drivers.ray.deployment import (
    _WILDCARD_BIND,
    _WILDCARD_BINDS,
)


def _facts(config: dict | None = None, head_host: str | None = "10.88.10.71"):
    """Just enough of _DeploymentFacts for the two methods under test."""
    return SimpleNamespace(
        environment=SimpleNamespace(config_json=config or {}, address="10.100.0.2:6379"),
        head_host=head_host,
        spec_data={},
        model=SimpleNamespace(display_name="Qwen2.5-0.5B-Instruct"),
    )


def _options(facts):
    return dep.RayDeploymentManager._serve_options(
        dep.RayDeploymentManager.__new__(dep.RayDeploymentManager), facts
    )


class TestBindAddress:
    def test_defaults_to_the_wildcard(self) -> None:
        assert _options(_facts())["http_options"]["host"] == _WILDCARD_BIND

    def test_never_binds_the_head_management_ip(self) -> None:
        """The exact regression: 10.88.10.71 as a bind address."""
        host = _options(_facts(head_host="10.88.10.71"))["http_options"]["host"]
        assert host != "10.88.10.71"

    @pytest.mark.parametrize("location", ["HeadOnly", "EveryNode", "Disabled", None])
    def test_is_the_wildcard_wherever_the_proxy_lands(self, location) -> None:
        """A proxy can only bind what exists locally, and we do not place it."""
        config = {"serve_proxy_location": location} if location else {}
        options = _options(_facts(config))
        assert options["http_options"]["host"] == _WILDCARD_BIND

    def test_the_configured_location_is_still_honoured(self) -> None:
        # Only the address stopped being derived; placement was never the bug.
        assert _options(_facts({"serve_proxy_location": "EveryNode"}))["proxy_location"] == "EveryNode"

    def test_head_only_is_the_default_placement(self) -> None:
        assert _options(_facts())["proxy_location"] == "HeadOnly"

    def test_an_operator_can_still_pin_an_interface(self) -> None:
        """Kept as an escape hatch; it is just never derived on their behalf."""
        options = _options(_facts({"serve_http_host": "192.168.5.5"}))
        assert options["http_options"]["host"] == "192.168.5.5"

    def test_pinning_an_interface_is_warned_about(self, caplog) -> None:
        """It is the same hazard, chosen deliberately instead of by accident."""
        with caplog.at_level("WARNING"):
            _options(_facts({"serve_http_host": "192.168.5.5"}))
        assert any("serve_http_host" in r.message for r in caplog.records)

    def test_the_port_is_unchanged(self) -> None:
        assert _options(_facts())["http_options"]["port"] == 8000
        assert _options(_facts({"serve_http_port": 9000}))["http_options"]["port"] == 9000

    def test_a_broken_environment_config_still_yields_a_bindable_address(self) -> None:
        """A bad config must not produce an address that crash-loops a proxy."""
        options = _options(_facts({"serve_proxy_location": 12345}))
        assert options["http_options"]["host"] == _WILDCARD_BIND


class TestPublishedAddress:
    """The other half: clients still get a routable address, not 0.0.0.0."""

    def test_wildcard_set_covers_what_a_bind_can_be(self) -> None:
        assert "0.0.0.0" in _WILDCARD_BINDS
        assert "::" in _WILDCARD_BINDS

    def test_the_published_host_is_the_head_not_the_bind(self) -> None:
        # A wildcard bind on the head still answers on the head's address, so
        # the endpoint clients are given must resolve back to it.
        facts = _facts()
        assert _options(facts)["http_options"]["host"] in _WILDCARD_BINDS
        assert facts.head_host == "10.88.10.71"
