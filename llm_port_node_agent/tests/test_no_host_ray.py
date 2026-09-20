"""The host agent must not depend on Ray (Phase 4B, F-04).

On the certified DGX hardware ``import ray`` fails on the host by design -
that is the "zero host pollution" property the whole air-gap direction rests
on.  The agent declared ``ray[serve]==2.58.0`` as a *base* dependency, so
installing it on a bare node pulled Ray onto the host, contradicting the
architecture it was built to serve.

These tests pin the two halves of the fix: Ray is not a base dependency, and
nothing in the package imports it at module scope, so the agent installs and
runs on a node where Ray is simply absent.
"""

from __future__ import annotations

import builtins
import pathlib
import subprocess
import sys

import pytest

_AGENT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Modules that must import on a host with no Ray at all.  The Ray-facing ones
# are included deliberately: their SDK imports are lazy so a missing runtime
# surfaces as a command result, not an ImportError that kills the agent.
_MUST_IMPORT_WITHOUT_RAY = [
    "llm_port_node_agent.collectors",
    "llm_port_node_agent.network",
    "llm_port_node_agent.dispatcher",
    "llm_port_node_agent.ray.container",
    "llm_port_node_agent.ray.manager",
    "llm_port_node_agent.ray.runtime",
    "llm_port_node_agent.ray.core",
    "llm_port_node_agent.ray.serve",
    "llm_port_node_agent.ray.state",
    "llm_port_node_agent.runtimes.docker",
]


def test_ray_is_not_a_base_dependency() -> None:
    """A bare-node install must not pull Ray onto the host."""
    pyproject = (_AGENT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    base = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert "ray" not in base, f"Ray must not be a base dependency; found in:\n{base}"

    # It stays available for nodes that do bootstrap Ray on the host.
    assert "ray-host = [" in pyproject
    assert 'ray[serve]==2.58.0' in pyproject


@pytest.mark.parametrize("module_name", _MUST_IMPORT_WITHOUT_RAY)
def test_modules_import_with_ray_unavailable(module_name: str) -> None:
    """Every ``import ray`` in the package must be lazy.

    Run in a fresh interpreter with a meta-path hook that makes ``import ray``
    raise exactly as it does on the DGX hosts.  A subprocess rather than an
    in-process monkeypatch on purpose: re-importing these modules inside the
    test session would leave the re-imported objects bound on the parent
    package and break every other test that already holds the originals.
    """
    probe = f"""
import sys

class _NoRay:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)

    def find_spec(self, name, path=None, target=None):
        if name == "ray" or name.startswith("ray."):
            raise ModuleNotFoundError("No module named 'ray'")
        return None

sys.meta_path.insert(0, _NoRay())
for cached in [m for m in list(sys.modules) if m == "ray" or m.startswith("ray.")]:
    del sys.modules[cached]

try:
    import ray
except ModuleNotFoundError:
    pass
else:
    raise SystemExit("probe is broken: ray was importable")

import importlib
importlib.import_module({module_name!r})
print("OK")
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_AGENT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"{module_name} does not import without host Ray:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    assert "OK" in completed.stdout


@pytest.mark.anyio()
async def test_containerized_ensure_runtime_needs_no_host_ray(tmp_path, monkeypatch) -> None:
    """The Phase 4B exit criterion, with the host import actually broken."""
    from llm_port_node_agent.event_buffer import EventBuffer
    from llm_port_node_agent.ray.container import RayContainerRuntime
    from llm_port_node_agent.ray.manager import RayManager
    from llm_port_node_agent.state_store import StateStore

    from tests.test_ray_container import _FakeRuntime, _payload

    real_import = builtins.__import__

    def _blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ray" or name.startswith("ray."):
            raise ModuleNotFoundError("No module named 'ray'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    manager = RayManager(
        state_store=StateStore(tmp_path / "state.json"),
        events=EventBuffer(),
        ray_base_path=str(tmp_path / "ray-base"),
        token_dir=tmp_path / "ray-tokens",
    )
    manager._container = RayContainerRuntime(runtime=_FakeRuntime())

    result = await manager.ensure_runtime({"version": "2.58.0", "runtime_bundle": _payload()})
    assert result["installed"] is True
    assert result["runtime"] == "container"
