"""What a model is, read from what the Hugging Face Hub says about it.

Pure: every function takes plain values (the Hub's model info fields, a
``config.json`` dict) and returns plain values, so the rules are testable
without the network.

Two rules carried through everything here:

* **A number nobody could learn is ``None``, never zero.** A repo without
  safetensors metadata has an unknown size, not a size of 0 -- and 0 would
  read as "fits anywhere".
* **Guesses are labelled.** Capabilities and quantization inferred from a name
  or tag are best effort; the architecture facts from ``config.json`` are what
  the memory estimate is built on.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

#: Bytes per element for the dtypes the Hub reports in safetensors metadata.
#: Packed 4-bit formats (AWQ/GPTQ/NVFP4) store their weights as I32/U8
#: elements, so counting those elements at their storage width gives the real
#: size on disk, which is what occupies accelerator memory.
DTYPE_BYTES: dict[str, int] = {
    "F64": 8, "I64": 8, "U64": 8,
    "F32": 4, "I32": 4, "U32": 4,
    "BF16": 2, "F16": 2, "I16": 2, "U16": 2,
    "F8_E4M3": 1, "F8_E5M2": 1, "F8_E8M0": 1, "I8": 1, "U8": 1, "BOOL": 1,
}

#: The dtype a model computes in, by safetensors name.
_DTYPE_NAMES = {"BF16": "bfloat16", "F16": "float16", "F32": "float32", "F8_E4M3": "fp8", "F8_E5M2": "fp8"}

_QUANT_PATTERNS: list[tuple[str, str]] = [
    (r"nvfp4", "nvfp4"),
    (r"mxfp4", "mxfp4"),
    (r"(?<![a-z])awq(?![a-z])", "awq"),
    (r"gptq", "gptq"),
    (r"(?<![a-z])fp8(?![a-z])|w8a8-fp8|f8_e4m3", "fp8"),
    (r"(?<![a-z])int4(?![a-z])|w4a16", "int4"),
    (r"(?<![a-z])int8(?![a-z])|w8a8", "int8"),
    (r"bnb|bitsandbytes|4bit|8bit", "bitsandbytes"),
    (r"(?<![a-z])gguf(?![a-z])|q4_k_m|q8_0", "gguf"),
]

_PARAMS_IN_NAME = re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])")
_ACTIVE_IN_NAME = re.compile(r"-A(\d+(?:\.\d+)?)B", re.IGNORECASE)


def weights_bytes(safetensors_parameters: dict[str, int] | None) -> int | None:
    """Bytes the weights take, from the Hub's dtype -> element count breakdown."""
    if not safetensors_parameters:
        return None
    total = 0
    for dtype, count in safetensors_parameters.items():
        width = DTYPE_BYTES.get(str(dtype).upper())
        if width is None:
            return None  # a dtype we cannot size: unknown, not a guess
        total += int(count) * width
    return total or None


def compute_dtype(safetensors_parameters: dict[str, int] | None) -> str | None:
    """The dtype holding most of the weights, as vLLM names it."""
    if not safetensors_parameters:
        return None
    dominant = max(safetensors_parameters.items(), key=lambda kv: kv[1])[0].upper()
    return _DTYPE_NAMES.get(dominant)


def params_from_name(name: str) -> float | None:
    """``Qwen3-8B`` -> 8.0 (billions). ``None`` when the name does not say."""
    matches = [float(m.group(1)) for m in _PARAMS_IN_NAME.finditer(name.split("/")[-1])]
    # "Qwen3-30B-A3B": the total is the largest number, the active one is marked with A.
    return max(matches) if matches else None


def active_params_from_name(name: str) -> float | None:
    """``Qwen3-30B-A3B`` -> 3.0: the parameters a mixture-of-experts reads per token."""
    match = _ACTIVE_IN_NAME.search(name)
    return float(match.group(1)) if match else None


def detect_quantization(name: str, tags: list[str], config: dict[str, Any] | None,
                        safetensors_parameters: dict[str, int] | None) -> str | None:
    """The quantization a checkpoint carries, if any (``None`` for full precision)."""
    quant_config = (config or {}).get("quantization_config") or {}
    method = str(quant_config.get("quant_method") or "").lower()
    if method:
        if method in {"compressed-tensors", "modelopt"}:
            fmt = str(quant_config.get("format") or quant_config.get("quant_algo") or "").lower()
            if "fp4" in fmt:
                return "nvfp4"
            if "fp8" in fmt or "float" in fmt:
                return "fp8"
            return "int4" if "pack" in fmt or "int4" in fmt else method
        return method
    haystack = " ".join([name.lower(), *[t.lower() for t in tags]])
    for pattern, label in _QUANT_PATTERNS:
        if re.search(pattern, haystack):
            return label
    dtypes = {str(d).upper() for d in (safetensors_parameters or {})}
    if dtypes & {"F8_E4M3", "F8_E5M2"}:
        return "fp8"
    return None


def detect_format(tags: list[str], library: str | None, has_safetensors: bool, siblings: list[str]) -> str:
    """``safetensors`` / ``gguf`` / ``mlx`` / ``pytorch`` / ``other``."""
    lowered = {t.lower() for t in tags}
    names = [s.lower() for s in siblings]
    if "gguf" in lowered or any(n.endswith(".gguf") for n in names):
        return "gguf"
    if "mlx" in lowered or (library or "").lower() == "mlx":
        return "mlx"
    if has_safetensors or any(n.endswith(".safetensors") for n in names):
        return "safetensors"
    if any(n.endswith(".bin") and "pytorch_model" in n for n in names):
        return "pytorch"
    return "other"


def detect_capabilities(*, repo_id: str, pipeline_tag: str | None, tags: list[str],
                        architectures: list[str], chat_template: str | None) -> list[str]:
    """What the model does beyond plain chat: tools, vision, reasoning, code, embeddings.

    Inferred, so best effort -- the detail view says so. ``tools`` is the one
    read from the model itself: a chat template that renders tool definitions.
    """
    name = repo_id.lower()
    lowered = {t.lower() for t in tags}
    arch = " ".join(a.lower() for a in architectures)
    caps: list[str] = []
    if pipeline_tag in {"feature-extraction", "sentence-similarity"} or "sentence-transformers" in lowered \
            or re.search(r"embed|(?<![a-z])bge-|(?<![a-z])e5-|gte-", name):
        caps.append("embedding")
    if pipeline_tag in {"image-text-to-text", "visual-question-answering", "image-to-text"} \
            or re.search(r"vl(?![a-z])|vision|llava|pixtral|forconditionalgeneration", arch + " " + name):
        caps.append("vision")
    if chat_template and re.search(r"\btools\b|tool_call", chat_template):
        caps.append("tools")
    if re.search(r"think|reason|(?<![a-z])r1(?![a-z0-9])|qwq|gpt-oss|magistral", name) \
            or ("qwen3" in name and "instruct-2507" not in name and "embedding" not in name
                and "coder" not in name and "vl" not in name):
        caps.append("reasoning")
    if re.search(r"coder|codestral|devstral|starcoder|codellama|(?<![a-z])code(?![a-z])", name):
        caps.append("code")
    if "embedding" in caps:
        # An embedding model answers /v1/embeddings and nothing else: whatever
        # its template mentions, it neither calls tools nor reasons.
        return ["embedding"]
    return caps


def task_of(capabilities: list[str], pipeline_tag: str | None) -> str:
    """``embedding`` / ``vision`` / ``chat`` / ``other`` -- what a card is filed under."""
    if "embedding" in capabilities:
        return "embedding"
    if "vision" in capabilities:
        return "vision"
    if pipeline_tag in {"text-generation", "text2text-generation", "conversational", None, ""}:
        return "chat"
    return "other"


@dataclass
class Architecture:
    """The shape of the network, from ``config.json``: what the memory estimate needs."""

    model_type: str | None = None
    architectures: list[str] = field(default_factory=list)
    num_layers: int | None = None
    hidden_size: int | None = None
    num_attention_heads: int | None = None
    num_kv_heads: int | None = None
    head_dim: int | None = None
    #: The context the model was trained for (``max_position_embeddings``).
    max_context: int | None = None
    #: True when loading needs the repository's own Python (``auto_map``).
    needs_remote_code: bool = False
    #: True for mixture-of-experts models.
    moe: bool = False

    def kv_bytes_per_token(self, dtype_bytes: int = 2) -> int | None:
        """KV cache bytes one token of context occupies (keys and values, every layer)."""
        if not (self.num_layers and self.num_kv_heads and self.head_dim):
            return None
        return 2 * self.num_layers * self.num_kv_heads * self.head_dim * dtype_bytes

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def architecture_from_config(config: dict[str, Any] | None) -> Architecture:
    """Read the architecture facts out of ``config.json``.

    Vision-language models keep the language model's shape under
    ``text_config``; that is the part the KV cache belongs to.
    """
    if not config:
        return Architecture()
    text = config.get("text_config") if isinstance(config.get("text_config"), dict) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            for source in (text, config):
                if source.get(key) is not None:
                    return source[key]
        return None

    heads = pick("num_attention_heads", "n_head", "num_heads")
    hidden = pick("hidden_size", "d_model", "n_embd")
    head_dim = pick("head_dim")
    if head_dim is None and heads and hidden:
        head_dim = int(hidden) // int(heads)
    kv_heads = pick("num_key_value_heads", "num_kv_heads", "multi_query_group_num") or heads
    return Architecture(
        model_type=config.get("model_type"),
        architectures=list(config.get("architectures") or []),
        num_layers=_int(pick("num_hidden_layers", "n_layer", "num_layers")),
        hidden_size=_int(hidden),
        num_attention_heads=_int(heads),
        num_kv_heads=_int(kv_heads),
        head_dim=_int(head_dim),
        max_context=_int(pick("max_position_embeddings", "n_positions", "max_sequence_length", "seq_length")),
        needs_remote_code=bool(config.get("auto_map")),
        moe=bool(pick("num_experts", "num_local_experts", "n_routed_experts")),
    )


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def vllm_runnable(fmt: str, task: str) -> tuple[bool, str | None]:
    """Whether vLLM on a cluster can serve this checkpoint, and if not, why (a code)."""
    if fmt == "gguf":
        return False, "gguf"
    if fmt == "mlx":
        return False, "mlx"
    if fmt == "other":
        return False, "no_weights"
    if task == "other":
        return False, "task"
    return True, None
