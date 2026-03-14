"""Code-defined settings registry and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from llm_port_backend.db.models.system_settings import SystemApplyScope

SettingType = Literal["string", "int", "bool", "secret", "json", "enum"]


@dataclass(frozen=True)
class SettingDefinition:
    """Registry definition for one setting key."""

    key: str
    type: SettingType
    category: str
    group: str
    label: str
    description: str
    is_secret: bool
    default: object
    apply_scope: SystemApplyScope
    service_targets: tuple[str, ...]
    protected: bool = False
    enum_values: tuple[str, ...] = ()


SETTINGS_REGISTRY: list[SettingDefinition] = [
    SettingDefinition(
        key="api.server.endpoint_url",
        type="string",
        category="api_server",
        group="endpoint",
        label="Endpoint URL",
        description="Swagger/OpenAPI URL for llm_port_api docs.",
        is_secret=False,
        default="/gateway-docs/docs",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="api.server.container_name",
        type="string",
        category="api_server",
        group="endpoint",
        label="API Container Name",
        description="Container name used for endpoint lifecycle operations.",
        is_secret=False,
        default="llm-port-api",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_api.jwt_secret",
        type="secret",
        category="auth",
        group="jwt",
        label="LLM API JWT Secret",
        description="JWT verification secret for llm_port_api.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
        protected=True,
    ),
    SettingDefinition(
        key="llm_port_backend.users_secret",
        type="secret",
        category="auth",
        group="jwt",
        label="Backend Users JWT Secret",
        description="Secret used to sign user API tokens. Both backend and API must agree on this value.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
        protected=True,
    ),
    SettingDefinition(
        key="llm_port_api.langfuse_enabled",
        type="bool",
        category="observability",
        group="langfuse",
        label="Langfuse Enabled",
        description="Enable gateway-to-Langfuse observability export.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.langfuse_host",
        type="string",
        category="observability",
        group="langfuse",
        label="Langfuse Host",
        description="Langfuse URL reachable by llm_port_api.",
        is_secret=False,
        default="http://langfuse-web:3000",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.langfuse_public_key",
        type="secret",
        category="observability",
        group="langfuse",
        label="Langfuse Public Key",
        description="Langfuse project public key.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.langfuse_secret_key",
        type="secret",
        category="observability",
        group="langfuse",
        label="Langfuse Secret Key",
        description="Langfuse project secret key.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
        protected=True,
    ),
    SettingDefinition(
        key="shared.redis.password",
        type="secret",
        category="database",
        group="redis",
        label="Redis Password",
        description="Redis auth password used by shared services.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.STACK_RECREATE,
        service_targets=("redis", "langfuse-web", "langfuse-worker", "llm-port-api"),
        protected=True,
    ),
    SettingDefinition(
        key="shared.postgres.password",
        type="secret",
        category="database",
        group="postgres",
        label="Postgres Password",
        description="Postgres admin password for the shared stack.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.STACK_RECREATE,
        service_targets=("postgres", "langfuse-web", "langfuse-worker"),
        protected=True,
    ),
    SettingDefinition(
        key="shared.grafana.admin_password",
        type="secret",
        category="logging",
        group="grafana",
        label="Grafana Admin Password",
        description="Grafana local admin password.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("grafana",),
        protected=True,
    ),
    SettingDefinition(
        key="llm_backend.hf_token",
        type="secret",
        category="llm",
        group="huggingface",
        label="Hugging Face Token",
        description="Hugging Face Hub access token for model downloads.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=("llm-port-backend",),
    ),
    # ── PII module settings ─────────────────────────────────────
    SettingDefinition(
        key="llm_port_api.pii_enabled",
        type="bool",
        category="modules",
        group="pii",
        label="PII Module Enabled",
        description="Enable PII detection and redaction via the PII service.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.pii_service_url",
        type="string",
        category="modules",
        group="pii",
        label="PII Service URL",
        description="Internal URL of the PII micro-service (e.g. http://llm-port-pii:8000 in Docker, http://127.0.0.1:8003/api in dev).",
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.pii_default_policy",
        type="json",
        category="modules",
        group="pii",
        label="Default PII Policy",
        description=(
            "Default PII policy JSON applied when no tenant-specific policy exists. "
            "Structure: {telemetry: {enabled, mode}, egress: {enabled_for_cloud, mode, fail_action}, "
            "presidio: {language, threshold, entities}}"
        ),
        is_secret=False,
        default={},
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    # ── MCP module settings ──────────────────────────────────
    SettingDefinition(
        key="llm_port_api.mcp_enabled",
        type="bool",
        category="modules",
        group="mcp",
        label="MCP Module Enabled",
        description="Enable the MCP Tool Registry for governed external tool access.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.mcp_service_url",
        type="string",
        category="modules",
        group="mcp",
        label="MCP Service URL",
        description=(
            "Internal URL of the MCP micro-service "
            "(e.g. http://llm-port-mcp:8000 in Docker, "
            "http://127.0.0.1:8007 in dev)."
        ),
        is_secret=False,
        default="http://llm-port-mcp:8000",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.mcp_service_token",
        type="secret",
        category="modules",
        group="mcp",
        label="MCP Service Token",
        description="Shared bearer token between API gateway and MCP service.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
        protected=True,
    ),
    # ── Skills module settings ───────────────────────────────
    SettingDefinition(
        key="llm_port_api.skills_enabled",
        type="bool",
        category="modules",
        group="skills",
        label="Skills Module Enabled",
        description="Enable the Skills Registry for reusable LLM instruction management.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.skills_service_url",
        type="string",
        category="modules",
        group="skills",
        label="Skills Service URL",
        description=(
            "Internal URL of the Skills micro-service "
            "(e.g. http://llm-port-skills:8000 in Docker, "
            "http://127.0.0.1:8008 in dev)."
        ),
        is_secret=False,
        default="http://llm-port-skills:8000",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
    ),
    SettingDefinition(
        key="llm_port_api.skills_service_token",
        type="secret",
        category="modules",
        group="skills",
        label="Skills Service Token",
        description="Shared bearer token between API gateway and Skills service.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-api",),
        protected=True,
    ),
    # ── RAG Lite ─────────────────────────────────────────────
    SettingDefinition(
        key="rag_lite.enabled",
        type="bool",
        category="modules",
        group="rag_lite",
        label="RAG Lite Enabled",
        description=(
            "Enable the embedded RAG Lite module (pgvector-only, no external "
            "RAG service). Ignored when the full RAG module is enabled."
        ),
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.embedding_provider_id",
        type="string",
        category="modules",
        group="rag_lite",
        label="Embedding Provider",
        description=(
            "UUID of the LLM provider to use for embeddings. "
            "Leave empty to auto-detect (first provider with "
            "supports_embeddings=true)."
        ),
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.embedding_model",
        type="string",
        category="modules",
        group="rag_lite",
        label="Embedding Model Name",
        description=(
            "Model name for the /v1/embeddings endpoint. "
            "Leave empty to use the provider's configured model."
        ),
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.embedding_dim",
        type="int",
        category="modules",
        group="rag_lite",
        label="Embedding Dimension",
        description="Actual embedding dimension returned by the model (e.g. 768, 1536).",
        is_secret=False,
        default=768,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.chunk_max_tokens",
        type="int",
        category="modules",
        group="rag_lite",
        label="Chunk Max Tokens",
        description="Maximum tokens per text chunk (approx. 4 chars/token).",
        is_secret=False,
        default=512,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.chunk_overlap_tokens",
        type="int",
        category="modules",
        group="rag_lite",
        label="Chunk Overlap Tokens",
        description="Overlap tokens between consecutive chunks.",
        is_secret=False,
        default=64,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.file_store_root",
        type="string",
        category="modules",
        group="rag_lite",
        label="File Store Root Path",
        description="Local filesystem path for storing uploaded RAG Lite documents.",
        is_secret=False,
        default="/data/llm-port/rag-lite",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.upload_max_file_mb",
        type="int",
        category="modules",
        group="rag_lite",
        label="Max Upload File Size (MB)",
        description="Maximum file upload size in megabytes for RAG Lite.",
        is_secret=False,
        default=20,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
]


def extend_registry(*definitions: SettingDefinition) -> None:
    """Append additional setting definitions (used by EE plugins)."""
    SETTINGS_REGISTRY.extend(definitions)


def registry_by_key() -> dict[str, SettingDefinition]:
    """Return mapping of key -> setting definition."""
    return {item.key: item for item in SETTINGS_REGISTRY}


def validate_value(defn: SettingDefinition, value: object) -> object:  # noqa: C901
    """Validate value against setting type and constraints."""
    if defn.type in {"string", "secret"}:
        if not isinstance(value, str):
            msg = f"Setting {defn.key} expects string value."
            raise ValueError(msg)
        return value
    if defn.type == "int":
        if not isinstance(value, int):
            msg = f"Setting {defn.key} expects integer value."
            raise ValueError(msg)
        return value
    if defn.type == "bool":
        if not isinstance(value, bool):
            msg = f"Setting {defn.key} expects boolean value."
            raise ValueError(msg)
        return value
    if defn.type == "json":
        if not isinstance(value, (dict, list)):
            msg = f"Setting {defn.key} expects object or array value."
            raise ValueError(msg)
        return value
    if defn.type == "enum":
        if not isinstance(value, str) or value not in defn.enum_values:
            msg = f"Setting {defn.key} expects one of: {', '.join(defn.enum_values)}."
            raise ValueError(msg)
        return value
    msg = f"Unsupported setting type for key {defn.key}."
    raise ValueError(msg)
