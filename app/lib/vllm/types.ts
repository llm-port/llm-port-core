/**
 * Type definitions for the vLLM engine-args registry and recipe system.
 *
 * These types are intentionally kept separate so a future "recipe library"
 * (e.g. fetched from a remote GitHub repo) can implement the same shape.
 */

export type VllmArgType = "string" | "number" | "boolean" | "enum";

export interface VllmEngineArgDef {
  /** CLI flag name without leading dashes, e.g. "max-model-len". */
  flag: string;
  /** Value type for the input control. */
  type: VllmArgType;
  /** Default value.  `undefined` = no default (optional param). */
  default?: string | number | boolean;
  /** Valid choices (enum type only). */
  choices?: string[];
  /** UI category id. */
  category: string;
  /** One-line description from official vLLM docs. */
  description: string;
  /** Minimum vLLM version that supports this arg (inclusive, e.g. "0.6.6"). */
  minVersion?: string;
  /** Maximum vLLM version that supports this arg (inclusive). */
  maxVersion?: string;
  /** For number inputs: step increment. */
  step?: number;
  /** For number inputs: minimum allowed value. */
  min?: number;
  /** For number inputs: maximum allowed value. */
  max?: number;
}

export interface VllmCategory {
  id: string;
  label: string;
}

export interface VllmRecipe {
  id: string;
  name: string;
  description: string;
  /** Regex pattern for auto-matching model display names. */
  modelPattern?: string;
  /** Target vLLM version (for documentation only). */
  engineVersion?: string;
  /** Map of flag → value for non-default overrides. */
  args: Record<string, string | number | boolean>;
}
