import enum
import os
from pathlib import Path
from tempfile import gettempdir
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL

_CPU_COUNT = os.cpu_count() or 1

TEMP_DIR = Path(gettempdir())


class LogLevel(str, enum.Enum):
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

    log_level: LogLevel = LogLevel.INFO
    # Variables for the database
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_user: str = "llm_port_pii"
    db_pass: str = "llm_port_pii"
    db_base: str = "admin"
    db_echo: bool = False
    db_pool_size: int = max(5, _CPU_COUNT * 3)
    db_max_overflow: int = max(10, _CPU_COUNT * 3)

    # Variables for Redis (optional — empty host disables Redis)
    redis_host: str = ""
    redis_port: int = 6379
    redis_user: Optional[str] = None
    redis_pass: Optional[str] = None
    redis_base: Optional[int] = None

    # This variable is used to define
    # multiproc_dir. It's required for [uvi|guni]corn projects.
    prometheus_dir: Path = TEMP_DIR / "prom"

    # Sentry's configuration.
    sentry_dsn: Optional[str] = None
    sentry_sample_rate: float = 1.0

    # Grpc endpoint for opentelemetry.
    # E.G. http://localhost:4317
    opentelemetry_endpoint: Optional[str] = None

    # PII detection settings
    pii_default_language: str = "en"
    pii_score_threshold: float = 0.35

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
    def redis_enabled(self) -> bool:
        """Return True when a Redis host is configured."""
        return bool(self.redis_host)

    @property
    def redis_url(self) -> URL:
        """
        Assemble REDIS URL from settings.

        :return: redis URL.
        """
        path = ""
        if self.redis_base is not None:
            path = f"/{self.redis_base}"
        return URL.build(
            scheme="redis",
            host=self.redis_host,
            port=self.redis_port,
            user=self.redis_user,
            password=self.redis_pass,
            path=path,
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LLM_PORT_PII_",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
