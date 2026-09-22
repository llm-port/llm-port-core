/**
 * Fixture payloads for the inference component tests.
 *
 * One source for every page test, so a contract change shows up as one edit
 * rather than as four drifting copies. The shapes are the API client's own
 * exported types, so a backend DTO change that the client has not followed
 * fails `tsc` here before any test runs.
 *
 * The partial fixtures are the interesting ones: they encode the two states
 * Phase 6 requires to render honestly rather than as zeros or errors.
 */
import type {
  ArtifactReadiness,
  ComputePool,
  ControlPlane,
  DeploymentMetrics,
  EnvironmentMetrics,
  EnvironmentNode,
  EnvironmentPlan,
  InferenceDeployment,
  InferenceEndpoint,
  InferenceEnvironment,
  LogPage,
} from "~/api/inference";
import type { ManagedNode } from "~/api/nodes";

export const HEAD_NODE_ID = "11111111-1111-4111-8111-111111111111";
export const WORKER_NODE_ID = "22222222-2222-4222-8222-222222222222";
export const ENV_ID = "33333333-3333-4333-8333-333333333333";
export const DEPLOYMENT_ID = "44444444-4444-4444-8444-444444444444";
export const MODEL_ID = "55555555-5555-4555-8555-555555555555";
export const CONTROL_PLANE_ID = "66666666-6666-4666-8666-666666666666";

export const controlPlane: ControlPlane = {
  id: CONTROL_PLANE_ID,
  name: "dgx-control-plane",
  driver: "ray",
  description: null,
  status: "ready",
  config: {},
  observed_status: {},
  credential_ref: null,
  generation: 1,
  observed_generation: 1,
  status_message: null,
  enabled: true,
  created_at: "2026-09-20T10:00:00Z",
  updated_at: "2026-09-20T10:00:00Z",
};

export const managedNodes: ManagedNode[] = [
  {
    id: HEAD_NODE_ID,
    agent_id: "spark-ts3202",
    host: "10.88.10.49",
    status: "healthy",
    version: "0.1.7",
    labels: {},
    capabilities: {},
    maintenance_mode: false,
    draining: false,
    scheduler_eligible: true,
    last_seen: "2026-09-20T12:00:00Z",
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-20T12:00:00Z",
  },
  {
    id: WORKER_NODE_ID,
    agent_id: "spark-3201",
    host: "10.88.10.71",
    status: "healthy",
    version: "0.1.7",
    labels: {},
    capabilities: {},
    maintenance_mode: false,
    draining: false,
    scheduler_eligible: true,
    last_seen: "2026-09-20T12:00:00Z",
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-20T12:00:00Z",
  },
];

export const environment: InferenceEnvironment = {
  id: ENV_ID,
  control_plane_id: CONTROL_PLANE_ID,
  name: "dgx-pair",
  description: "Two-node GB10 cluster",
  status: "ready",
  desired_state: "running",
  runtime_version: "2.58.0",
  head_node_id: HEAD_NODE_ID,
  address: "10.100.0.1:6379",
  config: {},
  capabilities: {},
  observed_status: {
    observation: { status: "ready", reconciled: true, reason: "cluster healthy" },
    conditions: [
      {
        type: "HeadActive",
        status: "True",
        reason: "HeadResponding",
        message: "Ray head node is alive and responding.",
      },
      {
        type: "WorkersJoined",
        status: "True",
        reason: "AllWorkersJoined",
        message: "2 of 2 nodes alive.",
      },
    ],
    resolved_fabric: {
      candidate_id: "cand-roce-1",
      fabric_type: "roce",
      cidr: "10.100.0.0/24",
      speed_gbps: 200,
      mtu: 9000,
      is_management: false,
      isolation_level: "isolated_direct",
      node_bindings: {
        [HEAD_NODE_ID]: {
          node_id: HEAD_NODE_ID,
          interface: "enP2p1s0",
          ip: "10.100.0.1",
          link_type: "roce",
          rdma_device: "rocep2s0",
          mtu: 9000,
          is_management: false,
          is_up: true,
        },
        [WORKER_NODE_ID]: {
          node_id: WORKER_NODE_ID,
          interface: "enP2p1s0",
          ip: "10.100.0.2",
          link_type: "roce",
          rdma_device: "rocep2s0",
          mtu: 9000,
          is_management: false,
          is_up: true,
        },
      },
    },
  },
  generation: 4,
  observed_generation: 4,
  status_message: null,
  created_at: "2026-09-20T10:00:00Z",
  updated_at: "2026-09-20T12:00:00Z",
};

export const POOL_ID = "77777777-7777-4777-8777-777777777777";
export const AMD_POOL_ID = "88888888-8888-4888-8888-888888888888";

export const environmentNodes: EnvironmentNode[] = [
  {
    node_id: HEAD_NODE_ID,
    role: "head",
    member_status: "alive",
    compute_pool_id: POOL_ID,
    observed: {},
    joined_at: "2026-09-20T11:00:00Z",
  },
  {
    node_id: WORKER_NODE_ID,
    role: "worker",
    member_status: "alive",
    compute_pool_id: POOL_ID,
    observed: {},
    joined_at: "2026-09-20T11:01:00Z",
  },
];

/** The usual case: identical machines, so one derived pool nobody named. */
export const computePools: ComputePool[] = [
  {
    id: POOL_ID,
    environment_id: ENV_ID,
    name: "gb10",
    signature: "nvidia/aarch64/gb10",
    accelerator_vendor: "nvidia",
    accelerator_family: "GB10",
    cpu_architecture: "aarch64",
    labels: {},
    managed: false,
    member_count: 2,
  },
];

/** A cluster that grew a second kind of machine. */
export const mixedComputePools: ComputePool[] = [
  ...computePools,
  {
    id: AMD_POOL_ID,
    environment_id: ENV_ID,
    name: "mi300x",
    signature: "amd/x86_64/mi300x",
    accelerator_vendor: "amd",
    accelerator_family: "MI300X",
    cpu_architecture: "x86_64",
    labels: {},
    managed: false,
    member_count: 1,
  },
];

export const deployment: InferenceDeployment = {
  id: DEPLOYMENT_ID,
  environment_id: ENV_ID,
  model_id: MODEL_ID,
  name: "qwen-serve",
  description: null,
  spec: {
    api_version: "inference.llmport.ai/v1alpha1",
    engine: { name: "vllm", config: {} },
    scale: { replicas: 2 },
  },
  desired_state: "active",
  phase: "running",
  generation: 3,
  observed_generation: 3,
  observed_status: {
    observation: { reconciled: true, driver: "ray", reason: "application running" },
    applied_config_hash: "abc123",
  },
  phase_message: null,
  ready_replicas: 1,
  total_replicas: 2,
  created_at: "2026-09-20T11:30:00Z",
  updated_at: "2026-09-20T12:00:00Z",
};

export const endpoints: InferenceEndpoint[] = [
  {
    id: "77777777-7777-4777-8777-777777777777",
    deployment_id: DEPLOYMENT_ID,
    name: "openai",
    path: "/v1",
    address: "http://10.100.0.1:8000",
    status: "published",
    status_message: null,
    published: {},
    created_at: "2026-09-20T11:45:00Z",
    updated_at: "2026-09-20T12:00:00Z",
  },
];

/**
 * Artifact readiness mid-sync: one node ready, one still syncing, one failed.
 *
 * This is the state Phase 6 requires to render as a per-node table rather
 * than a spinner -- a node stuck on FAILED must stay visible while the
 * others are still working.
 */
export const artifactsPartial: ArtifactReadiness = {
  model_id: MODEL_ID,
  desired_revision: "abc123def456",
  manifest_sha256: null,
  ready_node_ids: [HEAD_NODE_ID],
  pending_node_ids: [],
  failed_node_ids: [WORKER_NODE_ID],
  blockers: ["spark-3201: snapshot incomplete, retrying in 300s"],
  root_paths: { [HEAD_NODE_ID]: "/var/lib/llm-port/models/qwen" },
  all_ready: false,
};

export const artifactsReady: ArtifactReadiness = {
  model_id: MODEL_ID,
  desired_revision: "abc123def456",
  manifest_sha256: null,
  ready_node_ids: [HEAD_NODE_ID, WORKER_NODE_ID],
  pending_node_ids: [],
  failed_node_ids: [],
  blockers: [],
  root_paths: {
    [HEAD_NODE_ID]: "/var/lib/llm-port/models/qwen",
    [WORKER_NODE_ID]: "/var/lib/llm-port/models/qwen",
  },
  all_ready: true,
};

/** The reason the walkthrough's blocker exists: the worker exports no port. */
export const WORKER_METRICS_REASON =
  "1 of 2 live nodes export no metrics port; rebuild the runtime image if its metrics dependencies are missing";

export const deploymentMetricsPartial: DeploymentMetrics = {
  // A deployment the gateway knows about and that has served requests --
  // the state the card is normally read in.
  traffic: {
    window_sec: 3600,
    requests: 4,
    errors: 0,
    error_rate: 0,
    p50_ttft_ms: 97.5,
    p95_ttft_ms: 111.2,
    p50_latency_ms: 725.5,
    p95_latency_ms: 1532.35,
    prompt_tokens: 841,
    completion_tokens: 475,
    output_tokens_per_sec: 159,
    last_request_at: "2026-09-22T05:50:22Z",
  },
  deployment_id: DEPLOYMENT_ID,
  app_name: "llm-port-qwen-serve",
  app_status: "RUNNING",
  replicas_ready: 1,
  replicas_total: 2,
  deployments: [
    {
      deployment_name: "LLMDeployment:qwen",
      status: "HEALTHY",
      replicas_ready: 1,
      replicas_pending: 1,
      message: null,
    },
  ],
  scrape_targets: [
    {
      node_id: HEAD_NODE_ID,
      address: "10.100.0.1",
      port: 38129,
      url: "http://10.100.0.1:38129/metrics",
    },
  ],
  partials: [{ tier: "node_metrics", reason: WORKER_METRICS_REASON }],
  observed_at: "2026-09-20T12:00:00Z",
};

export const environmentMetricsPartial: EnvironmentMetrics = {
  environment_id: ENV_ID,
  alive: true,
  dashboard_url: "http://localhost:3001/d/vllm-rt-e1e1a315/vllm-runtime?orgId=1",
  version: "2.58.0",
  nodes_total: 2,
  nodes_alive: 2,
  gpus_total: 2,
  gpus_available: 1,
  cpus_total: 40,
  cpus_available: 20,
  scrape_targets: [
    {
      node_id: HEAD_NODE_ID,
      address: "10.100.0.1",
      port: 38129,
      url: "http://10.100.0.1:38129/metrics",
    },
  ],
  partials: [{ tier: "node_metrics", reason: WORKER_METRICS_REASON }],
  raw: {},
  observed_at: "2026-09-20T12:00:00Z",
};

export const logPage: LogPage = {
  source: "runtime_container",
  node_id: HEAD_NODE_ID,
  replica_id: null,
  lines: [
    {
      ts: "2026-09-20T12:00:00Z",
      level: "INFO",
      message: "Started LLMDeployment:qwen",
    },
    {
      ts: null,
      level: null,
      message: '  File "/opt/vllm/engine.py", line 42, in load',
    },
  ],
  truncated: false,
  next_cursor: null,
  detail: null,
};

/** An empty page that says why, so silence never reads as health. */
export const logPageUnreachable: LogPage = {
  source: "runtime_container",
  node_id: HEAD_NODE_ID,
  replica_id: null,
  lines: [],
  truncated: false,
  next_cursor: null,
  detail: "could not reach the node: timeout",
};

export const plan: EnvironmentPlan = {
  plan_id: "plan-1",
  environment_id: ENV_ID,
  created_at: "2026-09-20T12:05:00Z",
  inventory_revisions: {},
  candidates: [
    {
      candidate_id: "cand-roce-1",
      fabric_type: "roce",
      cidr: "10.100.0.0/24",
      speed_gbps: 200,
      mtu: 9000,
      is_management: false,
      isolation_level: "isolated_direct",
      confidence: "high",
      score: 95,
      recommended: true,
      recommendation_reason: "direct 200 Gb/s link, isolated from management",
      reasons: ["rdma capable", "jumbo frames"],
      node_bindings: {},
      validation: {
        performed: true,
        reachable: true,
        method: "tcp_challenge",
        listener_node_id: HEAD_NODE_ID,
        probe_results: [],
        detail: "reachable in 2ms",
      },
    },
    {
      candidate_id: "cand-mgmt",
      fabric_type: "ethernet",
      cidr: "10.88.10.0/24",
      speed_gbps: 10,
      mtu: 1500,
      is_management: true,
      isolation_level: "shared_management",
      confidence: "medium",
      score: 30,
      recommended: false,
      recommendation_reason: "",
      reasons: ["management network, shared with control traffic"],
      node_bindings: {},
      validation: {
        performed: false,
        reachable: null,
        method: "tcp_challenge",
        listener_node_id: null,
        probe_results: [],
        detail: "",
      },
    },
  ],
  rejected: [{ cidr: "172.17.0.0/16", reason: "docker bridge", interfaces: ["docker0"] }],
  recommended_candidate_id: "cand-roce-1",
  recommended_head_node_id: HEAD_NODE_ID,
  head_selection_reason: "most GPUs and lowest latency to peers",
  runtime: {
    bundle_id: "bundle-dgx-spark-gb10-v1",
    resolved: true,
    compatible: true,
    node_results: {},
    node_bundles: {},
    detail: "certified bundle present on both nodes",
  },
  warnings: [],
  blockers: [],
};
