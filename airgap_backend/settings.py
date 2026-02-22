import enum
import os
from pathlib import Path
from tempfile import gettempdir

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL

TEMP_DIR = Path(gettempdir())


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
    workers_count: int = 1
    # Enable uvicorn reloading
    reload: bool = False

    # Current environment
    environment: str = "dev"

    log_level: LogLevel = LogLevel.INFO
    users_secret: str = os.getenv("USERS_SECRET", "")
    # Variables for the database
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "llm_port_backend"
    db_pass: str = "llm_port_backend"  # noqa: S105
    db_base: str = "llm_port_backend"
    db_echo: bool = False

    # Variables for RabbitMQ
    rabbit_host: str = "llm-port-backend-rmq"
    rabbit_port: int = 5672
    rabbit_user: str = "guest"
    rabbit_pass: str = "guest"  # noqa: S105
    rabbit_vhost: str = "/"

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

    # LLM Server settings
    model_store_root: str = "/srv/llm-port/models"
    hf_token: str | None = None
    default_vllm_image: str = "vllm/vllm-openai:latest"

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
    loki_base_url: str = "http://loki:3100"
    logs_max_limit: int = 5000
    logs_default_limit: int = 200
    logs_allowed_labels_raw: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_PORT_BACKEND_LOGS_ALLOWED_LABELS"),
    )
    i18n_dir: str = "i18n"

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
    def logs_allowed_labels(self) -> set[str] | None:
        """Parse optional comma-separated allowlist for log labels."""
        if not self.logs_allowed_labels_raw:
            return None
        labels = {
            chunk.strip().lower()
            for chunk in self.logs_allowed_labels_raw.split(",")
            if chunk.strip()
        }
        return labels or None

    @property
    def i18n_path(self) -> Path:
        """Resolve translation directory path."""
        base = Path(self.i18n_dir)
        if base.is_absolute():
            return base
        return Path(__file__).resolve().parent.parent / base

    model_config = SettingsConfigDict(
        env_file=(".env", "airgap_backend/.env"),
        env_prefix="LLM_PORT_BACKEND_",
        env_file_encoding="utf-8",
    )


settings = Settings()
