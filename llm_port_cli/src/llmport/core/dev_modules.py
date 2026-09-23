"""The optional modules -- PII, MCP, skills -- in the dev environment.

``llmport dev up`` runs the backend and gateway on the host. The modules were
only ever wired for the container stack: each was addressed by its Docker
name (``http://llm-port-pii:8000``) and published no port, so a gateway on
the host could not reach one even when it was started by hand, and
``dev up`` had no way to start one at all. The backend's own defaults already
expected them on the host (``http://127.0.0.1:8003/api`` for PII), on the
ports the CLI reserves for them.

So they run the way the backend does: from their service directory, bound to
127.0.0.1 (only the gateway and backend call them), each on its reserved
port. This module works out what that takes -- which modules, their own
settings, and what the gateway and backend need to reach them -- as plain
dicts, so it can be tested without starting anything.
"""

from __future__ import annotations

from llmport.core.registry import MODULES, ModuleInfo

#: Where the host-run modules listen and where the shared infra is reached.
#: Never "localhost": see ``registry.INFRA_LOOPBACK``.
_HOST = "127.0.0.1"


class UnknownModuleError(ValueError):
    """A module name ``dev up`` does not know how to run."""


def dev_modules() -> list[ModuleInfo]:
    """Every module dev mode can run, in registry order."""
    return [m for m in MODULES.values() if m.dev_dir]


def select_modules(requested: str | None, profiles: list[str] | None) -> list[ModuleInfo]:
    """The modules to run.

    *requested* is ``--modules``: a comma-separated list, or ``none``. Without
    it, the modules switched on with ``llmport module enable`` (the config's
    profiles) are the ones that run.
    """
    runnable = {m.name: m for m in dev_modules()}
    if requested is None:
        wanted = [m.name for m in runnable.values() if m.profile in set(profiles or [])]
    else:
        names = [n.strip().lower() for n in requested.split(",") if n.strip()]
        if names in ([], ["none"]):
            return []
        unknown = [n for n in names if n not in runnable]
        if unknown:
            raise UnknownModuleError(
                f"Unknown module(s): {', '.join(unknown)}. "
                f"Available: {', '.join(runnable)}.",
            )
        wanted = names
    return [m for m in runnable.values() if m.name in wanted]


def dev_url(module: ModuleInfo) -> str:
    """The root URL a host-run module answers on."""
    return f"http://{_HOST}:{module.port}"


def service_token(module: ModuleInfo, shared: dict[str, str]) -> str:
    """The token the gateway and backend present to *module*.

    One value for all three sides, or the calls are refused. The shared
    ``.env`` wins, so a workspace that set its own keeps it.
    """
    return shared.get(f"LLM_PORT_{module.name.upper()}_SERVICE_TOKEN") or module.dev_service_token


def module_env(
    module: ModuleInfo,
    *,
    shared: dict[str, str],
    running: list[ModuleInfo],
) -> dict[str, str]:
    """The settings *module* runs with, for its own ``.env``."""
    p = f"LLM_PORT_{module.name.upper()}_"
    env = {
        p + "HOST": _HOST,
        p + "PORT": str(module.port),
        p + "ENVIRONMENT": "dev",
        # One worker: PII loads its spaCy model per worker (~1 GB each), and
        # MCP keeps its stdio connections per worker.
        p + "WORKERS_COUNT": "1",
        # Not reloaded: in this environment they are what the backend and
        # gateway call, not the code being edited, and a reload of PII reloads
        # its model.
        p + "RELOAD": "false",
    }
    if module.database:
        env.update({
            p + "DB_HOST": _HOST,
            p + "DB_PORT": "5432",
            p + "DB_USER": shared.get("POSTGRES_USER", "postgres"),
            p + "DB_PASS": shared.get("POSTGRES_PASSWORD", "postgres"),
            p + "DB_BASE": module.database,
        })
    if module.redis_base is not None:
        env.update({
            p + "REDIS_HOST": _HOST,
            p + "REDIS_PORT": "6379",
            p + "REDIS_BASE": str(module.redis_base),
        })
        redis_pass = shared.get("REDIS_AUTH") or shared.get("REDIS_PASSWORD")
        if redis_pass:
            env[p + "REDIS_PASS"] = redis_pass
    if module.dev_service_token:
        env[p + "SERVICE_TOKEN"] = service_token(module, shared)
    for key, value in module.dev_settings:
        env[p + key] = shared.get(p + key) or value
    by_name = {m.name: m for m in running}
    for key, other in module.dev_links:
        # Empty when the other one is not running: a URL to nothing would
        # make every call wait out a connect timeout instead of skipping.
        env[p + key] = dev_url(by_name[other]) if other in by_name else ""
    return env


def caller_env(
    service: str,
    *,
    shared: dict[str, str],
    running: list[ModuleInfo],
) -> dict[str, str]:
    """What the gateway (``API``) or backend (``BACKEND``) needs to reach them.

    Every module dev mode knows about gets its switch: on with its URL (and
    token) when it runs, off when it does not. Left on while nothing listens,
    the gateway sends each request to it anyway -- and under a PII policy
    that blocks when the service is down, every chat then fails with 502.
    """
    on = {m.name for m in running}
    env: dict[str, str] = {}
    for module in dev_modules():
        p = f"LLM_PORT_{service}_{module.name.upper()}_"
        env[p + "ENABLED"] = "true" if module.name in on else "false"
        if module.name not in on:
            continue
        url = dev_url(module)
        if service == "BACKEND":
            url += module.backend_url_suffix
        env[p + "SERVICE_URL"] = url
        if module.dev_service_token:
            env[p + "SERVICE_TOKEN"] = service_token(module, shared)
    return env
