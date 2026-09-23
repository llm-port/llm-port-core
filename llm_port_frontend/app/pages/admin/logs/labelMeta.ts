/**
 * What the logs page calls Loki's labels and their values.
 *
 * The filters used to be five hard-coded label names shown as raw keys --
 * "container", "job", "host" -- so a model's logs, whose streams carry
 * ``app`` / ``deployment`` / ``replica`` and no ``container``, could not be
 * filtered at all, and a machine's system journal appeared under "container"
 * as ``node-spark-3201``. Filters now come from whatever labels the logs
 * carry; this module only gives the known ones a name and an order, and
 * turns ids into the names an operator knows (machines, deployments).
 */
import type { TFunction } from "i18next";

/** Names for ids that appear in label values, looked up from the rest of the product. */
export interface NameLookups {
  /** host (the address the backend knows a machine by) -> machine name */
  machines: Map<string, string>;
  /** Ray application name (``llmport-<deployment id>``) -> deployment name */
  deployments: Map<string, string>;
}

export const NO_NAMES: NameLookups = { machines: new Map(), deployments: new Map() };

/**
 * Labels not worth a filter: Loki's own (``service_name`` duplicates the
 * container, app or job), and ``agent_id``, which is the machine again --
 * the Machine filter already shows it.
 */
const HIDDEN = new Set(["service_name", "agent_id", "container_id", "filename", "__stream_shard__"]);

/** The known labels, most useful first. Anything else follows, alphabetically. */
const ORDER = ["host", "job", "app", "deployment", "replica", "compose_service", "container", "level"];

export function filterLabels(keys: string[]): string[] {
  const rank = (key: string) => {
    const i = ORDER.indexOf(key);
    return i === -1 ? ORDER.length : i;
  };
  return keys
    .filter((key) => !HIDDEN.has(key) && !key.startsWith("__"))
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

export function labelTitle(key: string, t: TFunction): string {
  return ORDER.includes(key) ? t(`logs.label_${key}`, { defaultValue: key }) : key;
}

const JOB_KEYS: Record<string, string> = {
  "node-agent": "logs.job_node_agent",
  "ray-serve": "logs.job_ray_serve",
  docker: "logs.job_docker",
  "node-container": "logs.job_node_container",
};

/** A label value as the operator would name it. */
export function valueTitle(key: string, value: string, names: NameLookups, t: TFunction): string {
  switch (key) {
    case "host": {
      const name = names.machines.get(value);
      return name ? `${name} (${value})` : value;
    }
    case "job":
      return JOB_KEYS[value] ? t(JOB_KEYS[value], { defaultValue: value }) : value;
    case "app": {
      const name = names.deployments.get(value);
      if (name) return name;
      if (value.startsWith("llmport-") && names.deployments.size > 0) {
        return t("logs.deleted_deployment", { id: value.slice(8, 16), defaultValue: value });
      }
      return value;
    }
    case "deployment":
      if (value === "OpenAiIngress") return t("logs.component_ingress", { defaultValue: value });
      if (value.startsWith("LLMServer")) {
        const model = value.replace(/^LLMServer[_:]?/, "");
        return t("logs.component_server", { model, defaultValue: value });
      }
      return value;
    case "container": {
      // The agent labels the machine's journal ``node-<agent id>``: a machine, not a container.
      if (value.startsWith("node-")) {
        const agent = value.slice(5);
        if ([...names.machines.values()].includes(agent)) {
          return t("logs.agent_of", { name: agent, defaultValue: value });
        }
      }
      return value;
    }
    case "level":
      return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
    default:
      return value;
  }
}

/** The machine a stream came from, by name where known. */
export function machineOf(labels: Record<string, string>, names: NameLookups): string {
  const host = labels.host ?? "";
  if (!host) return labels.agent_id ?? "";
  return names.machines.get(host) ?? labels.agent_id ?? host;
}

/** What produced a stream, in words: a container, a model's component, the agent. */
export function sourceOf(labels: Record<string, string>, names: NameLookups, t: TFunction): string {
  const job = labels.job ?? "";
  if (job === "ray-serve") {
    const component = labels.deployment ? valueTitle("deployment", labels.deployment, names, t) : "";
    const app = labels.app ? valueTitle("app", labels.app, names, t) : "";
    return [app, component].filter(Boolean).join(" · ") || valueTitle("job", job, names, t);
  }
  if (job === "node-agent") return valueTitle("job", job, names, t);
  const container = labels.compose_service ?? labels.container ?? "";
  if (container) return container;
  return job ? valueTitle("job", job, names, t) : "";
}

/** Whether a stream's source is a container on this server, which the Containers page lists. */
export function isServerContainer(labels: Record<string, string>): boolean {
  return (labels.job ?? "") === "docker" && Boolean(labels.container);
}
