/**
 * vLLM engine-argument definitions — the "parameter catalogue".
 *
 * Each entry maps 1-to-1 to a vLLM CLI flag documented at:
 *   v0.7.3  https://docs.vllm.ai/en/v0.7.3/serving/engine_args.html
 *   v0.6.6  https://docs.vllm.ai/en/v0.6.6/usage/engine_args.html
 *
 * Only a curated subset of ~40 most-useful flags is included.
 * Users can still pass unlisted flags via the "Extra Arguments" field.
 *
 * Maintenance:  when a new vLLM version ships, add/update entries
 * here and bump the `minVersion` / `maxVersion` markers.
 */
import type { VllmEngineArgDef, VllmCategory } from "./types";

// ── Categories (display order) ──────────────────────────────────────

export const VLLM_CATEGORIES: VllmCategory[] = [
  { id: "model", label: "Model & Tokenizer" },
  { id: "compute", label: "Memory & Compute" },
  { id: "parallel", label: "Parallelism" },
  { id: "scheduling", label: "Scheduling" },
  { id: "quantization", label: "Quantization" },
  { id: "lora", label: "LoRA Adapters" },
  { id: "serving", label: "Serving & Output" },
  { id: "advanced", label: "Advanced" },
];

// ── Argument definitions ────────────────────────────────────────────

export const VLLM_ENGINE_ARGS: VllmEngineArgDef[] = [
  // ─── Model & Tokenizer ────────────────────────────────────────
  {
    flag: "task",
    type: "enum",
    default: "auto",
    choices: [
      "auto",
      "generate",
      "embedding",
      "embed",
      "classify",
      "score",
      "reward",
      "transcription",
    ],
    category: "model",
    description:
      "The task to use the model for. Each vLLM instance only supports one task.",
  },
  {
    flag: "trust-remote-code",
    type: "boolean",
    default: false,
    category: "model",
    description:
      "Trust remote code from HuggingFace. Required for some models (e.g. Jina, Qwen).",
  },
  {
    flag: "tokenizer-mode",
    type: "enum",
    default: "auto",
    choices: ["auto", "slow", "mistral", "custom"],
    category: "model",
    description:
      '"auto" uses the fast tokenizer if available; "slow" always uses the slow tokenizer; "custom" uses a preregistered tokenizer.',
  },
  {
    flag: "revision",
    type: "string",
    category: "model",
    description:
      "Model version to use (branch name, tag, or commit id). Defaults to the latest.",
  },
  {
    flag: "load-format",
    type: "enum",
    default: "auto",
    choices: [
      "auto",
      "pt",
      "safetensors",
      "npcache",
      "dummy",
      "tensorizer",
      "sharded_state",
      "gguf",
      "bitsandbytes",
      "mistral",
      "runai_streamer",
    ],
    category: "model",
    description:
      'Format of the model weights to load. "auto" tries safetensors, then pytorch.',
  },
  {
    flag: "config-format",
    type: "enum",
    default: "auto",
    choices: ["auto", "hf", "mistral"],
    category: "model",
    description: "Format of the model config to load.",
  },

  // ─── Memory & Compute ─────────────────────────────────────────
  {
    flag: "dtype",
    type: "enum",
    default: "auto",
    choices: ["auto", "half", "float16", "bfloat16", "float", "float32"],
    category: "compute",
    description:
      'Data type for weights and activations. "auto" uses FP16 for FP32/FP16 models and BF16 for BF16 models.',
  },
  {
    flag: "kv-cache-dtype",
    type: "enum",
    default: "auto",
    choices: ["auto", "fp8", "fp8_e5m2", "fp8_e4m3"],
    category: "compute",
    description:
      'KV cache data type. "auto" uses the model data type. CUDA 11.8+ supports fp8.',
  },
  {
    flag: "max-model-len",
    type: "number",
    category: "compute",
    description:
      "Model context length. If unspecified, derived from the model config automatically.",
    min: 1,
    step: 256,
  },
  {
    flag: "gpu-memory-utilization",
    type: "number",
    default: 0.9,
    category: "compute",
    description:
      "Fraction of GPU memory for the model executor (0.0–1.0). Per-instance limit.",
    min: 0.05,
    max: 1.0,
    step: 0.05,
  },
  {
    flag: "swap-space",
    type: "number",
    default: 4,
    category: "compute",
    description: "CPU swap space size (GiB) per GPU.",
    min: 0,
    step: 1,
  },
  {
    flag: "cpu-offload-gb",
    type: "number",
    default: 0,
    category: "compute",
    description:
      "Space in GiB to offload to CPU per GPU. Virtually increases GPU memory. Requires fast CPU-GPU interconnect.",
    min: 0,
    step: 1,
  },
  {
    flag: "enforce-eager",
    type: "boolean",
    default: false,
    category: "compute",
    description:
      "Always use eager-mode PyTorch. Required for GPUs with compute capability < 8.0 (Turing, Volta).",
  },
  {
    flag: "max-seq-len-to-capture",
    type: "number",
    default: 8192,
    category: "compute",
    description:
      "Maximum sequence length covered by CUDA graphs. Longer sequences fall back to eager mode.",
    min: 1,
    step: 256,
  },
  {
    flag: "enable-prefix-caching",
    type: "boolean",
    default: false,
    category: "compute",
    description:
      "Enable automatic prefix caching for repeated prompt prefixes.",
  },
  {
    flag: "block-size",
    type: "enum",
    choices: ["8", "16", "32", "64", "128"],
    category: "compute",
    description:
      "Token block size for contiguous chunks. On CUDA, only up to 32 is supported.",
  },

  // ─── Parallelism ──────────────────────────────────────────────
  {
    flag: "tensor-parallel-size",
    type: "number",
    default: 1,
    category: "parallel",
    description:
      "Number of tensor parallel replicas. Set to the number of GPUs to use.",
    min: 1,
    step: 1,
  },
  {
    flag: "pipeline-parallel-size",
    type: "number",
    default: 1,
    category: "parallel",
    description: "Number of pipeline stages.",
    min: 1,
    step: 1,
  },
  {
    flag: "distributed-executor-backend",
    type: "enum",
    choices: ["ray", "mp", "uni", "external_launcher"],
    category: "parallel",
    description:
      'Backend for distributed model workers. "mp" for single host, "ray" for multi-node.',
  },
  {
    flag: "max-parallel-loading-workers",
    type: "number",
    category: "parallel",
    description:
      "Load model sequentially in multiple batches to avoid RAM OOM with tensor parallel.",
    min: 1,
    step: 1,
  },

  // ─── Scheduling ───────────────────────────────────────────────
  {
    flag: "max-num-batched-tokens",
    type: "number",
    category: "scheduling",
    description: "Maximum number of batched tokens per iteration.",
    min: 1,
    step: 64,
  },
  {
    flag: "max-num-seqs",
    type: "number",
    category: "scheduling",
    description: "Maximum number of sequences per iteration.",
    min: 1,
    step: 1,
  },
  {
    flag: "num-scheduler-steps",
    type: "number",
    default: 1,
    category: "scheduling",
    description: "Maximum number of forward steps per scheduler call.",
    min: 1,
    step: 1,
  },
  {
    flag: "scheduler-delay-factor",
    type: "number",
    default: 0.0,
    category: "scheduling",
    description:
      "Delay factor multiplied by previous prompt latency before scheduling next prompt.",
    min: 0,
    step: 0.1,
  },
  {
    flag: "enable-chunked-prefill",
    type: "boolean",
    default: false,
    category: "scheduling",
    description:
      "Allow prefill requests to be chunked based on max_num_batched_tokens.",
  },
  {
    flag: "scheduling-policy",
    type: "enum",
    default: "fcfs",
    choices: ["fcfs", "priority"],
    category: "scheduling",
    description:
      '"fcfs" = first come first served; "priority" = requests handled by given priority.',
  },

  // ─── Quantization ────────────────────────────────────────────
  {
    flag: "quantization",
    type: "enum",
    choices: [
      "aqlm",
      "awq",
      "deepspeedfp",
      "tpu_int8",
      "fp8",
      "ptpc_fp8",
      "fbgemm_fp8",
      "modelopt",
      "marlin",
      "gguf",
      "gptq_marlin_24",
      "gptq_marlin",
      "awq_marlin",
      "gptq",
      "compressed-tensors",
      "bitsandbytes",
      "qqq",
      "hqq",
      "experts_int8",
      "neuron_quant",
      "ipex",
      "quark",
      "moe_wna16",
    ],
    category: "quantization",
    description:
      "Method used to quantize weights. Auto-detected from model config if not set.",
  },
  {
    flag: "rope-scaling",
    type: "string",
    category: "quantization",
    description:
      'RoPE scaling configuration in JSON format. e.g. {"rope_type":"dynamic","factor":2.0}',
  },
  {
    flag: "rope-theta",
    type: "number",
    category: "quantization",
    description:
      "RoPE theta value. Use with rope-scaling to improve performance of scaled models.",
    min: 0,
    step: 1000,
  },

  // ─── LoRA ─────────────────────────────────────────────────────
  {
    flag: "enable-lora",
    type: "boolean",
    default: false,
    category: "lora",
    description: "Enable handling of LoRA adapters.",
  },
  {
    flag: "max-loras",
    type: "number",
    default: 1,
    category: "lora",
    description: "Max number of LoRAs in a single batch.",
    min: 1,
    step: 1,
  },
  {
    flag: "max-lora-rank",
    type: "number",
    default: 16,
    category: "lora",
    description: "Max LoRA rank.",
    min: 1,
    step: 8,
  },
  {
    flag: "lora-extra-vocab-size",
    type: "number",
    default: 256,
    category: "lora",
    description:
      "Maximum extra vocabulary size that can be present in a LoRA adapter.",
    min: 0,
    step: 64,
  },
  {
    flag: "lora-dtype",
    type: "enum",
    default: "auto",
    choices: ["auto", "float16", "bfloat16"],
    category: "lora",
    description: "Data type for LoRA. If auto, defaults to base model dtype.",
  },

  // ─── Serving & Output ─────────────────────────────────────────
  {
    flag: "device",
    type: "enum",
    default: "auto",
    choices: ["auto", "cuda", "neuron", "cpu", "openvino", "tpu", "xpu", "hpu"],
    category: "serving",
    description: "Device type for vLLM execution.",
  },
  {
    flag: "served-model-name",
    type: "string",
    category: "serving",
    description:
      "Model name(s) used in the API. If not specified, the model name from --model is used.",
  },
  {
    flag: "guided-decoding-backend",
    type: "enum",
    default: "xgrammar",
    choices: ["outlines", "lm-format-enforcer", "xgrammar"],
    category: "serving",
    description:
      "Engine for guided decoding (JSON schema / regex). Can be overridden per request.",
  },
  {
    flag: "disable-log-stats",
    type: "boolean",
    default: false,
    category: "serving",
    description: "Disable logging statistics.",
  },
  {
    flag: "disable-log-requests",
    type: "boolean",
    default: false,
    category: "serving",
    description: "Disable logging individual requests.",
  },
  {
    flag: "seed",
    type: "number",
    default: 0,
    category: "serving",
    description: "Random seed for operations.",
    min: 0,
    step: 1,
  },

  // ─── Advanced ─────────────────────────────────────────────────
  {
    flag: "compilation-config",
    type: "string",
    category: "advanced",
    description:
      "torch.compile optimisation level (0-3) or full JSON config. Level 3 recommended for production.",
  },
  {
    flag: "hf-overrides",
    type: "string",
    category: "advanced",
    description:
      "Extra arguments for the HuggingFace config, as a JSON string.",
  },
  {
    flag: "preemption-mode",
    type: "enum",
    choices: ["recompute", "swap"],
    category: "advanced",
    description:
      '"recompute" preempts by recomputing; "swap" preempts by block swapping.',
  },
  {
    flag: "model-impl",
    type: "enum",
    default: "auto",
    choices: ["auto", "vllm", "transformers"],
    category: "advanced",
    minVersion: "0.7.0",
    description:
      'Model implementation to use. "auto" prefers vLLM, falls back to Transformers.',
  },
  {
    flag: "enable-sleep-mode",
    type: "boolean",
    default: false,
    category: "advanced",
    minVersion: "0.7.0",
    description: "Enable sleep mode for the engine (CUDA only).",
  },
  {
    flag: "override-pooler-config",
    type: "string",
    category: "advanced",
    description:
      'Override pooling method for pooling models. JSON, e.g. {"pooling_type":"mean","normalize":false}.',
  },
];

// ── Utility ─────────────────────────────────────────────────────────

/**
 * Simple semver-ish comparison for the vLLM version strings we use
 * (only major.minor.patch with single-digit components).
 */
function compareVersions(a: string, b: string): number {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const va = pa[i] ?? 0;
    const vb = pb[i] ?? 0;
    if (va !== vb) return va - vb;
  }
  return 0;
}

/** Filter arg definitions by the currently selected vLLM version. */
export function filterArgsByVersion(
  args: VllmEngineArgDef[],
  version: string,
): VllmEngineArgDef[] {
  return args.filter((a) => {
    if (a.minVersion && compareVersions(version, a.minVersion) < 0)
      return false;
    if (a.maxVersion && compareVersions(version, a.maxVersion) > 0)
      return false;
    return true;
  });
}
