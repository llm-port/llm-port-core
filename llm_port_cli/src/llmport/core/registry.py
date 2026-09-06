"""Central registry — single source of truth for llm.port metadata.

Every component (repos, ports, modules, services, databases, dev env vars)
is defined exactly **once** here.  Consumer modules import from this file
instead of hard-coding values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# ── GitHub organisation ───────────────────────────────────────────

GITHUB_ORG = "llm-port"
CORE_REPO = "llm-port-core"

# ── Container registry ───────────────────────────────────────────

DEFAULT_REGISTRY = "ghcr.io/llm-port"


# ── Repository definitions ───────────────────────────────────────


@dataclass(frozen=True)
class RepoInfo:
    """Metadata for a single llm.port repository."""

    github_name: str  # e.g. "llm-port-backend"
    local_dir: str  # e.g. "llm_port_backend"
    description: str = ""


REPOS: list[RepoInfo] = [
    # ── Core platform ─────────────────────────────────────────
    RepoInfo("llm-port-backend", "llm_port_backend", "Backend API server"),
    RepoInfo("llm-port-frontend", "llm_port_frontend", "React frontend"),
    RepoInfo("llm-port-api", "llm_port_api", "LLM Gateway API"),
    RepoInfo("llm-port-pii", "llm_port_pii", "PII redaction service"),
    RepoInfo("llm-port-mcp", "llm_port_mcp", "MCP Tool Registry"),
    RepoInfo("llm-port-skills", "llm_port_skills", "Skills Registry"),
    RepoInfo("llm-port-shared", "llm_port_shared", "Shared infra (compose)"),
    RepoInfo("llm-port-cli", "llm_port_cli", "CLI"),
]

REPO_DIR_MAP: dict[str, str] = {r.github_name: r.local_dir for r in REPOS}
"""Map GitHub repo name → local directory name."""

REPO_NAMES: list[str] = [r.github_name for r in REPOS]
"""All core repository names."""


# ── Module definitions ────────────────────────────────────────────


@dataclass(frozen=True)
class ModuleInfo:
    """An optional module that can be toggled via compose profiles."""

    name: str
    profile: str
    description: str
    container: str = ""
    port: int = 0
    service_url: str = ""
    env_vars: tuple[tuple[str, str], ...] = ()


MODULES: dict[str, ModuleInfo] = {
    "pii": ModuleInfo(
        name="pii",
        profile="pii",
        description="PII detection and redaction (Presidio + spaCy)",
        container="llm-port-pii",
        port=8003,
        service_url="http://llm-port-pii:8000",
        env_vars=(
            ("LLM_PORT_API_PII_ENABLED", "true"),
            ("LLM_PORT_API_PII_SERVICE_URL", "http://llm-port-pii:8000"),
        ),
    ),
    "mcp": ModuleInfo(
        name="mcp",
        profile="mcp",
        description="MCP Tool Registry (governed MCP server broker)",
        container="llm-port-mcp",
        port=8007,
        service_url="http://llm-port-mcp:8000",
        env_vars=(
            ("LLM_PORT_API_MCP_ENABLED", "true"),
            ("LLM_PORT_API_MCP_SERVICE_URL", "http://llm-port-mcp:8000"),
            ("LLM_PORT_BACKEND_MCP_ENABLED", "true"),
            ("LLM_PORT_BACKEND_MCP_SERVICE_URL", "http://llm-port-mcp:8000"),
        ),
    ),
    "skills": ModuleInfo(
        name="skills",
        profile="skills",
        description="Skills Registry (centralized LLM instruction sets)",
        container="llm-port-skills",
        port=8008,
        service_url="http://llm-port-skills:8000",
        env_vars=(
            ("LLM_PORT_API_SKILLS_ENABLED", "true"),
            ("LLM_PORT_API_SKILLS_SERVICE_URL", "http://llm-port-skills:8000"),
            ("LLM_PORT_BACKEND_SKILLS_ENABLED", "true"),
            ("LLM_PORT_BACKEND_SKILLS_SERVICE_URL", "http://llm-port-skills:8000"),
        ),
    ),
}

# Legacy dict-of-dicts shape consumed by commands/module.py
MODULES_COMPAT: dict[str, dict[str, str]] = {
    name: {"profile": m.profile, "description": m.description}
    for name, m in MODULES.items()
}


# ── Port registry ────────────────────────────────────────────────

KNOWN_PORTS: list[tuple[int, str]] = [
    # Core infrastructure
    (5432, "PostgreSQL"),
    (5672, "RabbitMQ AMQP"),
    (15672, "RabbitMQ Management"),
    (6379, "Redis"),
    (9000, "MinIO API"),
    (9001, "MinIO Console"),
    # Observability
    (3001, "Grafana"),
    (3100, "Loki"),
    (12345, "Alloy (OpenTelemetry)"),
    # Application services
    (8000, "Backend"),
    (5173, "Frontend (dev)"),
    (8001, "API Gateway"),
    (8003, "PII Service"),
    (8007, "MCP Tool Registry"),
    (8008, "Skills Registry"),
    # Tools
    (5050, "pgAdmin"),
    (3000, "Langfuse"),
    (8123, "ClickHouse HTTP"),
    (9181, "ClickHouse Native"),
]


# ── Database registry ────────────────────────────────────────────

DATABASES: list[str] = [
    "llm_port_backend",
    "llm_api",
    "pii",
    "llm_mcp",
    "llm_skills",
    "langfuse",
]

POSTGRES_CONTAINER = "llm-port-postgres"

# Services that use pre-built public images and should be started in dev mode.
# Application services (backend, frontend, api) are run natively on the host.
INFRA_SERVICES: list[str] = [
    "postgres",
    "redis",
    "minio",
    "clickhouse",
    "langfuse-worker",
    "langfuse-web",
    "llm-port-rmq",
    "loki",
    "grafana",
    "alloy",
]


# ── Dev backend env vars ─────────────────────────────────────────

BACKEND_DEV_ENV: dict[str, str] = {
    # Bind the dev backend to all interfaces so it is reachable from
    # other machines on the LAN (remote dev server, node agents,
    # external API clients). Override with LLM_PORT_BACKEND_HOST=
    # 127.0.0.1 in the local .env to keep it local-only.
    "LLM_PORT_BACKEND_HOST": "0.0.0.0",
    "LLM_PORT_BACKEND_PORT": "8000",
    # Session cookies must not carry the Secure flag in dev: browsers
    # drop Secure cookies over plain HTTP, which would silently break
    # login on a remote LAN access (HTTP, no TLS). Prod keeps the
    # default (True) behind TLS/nginx.
    "LLM_PORT_BACKEND_COOKIE_SECURE": "false",
    # The dev API gateway runs on the host's port 8001.
    "LLM_PORT_BACKEND_GATEWAY_URL": "http://127.0.0.1:8001",
    "LLM_PORT_BACKEND_RELOAD": "true",
    "LLM_PORT_BACKEND_DB_HOST": "localhost",
    "LLM_PORT_BACKEND_DB_PORT": "5432",
    "LLM_PORT_BACKEND_DB_USER": "llm_port_backend",
    "LLM_PORT_BACKEND_DB_PASS": "llm_port_backend",
    "LLM_PORT_BACKEND_DB_BASE": "llm_port_backend",
    "LLM_PORT_BACKEND_RABBIT_HOST": "localhost",
    "LLM_PORT_BACKEND_RABBIT_PORT": "5672",
    # Matches the per-service AMQP user created in
    # llm_port_shared/rabbitmq/definitions.json by `llmport dev init`
    # (see backend_dev_env_for for the password).
    "LLM_PORT_BACKEND_RABBIT_USER": "llmport-backend",
    "LLM_PORT_BACKEND_RABBIT_PASS": "devpassword",
    "LLM_PORT_BACKEND_RABBIT_VHOST": "/",
    "LLM_PORT_BACKEND_SETTINGS_MASTER_KEY": "dev-settings-master-key-change-me",
}


#: Default env for the API gateway dev process (``llmport dev up``).
#:
#: The gateway is the OpenAI-compatible ``/v1`` edge. In dev it runs as a
#: host process on port 8001 (bound to all interfaces, mirroring the
#: backend) and points at the shared 127.0.0.1-published infra (postgres
#: / redis / rabbit). Chat-file uploads go to a host-local directory
#: instead of the containerized ``/data/llm-port`` volume.
GATEWAY_DEV_ENV: dict[str, str] = {
    "LLM_PORT_API_HOST": "0.0.0.0",
    "LLM_PORT_API_PORT": "8001",
    "LLM_PORT_API_DB_HOST": "localhost",
    "LLM_PORT_API_DB_PORT": "5432",
    "LLM_PORT_API_REDIS_HOST": "localhost",
    "LLM_PORT_API_REDIS_PORT": "6379",
    "LLM_PORT_API_RABBIT_HOST": "localhost",
    "LLM_PORT_API_RABBIT_PORT": "5672",
    "LLM_PORT_API_RAG_LITE_BACKEND_URL": "http://127.0.0.1:8000",
    "LLM_PORT_API_CHAT_FILE_STORE_ROOT": "~/.llmport-dev/chat-files",
}

# ── Dev process patterns (for status checks) ─────────────────────


@dataclass(frozen=True)
class DevProcess:
    """A dev-mode process pattern for ``llmport dev status``."""

    name: str
    pattern: str
    url: str = "—"


DEV_PROCESSES: list[DevProcess] = [
    DevProcess("Backend", "llm_port_backend", "http://localhost:8000"),
    DevProcess("Worker", "taskiq worker", "—"),
    DevProcess("Frontend", "npm run dev", "http://localhost:5173"),
    DevProcess("API gateway", "llm_port_api", "http://localhost:8001"),
]


# ── Dev endpoint summary (for ``llmport dev up``) ────────────────

DEV_ENDPOINTS: list[tuple[str, str]] = [
    ("Backend", "http://localhost:8000"),
    ("API Docs", "http://localhost:8000/api/docs"),
    ("Worker", "Taskiq (RabbitMQ)"),
    ("Frontend", "http://localhost:5173"),
    ("Grafana", "http://localhost:3001"),
    ("RabbitMQ", "http://localhost:15672"),
    ("LLM API", "http://localhost:8001"),
    ("MCP Registry", "http://localhost:8007"),
    ("Skills Registry", "http://localhost:8008"),
]


# ── Convenience helpers ──────────────────────────────────────────


def repo_clone_url(repo: str, *, method: str = "https", token: str = "") -> str:
    """Return the git clone URL for a repo in the llm-port org.

    When *token* is provided and *method* is ``"https"``, the token is
    embedded in the URL so that ``git clone`` can access private repos.
    """
    if method == "ssh":
        return f"git@github.com:{GITHUB_ORG}/{repo}.git"
    if token:
        return f"https://x-access-token:{token}@github.com/{GITHUB_ORG}/{repo}.git"
    return f"https://github.com/{GITHUB_ORG}/{repo}"


def core_repo_clone_url(*, method: str = "https", token: str = "") -> str:
    """Return the git clone URL for the core monorepo."""
    return repo_clone_url(CORE_REPO, method=method, token=token)


def module_env_vars(profile: str) -> dict[str, str]:
    """Return the env vars needed to enable a module by profile name.

    Used by ``env_gen.dev_env_vars()`` and ``env_gen.default_env_vars()``
    to inject module-specific settings into generated ``.env`` files.

    Each ``ModuleInfo`` carries its own ``env_vars`` tuple so that
    enterprise modules registered via ``extend_modules()`` are handled
    automatically — no hard-coded profile branches needed.
    """
    mod = MODULES.get(profile)
    if not mod:
        return {}
    return dict(mod.env_vars)


# ── Extension API (used by llmport-ee to layer on EE data) ────────


def _rebuild_derived() -> None:
    """Rebuild derived dicts/lists after extending REPOS."""
    REPO_DIR_MAP.clear()
    REPO_DIR_MAP.update({r.github_name: r.local_dir for r in REPOS})
    REPO_NAMES.clear()
    REPO_NAMES.extend(r.github_name for r in REPOS)
    MODULES_COMPAT.clear()
    MODULES_COMPAT.update(
        {name: {"profile": m.profile, "description": m.description} for name, m in MODULES.items()}
    )


def extend_repos(extra: list[RepoInfo]) -> None:
    """Append repositories to the registry (idempotent)."""
    existing = {r.github_name for r in REPOS}
    for repo in extra:
        if repo.github_name not in existing:
            REPOS.append(repo)
            existing.add(repo.github_name)
    _rebuild_derived()


def extend_modules(extra: dict[str, ModuleInfo]) -> None:
    """Add modules to the registry (idempotent)."""
    for name, mod in extra.items():
        if name not in MODULES:
            MODULES[name] = mod
    _rebuild_derived()


def extend_ports(extra: list[tuple[int, str]]) -> None:
    """Append ports to the known-ports list (idempotent)."""
    existing = {p for p, _ in KNOWN_PORTS}
    for port, label in extra:
        if port not in existing:
            KNOWN_PORTS.append((port, label))
            existing.add(port)


def extend_databases(extra: list[str]) -> None:
    """Append database names to the registry (idempotent)."""
    existing = set(DATABASES)
    for db in extra:
        if db not in existing:
            DATABASES.append(db)
            existing.add(db)


def extend_dev_endpoints(extra: list[tuple[str, str]]) -> None:
    """Append dev endpoint entries (idempotent by name)."""
    existing = {name for name, _ in DEV_ENDPOINTS}
    for name, url in extra:
        if name not in existing:
            DEV_ENDPOINTS.append((name, url))
            existing.add(name)


def extend_dev_processes(extra: list[DevProcess]) -> None:
    """Append dev process patterns (idempotent by name)."""
    existing = {p.name for p in DEV_PROCESSES}
    for proc in extra:
        if proc.name not in existing:
            DEV_PROCESSES.append(proc)
            existing.add(proc.name)


def _read_env_values(path: Path) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` env file into a dict."""
    path = Path(path)
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def backend_dev_env_for(shared_env_path: Path) -> dict[str, str]:
    """Build the backend dev ``.env`` with correct RabbitMQ credentials.

    The broker users are loaded from
    ``llm_port_shared/rabbitmq/definitions.json``, which dev init regenerates
    from the shared ``.env`` passwords. The backend must therefore log in as
    ``llmport-backend`` with ``RABBITMQ_BACKEND_PASS`` — ``BACKEND_DEV_ENV``'s
    historical ``guest/guest`` default does not exist once definitions are
    loaded (RabbitMQ reports ``ACCESS_REFUSED``).
    """
    env = dict(BACKEND_DEV_ENV)
    shared = _read_env_values(shared_env_path)
    backend_pass = shared.get("RABBITMQ_BACKEND_PASS")
    if backend_pass:
        env["LLM_PORT_BACKEND_RABBIT_USER"] = "llmport-backend"
        env["LLM_PORT_BACKEND_RABBIT_PASS"] = backend_pass
        return env
    # Shared env predates per-service users — the only account guaranteed to
    # exist on the broker is the one the shared env configures for it.
    if shared.get("RABBITMQ_ADMIN_USER") and shared.get("RABBITMQ_ADMIN_PASS"):
        env["LLM_PORT_BACKEND_RABBIT_USER"] = shared["RABBITMQ_ADMIN_USER"]
        env["LLM_PORT_BACKEND_RABBIT_PASS"] = shared["RABBITMQ_ADMIN_PASS"]
    return env


def gateway_dev_env_for(shared_env_path: Path) -> dict[str, str]:
    """Build the gateway dev ``.env`` with credentials from the shared env.

    Mirrors :func:`backend_dev_env_for`: DB credentials come from
    ``POSTGRES_USER`` / ``POSTGRES_PASSWORD`` in
    ``llm_port_shared/.env``, the RabbitMQ broker login from
    ``RABBITMQ_API_USER`` / ``RABBITMQ_API_PASS`` (falling back to the
    admin pair for pre-per-service-user envs), and the Redis password
    from ``REDIS_PASSWORD``. Returns ``GATEWAY_DEV_ENV`` with the
    defaults when the shared env does not exist.
    """
    env = dict(GATEWAY_DEV_ENV)
    shared = _read_env_values(shared_env_path)
    if not shared:
        return env
    if shared.get("POSTGRES_USER"):
        env["LLM_PORT_API_DB_USER"] = shared["POSTGRES_USER"]
    if shared.get("POSTGRES_PASSWORD"):
        env["LLM_PORT_API_DB_PASS"] = shared["POSTGRES_PASSWORD"]
    if shared.get("RABBITMQ_API_USER"):
        env["LLM_PORT_API_RABBIT_USER"] = shared["RABBITMQ_API_USER"]
        if shared.get("RABBITMQ_API_PASS"):
            env["LLM_PORT_API_RABBIT_PASS"] = shared["RABBITMQ_API_PASS"]
    elif shared.get("RABBITMQ_ADMIN_USER") and shared.get("RABBITMQ_ADMIN_PASS"):
        env["LLM_PORT_API_RABBIT_USER"] = shared["RABBITMQ_ADMIN_USER"]
        env["LLM_PORT_API_RABBIT_PASS"] = shared["RABBITMQ_ADMIN_PASS"]
    if shared.get("REDIS_PASSWORD"):
        env["LLM_PORT_API_REDIS_PASS"] = shared["REDIS_PASSWORD"]
    return env


def extend_backend_dev_env(extra: dict[str, str]) -> None:
    """Merge extra env vars into ``BACKEND_DEV_ENV`` (idempotent).

    Existing keys are **not** overwritten.  This allows the EE CLI
    overlay to inject ``LLM_PORT_EE_*`` variables that will be written
    to the backend ``.env`` file by ``dev init`` and ``dev up``.
    """
    for key, value in extra.items():
        BACKEND_DEV_ENV.setdefault(key, value)
