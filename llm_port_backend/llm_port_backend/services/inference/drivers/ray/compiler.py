"""Deterministic compiler from an LLM.Port deployment spec to a Ray config.

Phase 3 turns an :class:`~llm_port_backend.services.inference.schemas.InferenceDeploymentSpecV1Alpha1`
plus the resolved :class:`~llm_port_backend.db.models.llm.LLMModel` record into the
``LLMServingArgs``-shaped dictionary that the head-node agent hands to
``ray.serve.llm.build_openai_app`` and deploys with ``serve.run``.

The compiler is **pure and Ray-free** on purpose: it imports nothing from
``ray`` and performs no I/O, so it can be unit-tested against golden fixtures
without a cluster, and it can never pull the (much larger, GPU-oriented) Ray
dependency into the backend image.  The only Ray knowledge it encodes are the
field names and value constraints of ``ray.serve.llm.LLMConfig`` /
``LLMServingArgs`` (verified against the pinned ``ray[serve]==2.58.0``), and a
whitelist of ``AcceleratorType`` values the backend can safely emit.

The mapping (see the Ray migration plan, Phase 3):

    engine.name         -> ``llm_engine`` ("vllm" -> "vLLM")
    engine.config       -> ``engine_kwargs``
    scale.replicas      -> ``deployment_config.num_replicas``
    scale.autoscale     -> ``deployment_config.autoscaling_config``
    resources.replica   -> ``placement_group_config.bundle_per_worker`` (only
                           for fractional GPU / explicit CPU; else Ray's
                           per-device default bundles)
    resources.placement,
    topology.nodes      -> ``placement_group_config.strategy``
    topology.*          -> ``engine_kwargs.tensor/pipeline_parallel_size``
    (accelerator)       -> ``accelerator_type``   (whitelisted, else omitted)
    (model/artifacts)   -> ``model_loading_config`` (HF id or local path)

Advanced features (P/D disaggregation, LoRA, prefix/KV-aware routing, custom
router replicas) are deliberately **not** emitted; they are left to Ray's
defaults and a later phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from llm_port_backend.services.inference.schemas import (
    InferenceDeploymentSpecV1Alpha1,
    parse_inference_deployment_spec,
)

# Ray's ``ray.serve.llm`` only supports the vLLM engine.  The stored spec uses
# the short form (``"vllm"``); Ray's ``LLMConfig.llm_engine`` validator
# requires the exact ``"vLLM"`` spelling, and rejects everything else.
_RAY_ENGINE_VLLM = "vLLM"
_SUPPORTED_ENGINE_NAMES = frozenset({"vllm"})

# Accelerators Ray's ``AcceleratorType`` registry (``ray[serve]==2.58.0``)
# actually accepts.  ``LLMConfig.accelerator_type`` raises
# "Unsupported accelerator type" for anything not on this list, so the
# compiler only emits a value it is certain Ray will accept.  Hardware Ray does
# not know (e.g. the DGX Spark's GB10) is intentionally left as ``None`` —
# vLLM schedules on plain ``GPU`` resources in that case, which is the correct
# behaviour for an unregistered accelerator.  ``A10`` is folded to ``A10G``
# (Ray's alias) so a bare "A10" spec still deploys.
_RAY_ACCELERATOR_WHITELIST: frozenset[str] = frozenset({
    "V100",
    "P100",
    "T4",
    "P4",
    "K80",
    "A10G",
    "L4",
    "L40S",
    "A100",
    "A100-40G",
    "A100-80G",
    "H100",
    "H200",
    "H20",
    "B200",
    "B300",
    "GB200",
    "GB300",
    "RTX-PRO-6000",
})
_ACCELERATOR_ALIASES: dict[str, str] = {
    "A10": "A10G",
    # Case-normalize common spellings without changing the family.
    "A100 40GB": "A100-40G",
    "A100 80GB": "A100-80G",
    "H100 80GB": "H100",
    "RTX PRO 6000": "RTX-PRO-6000",
}

# ``ModelLoadingConfig.model_id`` is the label clients hit in ``/v1/models``
# and use on every request.  It must be non-empty; we derive it from the
# record's ``display_name`` (sanitized) and fall back to the HF repo id.
_MODEL_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MODEL_ID_MAX = 128


class ArtifactResolutionError(ValueError):
    """Raised when the deployment's model artifact cannot be resolved.

    Subclasses ``ValueError`` so it is caught by the orchestrator's existing
    ``validate``/``plan`` error handling and surfaced as a deployment
    ``FAILED`` phase rather than an unhandled loop crash.
    """


class DeploymentValidationError(ValueError):
    """Raised when an InferenceDeploymentSpec is unsupported or invalid for Ray."""


def validate_spec(spec: InferenceDeploymentSpecV1Alpha1) -> None:
    """Validate that the spec is supported by the Ray driver.

    Raises:
        DeploymentValidationError: if any unsupported field or invalid combination is detected.
    """
    engine_name = (spec.engine.name or "").strip().lower()
    if engine_name not in _SUPPORTED_ENGINE_NAMES:
        raise DeploymentValidationError(
            f"unsupported engine for Ray: {spec.engine.name!r} (expected one of {sorted(_SUPPORTED_ENGINE_NAMES)})"
        )
    if spec.replica_routing and spec.replica_routing.kv_aware:
        raise DeploymentValidationError(
            "KV-aware replica routing is not supported on Ray Serve v1alpha1"
        )
    if spec.scale.autoscale is not None:
        autoscale = spec.scale.autoscale
        min_rep = autoscale.min_replicas
        max_rep = autoscale.max_replicas
        if min_rep is not None and max_rep is not None and min_rep > max_rep:
            raise DeploymentValidationError(
                f"min_replicas ({min_rep}) cannot be greater than max_replicas ({max_rep})"
            )
        # Ray Serve autoscales on ongoing requests per replica, not on a
        # utilisation fraction; mapping one onto the other would scale to
        # max_replicas under any load.  Require Ray's own knob explicitly.
        if autoscale.target_utilization is not None or autoscale.metrics is not None:
            raise DeploymentValidationError(
                "scale.autoscale.target_utilization/metrics have no Ray Serve equivalent; "
                "use extensions.ray.target_ongoing_requests instead"
            )
    _ray_target_ongoing_requests(spec)  # validates the extension's type
    _placement(spec)  # validates the GPU / topology combination
    _extension_env_vars(spec)  # validates extension runtime_env keys


@dataclass(frozen=True)
class ArtifactResolution:
    """How the model weights reach the Ray worker.

    A resolved artifact is either a *local path* already present on the
    environment node(s) (``is_local``) or a *remote* (Hugging Face) id that the
    engine downloads on first worker start.
    """

    model_id: str
    model_source: str
    is_local: bool
    is_remote: bool


def _sanitize_model_id(raw: str | None, fallback: str | None) -> str:
    """Turn an arbitrary display name into a request-safe ``model_id``."""
    source = raw or fallback or None
    if not source:
        raise ArtifactResolutionError(
            "cannot derive a model_id: the LLMModel has neither display_name nor hf_repo_id"
        )
    cleaned = _MODEL_ID_SAFE.sub("-", source.strip()).strip("-.")
    cleaned = cleaned or "model"
    return cleaned[:_MODEL_ID_MAX]


def resolve_artifact(
    *,
    spec: InferenceDeploymentSpecV1Alpha1,
    model_display_name: str,
    model_source: str,
    hf_repo_id: str | None = None,
    hf_revision: str | None = None,
    availability_root_path: str | None = None,
) -> ArtifactResolution:
    """Resolve where the engine loads the weights from.

    Resolution order for ``model_source``:

    1. spec ``artifacts.source == "local_path"``  → the spec's ``root_path``;
    2. spec ``artifacts.source == "remote"``      → the spec's ``root_path``
       (a remote URI) when present, else the model's HF repo id;
    3. spec ``artifacts.source == "sync"`` (default) → the per-node
       ``ModelAvailability.root_path`` when the artifact is known-ready, else
       the model's HF repo id (the engine falls back to a download).

    The Hugging Face source prefers the record's ``hf_repo_id`` (the canonical
    remote identity) when the model is a HF model; otherwise the
    ``display_name`` acts as the repo id.  ``model_id`` is always a sanitized
    local label, independent of ``model_source``.

    Raises:
        ArtifactResolutionError: if a local-path source has no usable path.
    """
    artifacts = spec.artifacts
    source = (artifacts.source or "sync").strip().lower()

    if source == "local_path":
        path = artifacts.root_path
        if not path:
            raise ArtifactResolutionError(
                "artifacts.source='local_path' requires artifacts.root_path"
            )
        model_id = _sanitize_model_id(model_display_name, hf_repo_id)
        return ArtifactResolution(
            model_id=model_id,
            model_source=path,
            is_local=True,
            is_remote=False,
        )

    if source == "remote":
        path = artifacts.root_path
        repo = path or hf_repo_id or model_display_name
        model_id = _sanitize_model_id(model_display_name, hf_repo_id)
        return ArtifactResolution(
            model_id=model_id,
            model_source=repo,
            is_local=False,
            is_remote=True,
        )

    # source == "sync" (default): prefer a synced local root if we know the
    # artifact is present; otherwise let the engine pull from the HF repo.
    if availability_root_path:
        model_id = _sanitize_model_id(model_display_name, hf_repo_id)
        return ArtifactResolution(
            model_id=model_id,
            model_source=availability_root_path,
            is_local=True,
            is_remote=False,
        )
    repo = hf_repo_id or model_display_name
    model_id = _sanitize_model_id(model_display_name, hf_repo_id)
    return ArtifactResolution(
        model_id=model_id,
        model_source=repo,
        is_local=False,
        is_remote=True,
    )


def _accelerator_type(raw: str | None) -> str | None:
    """Map a spec accelerator name onto a Ray-accepted value, else ``None``.

    An unknown accelerator is *omitted* (``None``) rather than emitted, because
    ``LLMConfig`` would reject it and the deployment would fail to build.  A
    ``None`` accelerator is valid and lets vLLM schedule on generic ``GPU``
    resources.
    """
    if not raw:
        return None
    name = raw.strip()
    if not name:
        return None
    if name in _ACCELERATOR_ALIASES:
        name = _ACCELERATOR_ALIASES[name]
    if name in _RAY_ACCELERATOR_WHITELIST:
        return name
    return None


def _deployment_config(
    *,
    replicas: int,
    min_replicas: int | None,
    max_replicas: int | None,
    target_ongoing_requests: float | None = None,
    upscale_delay_s: float | None = None,
    downscale_delay_s: float | None = None,
) -> dict[str, Any]:
    """Build ``LLMConfig.deployment_config`` for a resolved replica count.

    * ACTIVE with fixed replicas → ``num_replicas``.
    * ACTIVE with autoscaling bounds → ``autoscaling_config`` (min/max).
      When ``autoscaling_config`` is emitted, ``num_replicas`` is NEVER emitted.
    * STOPPED → ``num_replicas: 0`` (scale to zero, no live workers).

    ``num_replicas`` and ``autoscaling_config`` are mutually exclusive in Ray's
    ``DeploymentConfig``; exactly one path is taken here.
    """
    config: dict[str, Any] = {}
    if replicas <= 0 and max_replicas is None:
        # Scale to zero with fixed replicas. Serve accepts num_replicas=0.
        config["num_replicas"] = 0
        return config

    if max_replicas is not None:
        autoscaling: dict[str, Any] = {
            "min_replicas": int(min_replicas if min_replicas is not None else 1),
            "max_replicas": int(max_replicas),
        }
        if target_ongoing_requests is not None:
            autoscaling["target_ongoing_requests"] = target_ongoing_requests
        if upscale_delay_s is not None:
            autoscaling["upscale_delay_s"] = float(upscale_delay_s)
        if downscale_delay_s is not None:
            autoscaling["downscale_delay_s"] = float(downscale_delay_s)
        config["autoscaling_config"] = autoscaling
    else:
        config["num_replicas"] = int(replicas)
    return config


def _engine_kwargs(
    *,
    engine_config: dict[str, Any],
    tensor_parallel_size: int | None,
    pipeline_parallel_size: int | None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Merge spec engine kwargs with topology-derived TP/PP and model revision.

    The operator-provided ``engine.config`` wins for any key it sets (an
    explicit override), but a topology TP/PP that the operator did *not* also
    place in ``engine.config`` is always injected, because vLLM reads TP/PP
    exclusively from ``engine_kwargs``.
    """
    kwargs: dict[str, Any] = dict(engine_config or {})
    if tensor_parallel_size is not None:
        kwargs.setdefault("tensor_parallel_size", int(tensor_parallel_size))
    if pipeline_parallel_size is not None:
        kwargs.setdefault("pipeline_parallel_size", int(pipeline_parallel_size))
    if revision:
        kwargs.setdefault("revision", str(revision))
    return kwargs


_PLACEMENT_STRATEGIES = frozenset({"PACK", "STRICT_PACK", "SPREAD", "STRICT_SPREAD"})

# Env vars a (user-controlled) spec may set on replicas.  Everything else in
# ``runtime_env`` (pip, working_dir, py_modules, ...) installs or runs code on
# the GPU nodes and is therefore not accepted from a spec.
_ENV_VAR_PREFIXES = ("VLLM_", "HF_", "NCCL_", "CUDA_")
_ENV_VAR_DENYLIST = frozenset({"CUDA_VISIBLE_DEVICES"})


def _ray_extension(spec: InferenceDeploymentSpecV1Alpha1) -> dict[str, Any]:
    ext = (spec.extensions or {}).get("ray") if isinstance(spec.extensions, dict) else None
    return ext if isinstance(ext, dict) else {}


def _ray_target_ongoing_requests(spec: InferenceDeploymentSpecV1Alpha1) -> float | None:
    value = _ray_extension(spec).get("target_ongoing_requests")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise DeploymentValidationError("extensions.ray.target_ongoing_requests must be a positive number")
    return float(value)


def _extension_env_vars(spec: InferenceDeploymentSpecV1Alpha1) -> dict[str, str]:
    """Allow-listed env vars from the spec's extensions.

    Accepted sources: ``extensions.env_vars``, ``extensions.runtime_env.env_vars``
    and ``extensions.ray.runtime_env.env_vars``.

    Raises:
        DeploymentValidationError: for any other ``runtime_env`` key or an env
            var outside the allow-list.
    """
    exts = spec.extensions if isinstance(spec.extensions, dict) else {}
    merged: dict[str, Any] = {}
    if isinstance(exts.get("env_vars"), dict):
        merged.update(exts["env_vars"])
    for label, runtime_env in (
        ("extensions.runtime_env", exts.get("runtime_env")),
        ("extensions.ray.runtime_env", _ray_extension(spec).get("runtime_env")),
    ):
        if not isinstance(runtime_env, dict):
            continue
        for key, value in runtime_env.items():
            if key != "env_vars":
                raise DeploymentValidationError(
                    f"{label}.{key} is not accepted from a deployment spec; only env_vars may be set"
                )
            if isinstance(value, dict):
                merged.update(value)
    for key in merged:
        if key in _ENV_VAR_DENYLIST or not str(key).startswith(_ENV_VAR_PREFIXES):
            raise DeploymentValidationError(
                f"env var {key!r} is not allowed (allowed prefixes: {', '.join(_ENV_VAR_PREFIXES)}; "
                f"never {', '.join(sorted(_ENV_VAR_DENYLIST))})"
            )
    return {str(k): str(v) for k, v in merged.items()}


def _placement(spec: InferenceDeploymentSpecV1Alpha1) -> dict[str, Any] | None:
    """``placement_group_config`` for the replica, or ``None`` for Ray's default.

    Ray LLM's default is one ``{"GPU": 1}`` bundle per device (TP×PP) with
    strategy ``PACK`` (cross-node, best effort) — the right shape for
    integral GPUs, including TP/PP spread over several 1-GPU nodes.  A config
    is emitted only to change that: a fractional GPU per worker, an explicit
    CPU request, a placement strategy, or a ``topology.nodes`` pin (which maps
    to a strategy by the section-15 rule lock).  It always uses ``bundle_per_worker``
    (Ray expands it to TP×PP bundles; a config without bundles would produce
    *no* bundles), and never a hand-built ``accelerator_type:X`` key — Ray adds
    its own fractional accelerator hint from ``LLMConfig.accelerator_type``.

    Raises:
        DeploymentValidationError: GPU count inconsistent with TP×PP, or an
            unknown/impossible placement request.
    """
    topology = spec.topology
    num_devices = int(topology.tensor_parallel_size or 1) * int(topology.pipeline_parallel_size or 1)
    replica = spec.resources.replica
    gpus = float(replica.gpus) if replica.gpus is not None else None

    per_worker_gpu = 1.0
    if gpus is not None and gpus != num_devices:
        if num_devices == 1 and 0 < gpus < 1:
            per_worker_gpu = gpus  # fractional single-device replica
        else:
            raise DeploymentValidationError(
                f"resources.replica.gpus ({replica.gpus}) must equal tensor_parallel_size x "
                f"pipeline_parallel_size ({num_devices}), or be a fraction when that is 1"
            )

    strategy: str | None = None
    if spec.resources.placement:
        strategy = spec.resources.placement.strip().upper()
        if strategy not in _PLACEMENT_STRATEGIES:
            raise DeploymentValidationError(
                f"resources.placement {spec.resources.placement!r} is not one of {sorted(_PLACEMENT_STRATEGIES)}"
            )
    else:
        # Check extensions.ray for placementStrategy override
        ray_ext = _ray_extension(spec)
        ext_placement = ray_ext.get("placementStrategy") or ray_ext.get("placement_strategy")
        if ext_placement and isinstance(ext_placement, str):
            strategy = ext_placement.strip().upper()
            if strategy not in _PLACEMENT_STRATEGIES:
                raise DeploymentValidationError(
                    f"extensions.ray.placementStrategy {ext_placement!r} is not one of {sorted(_PLACEMENT_STRATEGIES)}"
                )

    if topology.nodes is not None:
        if topology.nodes > num_devices:
            raise DeploymentValidationError(
                f"topology.nodes ({topology.nodes}) exceeds the replica's devices ({num_devices})"
            )
        if strategy == "STRICT_PACK" and topology.nodes > 1:
            raise DeploymentValidationError(
                f"STRICT_PACK is impossible with topology.nodes ({topology.nodes}) > 1; "
                "STRICT_PACK forces all bundles onto a single node and causes deadlock"
            )
        if strategy is None:
            # Rule lock (Phase3_upgrade.md section 15, evidence-driven on the
            # DGX Spark pair): nodes == 1 colocates strictly, nodes > 1 spreads.
            # Without this, ``topology.nodes`` is a no-op and Ray's default soft
            # PACK can spill a TP group across hosts for nodes: 1 (silent
            # tensor parallelism over the network) or pack nodes: 2 onto one
            # host.  An explicit operator strategy still wins — it is validated
            # against the deadlock case just above.
            strategy = "STRICT_PACK" if topology.nodes == 1 else "SPREAD"

    if per_worker_gpu == 1.0 and replica.cpu is None and strategy is None:
        return None
    bundle: dict[str, float] = {"GPU": per_worker_gpu}
    if replica.cpu is not None:
        bundle["CPU"] = float(replica.cpu) / num_devices
    config: dict[str, Any] = {"bundle_per_worker": bundle}
    if strategy is not None:
        config["strategy"] = strategy
    return config


def compile_spec(
    *,
    spec: InferenceDeploymentSpecV1Alpha1,
    artifact: ArtifactResolution,
    desired_state: str = "active",
    accelerator_type: str | None = None,
    revision: str | None = None,
    runtime_env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one resolved deployment into a Ray ``LLMServingArgs`` document.

    Args:
        spec: the validated deployment spec.
        artifact: the resolved artifact (``model_id`` / ``model_source``).
        desired_state: ``"active"`` or ``"stopped"`` (affects replica count).
        accelerator_type: resolved accelerator name *before* the Ray whitelist
            mapping (the caller passes the raw spec value; this function maps).
        revision: optional model revision to thread into engine_kwargs.
        runtime_env: optional runtime_env dictionary to inject into LLMConfig.

    Returns:
        A plain dictionary shaped like ``ray.serve.llm.LLMServingArgs`` with a
        single ``llm_configs`` entry.  It carries only JSON-safe values and no
        Ray types, so it can be embedded in a control-plane command payload.
    """
    validate_spec(spec)

    topology = spec.topology
    tp = topology.tensor_parallel_size
    pp = topology.pipeline_parallel_size

    effective_revision = spec.artifacts.revision or revision
    engine_kwargs = _engine_kwargs(
        engine_config=spec.engine.config,
        tensor_parallel_size=tp,
        pipeline_parallel_size=pp,
        revision=effective_revision,
    )

    # Determine the active replica representation.
    stopped = (desired_state or "active").strip().lower() == "stopped"
    scale = spec.scale
    if scale.autoscale is not None:
        replicas = int(scale.autoscale.min_replicas)
        min_replicas = int(scale.autoscale.min_replicas)
        max_replicas = int(scale.autoscale.max_replicas)
    else:
        replicas = int(scale.replicas if scale.replicas is not None else 1)
        min_replicas = None
        max_replicas = None

    deployment_config = _deployment_config(
        replicas=0 if stopped else replicas,
        min_replicas=None if stopped else min_replicas,
        max_replicas=None if stopped else max_replicas,
        target_ongoing_requests=_ray_target_ongoing_requests(spec),
        upscale_delay_s=(scale.autoscale.scale_up_timeout if scale.autoscale else None),
        downscale_delay_s=(scale.autoscale.scale_down_timeout if scale.autoscale else None),
    )

    effective_accelerator = _accelerator_type(accelerator_type)

    placement_group_config = None if stopped else _placement(spec)

    model_loading_config: dict[str, Any] = {
        "model_id": artifact.model_id,
        "model_source": artifact.model_source,
    }

    llm_config: dict[str, Any] = {
        "llm_engine": _RAY_ENGINE_VLLM,
        "model_loading_config": model_loading_config,
        "engine_kwargs": engine_kwargs,
        "deployment_config": deployment_config,
    }

    # runtime_env: the spec (user-controlled) may only contribute allow-listed
    # env vars; ``runtime_env`` passed by the backend itself is trusted.
    effective_runtime_env: dict[str, Any] = dict(runtime_env or {})
    spec_env_vars = _extension_env_vars(spec)
    if spec_env_vars:
        env_vars = dict(effective_runtime_env.get("env_vars") or {})
        env_vars.update(spec_env_vars)
        effective_runtime_env["env_vars"] = env_vars
    if effective_runtime_env:
        llm_config["runtime_env"] = effective_runtime_env

    # accelerator_type only when Ray will accept it (None is a valid omission).
    if effective_accelerator is not None:
        llm_config["accelerator_type"] = effective_accelerator
    if placement_group_config is not None:
        llm_config["placement_group_config"] = placement_group_config

    return {
        "llm_configs": [llm_config],
        "ingress_cls_config": {},  # default: ray.serve.llm.OpenAiIngress
    }


def compile_deployment(
    *,
    spec_data: dict[str, Any],
    model_display_name: str,
    model_source: str,
    hf_repo_id: str | None = None,
    hf_revision: str | None = None,
    availability_root_path: str | None = None,
    capabilities: dict[str, Any] | None = None,
    desired_state: str = "active",
    runtime_env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """End-to-end, testable compiler entrypoint.

    Validates *spec_data* through :func:`parse_inference_deployment_spec`,
    resolves the artifact, and returns the ``LLMServingArgs`` document.  This is
    the single function the orchestrator calls — and the one golden-fixture
    tests target.  ``capabilities`` is accepted for API stability (gating
    advanced features is Phase 4+); the deployment accelerator (if any) is read
    from the spec's ``resources.replica.accelerator``.
    """
    spec = parse_inference_deployment_spec(spec_data)
    effective_revision = spec.artifacts.revision or hf_revision
    artifact = resolve_artifact(
        spec=spec,
        model_display_name=model_display_name,
        model_source=model_source,
        hf_repo_id=hf_repo_id,
        hf_revision=effective_revision,
        availability_root_path=availability_root_path,
    )
    accelerator = (spec.resources.replica.accelerator or None)
    return compile_spec(
        spec=spec,
        artifact=artifact,
        desired_state=desired_state,
        accelerator_type=accelerator,
        revision=effective_revision,
        runtime_env=runtime_env,
    )


# Convenience re-export for callers that only need the whitelist.
__all__ = [
    "ArtifactResolution",
    "ArtifactResolutionError",
    "DeploymentValidationError",
    "_RAY_ACCELERATOR_WHITELIST",
    "compile_deployment",
    "compile_spec",
    "resolve_artifact",
    "validate_spec",
]
