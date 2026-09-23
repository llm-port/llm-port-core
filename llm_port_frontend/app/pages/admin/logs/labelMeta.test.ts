import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";

import { buildLogql } from "~/pages/admin/LogsPage";

import { filterLabels, labelTitle, machineOf, sourceOf, valueTitle, type NameLookups } from "./labelMeta";

// Enough of i18next for these: the English default, with {{x}} filled in.
const t = ((key: string, opts?: Record<string, unknown>) => {
  const english: Record<string, string> = {
    "logs.label_host": "Machine",
    "logs.label_job": "Source",
    "logs.label_app": "Deployment",
    "logs.label_deployment": "Component",
    "logs.label_level": "Level",
    "logs.label_container": "Container",
    "logs.job_node_agent": "System log",
    "logs.job_ray_serve": "Model serving",
    "logs.component_ingress": "API entry",
    "logs.component_server": "Model server ({{model}})",
    "logs.agent_of": "{{name}} system log",
    "logs.deleted_deployment": "Deleted deployment ({{id}})",
  };
  const text = english[key] ?? String(opts?.defaultValue ?? key);
  return text.replace(/\{\{(\w+)\}\}/g, (_, name) => String(opts?.[name] ?? ""));
}) as unknown as TFunction;

const names: NameLookups = {
  machines: new Map([
    ["10.88.10.71", "spark-3201"],
    ["10.88.10.49", "spark-ts3202"],
  ]),
  deployments: new Map([["llmport-e783c0c2-cde1-4385-9d2e-08ea4bd2a52e", "qwen-chat"]]),
};

describe("the filters", () => {
  it("come from the labels present, known ones first, Loki's own left out", () => {
    const present = ["service_name", "level", "replica", "agent_id", "host", "app", "custom_tag", "job", "__stream_shard__"];
    expect(filterLabels(present)).toEqual(["host", "job", "app", "replica", "level", "custom_tag"]);
  });

  it("have names an operator knows, and unknown labels keep their own", () => {
    expect(labelTitle("host", t)).toBe("Machine");
    expect(labelTitle("job", t)).toBe("Source");
    expect(labelTitle("custom_tag", t)).toBe("custom_tag");
  });
});

describe("label values", () => {
  it("name the machine behind an address", () => {
    expect(valueTitle("host", "10.88.10.71", names, t)).toBe("spark-3201 (10.88.10.71)");
    expect(valueTitle("host", "172.17.0.9", names, t)).toBe("172.17.0.9");
  });

  it("name the deployment behind a Ray app, or say it is gone", () => {
    expect(valueTitle("app", "llmport-e783c0c2-cde1-4385-9d2e-08ea4bd2a52e", names, t)).toBe("qwen-chat");
    expect(valueTitle("app", "llmport-b64e389b-58ae-4b1d-a754-1f9f48e22220", names, t)).toBe(
      "Deleted deployment (b64e389b)",
    );
  });

  it("call a machine's journal its system log, not a container", () => {
    // What the page used to show under "container": the machine's journal.
    expect(valueTitle("container", "node-spark-3201", names, t)).toBe("spark-3201 system log");
    expect(valueTitle("container", "llm-port-postgres", names, t)).toBe("llm-port-postgres");
  });

  it("read a model's components in words", () => {
    expect(valueTitle("deployment", "OpenAiIngress", names, t)).toBe("API entry");
    expect(valueTitle("deployment", "LLMServer_Qwen2_5-0_5B-Instruct", names, t)).toBe(
      "Model server (Qwen2_5-0_5B-Instruct)",
    );
  });
});

describe("the table's machine and source", () => {
  it("say where a model's line came from", () => {
    const labels = {
      job: "ray-serve",
      host: "10.88.10.71",
      app: "llmport-e783c0c2-cde1-4385-9d2e-08ea4bd2a52e",
      deployment: "OpenAiIngress",
    };
    expect(machineOf(labels, names)).toBe("spark-3201");
    expect(sourceOf(labels, names, t)).toBe("qwen-chat · API entry");
  });

  it("say a machine's journal is its system log", () => {
    // The agent ships the whole journal -- every service on the host.
    const labels = { job: "node-agent", host: "10.88.10.49", container: "node-spark-ts3202" };
    expect(machineOf(labels, names)).toBe("spark-ts3202");
    expect(sourceOf(labels, names, t)).toBe("System log");
  });

  it("name a server container by its service", () => {
    expect(sourceOf({ job: "docker", container: "llm-port-postgres" }, names, t)).toBe("llm-port-postgres");
  });
});

describe("the query with nothing chosen", () => {
  it("matches every stream, not only those with the first filter's label", () => {
    // It was {container=~".+"}, which hid every model log.
    expect(buildLogql({}, "")).toBe('{job=~".+"}');
    expect(buildLogql({ host: "10.88.10.71" }, "error")).toBe('{host="10.88.10.71"} |= "error"');
  });
});
