"""`llmport doctor` checks the ports the install publishes, not a developer's.

It listed the dev servers' ports (8000, 5173, 5432, ...) and, once LLM.Port
was running, reported every port it had itself taken as "in use". A port
held by something else is what stops a deploy ("port is already
allocated": Prometheus and MinIO both wanted 9090 on the release VM).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from llmport.core import detect

SHARED = Path(__file__).resolve().parents[2] / "llm_port_shared"


def test_ports_come_from_the_compose_file_and_our_own_are_not_conflicts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect, "_published_ports", lambda f, e: [(80, "nginx"), (3001, "grafana"), (9099, "prometheus")])
    monkeypatch.setattr(detect, "_ports_held_by_llm_port", lambda: {80})
    monkeypatch.setattr(detect, "check_port", lambda port, label="": detect.PortCheck(port, label, port in {80, 9099}))

    checks = {c.port: c for c in detect.check_install_ports(SHARED / "docker-compose.yaml")}

    assert checks[80].in_use and checks[80].ours, "LLM.Port's own nginx"
    assert not checks[3001].in_use
    assert checks[9099].in_use and not checks[9099].ours, "something else holds it: a deploy would fail"


def test_without_docker_the_published_ports_are_still_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setattr(detect, "check_port", lambda port, label="": detect.PortCheck(port, label, False))
    ports = [c.port for c in detect.check_install_ports(SHARED / "docker-compose.yaml")]
    assert ports == [p for p, _ in detect.FALLBACK_INSTALL_PORTS]
    assert 80 in ports


@pytest.mark.skipif(detect.shutil.which("docker") is None, reason="needs the docker CLI")
def test_the_real_compose_file_publishes_the_console_on_80() -> None:
    published = dict(detect._published_ports(SHARED / "docker-compose.yaml", None) or [])  # noqa: SLF001
    assert published.get(80) == "nginx"
    assert 5173 not in published, "the dev frontend is not part of an install"


def test_doctor_fails_on_a_port_held_by_something_else(monkeypatch: pytest.MonkeyPatch) -> None:
    from llmport.commands import doctor

    report = detect.SystemReport(
        os=detect.OSInfo(system="Linux", release="6", version="", machine="x86_64"),
        docker=detect.DockerInfo(installed=True, version="29", compose_installed=True,
                                 compose_version="5", daemon_running=True),
        gpu=detect.GpuInfo(),
        ram=detect.RamInfo(total_gb=7.6, available_gb=5.0, used_pct=30.0),
        disk=detect.DiskInfo(path="/", total_gb=100, free_gb=50, used_pct=50.0),
        ports=[],
        tools=[],
    )
    monkeypatch.setattr(doctor, "full_report", lambda **k: report)
    monkeypatch.setattr(doctor, "check_install_ports",
                        lambda *a: [detect.PortCheck(80, "nginx", True, ours=False)])
    monkeypatch.setattr(doctor, "_install_compose", lambda: (None, None))

    result = CliRunner().invoke(doctor.doctor_cmd, obj={})
    assert "in use by something else" in result.output
    assert "Some prerequisites are missing" in result.output
    assert "poetry" not in result.output, "no developer tools on a server check"
    assert "7.6 GB" in result.output and "recommended" not in result.output, "an 8 GB machine passes"
