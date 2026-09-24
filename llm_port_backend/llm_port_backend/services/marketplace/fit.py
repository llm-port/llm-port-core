"""Will a model run on a cluster, and how -- asked before anything is downloaded.

The estimate is the one vLLM itself has to satisfy at start-up: on every
accelerator of a copy,

    weights / TP  +  KV cache for the context / TP  +  runtime overhead
        <=  gpu_memory_utilization x the accelerator's memory

where TP is the tensor-parallel size (how many accelerators one copy spans).
The smallest TP that satisfies it is the plan; a TP larger than the cluster has
accelerators means the model is too large for it.

It is an estimate, and says so: the overhead is a flat allowance for the CUDA
context, activations and graphs, and the KV cache is computed for full
attention (sliding-window and latent-attention models need less). It errs on
the side of "needs more", never on "fits".

Two numbers are kept apart on purpose:

* **fits** -- the cluster's accelerators are big enough, when they are free;
* **fits now** -- enough of them are free at this moment. A GPU already
  holding another model is capacity the operator has, but not today.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

GIB = 1024**3

#: Per-accelerator allowance for what is not weights or KV cache: the CUDA
#: context, activation buffers and captured graphs. vLLM's own profiling run
#: measures this; a flat allowance keeps the estimate on the safe side.
OVERHEAD_BYTES = int(1.5 * GIB)

#: The most of an accelerator's memory a single engine is planned to use.
#: Above this, drivers and anything else on the card have nowhere to go.
MAX_UTILIZATION = 0.90

#: The least worth suggesting: below this the KV cache is too small to serve
#: more than a request or two.
MIN_UTILIZATION = 0.10

#: The context the fit is judged at when the operator has not chosen one: long
#: enough for real conversations, short enough that every model is judged on
#: its weights rather than on a 128K window nobody asked for.
DEFAULT_CONTEXT = 8192

#: Tensor-parallel sizes vLLM supports well.
TP_SIZES = (1, 2, 4, 8, 16)


@dataclass
class Gpu:
    name: str
    total_bytes: int
    #: Free right now, as the machine last reported; ``None`` when not reported.
    free_bytes: int | None = None


@dataclass
class Machine:
    node_id: str
    name: str
    gpus: list[Gpu] = field(default_factory=list)


@dataclass
class ClusterHardware:
    environment_id: str
    name: str
    status: str
    machines: list[Machine] = field(default_factory=list)
    #: vLLM version the cluster's runtime carries, when known.
    vllm_version: str | None = None

    @property
    def gpus(self) -> list[Gpu]:
        return [gpu for machine in self.machines for gpu in machine.gpus]

    @property
    def accelerator_name(self) -> str | None:
        gpus = self.gpus
        return gpus[0].name if gpus else None

    def to_dict(self) -> dict[str, Any]:
        gpus = self.gpus
        data = asdict(self)
        data["gpu_count"] = len(gpus)
        data["gpu_bytes"] = min((g.total_bytes for g in gpus), default=None)
        data["accelerator"] = self.accelerator_name
        return data


@dataclass
class ModelNeeds:
    """What serving a model takes, independent of where."""

    weights_bytes: int | None
    kv_bytes_per_token: int | None
    max_context: int | None
    attention_heads: int | None = None
    kv_heads: int | None = None


@dataclass
class Fit:
    """How a model runs on one cluster, or why it does not."""

    #: ``fits`` / ``too_large`` / ``unknown`` / ``no_accelerators``
    status: str
    #: Accelerators one copy spans (tensor parallel), or a fraction of one.
    gpus_per_copy: float | None = None
    tensor_parallel: int | None = None
    #: Copies the cluster holds when its accelerators are free.
    copies: int = 0
    #: Copies it holds with what is free right now.
    copies_now: int = 0
    context: int | None = None
    #: The longest context a copy holds at the planned size (capped at the model's own).
    max_context: int | None = None
    needed_bytes_per_gpu: int | None = None
    gpu_bytes: int | None = None
    weights_bytes: int | None = None
    kv_bytes_per_token: int | None = None
    suggested_gpu_memory_utilization: float | None = None
    #: A copy of a small model can share an accelerator with others.
    shareable: bool = False
    #: Machine-readable reasons, for the interface to put into words.
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round_up(value: float, step: float = 0.05) -> float:
    return round(math.ceil(value / step - 1e-9) * step, 2)


def per_gpu_bytes(needs: ModelNeeds, *, tp: int, context: int, kv_bytes_per_token: int | None) -> int:
    """Memory one accelerator of a TP-way copy needs at *context* tokens."""
    weights = needs.weights_bytes or 0
    kv = (kv_bytes_per_token or 0) * context
    return math.ceil(weights / tp + kv / tp) + OVERHEAD_BYTES


def plan(needs: ModelNeeds, hardware: ClusterHardware, *, context: int | None = None,
         kv_dtype_bytes: int = 2, allow_sharing: bool = True) -> Fit:
    """The smallest shape that runs *needs* on *hardware*, or why none does."""
    gpus = hardware.gpus
    if not gpus:
        return Fit(status="no_accelerators", notes=["no_accelerators"])
    if not needs.weights_bytes:
        return Fit(status="unknown", notes=["size_unknown"])

    notes: list[str] = []
    kv_per_token = needs.kv_bytes_per_token
    if kv_per_token is not None and kv_dtype_bytes != 2:
        kv_per_token = kv_per_token * kv_dtype_bytes // 2
    if kv_per_token is None:
        # No architecture to size the cache from: allow a fifth of the weights
        # for it, as a flat margin, and say the context figure is a guess.
        notes.append("kv_estimated")
    ctx = context or min(needs.max_context or DEFAULT_CONTEXT, DEFAULT_CONTEXT)
    if needs.max_context and ctx > needs.max_context:
        notes.append("context_over_model_max")

    gpu_bytes = min(g.total_bytes for g in gpus)
    budget = MAX_UTILIZATION * gpu_bytes

    def need(tp: int) -> int:
        if kv_per_token is None:
            return math.ceil(needs.weights_bytes * 1.2 / tp) + OVERHEAD_BYTES
        return per_gpu_bytes(needs, tp=tp, context=ctx, kv_bytes_per_token=kv_per_token)

    chosen: int | None = None
    for tp in TP_SIZES:
        if tp > len(gpus):
            break
        if needs.attention_heads and needs.attention_heads % tp:
            continue  # vLLM splits attention heads evenly across the copy
        if need(tp) <= budget:
            chosen = tp
            break
    if chosen is None:
        biggest = max((tp for tp in TP_SIZES if tp <= len(gpus)), default=1)
        return Fit(
            status="too_large",
            needed_bytes_per_gpu=need(biggest),
            gpu_bytes=gpu_bytes,
            weights_bytes=needs.weights_bytes,
            kv_bytes_per_token=kv_per_token,
            context=ctx,
            notes=[*notes, "too_large"],
        )

    needed = need(chosen)
    # Room for more than one conversation: the KV cache for the context plus
    # eight 4K conversations, whichever is larger, within the planned ceiling.
    headroom_tokens = max(ctx, 8 * 4096)
    if kv_per_token is not None:
        wanted = per_gpu_bytes(needs, tp=chosen, context=headroom_tokens, kv_bytes_per_token=kv_per_token)
    else:
        wanted = needed
    utilization = min(MAX_UTILIZATION, max(MIN_UTILIZATION, _round_up(wanted * 1.1 / gpu_bytes)))
    utilization = max(utilization, _round_up(needed / gpu_bytes))
    utilization = min(utilization, MAX_UTILIZATION)

    shareable = allow_sharing and chosen == 1 and utilization <= 0.5
    gpus_per_copy: float = utilization if shareable else float(chosen)
    if shareable:
        copies = sum(int(1 // utilization) for _ in gpus)
        copies_now = sum(
            int((g.free_bytes if g.free_bytes is not None else g.total_bytes) / gpu_bytes // utilization)
            for g in gpus
        )
    else:
        copies = len(gpus) // chosen
        # vLLM refuses to start unless the memory it is told to use is free.
        free_gpus = [g for g in gpus if g.free_bytes is None or g.free_bytes >= utilization * g.total_bytes]
        copies_now = len(free_gpus) // chosen
    if copies_now < 1:
        notes.append("busy_now")

    max_context = None
    if kv_per_token:
        room = MAX_UTILIZATION * gpu_bytes * chosen - needs.weights_bytes - OVERHEAD_BYTES * chosen
        max_context = max(0, int(room // kv_per_token))
        if needs.max_context:
            max_context = min(max_context, needs.max_context)

    return Fit(
        status="fits",
        gpus_per_copy=round(gpus_per_copy, 2),
        tensor_parallel=chosen,
        copies=copies,
        copies_now=copies_now,
        context=ctx,
        max_context=max_context,
        needed_bytes_per_gpu=needed,
        gpu_bytes=gpu_bytes,
        weights_bytes=needs.weights_bytes,
        kv_bytes_per_token=kv_per_token,
        suggested_gpu_memory_utilization=utilization,
        shareable=shareable,
        notes=notes,
    )


def quick_needs(weights: int | None) -> ModelNeeds:
    """Needs from the weights alone, for a list where no config has been read."""
    return ModelNeeds(weights_bytes=weights, kv_bytes_per_token=None, max_context=None)
