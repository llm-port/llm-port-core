"""Every httpx client in the backend is given its SSL context.

Left to its default, ``httpx.AsyncClient()`` builds a new SSL context and
loads the CA bundle into it: ~400 ms on the Windows dev workstation, done
synchronously on the event loop, even for a plain http:// URL. Clients made
per request -- health checks, admin pages that poll, the chat proxy -- froze
the whole backend for that long each time. ``default_httpx_verify()`` is built
once; pass it (or a ``build_httpx_verify`` result) as ``verify=``.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import httpx

from llm_port_backend.services.tls import default_httpx_verify

PACKAGE = Path(__file__).resolve().parents[1] / "llm_port_backend"


def _constructions_without_verify() -> list[str]:
    found = []
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "httpx"
                and node.func.attr in ("AsyncClient", "Client")
                and not any(k.arg in ("verify", None) for k in node.keywords)
            ):
                found.append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno}")
    return found


def test_every_client_is_given_an_ssl_context() -> None:
    assert _constructions_without_verify() == []


def test_a_client_with_the_shared_context_is_cheap() -> None:
    default_httpx_verify()  # built once, before timing
    started = time.perf_counter()
    for _ in range(10):
        httpx.AsyncClient(verify=default_httpx_verify())
    assert (time.perf_counter() - started) / 10 < 0.05
