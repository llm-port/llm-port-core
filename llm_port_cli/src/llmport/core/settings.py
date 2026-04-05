"""Configuration file (llmport.yaml) read/write utilities.

The CLI persists its state in a YAML file located at:
  - ``$LLMPORT_CONFIG`` env var (if set)
  - ``<install_dir>/llmport.yaml``
  - ``%LOCALAPPDATA%/llmport/llmport.yaml`` (Windows)
  - ``~/.config/llmport/llmport.yaml`` (Linux/macOS)

The schema is intentionally flat:  top-level scalars for the common
case, with a ``dev:`` section for dev-mode specifics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from llmport.core.registry import (
    REPO_DIR_MAP,
    REPO_NAMES,
    repo_clone_url,
)

# Re-export for backward compatibility
__all__ = ["REPO_DIR_MAP", "repo_clone_url"]


# ── Default paths ─────────────────────────────────────────────────


def _default_config_dir() -> Path:
    """Return platform-appropriate config directory."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "llmport"
    return Path.home() / ".config" / "llmport"


_DEFAULT_CONFIG_DIR = _default_config_dir()
_DEFAULT_CONFIG_FILE = _DEFAULT_CONFIG_DIR / "llmport.yaml"


@dataclass
class DevConfig:
    """Dev-mode specific configuration."""

    workspace_dir: str = ""
    clone_method: str = "https"  # "https" or "ssh"
    branch: str = "master"
    repos: list[str] = field(default_factory=lambda: list(REPO_NAMES))
    github_token: str = ""  # GitHub PAT or OAuth token for private repos


@dataclass
class LlmportConfig:
    """Top-level CLI configuration."""

    version: int = 1
    install_dir: str = ""
    compose_file: str = "docker-compose.yaml"
    compose_dev_file: str = "docker-compose.dev.yaml"
    profiles: list[str] = field(default_factory=list)
    admin_email: str = ""
    api_url: str = "http://localhost:8000"
    api_token: str = ""
    dev: DevConfig = field(default_factory=DevConfig)

    # ── Derived paths ─────────────────────────────────────────

    @property
    def install_path(self) -> Path:
        """Resolved install directory."""
        return Path(self.install_dir).expanduser().resolve() if self.install_dir else Path.cwd()

    @property
    def compose_path(self) -> Path:
        """Path to the main docker-compose file."""
        return self.install_path / self.compose_file

    @property
    def compose_dev_path(self) -> Path:
        """Path to the dev overlay docker-compose file."""
        return self.install_path / self.compose_dev_file

    @property
    def env_path(self) -> Path:
        """Path to the .env file."""
        return self.install_path / ".env"

    @property
    def dev_workspace_path(self) -> Path:
        """Resolved dev workspace directory."""
        if self.dev.workspace_dir:
            return Path(self.dev.workspace_dir).expanduser().resolve()
        return Path.cwd()


# ── Read/write ────────────────────────────────────────────────────


def config_path() -> Path:
    """Return the active config file path."""
    env = os.environ.get("LLMPORT_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_CONFIG_FILE


def load_config() -> LlmportConfig:
    """Load the config from disk, or return defaults if missing."""
    path = config_path()
    if not path.exists():
        return LlmportConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _from_dict(data)


def save_config(cfg: LlmportConfig) -> Path:
    """Write the config to disk.  Creates parent dirs as needed."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(_to_dict(cfg), default_flow_style=False, sort_keys=False), encoding="utf-8")
    return path


# ── Serialisation helpers ─────────────────────────────────────────


def _from_dict(data: dict[str, Any]) -> LlmportConfig:
    """Build a ``LlmportConfig`` from a raw dict."""
    dev_data = data.pop("dev", {}) or {}
    dev = DevConfig(**{k: v for k, v in dev_data.items() if k in DevConfig.__dataclass_fields__})
    cfg = LlmportConfig(
        **{k: v for k, v in data.items() if k in LlmportConfig.__dataclass_fields__ and k != "dev"},
        dev=dev,
    )
    return cfg


def _to_dict(cfg: LlmportConfig) -> dict[str, Any]:
    """Serialise a ``LlmportConfig`` to a plain dict for YAML."""
    from dataclasses import asdict

    return asdict(cfg)
