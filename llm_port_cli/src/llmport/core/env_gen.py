""".env file generation from Jinja2 templates.

Generates a production ``.env`` file with cryptographically random
secrets replacing every ``CHANGEME_*`` placeholder in the template.
The user can then override individual values via ``llmport config set``.
"""

from __future__ import annotations

import secrets
import string
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from llmport.core.registry import module_env_vars
from llmport.core.sysinfo import calculate_tune_profile

# Keys that contain cryptographic secrets. These must never be replaced
# when regenerating an .env file because changing them would make
# previously encrypted data (chat content, DB settings, etc.) unreadable.
SECRET_KEYS: frozenset[str] = frozenset({
    "POSTGRES_PASSWORD",
    "REDIS_AUTH",
    "MINIO_ROOT_PASSWORD",
    "CLICKHOUSE_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
    "RABBITMQ_ADMIN_PASS",
    "RABBITMQ_BACKEND_PASS",
    "RABBITMQ_API_PASS",
    "RABBITMQ_PII_PASS",
    "LANGFUSE_NEXTAUTH_SECRET",
    "LANGFUSE_SALT",
    "LANGFUSE_ENCRYPTION_KEY",
    "LLM_PORT_BACKEND_SETTINGS_MASTER_KEY",
    "LLM_PORT_API_ENCRYPTION_KEY",
    "USERS_SECRET",
    # Inter-service auth tokens — each downstream service has one shared
    # value across (api → service), (backend → service), (service receiver).
    # All three keys per service MUST stay in lock-step, so they're listed
    # together here to be preserved as a group on .env regeneration.
    "LLM_PORT_API_MCP_SERVICE_TOKEN",
    "LLM_PORT_BACKEND_MCP_SERVICE_TOKEN",
    "LLM_PORT_MCP_SERVICE_TOKEN",
    "LLM_PORT_API_SKILLS_SERVICE_TOKEN",
    "LLM_PORT_BACKEND_SKILLS_SERVICE_TOKEN",
    "LLM_PORT_SKILLS_SERVICE_TOKEN",
    "LLM_PORT_BACKEND_RAG_SERVICE_TOKEN",
    "LLM_PORT_RAG_SERVICE_TOKEN",
})


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a dict, ignoring comments and blanks."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key:
            result[key.strip()] = value.strip()
    return result


def _random_password(length: int = 32) -> str:
    """Generate a URL-safe random password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _random_hex(length: int = 64) -> str:
    """Generate a hex string of the given length."""
    return secrets.token_hex(length // 2)


def _random_secret(length: int = 48) -> str:
    """Generate a URL-safe base64 secret."""
    return secrets.token_urlsafe(length)


# ── Dev-mode defaults (matching existing shared .env) ─────────────


def dev_env_vars(
    *,
    profiles: list[str] | None = None,
) -> dict[str, str]:
    """Build env vars with standard dev credentials.

    Uses ``devpassword`` everywhere — matches the existing
    ``llm_port_shared/.env`` used during development.
    """
    pw = "devpassword"

    env = {
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": pw,
        "POSTGRES_DB": "postgres",
        "GRAFANA_ADMIN_USER": "admin",
        "GRAFANA_ADMIN_PASSWORD": pw,
        "LANGFUSE_NEXTAUTH_SECRET": "dev-langfuse-nextauth-secret",
        "LANGFUSE_SALT": "dev-langfuse-salt",
        "LANGFUSE_ENCRYPTION_KEY": "0" * 64,
        "LANGFUSE_DATABASE_URL": f"postgresql://postgres:{pw}@postgres:5432/langfuse",
        "CLICKHOUSE_USER": "clickhouse",
        "CLICKHOUSE_PASSWORD": pw,
        "CLICKHOUSE_MIGRATION_URL": "clickhouse://clickhouse:9000",
        "CLICKHOUSE_URL": "http://clickhouse:8123",
        "REDIS_AUTH": pw,
        "MINIO_ROOT_USER": "minio",
        "MINIO_ROOT_PASSWORD": pw,
        "LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT": "http://minio:9000",
        "LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT": "http://minio:9000",
        # RabbitMQ — admin for management console, per-service for AMQP
        "RABBITMQ_ADMIN_USER": "admin",
        "RABBITMQ_ADMIN_PASS": pw,
        "RABBITMQ_BACKEND_PASS": pw,
        "RABBITMQ_API_PASS": pw,
        "RABBITMQ_PII_PASS": pw,
        # Settings master key — used to encrypt/decrypt secrets stored in the DB.
        # Must match LLM_PORT_BACKEND_SETTINGS_MASTER_KEY in the backend .env.
        # JWT secrets are seeded automatically in the DB on first startup.
        "LLM_PORT_BACKEND_SETTINGS_MASTER_KEY": "dev-settings-master-key-change-me",
        "LLM_PORT_API_ENCRYPTION_KEY": "dev-encryption-key-change-me",
        "LLM_PORT_API_LANGFUSE_ENABLED": "false",
        "LLM_PORT_BACKEND_GATEWAY_URL": "http://llm-port-api:8000",
        # Langfuse initial admin seeding
        "LANGFUSE_INIT_USER_EMAIL": "",
        "LANGFUSE_INIT_USER_PASSWORD": "",
        "LANGFUSE_INIT_USER_NAME": "Admin",
        "LANGFUSE_INIT_ORG_ID": "llm-port-org",
        "LANGFUSE_INIT_ORG_NAME": "llm-port",
        "LANGFUSE_INIT_PROJECT_ID": "llm-port-project",
        "LANGFUSE_INIT_PROJECT_NAME": "llm-port",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "",
    }

    # Scalability tuning (conservative dev defaults)
    env.update(calculate_tune_profile("dev").to_env_dict())

    profs = profiles or []
    for prof in profs:
        env.update(module_env_vars(prof))

    return env


# ── Production defaults (random secrets) ──────────────────────────


def default_env_vars(
    *,
    admin_email: str = "admin@example.com",
    admin_password: str = "",
    profiles: list[str] | None = None,
) -> dict[str, str]:
    """Build a dict of env vars with generated secrets.

    These correspond to the placeholders in the ``.env.j2`` template.
    """
    postgres_password = _random_password(24)
    redis_password = _random_password(24)
    minio_password = _random_password(24)

    # Inter-service auth tokens. One random value per downstream service,
    # written to all three env vars (api → service, backend → service,
    # service receiver) so they stay in lock-step. The receiver-side
    # ``verify_service_token`` fails open when its env var is empty, so it
    # is critical that these are generated and propagated for production
    # deployments.
    mcp_service_token = _random_secret()
    skills_service_token = _random_secret()
    rag_service_token = _random_secret()

    env = {
        # Postgres
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_DB": "postgres",
        # Grafana
        "GRAFANA_ADMIN_USER": "admin",
        "GRAFANA_ADMIN_PASSWORD": admin_password or _random_password(16),
        # Langfuse
        "LANGFUSE_NEXTAUTH_SECRET": _random_secret(),
        "LANGFUSE_SALT": _random_secret(32),
        "LANGFUSE_ENCRYPTION_KEY": _random_hex(64),
        "LANGFUSE_DATABASE_URL": f"postgresql://postgres:{postgres_password}@postgres:5432/langfuse",
        # ClickHouse
        "CLICKHOUSE_USER": "clickhouse",
        "CLICKHOUSE_PASSWORD": _random_password(24),
        # Redis
        "REDIS_AUTH": redis_password,
        # MinIO
        "MINIO_ROOT_USER": "minio",
        "MINIO_ROOT_PASSWORD": minio_password,
        # RabbitMQ — admin for management console, per-service for AMQP
        "RABBITMQ_ADMIN_USER": "admin",
        "RABBITMQ_ADMIN_PASS": _random_password(24),
        "RABBITMQ_BACKEND_PASS": _random_password(24),
        "RABBITMQ_API_PASS": _random_password(24),
        "RABBITMQ_PII_PASS": _random_password(24),
        # Settings master key — used to encrypt/decrypt secrets stored in the DB.
        # JWT secrets are seeded automatically in the DB on first startup.
        "LLM_PORT_BACKEND_SETTINGS_MASTER_KEY": _random_secret(),
        "LLM_PORT_API_ENCRYPTION_KEY": _random_secret(),
        "LLM_PORT_API_LANGFUSE_ENABLED": "false",
        "LLM_PORT_BACKEND_GATEWAY_URL": "http://llm-port-api:8000",
        "LLM_PORT_BACKEND_ENVIRONMENT": "production",
        "USERS_SECRET": _random_secret(),
        # Inter-service auth tokens (see comment above). Each pair of
        # caller env vars must equal the receiver's env var.
        "LLM_PORT_API_MCP_SERVICE_TOKEN": mcp_service_token,
        "LLM_PORT_BACKEND_MCP_SERVICE_TOKEN": mcp_service_token,
        "LLM_PORT_MCP_SERVICE_TOKEN": mcp_service_token,
        "LLM_PORT_API_SKILLS_SERVICE_TOKEN": skills_service_token,
        "LLM_PORT_BACKEND_SKILLS_SERVICE_TOKEN": skills_service_token,
        "LLM_PORT_SKILLS_SERVICE_TOKEN": skills_service_token,
        "LLM_PORT_BACKEND_RAG_SERVICE_TOKEN": rag_service_token,
        "LLM_PORT_RAG_SERVICE_TOKEN": rag_service_token,
        # Langfuse initial admin seeding (populated during bootstrap)
        "LANGFUSE_INIT_USER_EMAIL": "",
        "LANGFUSE_INIT_USER_PASSWORD": "",
        "LANGFUSE_INIT_USER_NAME": "Admin",
        "LANGFUSE_INIT_ORG_ID": "llm-port-org",
        "LANGFUSE_INIT_ORG_NAME": "llm-port",
        "LANGFUSE_INIT_PROJECT_ID": "llm-port-project",
        "LANGFUSE_INIT_PROJECT_NAME": "llm-port",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "",
    }

    # Scalability tuning (auto-detect host resources for prod)
    env.update(calculate_tune_profile("prod").to_env_dict())

    # Module activation
    profs = profiles or []
    for prof in profs:
        env.update(module_env_vars(prof))

    return env


# ── Template rendering ────────────────────────────────────────────


def render_env_file(
    template_dir: Path,
    *,
    template_name: str = ".env.j2",
    variables: dict[str, str] | None = None,
) -> str:
    """Render the ``.env.j2`` template with the given variables."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(),
        keep_trailing_newline=True,
    )
    tmpl = env.get_template(template_name)
    return tmpl.render(**(variables or {}))


def write_env_file(
    output_path: Path,
    variables: dict[str, str],
    *,
    preserve_secrets: bool = False,
) -> Path:
    """Write a ``.env`` file from a variable dict (no template needed).

    This is the simpler path: just write ``KEY=VALUE`` lines directly.
    Suitable when we don't need Jinja2 logic in the env file.

    If *preserve_secrets* is True and *output_path* already exists,
    any keys listed in :data:`SECRET_KEYS` that are present in the
    existing file will be kept instead of being replaced.
    """
    if preserve_secrets:
        existing = read_env_file(output_path)
        for key in SECRET_KEYS:
            if key in existing and key in variables:
                variables[key] = existing[key]
        # Also fix derived values that embed a secret
        if "POSTGRES_PASSWORD" in existing and "LANGFUSE_DATABASE_URL" in variables:
            pw = existing["POSTGRES_PASSWORD"]
            user = variables.get("POSTGRES_USER", "postgres")
            db = variables.get("POSTGRES_DB", "langfuse")
            variables["LANGFUSE_DATABASE_URL"] = (
                f"postgresql://{user}:{pw}@postgres:5432/{db}"
            )
    lines: list[str] = []
    lines.append("# Generated by llmport init — do not edit manually unless you know what you're doing.")
    lines.append(f"# Generated at: {_timestamp()}")
    lines.append("")

    # Group by prefix for readability
    groups: dict[str, list[tuple[str, str]]] = {"core": []}
    for key, value in variables.items():
        if key.startswith("LLM_PORT_"):
            group = "llm_port"
        elif key.startswith("LANGFUSE_"):
            group = "langfuse"
        elif key.startswith("CLICKHOUSE_"):
            group = "clickhouse"
        else:
            group = "core"
        groups.setdefault(group, []).append((key, value))

    group_labels = {
        "core": "Core Infrastructure",
        "langfuse": "Langfuse",
        "clickhouse": "ClickHouse",
        "llm_port": "llm.port Services",
    }

    for group_key in ["core", "langfuse", "clickhouse", "llm_port"]:
        items = groups.get(group_key, [])
        if not items:
            continue
        lines.append(f"# ── {group_labels.get(group_key, group_key)} ──")
        for k, v in items:
            lines.append(f"{k}={v}")
        lines.append("")

    # Always write LF line endings — .env is consumed by Linux containers.
    output_path.write_bytes("\n".join(lines).encode("utf-8"))
    return output_path


def _timestamp() -> str:
    """Return an ISO timestamp string."""
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
