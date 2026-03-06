"""Central registry — single source of truth for llm.port metadata.

Every component (repos, ports, modules, services, databases, dev env vars)
is defined exactly **once** here.  Consumer modules import from this file
instead of hard-coding values.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── GitHub organisation ───────────────────────────────────────────

GITHUB_ORG = "llm-port"


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
    RepoInfo("llm-port-rag", "llm_port_rag", "RAG service"),
    RepoInfo("llm-port-pii", "llm_port_pii", "PII redaction service"),
    RepoInfo("llm-port-auth", "llm_port_auth", "Auth service"),
    RepoInfo("llm-port-mailer", "llm_port_mailer", "Mailer service"),
    RepoInfo("llm-port-docling", "llm_port_docling", "Document parsing service"),
    RepoInfo("llm-port-shared", "llm_port_shared", "Shared infra (compose)"),
    RepoInfo("llm-port-dev", "llm_port_dev", "Dev tooling & docs"),
    RepoInfo("llm-port-cli", "llm_port_cli", "CLI"),
    RepoInfo(".github", ".github", "GitHub profile & CI"),
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


MODULES: dict[str, ModuleInfo] = {
    "rag": ModuleInfo(
        name="rag",
        profile="rag",
        description="Retrieval-Augmented Generation — document ingestion, vector search",
        container="llm-port-rag",
        port=8002,
        service_url="http://llm-port-rag:8000",
    ),
    "pii": ModuleInfo(
        name="pii",
        profile="pii",
        description="PII detection and redaction (Presidio + spaCy)",
        container="llm-port-pii",
        port=8003,
        service_url="http://llm-port-pii:8000",
    ),
    "auth": ModuleInfo(
        name="auth",
        profile="auth",
        description="External authentication provider (Keycloak)",
        container="llm-port-auth",
        port=8004,
        service_url="http://llm-port-auth:8000",
    ),
    "mailer": ModuleInfo(
        name="mailer",
        profile="mailer",
        description="Email notifications",
        container="llm-port-mailer",
        port=8005,
        service_url="http://llm-port-mailer:8000",
    ),
    "docling": ModuleInfo(
        name="docling",
        profile="docling",
        description="Document parsing & conversion (Docling)",
        container="llm-port-docling",
        port=8006,
        service_url="http://llm-port-docling:8000",
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
    (8002, "RAG Service"),
    (8003, "PII Service"),
    (8004, "Auth Service"),
    (8005, "Mailer Service"),
    (8006, "Docling Service"),
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
    "rag",
    "pii",
    "langfuse",
]

POSTGRES_CONTAINER = "llm-port-postgres"


# ── Dev backend env vars ─────────────────────────────────────────

BACKEND_DEV_ENV: dict[str, str] = {
    "LLM_PORT_BACKEND_HOST": "localhost",
    "LLM_PORT_BACKEND_PORT": "8000",
    "LLM_PORT_BACKEND_RELOAD": "true",
    "LLM_PORT_BACKEND_DB_HOST": "localhost",
    "LLM_PORT_BACKEND_DB_PORT": "5432",
    "LLM_PORT_BACKEND_DB_USER": "llm_port_backend",
    "LLM_PORT_BACKEND_DB_PASS": "llm_port_backend",
    "LLM_PORT_BACKEND_DB_BASE": "llm_port_backend",
    "LLM_PORT_BACKEND_RABBIT_HOST": "localhost",
    "LLM_PORT_BACKEND_RABBIT_PORT": "5672",
    "LLM_PORT_BACKEND_RABBIT_USER": "guest",
    "LLM_PORT_BACKEND_RABBIT_PASS": "guest",
    "LLM_PORT_BACKEND_RABBIT_VHOST": "/",
    "LLM_PORT_BACKEND_SETTINGS_MASTER_KEY": "dev-settings-master-key-change-me",
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
]


# ── Dev endpoint summary (for ``llmport dev up``) ────────────────

DEV_ENDPOINTS: list[tuple[str, str]] = [
    ("Backend", "http://localhost:8000"),
    ("API Docs", "http://localhost:8000/api/docs"),
    ("Worker", "Taskiq (RabbitMQ)"),
    ("Frontend", "http://localhost:5173"),
    ("Grafana", "http://localhost:3001"),
    ("pgAdmin", "http://localhost:5050"),
    ("RabbitMQ", "http://localhost:15672"),
    ("LLM API", "http://localhost:8001"),
    ("RAG API", "http://localhost:8002"),
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


def module_env_vars(profile: str) -> dict[str, str]:
    """Return the env vars needed to enable a module by profile name.

    Used by ``env_gen.dev_env_vars()`` and ``env_gen.default_env_vars()``
    to inject module-specific settings into generated ``.env`` files.
    """
    mod = MODULES.get(profile)
    if not mod or not mod.service_url:
        return {}
    env: dict[str, str] = {}
    if profile == "rag":
        env["LLM_PORT_BACKEND_RAG_ENABLED"] = "true"
        env["LLM_PORT_API_RAG_ENABLED"] = "true"
        env["LLM_PORT_API_RAG_SERVICE_URL"] = mod.service_url
    elif profile == "pii":
        env["LLM_PORT_API_PII_ENABLED"] = "true"
        env["LLM_PORT_API_PII_SERVICE_URL"] = mod.service_url
    elif profile == "auth":
        env["LLM_PORT_API_AUTH_ENABLED"] = "true"
        env["LLM_PORT_API_AUTH_SERVICE_URL"] = mod.service_url
    return env


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


def extend_backend_dev_env(extra: dict[str, str]) -> None:
    """Merge extra env vars into ``BACKEND_DEV_ENV`` (idempotent).

    Existing keys are **not** overwritten.  This allows the EE CLI
    overlay to inject ``LLM_PORT_EE_*`` variables that will be written
    to the backend ``.env`` file by ``dev init`` and ``dev up``.
    """
    for key, value in extra.items():
        BACKEND_DEV_ENV.setdefault(key, value)
