/**
 * pageHelp — route-to-help-content registry for the "About This Page" drawer.
 *
 * Each entry maps a route prefix to i18n keys in the "tour" namespace
 * under the `page_help.*` subtree. The first matching prefix wins,
 * so more-specific routes must come before their parents.
 */

export interface PageHelpEntry {
  /** i18n key for page title (tour namespace) */
  titleKey: string;
  /** i18n key for page description (tour namespace) */
  descriptionKey: string;
  /** i18n keys for bullet-point actions (tour namespace) */
  actionKeys: string[];
}

interface RouteMapping {
  prefix: string;
  entry: PageHelpEntry;
}

/**
 * Ordered list — most-specific prefixes first.
 */
const ROUTE_MAP: RouteMapping[] = [
  // ── Dashboard ────────────────────────────────────────────────────
  {
    prefix: "/admin/dashboard",
    entry: {
      titleKey: "page_help.dashboard.title",
      descriptionKey: "page_help.dashboard.description",
      actionKeys: [
        "page_help.dashboard.action_health",
        "page_help.dashboard.action_containers",
        "page_help.dashboard.action_metrics",
      ],
    },
  },

  // ── Containers ───────────────────────────────────────────────────
  {
    prefix: "/admin/containers/new",
    entry: {
      titleKey: "page_help.create_container.title",
      descriptionKey: "page_help.create_container.description",
      actionKeys: [
        "page_help.create_container.action_image",
        "page_help.create_container.action_config",
      ],
    },
  },
  {
    prefix: "/admin/containers/",
    entry: {
      titleKey: "page_help.container_detail.title",
      descriptionKey: "page_help.container_detail.description",
      actionKeys: [
        "page_help.container_detail.action_logs",
        "page_help.container_detail.action_restart",
        "page_help.container_detail.action_env",
      ],
    },
  },
  {
    prefix: "/admin/containers",
    entry: {
      titleKey: "page_help.containers.title",
      descriptionKey: "page_help.containers.description",
      actionKeys: [
        "page_help.containers.action_list",
        "page_help.containers.action_start_stop",
        "page_help.containers.action_create",
      ],
    },
  },

  // ── Images / Networks / Stacks ───────────────────────────────────
  {
    prefix: "/admin/images",
    entry: {
      titleKey: "page_help.images.title",
      descriptionKey: "page_help.images.description",
      actionKeys: [
        "page_help.images.action_pull",
        "page_help.images.action_remove",
      ],
    },
  },
  {
    prefix: "/admin/networks",
    entry: {
      titleKey: "page_help.networks.title",
      descriptionKey: "page_help.networks.description",
      actionKeys: [
        "page_help.networks.action_inspect",
        "page_help.networks.action_create",
      ],
    },
  },
  {
    prefix: "/admin/stacks",
    entry: {
      titleKey: "page_help.stacks.title",
      descriptionKey: "page_help.stacks.description",
      actionKeys: [
        "page_help.stacks.action_manage",
        "page_help.stacks.action_deploy",
      ],
    },
  },

  // ── Observability ────────────────────────────────────────────────
  {
    prefix: "/admin/observability",
    entry: {
      titleKey: "page_help.observability.title",
      descriptionKey: "page_help.observability.description",
      actionKeys: [
        "page_help.observability.action_cost",
        "page_help.observability.action_latency",
        "page_help.observability.action_tokens",
      ],
    },
  },

  // ── Logs / Audit ─────────────────────────────────────────────────
  {
    prefix: "/admin/logs",
    entry: {
      titleKey: "page_help.logs.title",
      descriptionKey: "page_help.logs.description",
      actionKeys: [
        "page_help.logs.action_filter",
        "page_help.logs.action_audit",
      ],
    },
  },

  // ── Security Map ─────────────────────────────────────────────────
  {
    prefix: "/admin/security-map",
    entry: {
      titleKey: "page_help.security_map.title",
      descriptionKey: "page_help.security_map.description",
      actionKeys: [
        "page_help.security_map.action_residency",
        "page_help.security_map.action_providers",
      ],
    },
  },

  // ── LLM ──────────────────────────────────────────────────────────
  {
    prefix: "/admin/llm/agent-trace",
    entry: {
      titleKey: "page_help.agent_trace.title",
      descriptionKey: "page_help.agent_trace.description",
      actionKeys: [
        "page_help.agent_trace.action_inspect",
        "page_help.agent_trace.action_replay",
      ],
    },
  },
  {
    prefix: "/admin/llm/endpoint",
    entry: {
      titleKey: "page_help.endpoint.title",
      descriptionKey: "page_help.endpoint.description",
      actionKeys: [
        "page_help.endpoint.action_docs",
        "page_help.endpoint.action_test",
      ],
    },
  },
  {
    prefix: "/admin/llm/models/",
    entry: {
      titleKey: "page_help.model_detail.title",
      descriptionKey: "page_help.model_detail.description",
      actionKeys: [
        "page_help.model_detail.action_params",
        "page_help.model_detail.action_test",
      ],
    },
  },
  {
    prefix: "/admin/llm/models",
    entry: {
      titleKey: "page_help.models.title",
      descriptionKey: "page_help.models.description",
      actionKeys: [
        "page_help.models.action_browse",
        "page_help.models.action_pull",
        "page_help.models.action_assign",
      ],
    },
  },
  {
    prefix: "/admin/llm/runtimes/",
    entry: {
      titleKey: "page_help.runtime_detail.title",
      descriptionKey: "page_help.runtime_detail.description",
      actionKeys: [
        "page_help.runtime_detail.action_config",
        "page_help.runtime_detail.action_models",
      ],
    },
  },
  {
    prefix: "/admin/llm/runtimes",
    entry: {
      titleKey: "page_help.runtimes.title",
      descriptionKey: "page_help.runtimes.description",
      actionKeys: [
        "page_help.runtimes.action_list",
        "page_help.runtimes.action_health",
      ],
    },
  },
  {
    prefix: "/admin/llm/providers",
    entry: {
      titleKey: "page_help.providers.title",
      descriptionKey: "page_help.providers.description",
      actionKeys: [
        "page_help.providers.action_add",
        "page_help.providers.action_test",
        "page_help.providers.action_route",
      ],
    },
  },

  // ── Nodes ────────────────────────────────────────────────────────
  {
    prefix: "/admin/nodes/profiles",
    entry: {
      titleKey: "page_help.node_profiles.title",
      descriptionKey: "page_help.node_profiles.description",
      actionKeys: [
        "page_help.node_profiles.action_create",
        "page_help.node_profiles.action_assign",
      ],
    },
  },
  {
    prefix: "/admin/nodes/",
    entry: {
      titleKey: "page_help.node_detail.title",
      descriptionKey: "page_help.node_detail.description",
      actionKeys: [
        "page_help.node_detail.action_status",
        "page_help.node_detail.action_drain",
      ],
    },
  },
  {
    prefix: "/admin/nodes",
    entry: {
      titleKey: "page_help.nodes.title",
      descriptionKey: "page_help.nodes.description",
      actionKeys: [
        "page_help.nodes.action_enroll",
        "page_help.nodes.action_monitor",
        "page_help.nodes.action_schedule",
      ],
    },
  },

  // ── Scheduler ────────────────────────────────────────────────────
  {
    prefix: "/admin/scheduler",
    entry: {
      titleKey: "page_help.scheduler.title",
      descriptionKey: "page_help.scheduler.description",
      actionKeys: [
        "page_help.scheduler.action_queue",
        "page_help.scheduler.action_policies",
      ],
    },
  },

  // ── Access Control ───────────────────────────────────────────────
  {
    prefix: "/admin/users",
    entry: {
      titleKey: "page_help.users.title",
      descriptionKey: "page_help.users.description",
      actionKeys: [
        "page_help.users.action_create",
        "page_help.users.action_roles",
        "page_help.users.action_keys",
      ],
    },
  },
  {
    prefix: "/admin/roles",
    entry: {
      titleKey: "page_help.roles.title",
      descriptionKey: "page_help.roles.description",
      actionKeys: [
        "page_help.roles.action_create",
        "page_help.roles.action_permissions",
      ],
    },
  },
  {
    prefix: "/admin/groups",
    entry: {
      titleKey: "page_help.groups.title",
      descriptionKey: "page_help.groups.description",
      actionKeys: [
        "page_help.groups.action_create",
        "page_help.groups.action_members",
      ],
    },
  },
  {
    prefix: "/admin/auth-providers",
    entry: {
      titleKey: "page_help.auth_providers.title",
      descriptionKey: "page_help.auth_providers.description",
      actionKeys: [
        "page_help.auth_providers.action_add",
        "page_help.auth_providers.action_test",
      ],
    },
  },

  // ── PII ──────────────────────────────────────────────────────────
  {
    prefix: "/admin/pii/dashboard",
    entry: {
      titleKey: "page_help.pii_dashboard.title",
      descriptionKey: "page_help.pii_dashboard.description",
      actionKeys: [
        "page_help.pii_dashboard.action_stats",
        "page_help.pii_dashboard.action_entities",
      ],
    },
  },
  {
    prefix: "/admin/pii/activity",
    entry: {
      titleKey: "page_help.pii_activity.title",
      descriptionKey: "page_help.pii_activity.description",
      actionKeys: [
        "page_help.pii_activity.action_filter",
        "page_help.pii_activity.action_export",
      ],
    },
  },
  {
    prefix: "/admin/pii/policies",
    entry: {
      titleKey: "page_help.pii_policies.title",
      descriptionKey: "page_help.pii_policies.description",
      actionKeys: [
        "page_help.pii_policies.action_create",
        "page_help.pii_policies.action_assign",
      ],
    },
  },

  // ── RAG ──────────────────────────────────────────────────────────
  {
    prefix: "/admin/rag/knowledge-base",
    entry: {
      titleKey: "page_help.rag_knowledge_base.title",
      descriptionKey: "page_help.rag_knowledge_base.description",
      actionKeys: [
        "page_help.rag_knowledge_base.action_upload",
        "page_help.rag_knowledge_base.action_search",
      ],
    },
  },
  {
    prefix: "/admin/rag/documents",
    entry: {
      titleKey: "page_help.rag_documents.title",
      descriptionKey: "page_help.rag_documents.description",
      actionKeys: [
        "page_help.rag_documents.action_browse",
        "page_help.rag_documents.action_delete",
      ],
    },
  },
  {
    prefix: "/admin/rag/collections",
    entry: {
      titleKey: "page_help.rag_collections.title",
      descriptionKey: "page_help.rag_collections.description",
      actionKeys: [
        "page_help.rag_collections.action_create",
        "page_help.rag_collections.action_manage",
      ],
    },
  },
  {
    prefix: "/admin/rag/runtime",
    entry: {
      titleKey: "page_help.rag_runtime.title",
      descriptionKey: "page_help.rag_runtime.description",
      actionKeys: [
        "page_help.rag_runtime.action_config",
        "page_help.rag_runtime.action_status",
      ],
    },
  },
  {
    prefix: "/admin/rag/collectors",
    entry: {
      titleKey: "page_help.rag_collectors.title",
      descriptionKey: "page_help.rag_collectors.description",
      actionKeys: [
        "page_help.rag_collectors.action_add",
        "page_help.rag_collectors.action_schedule",
      ],
    },
  },
  {
    prefix: "/admin/rag/explorer",
    entry: {
      titleKey: "page_help.rag_explorer.title",
      descriptionKey: "page_help.rag_explorer.description",
      actionKeys: [
        "page_help.rag_explorer.action_query",
        "page_help.rag_explorer.action_chunks",
      ],
    },
  },
  {
    prefix: "/admin/rag/publishes",
    entry: {
      titleKey: "page_help.rag_publishes.title",
      descriptionKey: "page_help.rag_publishes.description",
      actionKeys: [
        "page_help.rag_publishes.action_publish",
        "page_help.rag_publishes.action_status",
      ],
    },
  },
  {
    prefix: "/admin/rag/search",
    entry: {
      titleKey: "page_help.rag_search.title",
      descriptionKey: "page_help.rag_search.description",
      actionKeys: [
        "page_help.rag_search.action_query",
        "page_help.rag_search.action_compare",
      ],
    },
  },

  // ── Chat ─────────────────────────────────────────────────────────
  {
    prefix: "/admin/chat/projects",
    entry: {
      titleKey: "page_help.chat_projects.title",
      descriptionKey: "page_help.chat_projects.description",
      actionKeys: [
        "page_help.chat_projects.action_create",
        "page_help.chat_projects.action_models",
      ],
    },
  },
  {
    prefix: "/admin/chat/sessions",
    entry: {
      titleKey: "page_help.chat_sessions.title",
      descriptionKey: "page_help.chat_sessions.description",
      actionKeys: [
        "page_help.chat_sessions.action_browse",
        "page_help.chat_sessions.action_export",
      ],
    },
  },
  {
    prefix: "/admin/chat/attachments",
    entry: {
      titleKey: "page_help.chat_attachments.title",
      descriptionKey: "page_help.chat_attachments.description",
      actionKeys: [
        "page_help.chat_attachments.action_browse",
        "page_help.chat_attachments.action_clean",
      ],
    },
  },

  // ── MCP ──────────────────────────────────────────────────────────
  {
    prefix: "/admin/mcp/servers/",
    entry: {
      titleKey: "page_help.mcp_detail.title",
      descriptionKey: "page_help.mcp_detail.description",
      actionKeys: [
        "page_help.mcp_detail.action_tools",
        "page_help.mcp_detail.action_config",
      ],
    },
  },
  {
    prefix: "/admin/mcp/servers",
    entry: {
      titleKey: "page_help.mcp_servers.title",
      descriptionKey: "page_help.mcp_servers.description",
      actionKeys: [
        "page_help.mcp_servers.action_register",
        "page_help.mcp_servers.action_health",
      ],
    },
  },

  // ── Skills ───────────────────────────────────────────────────────
  {
    prefix: "/admin/skills/create",
    entry: {
      titleKey: "page_help.skills_create.title",
      descriptionKey: "page_help.skills_create.description",
      actionKeys: [
        "page_help.skills_create.action_define",
        "page_help.skills_create.action_test",
      ],
    },
  },
  {
    prefix: "/admin/skills/",
    entry: {
      titleKey: "page_help.skill_detail.title",
      descriptionKey: "page_help.skill_detail.description",
      actionKeys: [
        "page_help.skill_detail.action_edit",
        "page_help.skill_detail.action_test",
      ],
    },
  },
  {
    prefix: "/admin/skills",
    entry: {
      titleKey: "page_help.skills.title",
      descriptionKey: "page_help.skills.description",
      actionKeys: [
        "page_help.skills.action_browse",
        "page_help.skills.action_create",
      ],
    },
  },

  // ── Settings ─────────────────────────────────────────────────────
  {
    prefix: "/admin/settings",
    entry: {
      titleKey: "page_help.settings.title",
      descriptionKey: "page_help.settings.description",
      actionKeys: [
        "page_help.settings.action_general",
        "page_help.settings.action_security",
        "page_help.settings.action_features",
      ],
    },
  },

  // ── Profile ──────────────────────────────────────────────────────
  {
    prefix: "/admin/profile",
    entry: {
      titleKey: "page_help.profile.title",
      descriptionKey: "page_help.profile.description",
      actionKeys: [
        "page_help.profile.action_info",
        "page_help.profile.action_password",
      ],
    },
  },
];

/**
 * Resolve the best-matching page help entry for the current pathname.
 * Returns `null` if no match is found (shouldn't happen for /admin/* routes).
 */
export function resolvePageHelp(pathname: string): PageHelpEntry | null {
  for (const mapping of ROUTE_MAP) {
    if (pathname.startsWith(mapping.prefix)) {
      return mapping.entry;
    }
  }
  return null;
}
