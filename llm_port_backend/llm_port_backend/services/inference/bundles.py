"""Runtime Bundle Manifest specification and registry manager (Phase 4B).

Decouples container image references, OCI digest pinning, hardware requirements
and platform workarounds from deployment specifications:

- Defines immutable :class:`RuntimeBundleManifest` models.
- Expresses container needs as **semantic requirements** (``network_mode``,
  ``ipc_mode``, devices, mounts) rather than raw Docker CLI flags, so a bundle
  is portable across ``docker`` and ``podman`` handlers.
- Pins the exact OCI image identity, mandatory and machine-checkable, and
  carries the certification evidence that identity was granted under.
- Validates node architecture and GPU hardware compatibility.
- Injects certified platform-specific tuning (e.g. ``RAY_memory_monitor_refresh_ms=0``
  on GB10 unified memory).

Bundle identity is **generated from the build/certification artifacts**
(``runtime-manifest.json`` / ``build_report.json`` produced by the image build)
via :meth:`RuntimeBundleManifest.from_runtime_manifest` — never hand-written,
because a hand-written digest that disagrees with the artifact makes the
catalog authoritative-looking and wrong at the same time.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from llm_port_backend.db.models.node_control import InfraNode

log = logging.getLogger(__name__)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class BundleValidationError(ValueError):
    """A bundle manifest is not usable as a pinned, verifiable runtime."""


class AcceleratorSpec(BaseModel):
    """Accelerator hardware constraints for a bundle."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = "nvidia"
    families: list[str] = Field(default_factory=list)
    compute_capabilities: list[str] = Field(default_factory=list)
    min_cuda_driver: str | None = None


class TargetArchitecture(BaseModel):
    """Target host architecture."""

    model_config = ConfigDict(extra="forbid")

    cpu: str = "aarch64"  # or "x86_64"
    os: str = "linux"
    accelerator: AcceleratorSpec = Field(default_factory=AcceleratorSpec)


class ContainerMount(BaseModel):
    """Container volume mount definition."""

    model_config = ConfigDict(extra="forbid")

    host_path: str
    container_path: str
    mode: str = "ro"


class ContainerRequirements(BaseModel):
    """Semantic container requirements.

    Deliberately *not* a list of CLI flags: the node agent maps these onto
    whichever runtime handler it has (``docker`` / ``podman``), and a plan
    reviewer can read what the runtime needs without parsing argv.
    """

    model_config = ConfigDict(extra="forbid")

    network_mode: str = "host"
    ipc_mode: str = "host"
    pid_mode: str | None = None
    # "all", "none", or a device count/spec understood by the handler.
    gpus: str = "all"
    devices: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    shm_size: str | None = None
    ulimits: dict[str, str] = Field(default_factory=dict)


class ContainerSpec(BaseModel):
    """OCI container image specification, pinned by identity."""

    model_config = ConfigDict(extra="forbid")

    image: str
    # The image's content-addressable *config* identity, i.e. what
    # ``docker image inspect --format '{{.Id}}'`` reports.  Mandatory: this is
    # the integrity mechanism the air-gap direction rests on, and it is the
    # only identity that survives a ``docker save`` / ``docker load`` transfer
    # (a registry manifest digest does not).
    digest: str
    # Registry manifest digest (``RepoDigests``), when the image was pulled
    # rather than side-loaded.  Optional for exactly that reason.
    repo_digest: str | None = None
    runtime_handler: str = "docker"  # or "podman"
    requirements: ContainerRequirements = Field(default_factory=ContainerRequirements)
    mounts: list[ContainerMount] = Field(default_factory=list)

    @field_validator("digest", "repo_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_RE.match(value):
            raise ValueError(
                f"{value!r} is not a sha256 OCI digest (expected 'sha256:' + 64 hex chars)"
            )
        return value


class RayPlatformTuning(BaseModel):
    """Platform-specific Ray environment overrides."""

    model_config = ConfigDict(extra="forbid")

    env: dict[str, str] = Field(default_factory=dict)
    start_args: dict[str, Any] = Field(default_factory=dict)


class NcclPlatformTuning(BaseModel):
    """Platform-specific NCCL communication environment overrides."""

    model_config = ConfigDict(extra="forbid")

    env: dict[str, str] = Field(default_factory=dict)


class PlatformTuning(BaseModel):
    """Platform-specific system and framework tunings."""

    model_config = ConfigDict(extra="forbid")

    ray: RayPlatformTuning = Field(default_factory=RayPlatformTuning)
    nccl: NcclPlatformTuning = Field(default_factory=NcclPlatformTuning)
    # Diagnostic-only settings.  Never injected automatically: they are merged
    # by ``inject_platform_tuning(..., diagnostics=True)`` so that verbose
    # per-rank output is an operator decision, not a default.
    diagnostics: dict[str, str] = Field(default_factory=dict)


class CompatibilityMatrix(BaseModel):
    """Software stack shipped inside the bundle image.

    Every field is a fact read off the built artifact; §4B requires the full
    Ray/vLLM/Python/Torch/CUDA/NCCL/Triton set, not just the headline three.
    """

    model_config = ConfigDict(extra="forbid")

    ray_version: str
    vllm_version: str
    cuda_version: str
    python_version: str | None = None
    torch_version: str | None = None
    nccl_version: str | None = None
    triton_version: str | None = None
    transformers_version: str | None = None
    supported_fabrics: list[str] = Field(default_factory=lambda: ["roce", "infiniband", "ethernet"])


class CertificationEvidence(BaseModel):
    """What was actually certified, when, and with what result."""

    model_config = ConfigDict(extra="forbid")

    status: str = "uncertified"  # "passed", "partial", "failed", "uncertified"
    hardware_target: str | None = None
    timestamp: str | None = None
    checks_passed: int = 0
    checks_total: int = 0
    report_ref: str | None = None
    notes: list[str] = Field(default_factory=list)


class RuntimeBundleManifest(BaseModel):
    """Top-level immutable runtime bundle manifest document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "bundle.llmport.ai/v1alpha1"
    bundle_id: str
    display_name: str
    description: str = ""
    target_architecture: TargetArchitecture = Field(default_factory=TargetArchitecture)
    container: ContainerSpec
    platform_tuning: PlatformTuning = Field(default_factory=PlatformTuning)
    compatibility_matrix: CompatibilityMatrix
    certification: CertificationEvidence = Field(default_factory=CertificationEvidence)

    @classmethod
    def from_runtime_manifest(
        cls,
        manifest: dict[str, Any],
        *,
        bundle_id: str,
        display_name: str,
        description: str = "",
        target_architecture: TargetArchitecture | None = None,
        platform_tuning: PlatformTuning | None = None,
        mounts: list[ContainerMount] | None = None,
        requirements: ContainerRequirements | None = None,
        report_ref: str | None = None,
    ) -> "RuntimeBundleManifest":
        """Build a bundle entry from a built image's ``runtime-manifest.json``.

        This is the only supported way to mint a bundle's *identity*: image
        reference, digest, stack versions and certification evidence all come
        from the artifact the image build emitted, so the catalog cannot drift
        from what was certified.
        """
        stack = manifest.get("stack_components") or manifest.get("components") or {}
        image = manifest.get("release_tag") or manifest.get("target_image")
        digest = manifest.get("image_id")
        if not image:
            raise BundleValidationError("runtime manifest has no release_tag/target_image")
        if not digest:
            raise BundleValidationError(f"runtime manifest for {image} has no image_id")

        cert = manifest.get("certification") or {}
        checks = cert.get("checks") or []
        passed = sum(1 for c in checks if str(c.get("status", "")).upper() == "PASS")
        notes = [
            f"{c.get('name') or 'check'}: {c.get('status')} - {c.get('detail')}"
            for c in checks
            if str(c.get("status", "")).upper() != "PASS"
        ]
        status = str(cert.get("overall_status", "uncertified")).lower()
        if checks and passed < len(checks) and status == "passed":
            status = "partial"

        return cls(
            bundle_id=bundle_id,
            display_name=display_name,
            description=description,
            target_architecture=target_architecture or TargetArchitecture(),
            container=ContainerSpec(
                image=str(image),
                digest=str(digest),
                requirements=requirements or ContainerRequirements(),
                mounts=mounts or [],
            ),
            platform_tuning=platform_tuning or PlatformTuning(),
            compatibility_matrix=CompatibilityMatrix(
                ray_version=str(stack.get("ray", "")),
                vllm_version=str(stack.get("vllm", "")),
                cuda_version=str(stack.get("cuda", "")),
                python_version=_opt(stack.get("python")),
                torch_version=_opt(stack.get("torch")),
                nccl_version=_opt(stack.get("nccl")),
                triton_version=_opt(stack.get("triton")),
                transformers_version=_opt(stack.get("transformers")),
            ),
            certification=CertificationEvidence(
                status=status,
                hardware_target=_opt(cert.get("hardware_target")),
                timestamp=_opt(cert.get("timestamp")),
                checks_passed=passed,
                checks_total=len(checks) or int(cert.get("checks_total", 0) or 0),
                report_ref=report_ref,
                notes=notes,
            ),
        )


def _opt(value: Any) -> str | None:
    """Normalize an optional manifest field to ``str | None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Built-in certified DGX Spark Blackwell GB10 bundle
# ---------------------------------------------------------------------------
#
# Generated from ``llm_port_ray_migration/runtime_image/runtime-manifest.json``
# (image build + single-node certification, 2026-09-19).  Regenerate with
# ``RuntimeBundleManifest.from_runtime_manifest(json.load(open(...)), ...)``
# after every image rebuild — every value below is a fact off that artifact.
_CERTIFIED_DGX_SPARK_RUNTIME_MANIFEST: dict[str, Any] = {
    "release_tag": "llmport/ray-vllm-gb10:ray2.58-nv26.08",
    "image_id": "sha256:d5dd2c6ad48e571db57b59f80e8faf62814f6a8db0b8bb86067c8647a95ce7f3",
    "stack_components": {
        "python": "3.12.3",
        "cuda": "13.4",
        "nccl": "2.30.7",
        "torch": "2.14.0a0+4fdf77b940.nv26.08",
        "vllm": "0.27.1+93523f72.dev",
        "triton": "3.6.0+git5d72932fc5.nv26.3",
        "transformers": "5.14.1",
        "ray": "2.58.0",
    },
    "certification": {
        "hardware_target": "NVIDIA DGX Spark / GB10",
        "timestamp": "2026-09-19T19:33:24Z",
        "overall_status": "PASSED",
        "checks_total": 11,
        "checks": [
            {"name": "GPU Detection", "status": "PASS"},
            {"name": "BF16 CUDA Execution", "status": "PASS"},
            {"name": "Ray Head Startup", "status": "PASS"},
            {"name": "Ray SDK Probe (Dashboard-independent)", "status": "PASS"},
            {"name": "vLLM Engine Initialization (Offline)", "status": "PASS"},
            {"name": "vLLM Token Generation", "status": "PASS"},
            {"name": "Ray Serve LLM App Health", "status": "PASS"},
            {"name": "OpenAI Endpoint Response", "status": "PASS"},
            {"name": "OpenAI Streaming Response", "status": "PASS"},
            {"name": "Ray Serve Clean Shutdown", "status": "PASS"},
            {"name": "Prometheus Metrics Export", "status": "PASS"},
        ],
    },
}

CERTIFIED_DGX_SPARK_BUNDLE = RuntimeBundleManifest.from_runtime_manifest(
    _CERTIFIED_DGX_SPARK_RUNTIME_MANIFEST,
    bundle_id="bundle-dgx-spark-gb10-v1",
    display_name="NVIDIA DGX Spark Blackwell GB10 Runtime",
    description=(
        "Certified Ray 2.58 + vLLM 0.27.1 (NVIDIA nv26.08 build) image with "
        "Blackwell GB10 support over 200 Gb/s RoCE"
    ),
    report_ref="llm_port_ray_migration/runtime_image/runtime-manifest.json",
    target_architecture=TargetArchitecture(
        cpu="aarch64",
        os="linux",
        accelerator=AcceleratorSpec(
            vendor="nvidia",
            families=["Blackwell", "GB10"],
            compute_capabilities=["12.1"],
            min_cuda_driver="570.86.10",
        ),
    ),
    requirements=ContainerRequirements(
        network_mode="host",
        ipc_mode="host",
        gpus="all",
        # Verified present on both DGX Spark nodes.
        devices=["/dev/infiniband"],
        capabilities=["IPC_LOCK"],
    ),
    mounts=[
        ContainerMount(host_path="/srv/llm-port/models", container_path="/models", mode="ro"),
    ],
    platform_tuning=PlatformTuning(
        ray=RayPlatformTuning(
            env={
                # Certified necessary: the GB10's unified memory makes Ray's
                # host memory monitor evict workers spuriously.
                "RAY_memory_monitor_refresh_ms": "0",
            },
            start_args={
                "disable_usage_stats": True,
                "metrics_export_port": 8089,
            },
        ),
        nccl=NcclPlatformTuning(
            env={
                "NCCL_IB_RETRY_CNT": "7",
                "NCCL_BUFFSIZE": "16777216",
            },
        ),
        # Opt-in only: NCCL_DEBUG=INFO produces verbose per-rank output on
        # every collective and is a diagnostic, not a runtime setting.
        diagnostics={"NCCL_DEBUG": "INFO"},
    ),
)


class RuntimeBundleRegistry:
    """In-memory and file-backed registry for certified runtime bundles."""

    def __init__(self) -> None:
        self._bundles: dict[str, RuntimeBundleManifest] = {
            CERTIFIED_DGX_SPARK_BUNDLE.bundle_id: CERTIFIED_DGX_SPARK_BUNDLE,
        }

    def register_bundle(self, bundle: RuntimeBundleManifest) -> None:
        """Register a new or updated bundle after validating its identity."""
        self.validate_bundle(bundle)
        self._bundles[bundle.bundle_id] = bundle

    @staticmethod
    def validate_bundle(bundle: RuntimeBundleManifest) -> None:
        """Reject a bundle that cannot be pinned or resolved.

        The digest format is enforced by the model; this additionally refuses a
        bundle whose image reference or stack identity is empty, which would
        otherwise register as an authoritative-looking entry nothing can honour.
        """
        if not bundle.container.image.strip():
            raise BundleValidationError(f"bundle {bundle.bundle_id}: container.image is empty")
        matrix = bundle.compatibility_matrix
        missing = [
            name
            for name, value in (
                ("ray_version", matrix.ray_version),
                ("vllm_version", matrix.vllm_version),
                ("cuda_version", matrix.cuda_version),
            )
            if not str(value).strip()
        ]
        if missing:
            raise BundleValidationError(
                f"bundle {bundle.bundle_id}: compatibility_matrix.{', '.join(missing)} is empty"
            )

    def get_bundle(self, bundle_id: str) -> RuntimeBundleManifest | None:
        """Fetch a bundle by ID."""
        return self._bundles.get(bundle_id)

    def list_bundles(self) -> list[RuntimeBundleManifest]:
        """List all registered bundles."""
        return list(self._bundles.values())

    def load_from_yaml(self, path: Path | str) -> RuntimeBundleManifest:
        """Load, validate, and register a bundle from a YAML file."""
        file_path = Path(path)
        content = file_path.read_text(encoding="utf-8")
        raw_data = yaml.safe_load(content)
        manifest = RuntimeBundleManifest.model_validate(raw_data)
        self.register_bundle(manifest)
        return manifest

    def load_from_runtime_manifest(
        self,
        path: Path | str,
        *,
        bundle_id: str,
        display_name: str,
        **kwargs: Any,
    ) -> RuntimeBundleManifest:
        """Build and register a bundle from a built image's runtime manifest."""
        file_path = Path(path)
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        manifest = RuntimeBundleManifest.from_runtime_manifest(
            raw,
            bundle_id=bundle_id,
            display_name=display_name,
            report_ref=str(file_path),
            **kwargs,
        )
        self.register_bundle(manifest)
        return manifest

    def validate_node_compatibility(
        self,
        bundle: RuntimeBundleManifest,
        node: InfraNode,
    ) -> tuple[bool, str]:
        """Check if an InfraNode meets the bundle's hardware requirements."""
        caps = node.capabilities_json or {}
        gpu_info = caps.get("gpu") or {}
        gpus = gpu_info.get("devices") or []

        # Check GPU vendor
        vendor = (gpu_info.get("vendor") or caps.get("gpu_vendor") or "nvidia").lower()
        if vendor != bundle.target_architecture.accelerator.vendor.lower():
            return False, (
                f"Incompatible GPU vendor: expected "
                f"{bundle.target_architecture.accelerator.vendor}, found {vendor}"
            )

        # Host CPU architecture, when the node reported it.
        machine = str(caps.get("machine") or "").lower()
        expected_cpu = bundle.target_architecture.cpu.lower()
        if machine and expected_cpu:
            aliases = {"aarch64": {"aarch64", "arm64"}, "x86_64": {"x86_64", "amd64"}}
            accepted = aliases.get(expected_cpu, {expected_cpu})
            if machine not in accepted:
                return False, (
                    f"Incompatible CPU architecture: bundle targets {expected_cpu}, "
                    f"node reports {machine}"
                )

        # If bundle requires specific compute capability, verify if present
        required_caps = set(bundle.target_architecture.accelerator.compute_capabilities)
        if required_caps and gpus:
            node_caps = {
                str(g.get("compute_capability")) for g in gpus if g.get("compute_capability")
            }
            if node_caps and not required_caps.intersection(node_caps):
                return False, (
                    f"Compute capability mismatch: required {required_caps}, found {node_caps}"
                )

        return True, "Node is fully compatible with runtime bundle"

    def inject_platform_tuning(
        self,
        bundle: RuntimeBundleManifest,
        *,
        env_vars: dict[str, str] | None = None,
        diagnostics: bool = False,
    ) -> dict[str, str]:
        """Merge bundle platform tunings into the execution environment variables.

        ``diagnostics`` opts into the bundle's diagnostic settings (verbose
        NCCL tracing and similar); they are never applied by default.
        """
        merged: dict[str, str] = dict(env_vars or {})
        # Inject Ray tuning env vars
        for k, v in bundle.platform_tuning.ray.env.items():
            merged.setdefault(k, str(v))
        # Inject NCCL tuning env vars
        for k, v in bundle.platform_tuning.nccl.env.items():
            merged.setdefault(k, str(v))
        if diagnostics:
            for k, v in bundle.platform_tuning.diagnostics.items():
                merged.setdefault(k, str(v))
        return merged

    @staticmethod
    def container_launch_spec(
        bundle: RuntimeBundleManifest,
        *,
        name: str,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Render a bundle into the node-agent container launch contract.

        The semantic requirements are carried over as-is; turning them into
        handler flags is the agent's job (it owns the ``docker``/``podman``
        difference), which is exactly why they are not CLI strings here.
        """
        req = bundle.container.requirements
        return {
            "name": name,
            "image": bundle.container.image,
            "digest": bundle.container.digest,
            "repo_digest": bundle.container.repo_digest,
            "runtime_handler": bundle.container.runtime_handler,
            "requirements": req.model_dump(),
            "mounts": [m.model_dump() for m in bundle.container.mounts],
            "env": dict(env or {}),
        }


# Default singleton registry instance
default_bundle_registry = RuntimeBundleRegistry()
