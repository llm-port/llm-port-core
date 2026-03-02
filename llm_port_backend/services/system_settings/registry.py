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
        if not isinstance(value, dict):
            msg = f"Setting {defn.key} expects object value."
            raise ValueError(msg)
        return value
    if defn.type == "enum":
        if not isinstance(value, str) or value not in defn.enum_values:
            msg = f"Setting {defn.key} expects one of: {', '.join(defn.enum_values)}."
            raise ValueError(msg)
        return value
    msg = f"Unsupported setting type for key {defn.key}."
    raise ValueError(msg)
