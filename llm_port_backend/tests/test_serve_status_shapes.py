"""The agent speaks two dialects of GET_RAY_SERVE_STATUS; both must be read.

Its container path -- the Phase 4B runtime, and the only one certified
hardware uses -- returns the fields flat. Its legacy host path wraps them in
``serve``. Only the wrapped form was parsed, so on the DGX pair every probe
came back with no apps.

Nothing errored. The deployment simply never saw its own application, held at
"serve.run accepted; waiting for readiness observation", and the cluster page
showed "Starting" for a model that was already answering requests.
"""

from __future__ import annotations

from llm_port_backend.services.inference.drivers.ray.client import _parse_serve_status
from llm_port_backend.services.inference.drivers.ray.deployment import _serve_app_entry

APP = "llmport-b64e389b-58ae-4b1d-a754-1f9f48e22220"

_APP_ENTRY = {
    "status": "RUNNING",
    "deployments": {
        "OpenAiIngress": {"status": "HEALTHY", "num_replicas_ready": 1},
        "LLMServer:Qwen2_5-0_5B-Instruct": {
            "status": "HEALTHY",
            "num_replicas_ready": 1,
            "num_replicas_pending": 0,
        },
    },
}

# Captured from the DGX head, verbatim.
FLAT = {"available": True, "apps": {APP: _APP_ENTRY}, "detail": None}
WRAPPED = {"alive": True, "serve": {"available": True, "apps": {APP: _APP_ENTRY}}}


class TestFlatShape:
    """The container path, which is what actually runs."""

    def test_apps_are_found(self) -> None:
        assert list(_parse_serve_status(FLAT).apps) == [APP]

    def test_available_is_read(self) -> None:
        assert _parse_serve_status(FLAT).available is True

    def test_alive_falls_back_to_available(self) -> None:
        # The flat shape carries no ``alive``; Serve answering is what it means.
        assert _parse_serve_status(FLAT).alive is True

    def test_the_deployment_can_find_its_own_app(self) -> None:
        """The exact lookup that used to return None on healthy hardware."""
        entry = _serve_app_entry(_parse_serve_status(FLAT), APP)
        assert entry is not None
        assert entry["status"] == "RUNNING"
        assert entry["deployments"]["LLMServer:Qwen2_5-0_5B-Instruct"]["num_replicas_ready"] == 1


class TestWrappedShape:
    """The legacy host path must keep working."""

    def test_apps_are_found(self) -> None:
        assert list(_parse_serve_status(WRAPPED).apps) == [APP]

    def test_alive_and_available(self) -> None:
        parsed = _parse_serve_status(WRAPPED)
        assert parsed.alive is True
        assert parsed.available is True

    def test_the_deployment_can_find_its_own_app(self) -> None:
        assert _serve_app_entry(_parse_serve_status(WRAPPED), APP) is not None


class TestDegenerate:
    def test_none_is_not_alive(self) -> None:
        parsed = _parse_serve_status(None)
        assert parsed.alive is False and parsed.apps == {}

    def test_serve_down_flat(self) -> None:
        parsed = _parse_serve_status({"available": False, "apps": {}, "detail": "not started"})
        assert parsed.alive is False
        assert parsed.detail == "not started"

    def test_serve_down_wrapped(self) -> None:
        parsed = _parse_serve_status({"alive": False, "serve": {"available": False}})
        assert parsed.alive is False
        assert parsed.apps == {}

    def test_an_unknown_app_is_still_none(self) -> None:
        assert _serve_app_entry(_parse_serve_status(FLAT), "some-other-app") is None
