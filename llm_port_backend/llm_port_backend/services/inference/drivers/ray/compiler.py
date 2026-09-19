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
    resources.replica   -> ``placement_group_config`` (explicit bundles)
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
    target_utilization: float | None,
) -> dict[str, Any]:
    """Build ``LLMConfig.deployment_config`` for a resolved replica count.

    * ACTIVE with fixed replicas → ``num_replicas``.
    * ACTIVE with autoscaling bounds → ``autoscaling_config`` (min/max); the
      target utilization is mapped to the engine metric vLLM understands best
      (``target_ongoing_requests`` is left to Ray's engine default since
      ``target_utilization`` is a fraction, not a request count — emitting a
      fractional ``target_ongoing_requests`` would be meaningless to Serve).
    * STOPPED → ``num_replicas: 0`` (scale to zero, no live workers).

    ``num_replicas`` and ``autoscaling_config`` are mutually exclusive in Ray's
    ``DeploymentConfig``; exactly one path is taken here.
    """
    config: dict[str, Any] = {}
    if replicas <= 0:
        # Scale to zero.  Serve accepts num_replicas=0 (no live replicas).
        config["num_replicas"] = 0
        return config

    if max_replicas is not None:
        autoscaling: dict[str, Any] = {
            "min_replicas": int(min_replicas if min_replicas is not None else 1),
            "max_replicas": int(max_replicas),
        }
        config["autoscaling_config"] = autoscaling
        # ``num_replicas`` is the *initial* replica count for an autoscaled
        # deployment; anchor it at the min so we start at the lower bound.
        config["num_replicas"] = int(min_replicas if min_replicas is not None else 1)
    else:
        config["num_replicas"] = int(replicas)
    return config


def _engine_kwargs(
    *,
    engine_config: dict[str, Any],
    tensor_parallel_size: int | None,
    pipeline_parallel_size: int | None,
) -> dict[str, Any]:
    """Merge spec engine kwargs with topology-derived TP/PP.

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
    return kwargs


def _placement_group_config(
    *,
    gpus: float | None,
    cpu: float | None,
    accelerator_type: str | None,
) -> dict[str, Any] | None:
    """Build an explicit ``placement_group_config`` honouring the resource spec.

    vLLM will generate its own placement-group bundles from TP/PP when none is
    supplied (one ``GPU:1`` bundle per device).  We only emit an *explicit*
    bundle list when the operator asked for a non-default per-replica shape —
    a fractional or >1 GPU-per-bundle request — because those cannot be
    inferred from TP/PP alone.  ``cpu`` is applied as the per-bundle CPU
    request.  ``memory`` is intentionally not modelled (Ray bundles use GB of
    object-store/CPU, not the container-memory string the spec carries) and is
    a later-phase concern.

    Uses the ``bundles`` (not ``bundle_per_worker``) form: ``bundle_per_worker``
    is auto-replicated by ``tp*pp`` to GPU:1 per device, which would mis-size a
    fractional-GPU request; an explicit list keeps the request exactly as asked
    for.  Note that a single GPU bundle (the common "1 GPU per replica, TP=1"
    case) is *omitted* and left to vLLM's default generation, which is the
    exact same shape.
    """
    gpus = float(gpus) if gpus is not None else None
    cpu = float(cpu) if cpu is not None else None

    need_explicit = (
        (gpus is not None and not (gpus > 0 and gpus == int(gpus) and int(gpus) == 1))
    )
    # CPU-only or explicit-CPU requests also need a bundle so the CPU count is
    # honoured (the default GPU bundle carries no CPU).
    need_explicit = need_explicit or (cpu is not None and gpus is None)

    if not need_explicit:
        return None

    bundle: dict[str, float] = {}
    if gpus is not None and gpus > 0:
        bundle["GPU"] = gpus
    if cpu is not None:
        bundle["CPU"] = cpu
    if accelerator_type is not None:
        # Hint so the accelerator resource is reserved alongside the GPU.
        bundle[f"accelerator_type:{accelerator_type}"] = 1
    if not bundle:
        return None
    return {"bundles": [bundle], "strategy": "STRICT_PACK"}


def compile_spec(
    *,
    spec: InferenceDeploymentSpecV1Alpha1,
    artifact: ArtifactResolution,
    desired_state: str = "active",
    accelerator_type: str | None = None,
) -> dict[str, Any]:
    """Compile one resolved deployment into a Ray ``LLMServingArgs`` document.

    Args:
        spec: the validated deployment spec.
        artifact: the resolved artifact (``model_id`` / ``model_source``).
        desired_state: ``"active"`` or ``"stopped"`` (affects replica count).
        accelerator_type: resolved accelerator name *before* the Ray whitelist
            mapping (the caller passes the raw spec value; this function maps).

    Returns:
        A plain dictionary shaped like ``ray.serve.llm.LLMServingArgs`` with a
        single ``llm_configs`` entry.  It carries only JSON-safe values and no
        Ray types, so it can be embedded in a control-plane command payload.
    """
    engine = spec.engine
    if (engine.name or "").strip().lower() not in _SUPPORTED_ENGINE_NAMES:
        raise ValueError(f"unsupported engine for Ray: {engine.name!r}")

    topology = spec.topology
    tp = topology.tensor_parallel_size
    pp = topology.pipeline_parallel_size

    # Resolve TP/PP for both the engine kwargs and the (default) bundle
    # inference.  vLLM treats a missing value as 1.
    tp_int = int(tp) if tp else 1
    pp_int = int(pp) if pp else 1

    engine_kwargs = _engine_kwargs(
        engine_config=engine.config,
        tensor_parallel_size=tp,
        pipeline_parallel_size=pp,
    )

    # Determine the active replica representation.
    stopped = (desired_state or "active").strip().lower() == "stopped"
    scale = spec.scale
    if scale.autoscale is not None:
        replicas = int(scale.autoscale.min_replicas)
        min_replicas = int(scale.autoscale.min_replicas)
        max_replicas = int(scale.autoscale.max_replicas)
    else:
        replicas = int(scale.replicas or 1)
        min_replicas = None
        max_replicas = None

    deployment_config = _deployment_config(
        replicas=0 if stopped else replicas,
        min_replicas=None if stopped else min_replicas,
        max_replicas=None if stopped else max_replicas,
        target_utilization=(scale.autoscale.target_utilization if scale.autoscale else None),
    )

    effective_accelerator = _accelerator_type(accelerator_type)

    resources = spec.resources.replica
    placement_group_config = (
        None
        if stopped
        else _placement_group_config(
            gpus=resources.gpus,
            cpu=resources.cpu,
            accelerator_type=effective_accelerator,
        )
    )

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
    artifact = resolve_artifact(
        spec=spec,
        model_display_name=model_display_name,
        model_source=model_source,
        hf_repo_id=hf_repo_id,
        hf_revision=hf_revision,
        availability_root_path=availability_root_path,
    )
    accelerator = (spec.resources.replica.accelerator or None)
    return compile_spec(
        spec=spec,
        artifact=artifact,
        desired_state=desired_state,
        accelerator_type=accelerator,
    )


# Convenience re-export for callers that only need the whitelist.
__all__ = [
    "ArtifactResolution",
    "ArtifactResolutionError",
    "_RAY_ACCELERATOR_WHITELIST",
    "compile_deployment",
    "compile_spec",
    "resolve_artifact",
]
