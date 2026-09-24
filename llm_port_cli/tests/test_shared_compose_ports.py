"""No two services of the shipped compose file take the same host port.

Prometheus and MinIO's API both defaulted to 127.0.0.1:9090; only a
hand-edited .env kept them apart, and an upgrade with a generated one
stopped on "Bind for 127.0.0.1:9090 failed: port is already allocated".
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import yaml

COMPOSE = Path(__file__).parents[2] / "llm_port_shared" / "docker-compose.yaml"
_VARIABLE = re.compile(r"\$\{(\w+)(?::?-([^}]*))?\}")


def _host_port(entry: object) -> tuple[str, str] | None:
    """``(address, port)`` a service publishes on the host, every variable at its default."""
    if isinstance(entry, dict):
        published = entry.get("published")
        return (str(entry.get("host_ip") or "0.0.0.0"), str(published)) if published else None
    text = _VARIABLE.sub(lambda m: m.group(2) or "", str(entry)).split("/")[0]
    parts = text.split(":")
    if len(parts) == 3:
        return parts[0] or "0.0.0.0", parts[1]
    if len(parts) == 2:
        return "0.0.0.0", parts[0]
    return None


def test_no_two_services_take_the_same_host_port() -> None:
    spec = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    taken: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, service in (spec.get("services") or {}).items():
        for entry in service.get("ports") or []:
            port = _host_port(entry)
            if port:
                taken[port].append(name)
    wildcard = {p for (a, p) in taken if a == "0.0.0.0"}
    clashes = {k: v for k, v in taken.items() if len(v) > 1 or (k[0] != "0.0.0.0" and k[1] in wildcard)}
    assert not clashes, clashes


def test_prometheus_is_not_on_minios_port() -> None:
    spec = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    ports = [_host_port(e) for e in spec["services"]["prometheus"]["ports"]]
    assert ("127.0.0.1", "9099") in ports
