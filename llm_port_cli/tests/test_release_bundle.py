"""Installing without a source checkout: the deployment files the CLI carries.

``pip install llmport-cli`` then ``llmport deploy`` used to stop at "Cannot
find llm_port_shared": the compose file and what it mounts lived only in the
repository. The CLI now carries them (``hatch_build.py``), unpacks them into
an install, and runs the published images of its own version; ``upgrade``
with a newer CLI moves the install to the newer release.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from llmport import __version__
from llmport.commands import deploy as deploy_module
from llmport.commands import upgrade as upgrade_module
from llmport.core import bootstrap, bundle, clickhouse, compose

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "llm_port_shared"


def _hook():  # noqa: ANN202 - the build hook module, loaded from its file
    spec = importlib.util.spec_from_file_location("hatch_build", REPO / "llm_port_cli" / "hatch_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def carried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The files a built CLI carries, as the build hook selects them."""
    root = tmp_path / "bundle"
    for path, rel in _hook().bundle_files(SHARED):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, root / rel)
    monkeypatch.setattr(bundle, "bundled_files", lambda: root)
    monkeypatch.setattr(deploy_module, "bundled_files", lambda: root)
    return root


def _env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )


def test_a_release_carries_the_deployment_and_nothing_an_install_made() -> None:
    shipped = {rel for _path, rel in _hook().bundle_files(SHARED)}
    assert {"docker-compose.yaml", "nginx/nginx.conf", "initdb/01-init.sql",
            "clickhouse/config.d/system-logs.xml", "prometheus/prometheus.yml"} <= shipped
    for never in (".env", ".bootstrap-credentials", "rabbitmq/definitions.json", "prometheus/targets.json",
                  "docker-compose.dev.yaml", "base/Dockerfile"):
        assert never not in shipped, never
    assert not [rel for rel in shipped if rel.startswith("backups/")]
    assert [rel for rel in shipped if rel.startswith("agent-binaries/")] == ["agent-binaries/.gitignore"]


def test_unpacking_keeps_what_the_install_made(carried: Path, tmp_path: Path) -> None:
    install = tmp_path / "llm-port"
    written = bundle.unpack(install)
    assert "docker-compose.yaml" in written
    assert (install / "prometheus" / "targets.json").read_text() == "[]\n", "mounted: must exist as a file"
    assert bundle.installed_release(install) == __version__

    (install / ".env").write_text("POSTGRES_PASSWORD=keep\n", encoding="utf-8")
    (install / "prometheus" / "targets.json").write_text('[{"targets": ["x"]}]', encoding="utf-8")
    (install / "nginx" / "nginx.conf").write_text("edited", encoding="utf-8")

    again = bundle.unpack(install)
    assert again == ["nginx/nginx.conf"], "only the release's own file that changed"
    assert (install / ".env").read_text() == "POSTGRES_PASSWORD=keep\n"
    assert "targets" in (install / "prometheus" / "targets.json").read_text()


def _no_docker(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    monkeypatch.setattr(compose, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(compose, "foreign_containers", lambda ctx: [])
    monkeypatch.setattr(compose, "pull", lambda ctx, **k: calls.append("pull") or 0)
    monkeypatch.setattr(upgrade_module, "compose_up", lambda *a, **k: calls.append("up") or 0)
    monkeypatch.setattr(upgrade_module, "compose_build", lambda *a, **k: calls.append("build") or 0)
    monkeypatch.setattr(upgrade_module, "build_base_image", lambda *a, **k: calls.append("build") or 0)
    monkeypatch.setattr(clickhouse, "drop_stale_logs", lambda ctx: [])
    monkeypatch.setattr(bootstrap, "wait_for_backend", lambda *a, **k: True)
    monkeypatch.setattr(deploy_module, "_sync_postgres_password", lambda *a, **k: None)


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, install: Path) -> None:
    config = tmp_path / "llmport.yaml"
    config.write_text(f"version: 1\ninstall_dir: {install.as_posix()}\ncompose_file: docker-compose.yaml\n",
                      encoding="utf-8")
    monkeypatch.setenv("LLMPORT_CONFIG", str(config))


def test_an_upgrade_moves_a_release_install_to_this_release(
    carried: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = tmp_path / "llm-port"
    bundle.unpack(install)
    (install / bundle.MARKER).write_text(json.dumps({"version": "0.0.1"}), encoding="utf-8")
    (install / ".env").write_text(
        "VERSION=0.0.1\nPOSTGRES_PASSWORD=pgpw\nHF_CACHE_DIR=/models/hf\nGRAFANA_ADMIN_USER=ops@example.com\n"
        "LLM_PORT_HTTP_PORT=8080\n",
        encoding="utf-8",
    )
    _configure(tmp_path, monkeypatch, install)
    calls: list[str] = []
    _no_docker(monkeypatch, calls)

    result = CliRunner().invoke(upgrade_module.upgrade_cmd, ["-y", "--no-backup", "--skip-doctor"])
    assert result.exit_code == 0, result.output

    env = _env(install / ".env")
    assert env["VERSION"] == __version__, "the images move to this release"
    assert env["POSTGRES_PASSWORD"] == "pgpw"
    # What deploy and the operator set survives -- it used to be dropped.
    assert env["HF_CACHE_DIR"] == "/models/hf"
    assert env["GRAFANA_ADMIN_USER"] == "ops@example.com"
    assert env["LLM_PORT_HTTP_PORT"] == "8080"
    assert "REDIS_AUTH" in env, "keys a release introduces are added"
    assert calls == ["pull", "up"], "published images are pulled, not built"
    assert bundle.installed_release(install) == __version__


def test_a_cli_older_than_the_install_does_not_touch_it(
    carried: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = tmp_path / "llm-port"
    bundle.unpack(install)
    (install / bundle.MARKER).write_text(json.dumps({"version": "999.0.0"}), encoding="utf-8")
    (install / ".env").write_text("VERSION=999.0.0\n", encoding="utf-8")
    _configure(tmp_path, monkeypatch, install)
    calls: list[str] = []
    _no_docker(monkeypatch, calls)

    result = CliRunner().invoke(upgrade_module.upgrade_cmd, ["-y", "--no-backup", "--skip-doctor"])
    assert result.exit_code == 1
    assert "older than the install" in result.output
    assert calls == []
    assert _env(install / ".env")["VERSION"] == "999.0.0"


def test_deploy_pins_a_release_install_to_the_cli_version(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=pgpw\n", encoding="utf-8")
    deploy_module.pin_release(env)
    assert _env(env) == {"POSTGRES_PASSWORD": "pgpw", "VERSION": __version__}


def test_the_compose_project_is_named_so_volumes_do_not_follow_the_directory() -> None:
    text = (SHARED / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "\nname: llm_port_shared\n" in text, "an install unpacked into ~/llm-port keeps the checkout's volumes"
