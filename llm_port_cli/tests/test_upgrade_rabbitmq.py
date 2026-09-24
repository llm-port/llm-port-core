"""An upgrade writes RabbitMQ's users from the .env the services read.

RabbitMQ makes its users from ``rabbitmq/definitions.json`` on every start,
and the services log in with the passwords in ``.env``. Only ``deploy``
wrote the file: an install upgraded on a test VM kept a 13 Sep file with
other passwords, and the gateway and backend were refused ("invalid
credentials") while RabbitMQ itself was healthy.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from llmport.commands import upgrade as upgrade_module
from llmport.core import bootstrap, clickhouse, compose


def _matches(stored: str, password: str) -> bool:
    """RabbitMQ's check: base64(salt || sha256(salt || password))."""
    raw = base64.b64decode(stored)
    return hashlib.sha256(raw[:4] + password.encode()).digest() == raw[4:]


def test_the_upgrade_writes_rabbitmqs_users_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "llm_port_shared"
    (shared / "rabbitmq").mkdir(parents=True)
    (shared / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (shared / ".env").write_text(
        "RABBITMQ_ADMIN_USER=admin\nRABBITMQ_ADMIN_PASS=adminpw\n"
        "RABBITMQ_BACKEND_PASS=backendpw\nRABBITMQ_API_PASS=apipw\n",
        encoding="utf-8",
    )
    stale = {"users": [{"name": "llmport-api", "password_hash": "c3RhbGU=", "tags": ""}]}
    (shared / "rabbitmq" / "definitions.json").write_text(json.dumps(stale), encoding="utf-8")
    config = tmp_path / "llmport.yaml"
    config.write_text(f"version: 1\ninstall_dir: {shared.as_posix()}\ncompose_file: docker-compose.yaml\n",
                      encoding="utf-8")
    monkeypatch.setenv("LLMPORT_CONFIG", str(config))

    # Everything that would reach Docker.
    monkeypatch.setattr(compose, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(compose, "foreign_containers", lambda ctx: [])
    monkeypatch.setattr(upgrade_module, "compose_up", lambda *a, **k: 0)
    monkeypatch.setattr(clickhouse, "drop_stale_logs", lambda ctx: [])
    monkeypatch.setattr(bootstrap, "wait_for_backend", lambda *a, **k: True)
    from llmport.commands import deploy

    monkeypatch.setattr(deploy, "_sync_postgres_password", lambda *a, **k: None)

    result = CliRunner().invoke(upgrade_module.upgrade_cmd, ["-y", "--no-backup", "--no-build", "--skip-doctor"])
    assert result.exit_code == 0, result.output

    env = dict(line.split("=", 1) for line in (shared / ".env").read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.startswith("#"))
    users: dict[str, Any] = {
        u["name"]: u for u in json.loads((shared / "rabbitmq" / "definitions.json").read_text())["users"]
    }
    assert _matches(users["llmport-api"]["password_hash"], env["RABBITMQ_API_PASS"])
    assert _matches(users["llmport-backend"]["password_hash"], env["RABBITMQ_BACKEND_PASS"])
    assert env["RABBITMQ_API_PASS"] == "apipw", "the secrets in .env were kept"
