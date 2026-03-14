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
    """Application settings for the MCP Tool Registry service."""

    host: str = "127.0.0.1"
    port: int = 8000
    workers_count: int = 1  # stdio connections are per-worker; start with 1
    reload: bool = False

    environment: str = "dev"
    log_level: LogLevel = LogLevel.INFO

    # ── Database (PostgreSQL) ──
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_user: str = "llm_user"
    db_pass: str = "llm_user"
    db_base: str = "llm_mcp"
    db_echo: bool = False
    db_pool_size: int = max(5, _CPU_COUNT * 3)
    db_max_overflow: int = max(10, _CPU_COUNT * 3)

    # ── Redis (optional — empty host disables) ──
    redis_host: str = ""
    redis_port: int = 6379
    redis_user: Optional[str] = None
    redis_pass: Optional[str] = None
    redis_base: Optional[int] = None

    # ── Encryption ──
    encryption_key: str = ""

    # ── PII Service (optional) ──
    pii_service_url: str = ""

    # ── Internal service-to-service auth token ──
    service_token: str = ""

    # ── JWT (for admin API auth — validates backend-issued tokens) ──
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_audience: str = ""
    jwt_issuer: str = ""

    # ── MCP defaults ──
    default_pii_mode: str = "redact"
    default_timeout_sec: int = 60
    default_heartbeat_interval_sec: int = 30
    max_tool_loop_depth: int = 5

    # ── Observability ──
    prometheus_dir: Path = TEMP_DIR / "prom"
    sentry_dsn: Optional[str] = None
    sentry_sample_rate: float = 1.0
    opentelemetry_endpoint: Optional[str] = None

    @property
    def db_url(self) -> URL:
        """Assemble database URL from settings."""
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
        """Assemble Redis URL from settings."""
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
        env_prefix="LLM_PORT_MCP_",
        env_file_encoding="utf-8",
    )


settings = Settings()
