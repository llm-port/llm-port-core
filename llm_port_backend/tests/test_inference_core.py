"""Unit tests for the Phase 1 neutral-inference core (no DB, no Ray).

Covers the versioned spec schema, the capability document, the driver
registry, and the Phase 1 no-op reconciliation seams — everything that must
hold true *before* any backend driver is registered.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pydantic
import pytest

from llm_port_backend.services.inference import (
    API_VERSION_V1ALPHA1,
    DEFAULT_CAPABILITY_SECTIONS,
    CapabilityDocument,
    KnownSpecVersions,
    parse_inference_deployment_spec,
    registry,
)
from llm_port_backend.services.inference.reconciliation import (
    ReconciliationContext,
    reconcile_control_plane,
    reconcile_deployment,
    reconcile_environment,
)

# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


def _spec(**overrides: Any) -> dict[str, Any]:
    # The versioned parser requires an *explicit* api_version, so the base spec
    # always carries one.  The tests that exercise the version error path build
    # their own dicts without it.
    base: dict[str, Any] = {"api_version": API_VERSION_V1ALPHA1, "scale": {"replicas": 1}}
    base.update(overrides)
    return base


def test_parse_valid_replicas_spec() -> None:
    spec = parse_inference_deployment_spec(_spec())
    assert spec.api_version == API_VERSION_V1ALPHA1
    assert spec.scale.replicas == 1
    assert spec.scale.autoscale is None
    # defaults populated
    assert spec.engine.name == "vllm"
    assert spec.service.path == "/v1"


def test_parse_valid_autoscale_spec() -> None:
    spec = parse_inference_deployment_spec(
        _spec(scale={"autoscale": {"min_replicas": 0, "max_replicas": 4, "target_utilization": 0.9}}),
    )
    assert spec.scale.replicas is None
    assert spec.scale.autoscale is not None
    assert spec.scale.autoscale.max_replicas == 4


def test_parse_empty_dict_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_inference_deployment_spec({})


def test_parse_missing_api_version_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_inference_deployment_spec({"scale": {"replicas": 1}})


def test_parse_unknown_api_version_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_inference_deployment_spec({"api_version": "nope/v9", "scale": {"replicas": 1}})


def test_scale_requires_exactly_one_mode() -> None:
    # Neither replicas nor autoscale -> validation error (pydantic wraps the
    # model_validator ValueError).
    with pytest.raises(pydantic.ValidationError):
        parse_inference_deployment_spec(_spec(scale={}))


def test_scale_rejects_both_modes() -> None:
    with pytest.raises(pydantic.ValidationError):
        parse_inference_deployment_spec(
            _spec(scale={"replicas": 2, "autoscale": {"min_replicas": 0, "max_replicas": 3}}),
        )


def test_scale_rejects_unknown_field() -> None:
    with pytest.raises(pydantic.ValidationError):
        parse_inference_deployment_spec(
            {"api_version": API_VERSION_V1ALPHA1, "scale": {"replicas": 1, "bogus": 1}},
        )


def test_scale_rejects_zero_replicas() -> None:
    with pytest.raises(pydantic.ValidationError):
        parse_inference_deployment_spec(_spec(scale={"replicas": 0}))


def test_autoscale_requires_max_at_least_one() -> None:
    with pytest.raises(pydantic.ValidationError):
        parse_inference_deployment_spec(
            _spec(scale={"autoscale": {"min_replicas": 0, "max_replicas": 0}}),
        )


def test_top_level_rejects_unknown_field() -> None:
    with pytest.raises(pydantic.ValidationError):
        parse_inference_deployment_spec(_spec(unknown_top="x"))


def test_known_versions_map() -> None:
    assert API_VERSION_V1ALPHA1 in KnownSpecVersions
    assert KnownSpecVersions[API_VERSION_V1ALPHA1] is not None


# ---------------------------------------------------------------------------
# Capability document
# ---------------------------------------------------------------------------


def test_capability_from_dict_and_sections() -> None:
    doc = CapabilityDocument.from_dict(
        {
            "driver": "ray",
            "backend_version": "2.58.0",
            "deployment": {"fixed_replicas": True},
            "topology": {"multi_node": True},
            "routing": {"kv_aware": "experimental"},
            "artifacts": {"llmport_sync": True},
        },
    )
    assert doc.driver == "ray"
    assert doc.backend_version == "2.58.0"
    assert doc.supports("deployment") is True
    assert doc.section("deployment") == {"fixed_replicas": True}
    assert doc.section("does-not-exist") is None
    # all four DEFAULT sections are declared -> nothing missing
    assert doc.missing_required() == []


def test_capability_missing_required_reports_defaults() -> None:
    doc = CapabilityDocument.from_dict({"driver": "ray"})
    assert doc.missing_required() == list(DEFAULT_CAPABILITY_SECTIONS)


def test_capability_from_dict_requires_dict() -> None:
    with pytest.raises(TypeError):
        CapabilityDocument.from_dict([1, 2, 3])  # type: ignore[arg-type]


def test_capability_from_dict_default_driver() -> None:
    # A missing driver key defaults to 'unknown' rather than raising.
    doc = CapabilityDocument.from_dict({"backend_version": "1.0"})
    assert doc.driver == "unknown"


def test_capability_from_dict_rejects_empty_driver() -> None:
    with pytest.raises(ValueError):
        CapabilityDocument.from_dict({"driver": ""})


def test_capability_round_trip_to_json() -> None:
    doc = CapabilityDocument.from_dict({"driver": "ray", "deployment": {"a": 1}})
    assert '"driver": "ray"' in doc.to_json()


# ---------------------------------------------------------------------------
# Driver registry
# ---------------------------------------------------------------------------


def test_registry_register_get_contains_keys() -> None:
    class _DriverA:  # noqa: N801
        pass

    class _DriverB:  # noqa: N801
        pass

    reg = registry
    reg.register("a", _DriverA)
    reg.register("z", _DriverB)
    try:
        assert reg.get("a") is _DriverA
        assert reg.contains("z") is True
        assert reg.contains("missing") is False
        assert reg.get("missing") is None
        # keys() returns a sorted list.
        keys = reg.keys()
        assert "a" in keys
        assert "z" in keys
    finally:
        reg._drivers.pop("a", None)
        reg._drivers.pop("z", None)


def test_registry_rejects_conflicting_key() -> None:
    class _DriverA:  # noqa: N801
        pass

    class _DriverB:  # noqa: N801
        pass

    reg = registry
    reg.register("x", _DriverA)
    try:
        with pytest.raises(ValueError):
            reg.register("x", _DriverB)
        # Re-registering the *same* class is idempotent.
        reg.register("x", _DriverA)
        assert reg.get("x") is _DriverA
    finally:
        reg._drivers.pop("x", None)


# ---------------------------------------------------------------------------
# Phase 1 no-op reconciliation seams
# ---------------------------------------------------------------------------


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        session=object(),
        control_planes=SimpleNamespace(reconcile=AsyncMock()),
        environments=SimpleNamespace(reconcile=AsyncMock()),
        deployments=SimpleNamespace(reconcile=AsyncMock()),
    )


async def test_reconcile_control_plane_noop() -> None:
    context: ReconciliationContext = _context()  # type: ignore[assignment]
    cp = SimpleNamespace(id=uuid.uuid4(), driver="ray")
    report = await reconcile_control_plane(context, cp)
    assert report["reconciled"] is False
    assert report["driver"] == "ray"
    assert report["id"] == str(cp.id)
    context.control_planes.reconcile.assert_awaited_once_with(cp.id)


async def test_reconcile_control_plane_with_driver_is_phase_2() -> None:
    class _Probe:  # noqa: N801
        async def probe(self, cp: Any) -> dict[str, Any]:
            return {"id": str(cp.id), "driver": cp.driver, "reconciled": True, "reason": "probed"}

    context: ReconciliationContext = _context()  # type: ignore[assignment]
    cp = SimpleNamespace(id=uuid.uuid4(), driver="probe-driver")
    registry.register("probe-driver", _Probe)
    try:
        report = await reconcile_control_plane(context, cp)
        assert report["reconciled"] is True
        assert report["reason"] == "probed"
    finally:
        registry._drivers.pop("probe-driver", None)  # noqa: SLF001


async def test_reconcile_environment_noop() -> None:
    context: ReconciliationContext = _context()  # type: ignore[assignment]
    env = SimpleNamespace(id=uuid.uuid4())
    report = await reconcile_environment(context, env)
    assert report["reconciled"] is False
    context.environments.reconcile.assert_awaited_once_with(env.id)


async def test_reconcile_deployment_noop() -> None:
    context: ReconciliationContext = _context()  # type: ignore[assignment]
    dep = SimpleNamespace(id=uuid.uuid4())
    report = await reconcile_deployment(context, dep)
    assert report["reconciled"] is False
    context.deployments.reconcile.assert_awaited_once_with(dep.id)
