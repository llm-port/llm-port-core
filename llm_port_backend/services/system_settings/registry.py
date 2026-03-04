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


SETTINGS_REGISTRY: tuple[SettingDefinition, ...] = (
    SettingDefinition(
        key="api.server.endpoint_url",
        type="string",
        category="api_server",
        group="endpoint",
        label="Endpoint URL",
        description="Swagger/OpenAPI URL for llm_port_api docs.",
        is_secret=False,
        default="http://localhost:8001/api/docs",
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
    # -- Mailer module settings ----------------------------------------
    SettingDefinition(
        key="llm_port_mailer.enabled",
        type="bool",
        category="modules",
        group="mailer",
        label="Mailer Module Enabled",
        description="Enable the optional mailer module lifecycle controls.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_mailer.service_url",
        type="string",
        category="notifications",
        group="mailer",
        label="Mailer Service URL",
        description="Internal backend-to-mailer URL (for example http://llm-port-mailer:8000).",
        is_secret=False,
        default="http://llm-port-mailer:8000",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_mailer.frontend_base_url",
        type="string",
        category="notifications",
        group="mailer",
        label="Frontend Base URL",
        description="Base URL used to build password reset links.",
        is_secret=False,
        default="http://localhost:5173",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_mailer.admin_recipients",
        type="json",
        category="notifications",
        group="alerts",
        label="Admin Alert Recipients",
        description="Optional list of admin email recipients merged with active superusers.",
        is_secret=False,
        default=[],
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_mailer.alert_5xx_threshold_percent",
        type="int",
        category="notifications",
        group="alerts",
        label="Gateway 5xx Threshold (%)",
        description="5xx percentage threshold to trigger admin alerts.",
        is_secret=False,
        default=5,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_mailer.alert_5xx_window_minutes",
        type="int",
        category="notifications",
        group="alerts",
        label="Gateway 5xx Window (Minutes)",
        description="Rolling window used for gateway 5xx threshold calculations.",
        is_secret=False,
        default=5,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_mailer.alert_cooldown_minutes",
        type="int",
        category="notifications",
        group="alerts",
        label="Alert Cooldown (Minutes)",
        description="Dedup cooldown per alert fingerprint.",
        is_secret=False,
        default=30,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_mailer.api_token",
        type="secret",
        category="notifications",
        group="mailer",
        label="Mailer Internal API Token",
        description="Shared bearer token between backend dispatcher and mailer internal API.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-backend", "llm-port-mailer"),
    ),
    SettingDefinition(
        key="llm_port_mailer.grafana_webhook_token",
        type="secret",
        category="notifications",
        group="alerts",
        label="Grafana Webhook Token",
        description="Bearer token accepted by the optional Grafana alert webhook endpoint.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="llm_port_mailer.smtp.host",
        type="string",
        category="notifications",
        group="smtp",
        label="SMTP Host",
        description="SMTP server hostname for outgoing mail.",
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-mailer",),
    ),
    SettingDefinition(
        key="llm_port_mailer.smtp.port",
        type="int",
        category="notifications",
        group="smtp",
        label="SMTP Port",
        description="SMTP server port.",
        is_secret=False,
        default=587,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-mailer",),
    ),
    SettingDefinition(
        key="llm_port_mailer.smtp.username",
        type="secret",
        category="notifications",
        group="smtp",
        label="SMTP Username",
        description="SMTP username (optional).",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-mailer",),
    ),
    SettingDefinition(
        key="llm_port_mailer.smtp.password",
        type="secret",
        category="notifications",
        group="smtp",
        label="SMTP Password",
        description="SMTP password.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-mailer",),
        protected=True,
    ),
    SettingDefinition(
        key="llm_port_mailer.smtp.starttls",
        type="bool",
        category="notifications",
        group="smtp",
        label="SMTP STARTTLS",
        description="Enable STARTTLS upgrade for SMTP connections.",
        is_secret=False,
        default=True,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-mailer",),
    ),
    SettingDefinition(
        key="llm_port_mailer.smtp.ssl",
        type="bool",
        category="notifications",
        group="smtp",
        label="SMTP SSL",
        description="Use implicit SSL SMTP transport.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-mailer",),
    ),
    SettingDefinition(
        key="llm_port_mailer.from_email",
        type="string",
        category="notifications",
        group="smtp",
        label="From Email",
        description="Sender email address used in outgoing notifications.",
        is_secret=False,
        default="noreply@llm-port.local",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-mailer",),
    ),
    SettingDefinition(
        key="llm_port_mailer.from_name",
        type="string",
        category="notifications",
        group="smtp",
        label="From Name",
        description="Sender display name used in outgoing notifications.",
        is_secret=False,
        default="LLM Port",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("llm-port-mailer",),
    ),
)


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
