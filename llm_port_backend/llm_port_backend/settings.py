import enum
import os
import sys
from pathlib import Path
from tempfile import gettempdir
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL

_CPU_COUNT = os.cpu_count() or 1

TEMP_DIR = Path(gettempdir())


def _default_model_store_root() -> str:
    """Default model store: the standard HuggingFace hub cache location.

    Mirrors ``huggingface_hub``'s own default (``%LOCALAPPDATA%\\huggingface``
    on Windows when available, ``~/.cache/huggingface`` elsewhere) so an
    unprivileged install owns its model store without a root-owned
    ``/srv`` path. Containerized deployments override this via
    ``LLM_PORT_BACKEND_MODEL_STORE_ROOT`` in their compose env.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return str(Path(local) / "huggingface")
        return str(Path.home() / "AppData" / "Local" / "huggingface")
    return str(Path.home() / ".cache" / "huggingface")


class LogLevel(enum.StrEnum):
    """Possible log levels."""

    NOTSET = "NOTSET"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class Settings(BaseSettings):
    """
    Application settings.

    These parameters can be configured
    with environment variables.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    # quantity of workers for uvicorn
    workers_count: int = min(_CPU_COUNT, 4)
    # Enable uvicorn reloading
    reload: bool = False

    # Current environment
    environment: str = "dev"

    # Set to False when running behind HTTP (no TLS termination).
    cookie_secure: bool = True

    log_level: LogLevel = LogLevel.INFO
    users_secret: str = os.getenv("USERS_SECRET", "")

    def __init__(self, **kwargs: Any) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        # Auto-generate a JWT secret when none is provided so that
        # token generation works out-of-the-box during development.
        if not self.users_secret:
            import secrets  # noqa: PLC0415

            object.__setattr__(self, "users_secret", secrets.token_urlsafe(32))
    # Variables for the database
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_user: str = "llm_port_backend"
    db_pass: str = "llm_port_backend"  # noqa: S105
    db_base: str = "llm_port_backend"
    db_echo: bool = False
    db_pool_size: int = max(5, _CPU_COUNT * 3)
    db_max_overflow: int = max(10, _CPU_COUNT * 3)
    # PostgreSQL TLS
    # disable | prefer | require | verify-ca | verify-full
    db_ssl_mode: str = "disable"
    db_ssl_ca_bundle: str | None = None

    # Variables for RabbitMQ
    rabbit_host: str = "llm-port-rmq"
    rabbit_port: int = 5672
    rabbit_user: str = "guest"
    rabbit_pass: str = "guest"  # noqa: S105
    rabbit_vhost: str = "/"
    # RabbitMQ TLS — when True, the URL scheme is upgraded to amqps://
    # at connection time. Configure CA bundle path for custom trust roots.
    rabbit_ssl: bool = False
    rabbit_ssl_ca_bundle: str | None = None

    rabbit_pool_size: int = 2
    rabbit_channel_pool_size: int = 10

    # This variable is used to define
    # multiproc_dir. It's required for [uvi|guni]corn projects.
    prometheus_dir: Path = TEMP_DIR / "prom"

    # Sentry's configuration.
    sentry_dsn: str | None = None
    sentry_sample_rate: float = 1.0

    # Grpc endpoint for opentelemetry.
    # E.G. http://localhost:4317
    opentelemetry_endpoint: str | None = None
    # When True, the OTLP exporter is created with insecure=True
    # (no TLS). Set to False for TLS-protected collectors.
    opentelemetry_insecure: bool = True

    # ── Edge TLS material output paths ─────────────────────────────────
    # Where the admin "TLS activate" endpoint writes decrypted edge cert
    # material so the API process can pick it up at restart. Must be on
    # a volume the API container also mounts.
    tls_edge_material_dir: str = "/etc/llmport/tls"
    tls_edge_cert_basename: str = "edge.crt"
    tls_edge_key_basename: str = "edge.key"
    tls_edge_chain_basename: str = "edge-chain.pem"

    # LLM Server settings
    # Defaults to the standard HuggingFace cache in the user's home so
    # unprivileged host-process deployments (e.g. ``llmport dev up``) can
    # own the store; containerized deployments override it via
    # ``LLM_PORT_BACKEND_MODEL_STORE_ROOT``.
    model_store_root: str = _default_model_store_root()
    hf_token: str | None = None
    # Absolute path to a host-mounted HuggingFace cache directory.
    # When set (e.g. via the GPU compose overlay), auto_import_hf_cache
    # will scan this path for pre-downloaded models in addition to
    # the app-managed cache and the default HF cache.
    host_hf_cache_dir: str = ""
    default_vllm_image: str = "vllm/vllm-openai:latest"
    default_vllm_rocm_image: str = "vllm/vllm-openai-rocm:latest"
    # NGC image for NVIDIA ARM / unified-memory systems (Grace Hopper,
    # DGX Spark / GB10, Jetson).  Standard Docker Hub images are x86_64
    # only; this NGC build targets aarch64 + CUDA.
    # Pinned to a release tag to avoid driver-compatibility issues —
    # NGC "latest" may require a newer driver than what ships on the device.
    default_vllm_nvidia_arm_image: str = "nvcr.io/nvidia/vllm:26.03-py3"
    # Legacy image for GPUs with compute capability < 8.0 (Turing/Volta).
    # vLLM >= 0.7 uses the V1 engine which only supports FA2 (CC >= 8.0).
    # v0.6.6 uses V0 engine + XFormers (CC >= 7.0) and works in Docker
    # Desktop (WSL2) with --disable-frontend-multiprocessing to avoid
    # ZMQ epoll issues (ZMQError: Operation not supported).
    default_vllm_legacy_image: str = "vllm/vllm-openai:v0.6.6"

    # JSON-encoded list of additional vLLM image presets.
    # Each entry: {"label": "...", "image": "...", "vendor": "nvidia|amd|any", "description": "..."}
    # Example: '[{"label":"DGX Spark","image":"nvcr.io/nvidia/vllm:latest","vendor":"nvidia","description":"NVIDIA-optimised build for DGX Spark"}]'
    vllm_image_presets: str = "[]"
    llm_graph_db_host: str = "127.0.0.1"
    llm_graph_db_port: int = 5432
    llm_graph_db_user: str = "llm_user"
    llm_graph_db_pass: str = "llm_user"  # noqa: S105
    llm_graph_db_base: str = "llm_api"
    llm_graph_db_url_override: str | None = None
    rag_base_url: str = "http://127.0.0.1:8002/api"
    rag_service_token: str = "dev-rag-service-token"  # noqa: S105
    rag_runtime_secret_header_name: str = "x-embedding-api-key"  # noqa: S105
    rag_timeout_sec: float = 30.0
    rag_upload_max_file_mb: int = 50
    rag_upload_allowed_extensions: str = ".pdf,.docx,.txt,.md,.html,.csv,.json"
    rag_enabled: bool = False

    # PII module settings
    pii_enabled: bool = True
    pii_service_url: str = "http://127.0.0.1:8003/api"

    # Mailer module settings
    mailer_enabled: bool = False
    mailer_service_url: str = "http://127.0.0.1:8004"
    mailer_api_token: str = ""
    mailer_frontend_base_url: str = "http://localhost:5173"
    mailer_admin_recipients: list[str] | str = []
    mailer_alert_5xx_threshold_percent: int = 5
    mailer_alert_5xx_window_minutes: int = 5
    mailer_alert_cooldown_minutes: int = 30
    mailer_grafana_webhook_token: str = ""
    mailer_smtp_host: str = ""
    mailer_smtp_port: int = 587
    mailer_smtp_username: str = ""
    mailer_smtp_password: str = ""
    mailer_smtp_starttls: bool = True
    mailer_smtp_ssl: bool = False
    mailer_from_email: str = "noreply@llm-port.local"
    mailer_from_name: str = "LLM Port"

    # External Auth module settings (enterprise SSO)
    auth_enabled: bool = False
    auth_service_url: str = "http://127.0.0.1:8005"

    # Document Processor module settings (Docling)
    docling_enabled: bool = False
    docling_service_url: str = "http://127.0.0.1:8006"

    # MCP module settings
    mcp_enabled: bool = False
    mcp_service_url: str = "http://127.0.0.1:8007"
    mcp_service_token: str = "dev-mcp-service-token"

    # Skills module settings
    skills_enabled: bool = False
    skills_service_url: str = "http://127.0.0.1:8008"
    skills_service_token: str = "dev-skills-service-token"

    # RAG Lite module settings (embedded pgvector-based RAG)
    rag_lite_enabled: bool = False
    rag_lite_file_store_root: str = "/data/llm-port/rag-lite"
    rag_lite_embedding_provider_id: str = ""
    rag_lite_embedding_model: str = ""
    rag_lite_embedding_dim: int = 768
    rag_lite_chunk_max_tokens: int = 512
    rag_lite_chunk_overlap_tokens: int = 64
    rag_lite_upload_max_file_mb: int = 20

    # Chat & Sessions module settings (gateway feature, managed from backend)
    sessions_enabled: bool = True

    # API Gateway URL (for proxying user-facing chat requests)
    gateway_url: str = "http://127.0.0.1:9000"

    # Admin dashboard / Grafana embedding settings
    grafana_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LLM_PORT_BACKEND_GRAFANA_URL",
            "LLM_PORT_GRAFANA_URL",
        ),
    )
    grafana_dashboard_uid_overview: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LLM_PORT_BACKEND_GRAFANA_DASHBOARD_UID_OVERVIEW",
            "LLM_PORT_GRAFANA_DASHBOARD_UID_OVERVIEW",
        ),
    )
    grafana_panels_overview: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LLM_PORT_BACKEND_GRAFANA_PANELS_OVERVIEW",
            "LLM_PORT_GRAFANA_PANELS_OVERVIEW",
        ),
    )
    loki_base_url: str = "http://127.0.0.1:3100"
    # Verify TLS when querying Loki over HTTPS. Set to False only for
    # self-signed test setups; supply a CA bundle path for custom roots.
    loki_verify_tls: bool = True
    loki_ca_bundle: str | None = None
    logs_max_limit: int = 5000
    logs_default_limit: int = 200
    logs_allowed_labels_raw: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_PORT_BACKEND_LOGS_ALLOWED_LABELS"),
    )
    i18n_dir: str = "i18n"
    settings_master_key: str = "dev-settings-master-key-change-me"
    system_compose_file: str = "../llm_port_shared/docker-compose.yaml"
    system_agent_enabled: bool = False
    system_agent_token: str | None = None
    node_cluster_enabled: bool = False
    node_enrollment_ttl_minutes: int = 30
    node_command_default_timeout_sec: int = 900
    node_stream_idle_timeout_sec: int = 120

    # Runtime monitoring — vLLM /metrics scraped by the shared-stack
    # Prometheus service, with a per-runtime Grafana dashboard rendered
    # from a local template. Off by default; when on, the backend
    # generates prometheus/targets.json + dashboards/*.json in the
    # monitored directories below and reloads Prometheus.
    llm_monitoring_enabled: bool = False
    # Prometheus base URL, as reachable from the backend container
    # (compose service name in the shared stack, loopback in dev).
    prom_url: str = "http://127.0.0.1:9090"
    # Host path of the generated file_sd targets file (mounted into
    # both the prometheus and backend containers in the shared stack).
    prom_targets_file: str = "llm_port_shared/prometheus/targets.json"
    # Host directory the Grafana dashboard file provider reads; rendered
    # per-runtime dashboards (vllm-rt-*.json) are written here.
    prom_dashboard_dir: str = "llm_port_shared/grafana/provisioning/dashboards/json"
    # Reusable Grafana dashboard JSON template (see
    # llm_port_shared/grafana/dashboards/templates/vllm-runtime.json).
    dash_template: str = "llm_port_shared/grafana/dashboards/templates/vllm-runtime.json"
    # Grafana folder (title) used when provisioning runtime dashboards.
    dash_folder: str = "LLM Runtimes"

    @property
    def db_url(self) -> URL:
        """
        Assemble database URL from settings.

        :return: database URL.
        """
        return URL.build(
            scheme="postgresql+asyncpg",
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_pass,
            path=f"/{self.db_base}",
        )

    @property
    def rabbit_url(self) -> URL:
        """
        Assemble RabbitMQ URL from settings.

        :return: rabbit URL.
        """
        return URL.build(
            scheme="amqp",
            host=self.rabbit_host,
            port=self.rabbit_port,
            user=self.rabbit_user,
            password=self.rabbit_pass,
            path=self.rabbit_vhost,
        )

    @property
    def llm_graph_db_url(self) -> URL:
        """Assemble database URL for gateway trace read model."""
        if self.llm_graph_db_url_override:
            return URL(self.llm_graph_db_url_override)
        return URL.build(
            scheme="postgresql+asyncpg",
            host=self.llm_graph_db_host,
            port=self.llm_graph_db_port,
            user=self.llm_graph_db_user,
            password=self.llm_graph_db_pass,
            path=f"/{self.llm_graph_db_base}",
        )

    @property
    def logs_allowed_labels(self) -> set[str] | None:
        """Parse optional comma-separated allowlist for log labels."""
        if not self.logs_allowed_labels_raw:
            return None
        labels = {chunk.strip().lower() for chunk in self.logs_allowed_labels_raw.split(",") if chunk.strip()}
        return labels or None

    @property
    def i18n_path(self) -> Path:
        """Resolve translation directory path."""
        base = Path(self.i18n_dir)
        if base.is_absolute():
            return base
        return Path(__file__).resolve().parent.parent / base

    model_config = SettingsConfigDict(
        env_file=(".env", "llm_port_backend/.env"),
        env_prefix="LLM_PORT_BACKEND_",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
