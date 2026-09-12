export type {
  VllmEngineArgDef,
  VllmCategory,
  VllmRecipe,
  VllmArgType,
} from "./types";
export {
  VLLM_CATEGORIES,
  VLLM_ENGINE_ARGS,
  filterArgsByVersion,
} from "./registry";
export { VLLM_RECIPES, suggestRecipe } from "./recipes";

/**
 * Parse a raw string of vLLM CLI arguments into the token list stored as
 * `provider_config.extra_args`
 * passed to the container.
 *
 * Each token must be either a `--flag` / `--flag=value` / `--flag value`
 * pair, a bare `--flag`, or a plain value (numbers, paths, repo ids, etc.).
 * Tokens containing shell metacharacters are rejected so the string can
 * never be used to escape the container command.
 */
export function parseRawVllmArgs(raw: string): {
  args: string[];
  issues: string[];
} {
  const args: string[] = [];
  const issues: string[] = [];
  let expectValue = false;
  for (const tok of raw.split(/\s+/)) {
    if (!tok) continue;
    if (tok.startsWith("--")) {
      const name = tok.slice(2);
      const eqIdx = name.indexOf("=");
      const flagName = eqIdx >= 0 ? name.slice(0, eqIdx) : name;
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(flagName)) {
        issues.push(`Invalid flag: ${tok}`);
        expectValue = false;
        continue;
      }
      args.push(tok);
      expectValue = eqIdx < 0; // "--flag" (no "=value") expects a next token
    } else {
      if (!expectValue) {
        issues.push(`Unexpected value: ${tok}`);
        continue;
      }
      if (/[^\w.@/,=+\\\-]/.test(tok)) {
        issues.push(`Invalid value: ${tok}`);
        continue;
      }
      args.push(tok);
      expectValue = false;
    }
  }
  return { args, issues };
}
