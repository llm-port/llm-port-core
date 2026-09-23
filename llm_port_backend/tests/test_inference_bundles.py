"""Unit tests for Runtime Bundle Manifest manager and platform tuning injection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.bundles import (  # noqa: PLC0415
    CERTIFIED_DGX_SPARK_BUNDLE,
    ContainerSpec,
    RuntimeBundleManifest,
    RuntimeBundleRegistry,
    _load_runtime_manifest,
    compute_rootfs_digest,
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


def test_certified_bundle_identity_matches_the_deployed_image() -> None:
    """The catalogue entry is the manifest the image build wrote -- nothing else.

    This used to assert a literal digest, which made it a third hand-kept copy
    of a fact the build already records: the catalogue dict, this test and the
    manifest file each held an image id, and they drifted apart. The catalogue
    went on pinning an image that no longer existed anywhere, and every cluster
    start was refused by the integrity check -- correctly -- 303 times.

    So assert the derivation instead. Whatever the last build produced, the
    catalogue must pin exactly that, and derive its content identity from the
    same layers.
    """
    manifest = _load_runtime_manifest("runtime-manifest.json")
    bundle = CERTIFIED_DGX_SPARK_BUNDLE

    assert bundle.container.image == manifest["release_tag"]
    assert bundle.container.digest == manifest["image_id"]
    assert bundle.container.rootfs_digest == compute_rootfs_digest(
        list(manifest["rootfs_layers"])
    )

    # The stack is read from the helper inside the image; these pin the
    # platform the DGX pair is qualified on, and would move with a real
    # upgrade rather than with a rebuild.
    matrix = bundle.compatibility_matrix
    assert matrix.ray_version == "2.58.0"
    assert matrix.vllm_version == manifest["stack_components"]["vllm"]
    assert matrix.cuda_version == "13.4"
    assert matrix.python_version == "3.12.3"
    assert matrix.torch_version == "2.14.0a0+4fdf77b940.nv26.08"


def test_the_catalogue_is_not_transcribed_beside_the_manifest() -> None:
    """There must be no second, hand-kept copy for the two to disagree with."""
    from llm_port_backend.services.inference import bundles

    assert not hasattr(bundles, "_CERTIFIED_DGX_SPARK_RUNTIME_MANIFEST")


def test_a_build_time_manifest_records_nccl() -> None:
    """NCCL's version needs the library, not a GPU.

    The in-image helper only asked when a GPU was visible, and a build never
    has one -- so every manifest written at build time recorded nccl as None,
    and the catalogue quietly lost a version it used to show.
    """
    assert CERTIFIED_DGX_SPARK_BUNDLE.compatibility_matrix.nccl_version


def test_certification_status_is_not_inherited_across_a_rebuild() -> None:
    """A rebuilt image has not earned the previous image's evidence.

    The old entry read "partial, 10/11" because the shipped image was missing
    ``opencensus``.  The rebuild fixes that, but a *different* artifact cannot
    inherit the old run's result: certification is evidence from hardware, not
    a property of the Dockerfile.  Until ``remote_certify_2node.py`` runs
    against this image the honest status is "uncertified", which is also what
    stops the UI from presenting it as proven.
    """
    cert = CERTIFIED_DGX_SPARK_BUNDLE.certification
    assert cert.status == "uncertified"
    assert cert.checks_total == 0
    assert cert.checks_passed == 0


def test_rootfs_digest_is_canonical_and_order_sensitive() -> None:
    """The agent recomputes this independently; the rule has to be exact."""
    layers = ["sha256:" + "aa" * 32, "sha256:" + "bb" * 32]
    digest = compute_rootfs_digest(layers)
    assert digest.startswith("sha256:")
    assert compute_rootfs_digest(layers) == digest
    # Layer order is part of the identity: two images with the same layers in a
    # different order are not the same image.
    assert compute_rootfs_digest(list(reversed(layers))) != digest


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


def test_content_identity_is_derived_from_layers_not_transcribed() -> None:
    """A manifest carrying ``rootfs_layers`` yields the digest by computation.

    This is the fix for the image drift: the build emits the layer list and the
    catalog derives identity from it, so there is no hand-copied digest to
    disagree with the artifact.  ``rebuild_runtime_image.py`` deliberately
    writes no ``rootfs_digest`` for this reason.
    """
    layers = ["sha256:" + f"{index:02x}" * 32 for index in range(3)]
    manifest = {
        "release_tag": "example/img:v1",
        "image_id": "sha256:" + "ab" * 32,
        "rootfs_layers": layers,
        "stack_components": {"ray": "2.58.0", "vllm": "9.9.9", "cuda": "13.4"},
    }
    built = RuntimeBundleManifest.from_runtime_manifest(
        manifest, bundle_id="bundle-example", display_name="Example",
    )
    assert built.container.rootfs_digest == compute_rootfs_digest(layers)

    # An explicit digest still wins, so an older manifest keeps working.
    manifest["rootfs_digest"] = "sha256:" + "cd" * 32
    pinned = RuntimeBundleManifest.from_runtime_manifest(
        manifest, bundle_id="bundle-example", display_name="Example",
    )
    assert pinned.container.rootfs_digest == "sha256:" + "cd" * 32


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
    assert spec["rootfs_digest"] == CERTIFIED_DGX_SPARK_BUNDLE.container.rootfs_digest
    assert spec["requirements"]["network_mode"] == "host"
    assert spec["requirements"]["ipc_mode"] == "host"
    assert spec["env"]["VLLM_HOST_IP"] == "10.100.0.1"
    assert any(m["container_path"] == "/models" for m in spec["mounts"])



# ── resolution and node-supplied mounts ──────────────────────────────────


def _node(agent_id: str, **caps: object) -> InfraNode:
    return InfraNode(agent_id=agent_id, host="10.0.0.1", capabilities_json=dict(caps))


def test_resolution_follows_the_machine_not_a_pin() -> None:
    """Each node gets the image built for its own platform."""
    registry = RuntimeBundleRegistry()

    dgx = _node("spark", machine="aarch64", gpu_vendor="nvidia", gpu_count=1)
    workstation = _node("box", machine="x86_64", gpu_vendor="nvidia", gpu_count=1)

    assert registry.resolve_for_node(dgx).bundle_id == "bundle-dgx-spark-gb10-v1"
    assert (
        registry.resolve_for_node(workstation).bundle_id
        == "bundle-generic-x86_64-nvidia-v1"
    )


def test_a_node_that_has_not_reported_its_platform_resolves_to_nothing() -> None:
    """Silence is not a match.

    ``validate_node_compatibility`` is permissive -- an unreported field is
    not a reason something cannot work -- so without this an unknown machine
    matched every bundle and took whichever sorted first, which is how an
    aarch64 image would be sent to a machine nobody had inventoried.
    """
    registry = RuntimeBundleRegistry()
    assert registry.resolve_for_node(_node("unknown")) is None
    assert registry.resolve_for_node(_node("blank", machine="  ")) is None


def test_a_generic_bundle_takes_its_mounts_from_the_node() -> None:
    """The image says which container paths it needs; the node says where."""
    registry = RuntimeBundleRegistry()
    bundle = registry.get_bundle("bundle-generic-x86_64-nvidia-v1")
    assert bundle is not None
    # The point of a generic bundle: it names no host path, because it has
    # never seen the machine.
    assert bundle.container.mounts == []

    node = _node(
        "box",
        machine="x86_64",
        gpu_vendor="nvidia",
        paths={"model_store": "/home/op/.cache/huggingface", "ray_session": "/var/lib/llm-port/ray"},
    )
    spec = registry.container_launch_spec(bundle, name="llm-port-ray-runtime", node=node)
    by_target = {m["container_path"]: m for m in spec["mounts"]}
    assert by_target["/models"]["host_path"] == "/home/op/.cache/huggingface"
    assert by_target["/models"]["mode"] == "ro"
    assert by_target["/tmp/ray"]["host_path"] == "/var/lib/llm-port/ray"
    assert by_target["/tmp/ray"]["mode"] == "rw"


def test_a_bundle_that_declares_a_mount_keeps_it() -> None:
    """A bundle certified for one machine's layout is not overridden."""
    registry = RuntimeBundleRegistry()
    node = _node(
        "spark",
        machine="aarch64",
        gpu_vendor="nvidia",
        paths={"model_store": "/somewhere/else", "ray_session": "/elsewhere"},
    )
    spec = registry.container_launch_spec(
        CERTIFIED_DGX_SPARK_BUNDLE, name="llm-port-ray-runtime", node=node
    )
    by_target = {m["container_path"]: m["host_path"] for m in spec["mounts"]}
    assert by_target["/models"] == "/srv/llm-port/models"
    assert by_target["/tmp/ray"] == "/var/lib/llm-port/ray"


def test_a_node_with_no_reported_paths_gets_no_invented_ones() -> None:
    """Better an absent mount than one pointing at a guess."""
    registry = RuntimeBundleRegistry()
    bundle = registry.get_bundle("bundle-generic-x86_64-nvidia-v1")
    node = _node("box", machine="x86_64", gpu_vendor="nvidia")
    spec = registry.container_launch_spec(bundle, name="llm-port-ray-runtime", node=node)
    assert spec["mounts"] == []
