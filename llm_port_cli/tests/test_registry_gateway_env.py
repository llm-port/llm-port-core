"""Regression tests for the gateway dev-``.env`` Redis credential resolution.

``gateway_dev_env_for`` must surface the shared-``.env`` Redis password to the
gateway as ``LLM_PORT_API_REDIS_PASS``. The key ``dev init`` actually writes is
``REDIS_AUTH`` (the CLI-arg password used by the compose ``--requirepass``
flag); ``REDIS_PASSWORD`` is the historical prod-template alias and is only a
fallback. Root cause this pins: writing ``REDIS_AUTH`` but reading only
``REDIS_PASSWORD`` left the gateway auth-less, so every chat returned 500 with
``Authentication required``.
"""

from __future__ import annotations

from pathlib import Path

from llmport.core.registry import gateway_dev_env_for


def _write_env(tmp_path: Path, text: str) -> Path:
    """Write a shared ``.env`` file and return its path."""
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    return env


def test_redis_auth_key_is_forwarded(tmp_path: Path) -> None:
    """The primary ``REDIS_AUTH`` key written by ``dev init`` is forwarded."""
    path = _write_env(tmp_path, "REDIS_AUTH=secret-pass\nPOSTGRES_USER=llmport\n")
    env = gateway_dev_env_for(path)
    assert env["LLM_PORT_API_REDIS_PASS"] == "secret-pass"


def test_falls_back_to_redis_password_alias(tmp_path: Path) -> None:
    """``REDIS_PASSWORD`` is used when ``REDIS_AUTH`` is absent (prod template)."""
    path = _write_env(tmp_path, "REDIS_PASSWORD=alias-pass\n")
    env = gateway_dev_env_for(path)
    assert env["LLM_PORT_API_REDIS_PASS"] == "alias-pass"


def test_redis_auth_wins_over_alias(tmp_path: Path) -> None:
    """When both are set, ``REDIS_AUTH`` (the real key) takes precedence."""
    path = _write_env(tmp_path, "REDIS_AUTH=primary\nREDIS_PASSWORD=alias\n")
    env = gateway_dev_env_for(path)
    assert env["LLM_PORT_API_REDIS_PASS"] == "primary"


def test_no_redis_key_means_no_pass_set(tmp_path: Path) -> None:
    """With no Redis key in the shared env, no password is injected."""
    path = _write_env(tmp_path, "POSTGRES_USER=llmport\n")
    env = gateway_dev_env_for(path)
    assert "LLM_PORT_API_REDIS_PASS" not in env


def test_missing_shared_env_file_is_tolerated(tmp_path: Path) -> None:
    """A non-existent shared env degrades to the base defaults (no crash)."""
    env = gateway_dev_env_for(tmp_path / "does-not-exist.env")
    assert isinstance(env, dict)
    assert "LLM_PORT_API_REDIS_PASS" not in env
