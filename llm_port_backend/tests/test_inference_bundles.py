"""Unit tests for Runtime Bundle Manifest manager and platform tuning injection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.bundles import (
    CERTIFIED_DGX_SPARK_BUNDLE,
    ContainerSpec,
    RuntimeBundleManifest,
    RuntimeBundleRegistry,
)


def test_certified_dgx_spark_bundle_valid() -> None:
    """Verify built-in DGX Spark Blackwell bundle has required hardware tunings."""
    bundle = CERTIFIED_DGX_SPARK_BUNDLE
    assert bundle.bundle_id == "bundle-dgx-spark-gb10-v1"
    assert bundle.target_architecture.cpu == "aarch64"
    assert "12.1" in bundle.target_architecture.accelerator.compute_capabilities
    assert bundle.container.digest is not None
    assert bundle.container.digest.startswith("sha256:")

    # Crucial platform tuning for GB10 unified memory
    assert bundle.platform_tuning.ray.env.get("RAY_memory_monitor_refresh_ms") == "0"
    # NCCL_DEBUG is a diagnostic, not a runtime default: opt-in only.
    assert "NCCL_DEBUG" not in bundle.platform_tuning.nccl.env
    assert bundle.platform_tuning.diagnostics["NCCL_DEBUG"] == "INFO"


def test_certified_bundle_identity_matches_the_built_artifact() -> None:
    """The catalog entry must agree with the image that was actually certified.

    These are the values in ``runtime_image/runtime-manifest.json`` (and in
    ``docker image inspect`` on both DGX nodes).  A digest-pinned deployment is
    the integrity mechanism the air-gap direction rests on, so a drifted
    constant here is worse than no constant at all.
    """
    bundle = CERTIFIED_DGX_SPARK_BUNDLE
    assert bundle.container.image == "llmport/ray-vllm-gb10:ray2.58-nv26.08"
    assert bundle.container.digest == (
        "sha256:d5dd2c6ad48e571db57b59f80e8faf62814f6a8db0b8bb86067c8647a95ce7f3"
    )
    matrix = bundle.compatibility_matrix
    assert matrix.ray_version == "2.58.0"
    assert matrix.vllm_version == "0.27.1+93523f72.dev"
    assert matrix.cuda_version == "13.4"
    assert matrix.python_version == "3.12.3"
    assert matrix.torch_version == "2.14.0a0+4fdf77b940.nv26.08"
    assert matrix.nccl_version == "2.30.7"
    assert matrix.triton_version == "3.6.0+git5d72932fc5.nv26.3"

    assert bundle.certification.status == "passed"
    assert bundle.certification.checks_passed == bundle.certification.checks_total == 11
    assert bundle.certification.hardware_target == "NVIDIA DGX Spark / GB10"


def test_container_requirements_are_semantic_not_cli_flags() -> None:
    """Section 4B: semantic requirements, never raw Docker CLI strings."""
    req = CERTIFIED_DGX_SPARK_BUNDLE.container.requirements
    assert req.network_mode == "host"
    assert req.ipc_mode == "host"
    assert req.gpus == "all"
    assert "/dev/infiniband" in req.devices
    for value in (*req.devices, *req.capabilities, req.network_mode, req.ipc_mode, req.gpus):
        assert not str(value).startswith("-"), f"{value!r} looks like a CLI flag"


def test_bundle_digest_is_mandatory_and_checked() -> None:
    """A bundle that cannot be pinned must not be constructible."""
    with pytest.raises(ValidationError):
        ContainerSpec(image="llmport/ray-vllm-gb10:ray2.58-nv26.08")
    with pytest.raises(ValidationError):
        ContainerSpec(image="llmport/ray-vllm-gb10:ray2.58-nv26.08", digest="0.16.0")


def test_bundle_generated_from_runtime_manifest() -> None:
    """Bundle identity is generated from the build artifact, never hand-written."""
    manifest = {
        "release_tag": "example/img:v1",
        "image_id": "sha256:" + "ab" * 32,
        "stack_components": {"ray": "2.58.0", "vllm": "9.9.9", "cuda": "13.4"},
        "certification": {
            "overall_status": "PASSED",
            "hardware_target": "Example",
            "checks": [
                {"name": "a", "status": "PASS", "detail": "ok"},
                {"name": "b", "status": "PASS", "detail": "scraped head, worker timed out"},
            ],
        },
    }
    built = RuntimeBundleManifest.from_runtime_manifest(
        manifest, bundle_id="bundle-example", display_name="Example",
    )
    assert built.container.image == "example/img:v1"
    assert built.container.digest == "sha256:" + "ab" * 32
    assert built.compatibility_matrix.vllm_version == "9.9.9"
    assert built.certification.checks_passed == 2

    # A report that claims PASSED while a check did not pass is downgraded.
    manifest["certification"]["checks"][1]["status"] = "PARTIAL"
    partial = RuntimeBundleManifest.from_runtime_manifest(
        manifest, bundle_id="bundle-example", display_name="Example",
    )
    assert partial.certification.status == "partial"
    assert partial.certification.checks_passed == 1
    assert partial.certification.notes


def test_bundle_yaml_roundtrip(tmp_path: Path) -> None:
    """Verify bundle serialization and loading from YAML manifest."""
    yaml_file = tmp_path / "bundle.yaml"
    data = CERTIFIED_DGX_SPARK_BUNDLE.model_dump()
    yaml_file.write_text(yaml.dump(data), encoding="utf-8")

    registry = RuntimeBundleRegistry()
    loaded = registry.load_from_yaml(yaml_file)

    assert loaded.bundle_id == CERTIFIED_DGX_SPARK_BUNDLE.bundle_id
    assert loaded.container.image == CERTIFIED_DGX_SPARK_BUNDLE.container.image
    assert registry.get_bundle(loaded.bundle_id) is not None


def test_node_compatibility_validation() -> None:
    """Test validating node capabilities against bundle requirements."""
    registry = RuntimeBundleRegistry()
    bundle = CERTIFIED_DGX_SPARK_BUNDLE

    # 1. Compatible node
    good_node = InfraNode(
        agent_id="spark-ts3202",
        host="10.88.10.49",
        capabilities_json={
            "gpu": {
                "vendor": "nvidia",
                "devices": [{"name": "NVIDIA GB10", "compute_capability": "12.1"}],
            }
        },
    )
    ok, reason = registry.validate_node_compatibility(bundle, good_node)
    assert ok is True
    assert "fully compatible" in reason

    # 2. Incompatible vendor
    amd_node = InfraNode(
        agent_id="amd-node",
        host="10.88.10.99",
        capabilities_json={"gpu": {"vendor": "amd"}},
    )
    ok_amd, reason_amd = registry.validate_node_compatibility(bundle, amd_node)
    assert ok_amd is False
    assert "Incompatible GPU vendor" in reason_amd

    # 3. Incompatible compute capability
    old_node = InfraNode(
        agent_id="v100-node",
        host="10.88.10.98",
        capabilities_json={
            "gpu": {
                "vendor": "nvidia",
                "devices": [{"name": "NVIDIA V100", "compute_capability": "7.0"}],
            }
        },
    )
    ok_old, reason_old = registry.validate_node_compatibility(bundle, old_node)
    assert ok_old is False
    assert "Compute capability mismatch" in reason_old


def test_inject_platform_tuning() -> None:
    """Test platform tuning injection into runtime environment."""
    registry = RuntimeBundleRegistry()
    bundle = CERTIFIED_DGX_SPARK_BUNDLE

    initial_env = {"VLLM_HOST_IP": "10.100.0.1"}
    injected = registry.inject_platform_tuning(bundle, env_vars=initial_env)

    assert injected["VLLM_HOST_IP"] == "10.100.0.1"
    assert injected["RAY_memory_monitor_refresh_ms"] == "0"
    assert injected["NCCL_IB_RETRY_CNT"] == "7"
    # Verbose per-rank tracing is not a runtime default.
    assert "NCCL_DEBUG" not in injected

    opted_in = registry.inject_platform_tuning(
        bundle, env_vars=initial_env, diagnostics=True,
    )
    assert opted_in["NCCL_DEBUG"] == "INFO"


def test_container_launch_spec_carries_semantic_requirements() -> None:
    """The agent launch contract passes requirements through, not flags."""
    registry = RuntimeBundleRegistry()
    spec = registry.container_launch_spec(
        CERTIFIED_DGX_SPARK_BUNDLE,
        name="llm-port-ray-runtime",
        env={"VLLM_HOST_IP": "10.100.0.1"},
    )
    assert spec["image"] == CERTIFIED_DGX_SPARK_BUNDLE.container.image
    assert spec["digest"] == CERTIFIED_DGX_SPARK_BUNDLE.container.digest
    assert spec["requirements"]["network_mode"] == "host"
    assert spec["requirements"]["ipc_mode"] == "host"
    assert spec["env"]["VLLM_HOST_IP"] == "10.100.0.1"
    assert any(m["container_path"] == "/models" for m in spec["mounts"])

