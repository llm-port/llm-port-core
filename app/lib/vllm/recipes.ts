/**
 * Built-in vLLM engine-arg recipes — pre-configured parameter sets
 * for common model types and deployment scenarios.
 *
 * Design for modularity:
 *   • This file can be replaced with a fetch() to a remote recipe
 *     repository (e.g. a public GitHub JSON file) in the future.
 *   • Each recipe only lists non-default values; the registry supplies
 *     defaults for everything else.
 *   • `modelPattern` is a regex matched against the model's display_name
 *     so the UI can auto-suggest a recipe when the user picks a model.
 */
import type { VllmRecipe } from "./types";

export const VLLM_RECIPES: VllmRecipe[] = [
  {
    id: "embedding",
    name: "Embedding Model",
    description:
      "Optimised for embedding/reranker models (Jina, BGE, E5, etc.). " +
      "Low memory footprint, short context.",
    modelPattern: "embed|jina|bge|e5|gte|instructor|nomic",
    args: {
      task: "embed",
      "trust-remote-code": true,
      "max-model-len": 8192,
      "gpu-memory-utilization": 0.3,
      dtype: "float16",
    },
  },
  {
    id: "chat-small",
    name: "Chat Model (≤ 13B)",
    description:
      "Good defaults for small chat/instruct models — Llama 7B/8B, Mistral 7B, Phi, Gemma 2B/7B.",
    modelPattern: "7b|8b|3b|2b|1b|phi|gemma.*2b|gemma.*7b|mistral.*7b|qwen.*7b",
    args: {
      "gpu-memory-utilization": 0.9,
      "enable-prefix-caching": true,
    },
  },
  {
    id: "chat-large",
    name: "Chat Model (≥ 30B, multi-GPU)",
    description:
      "Multi-GPU configuration for large models — Llama 70B, Mixtral 8×7B, Qwen 72B.",
    modelPattern: "70b|72b|65b|34b|mixtral|deepseek.*67b",
    args: {
      "tensor-parallel-size": 2,
      "gpu-memory-utilization": 0.95,
      "enable-prefix-caching": true,
    },
  },
  {
    id: "legacy-gpu",
    name: "Legacy GPU (Turing/Volta)",
    description:
      "For GPUs with compute capability < 8.0 (GTX 10/20 series, TITAN RTX, V100). " +
      "Uses eager mode and XFormers backend.",
    args: {
      "enforce-eager": true,
      dtype: "float16",
      "swap-space": 1,
    },
  },
  {
    id: "cpu-only",
    name: "CPU Only",
    description:
      "Run vLLM without a GPU. Slow but useful for testing or very small models.",
    args: {
      device: "cpu",
      dtype: "float32",
    },
  },
  {
    id: "low-memory",
    name: "Low VRAM (≤ 8 GB)",
    description:
      "Squeeze a model into limited GPU memory with reduced context, offloading, and FP16.",
    args: {
      "gpu-memory-utilization": 0.95,
      "max-model-len": 2048,
      dtype: "float16",
      "cpu-offload-gb": 4,
      "swap-space": 2,
    },
  },
  {
    id: "high-throughput",
    name: "High Throughput Server",
    description:
      "Optimised for high-throughput production serving with prefix caching and chunked prefill.",
    args: {
      "enable-prefix-caching": true,
      "enable-chunked-prefill": true,
      "gpu-memory-utilization": 0.95,
      "disable-log-requests": true,
    },
  },
];

/**
 * Find the best matching recipe for a model name.
 * Returns the recipe id or `undefined` if no pattern matches.
 */
export function suggestRecipe(
  modelDisplayName: string,
  recipes: VllmRecipe[] = VLLM_RECIPES,
): string | undefined {
  const lower = modelDisplayName.toLowerCase();
  for (const r of recipes) {
    if (r.modelPattern && new RegExp(r.modelPattern, "i").test(lower)) {
      return r.id;
    }
  }
  return undefined;
}
