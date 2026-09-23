"""The agent's version is declared once, and every copy agrees with it.

Before this, 0.1.8 named several different binaries -- the licence bundling,
the keepalive fix and the resumable transfer all shipped under it -- so the
version an operator saw in the console said nothing about which build a node
was actually running.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import llm_port_node_agent
from llm_port_node_agent import __main__ as agent_main

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_the_package_version_is_the_project_version() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert llm_port_node_agent.__version__ == project["version"]


def test_the_cli_reports_the_package_version() -> None:
    assert agent_main._AGENT_VERSION == llm_port_node_agent.__version__
