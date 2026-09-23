"""``dev up`` runs the optional modules on the host, beside the backend.

Before, it could not run them at all: ``llmport module enable`` only recorded
a compose profile for the container stack, and a module started by hand was
addressed by its Docker name with no port published, so the host-run gateway
could not reach it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmport.commands.dev import dev_up
from llmport.core.dev_modules import UnknownModuleError, caller_env, module_env, select_modules
from llmport.core.registry import MODULES

SHARED = {"POSTGRES_USER": "postgres", "POSTGRES_PASSWORD": "devpassword", "REDIS_AUTH": "redispass"}


def _names(modules: list) -> list[str]:
    return [m.name for m in modules]


# ── Which modules run ─────────────────────────────────────────────


def test_modules_switched_on_with_module_enable_run_by_default() -> None:
    assert _names(select_modules(None, ["pii", "skills"])) == ["pii", "skills"]
    assert select_modules(None, None) == []


def test_the_flag_decides_when_given() -> None:
    assert _names(select_modules("mcp, PII", ["skills"])) == ["pii", "mcp"]
    assert select_modules("none", ["pii"]) == []


def test_a_misspelt_module_is_refused_not_ignored() -> None:
    with pytest.raises(UnknownModuleError, match="pi.*Available: pii, mcp, skills"):
        select_modules("pi", None)


# ── What each one runs with ───────────────────────────────────────


def test_a_module_listens_on_its_reserved_port_on_loopback() -> None:
    env = module_env(MODULES["pii"], shared=SHARED, running=[MODULES["pii"]])
    assert env["LLM_PORT_PII_HOST"] == "127.0.0.1"
    assert env["LLM_PORT_PII_PORT"] == "8003"
    assert env["LLM_PORT_PII_WORKERS_COUNT"] == "1"
    assert not any("DB_" in key for key in env), "PII keeps no database"


def test_mcp_gets_its_database_redis_and_the_pii_it_can_reach() -> None:
    mcp, pii = MODULES["mcp"], MODULES["pii"]
    env = module_env(mcp, shared=SHARED, running=[pii, mcp])
    assert env["LLM_PORT_MCP_PORT"] == "8007"
    assert env["LLM_PORT_MCP_DB_BASE"] == "llm_mcp"
    assert env["LLM_PORT_MCP_DB_PASS"] == "devpassword"
    assert env["LLM_PORT_MCP_REDIS_BASE"] == "3"
    assert env["LLM_PORT_MCP_REDIS_PASS"] == "redispass"
    assert env["LLM_PORT_MCP_PII_SERVICE_URL"] == "http://127.0.0.1:8003"

    alone = module_env(mcp, shared=SHARED, running=[mcp])
    assert alone["LLM_PORT_MCP_PII_SERVICE_URL"] == "", "no URL to a PII that is not running"


def test_one_token_on_both_sides_and_the_shared_env_wins() -> None:
    skills = MODULES["skills"]
    shared = {**SHARED, "LLM_PORT_SKILLS_SERVICE_TOKEN": "from-shared"}
    receiver = module_env(skills, shared=shared, running=[skills])["LLM_PORT_SKILLS_SERVICE_TOKEN"]
    gateway = caller_env("API", shared=shared, running=[skills])["LLM_PORT_API_SKILLS_SERVICE_TOKEN"]
    backend = caller_env("BACKEND", shared=shared, running=[skills])["LLM_PORT_BACKEND_SKILLS_SERVICE_TOKEN"]
    assert receiver == gateway == backend == "from-shared"


# ── What the gateway and backend are told ─────────────────────────


def test_the_gateway_and_backend_reach_a_running_module_on_the_host() -> None:
    pii = MODULES["pii"]
    gateway = caller_env("API", shared=SHARED, running=[pii])
    backend = caller_env("BACKEND", shared=SHARED, running=[pii])
    assert gateway["LLM_PORT_API_PII_ENABLED"] == "true"
    assert gateway["LLM_PORT_API_PII_SERVICE_URL"] == "http://127.0.0.1:8003"
    assert backend["LLM_PORT_BACKEND_PII_SERVICE_URL"] == "http://127.0.0.1:8003/api"


def test_a_module_that_does_not_run_is_switched_off() -> None:
    """Left on, the gateway sends every request to a port nothing listens on."""
    gateway = caller_env("API", shared=SHARED, running=[])
    assert gateway["LLM_PORT_API_PII_ENABLED"] == "false"
    assert gateway["LLM_PORT_API_MCP_ENABLED"] == "false"
    assert gateway["LLM_PORT_API_SKILLS_ENABLED"] == "false"
    assert "LLM_PORT_API_PII_SERVICE_URL" not in gateway


# ── Writing it down ───────────────────────────────────────────────


def test_env_values_are_set_in_place_and_the_rest_kept(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# mine\nLLM_PORT_API_PII_ENABLED=false\nLLM_PORT_API_DB_PASS=secret\n", encoding="utf-8")

    changed = dev_up._apply_env(env, {
        "LLM_PORT_API_PII_ENABLED": "true",
        "LLM_PORT_API_PII_SERVICE_URL": "http://127.0.0.1:8003",
    }, header="modules")

    assert changed
    assert env.read_text(encoding="utf-8") == (
        "# mine\nLLM_PORT_API_PII_ENABLED=true\nLLM_PORT_API_DB_PASS=secret\n\n"
        "# ── modules ──\nLLM_PORT_API_PII_SERVICE_URL=http://127.0.0.1:8003\n"
    )
    assert not dev_up._apply_env(env, {"LLM_PORT_API_PII_ENABLED": "true"}, header="modules")


def test_a_module_missing_from_the_workspace_is_skipped(tmp_path: Path) -> None:
    assert not dev_up._module_present(tmp_path, MODULES["pii"])
    (tmp_path / "llm_port_pii").mkdir()
    assert dev_up._module_present(tmp_path, MODULES["pii"])


def test_module_processes_are_recognised_as_this_workspaces_services() -> None:
    for module in ("llm_port_pii", "llm_port_mcp", "llm_port_skills"):
        assert f"uv run -m {module}" in dev_up._SERVICE_INVOCATIONS


def test_what_a_module_needs_is_installed_into_the_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not into the CLI's own virtualenv, which ``uv pip`` otherwise prefers."""
    monkeypatch.setenv("VIRTUAL_ENV", "C:/cli/.venv")
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    class _Done:
        def __init__(self, code: int) -> None:
            self.returncode = code

    def run(args: list[str], *_: object, **kwargs: object) -> _Done:
        calls.append((args, kwargs.get("env")))  # type: ignore[arg-type]
        return _Done(1 if args[:2] == ["uv", "run"] else 0)  # the model is missing

    monkeypatch.setattr(dev_up.subprocess, "run", run)
    dev_up._install_module_deps(tmp_path, MODULES["pii"])

    assert [args[:3] for args, _ in calls] == [
        ["uv", "sync", "--locked"], ["uv", "run", "--no-sync"], ["uv", "pip", "install"],
    ]
    assert all(env is not None and "VIRTUAL_ENV" not in env for _, env in calls)
