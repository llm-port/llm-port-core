"""Where a provider's prompts go is decided by where it is, not by how it was added.

The data residency map counted ``remote_endpoint`` as "cloud": a vLLM found on
one of our own enrolled machines, or a self-hosted server on the LAN, showed as
cloud, and providers served by our own clusters were not counted at all.
"""

from __future__ import annotations

import ipaddress
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.models.llm import LLMProvider, ProviderTarget, ProviderType
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.services.llm import residency as r


def _provider(url: str | None = None, *, target: str = "remote_endpoint", ptype: str = "vllm",
              litellm: str | None = None, override: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), endpoint_url=url, target=target, type=ptype,
                           litellm_provider=litellm, residency_override=override)


def _ctx(resolved: dict[str, tuple[str, ...]] | None = None, **kw: Any) -> r.Context:
    return r.Context(resolved=dict(resolved or {}), **kw)


# ── ours by construction, cloud by declaration ──────────────────────────


def test_what_we_run_is_on_our_machines() -> None:
    assert r.classify(_provider(target="local_docker"), _ctx()).kind == r.MACHINES
    cluster = r.classify(_provider(target="inference_cluster"), _ctx())
    assert (cluster.kind, cluster.source) == (r.MACHINES, "managed"), "used to be counted nowhere"


def test_a_cloud_api_is_external_without_any_network() -> None:
    declared = r.classify(_provider("https://api.anthropic.com", litellm="anthropic"), _ctx())
    assert (declared.kind, declared.source, declared.provider) == (r.EXTERNAL, "cloud_provider", "anthropic")
    by_host = r.classify(_provider("https://myorg.openai.azure.com/v1"), _ctx())
    assert (by_host.kind, by_host.source) == (r.EXTERNAL, "cloud_host")
    assert r.classify(_provider(None, ptype="cloud"), _ctx()).kind == r.EXTERNAL


# ── where a self-hosted endpoint is ──────────────────────────────────────


def test_an_endpoint_on_an_enrolled_machine_is_on_our_machines() -> None:
    """The DGX route and every found vLLM: they were "cloud"."""
    ctx = _ctx(machine_addresses={"10.88.10.71": "spark-3201"})
    found = r.classify(_provider("http://10.88.10.71:8000/v1"), ctx)
    assert (found.kind, found.source, found.machine) == (r.MACHINES, "machine", "spark-3201")


def test_a_private_address_is_our_network() -> None:
    lan = r.classify(_provider("http://10.1.2.3:8000"), _ctx())
    assert (lan.kind, lan.source, lan.addresses) == (r.PRIVATE, "private_address", ("10.1.2.3",))
    named = r.classify(_provider("http://vllm.office.lan:8000"), _ctx({"vllm.office.lan": ("192.168.1.20",)}))
    assert named.kind == r.PRIVATE
    tailscale = r.classify(_provider("http://100.122.30.59:8000"), _ctx())
    assert tailscale.kind == r.PRIVATE, "the 100.64/10 range is never routed on the internet"


def test_a_public_address_is_external() -> None:
    # Real public addresses: the documentation ranges (192.0.2/24 ...) count as private in Python.
    public = r.classify(_provider("https://llm.example.net"), _ctx({"llm.example.net": ("34.117.59.81", "10.0.0.5")}))
    assert (public.kind, public.source) == (r.EXTERNAL, "public_address"), "any public address is enough"


def test_a_datacenters_own_public_range_can_be_declared_internal() -> None:
    ctx = _ctx({"llm.corp.example": ("81.2.69.7",)}, internal_networks=r.parse_networks("81.2.69.0/24"))
    own = r.classify(_provider("https://llm.corp.example"), ctx)
    assert (own.kind, own.source) == (r.PRIVATE, "internal_network")


def test_this_server_is_one_of_our_machines() -> None:
    for url in ("http://localhost:8000", "http://host.docker.internal:11434", "http://127.0.0.1:8000"):
        assert r.classify(_provider(url), _ctx()).kind == r.MACHINES, url


def test_what_cannot_be_told_is_unknown_not_guessed() -> None:
    assert r.classify(_provider("http://gone.invalid"), _ctx()).source == "unresolved"
    assert r.classify(_provider(None), _ctx()).source == "no_endpoint"


def test_an_administrator_has_the_last_word() -> None:
    """A LAN proxy that forwards to a cloud API has a private address."""
    proxy = r.classify(_provider("http://10.1.2.3:4000", override="external"), _ctx())
    assert (proxy.kind, proxy.source) == (r.EXTERNAL, "override")
    assert r.needs_dns(_provider("http://proxy.lan", override="external")) is None


# ── the evidence ─────────────────────────────────────────────────────────


def test_only_hostnames_that_decide_something_are_looked_up() -> None:
    assert r.needs_dns(_provider("http://vllm.office.lan:8000")) == "vllm.office.lan"
    assert r.needs_dns(_provider("http://10.1.2.3:8000")) is None
    assert r.needs_dns(_provider("https://api.openai.com/v1")) is None
    assert r.needs_dns(_provider("http://x", target="local_docker")) is None
    assert r.endpoint_host("vllm.lan:8000") == "vllm.lan"


def test_a_docker_bridge_names_no_machine() -> None:
    node = SimpleNamespace(agent_id="spark-3201", host="10.88.10.71", capabilities_json={"network": {"fabrics": [
        {"ip": "172.17.0.1", "is_virtual": True},
        {"ip": "10.100.0.2", "is_virtual": False},
    ]}})
    assert r.machine_addresses([node]) == {"10.88.10.71": "spark-3201", "10.100.0.2": "spark-3201"}


def test_bad_networks_are_skipped() -> None:
    assert r.parse_networks("203.0.113.0/24, nonsense ,2001:db8::/32") == [
        ipaddress.ip_network("203.0.113.0/24"), ipaddress.ip_network("2001:db8::/32"),
    ]


@pytest.mark.anyio
async def test_hosts_are_resolved_together_and_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    r.clear_cache()
    asked: list[str] = []

    class _Loop:
        async def getaddrinfo(self, host: str, *_: Any, **__: Any) -> list[Any]:
            asked.append(host)
            return [(2, 1, 6, "", ("192.168.7.9", 0))]

    monkeypatch.setattr(r.asyncio, "get_running_loop", _Loop)
    ctx = _ctx()
    found = await r.classify_all([_provider("http://a.lan"), _provider("http://a.lan:9000")], ctx)
    assert {v.kind for v in found.values()} == {r.PRIVATE}
    await r.classify_all([_provider("http://a.lan")], _ctx())
    assert asked == ["a.lan"], "one lookup, then the cache"


# ── the API ──────────────────────────────────────────────────────────────


async def _admin(dbsession: AsyncSession, fastapi_app: FastAPI) -> None:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    admin = User(email=f"admin-{uuid.uuid4().hex}@test.local", hashed_password="x",
                 is_verified=True, is_active=True, is_superuser=False)
    dbsession.add(admin)
    await dbsession.flush()
    await rbac.assign_role(admin.id, (await rbac.get_role_by_name("admin")).id)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin


@pytest.mark.anyio
async def test_the_providers_api_says_where_each_one_is(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    await _admin(dbsession, fastapi_app)
    dbsession.add(InfraNode(agent_id=f"spark-{uuid.uuid4().hex[:6]}", host="10.88.10.71", status="healthy"))
    found = LLMProvider(name=f"found-{uuid.uuid4().hex[:6]}", type=ProviderType.VLLM,
                        target=ProviderTarget.REMOTE_ENDPOINT, endpoint_url="http://10.88.10.71:8000")
    cloud = LLMProvider(name=f"claude-{uuid.uuid4().hex[:6]}", type=ProviderType.CLOUD,
                        target=ProviderTarget.REMOTE_ENDPOINT, litellm_provider="anthropic")
    dbsession.add_all([found, cloud])
    await dbsession.commit()

    listed = {p["id"]: p for p in (await client.get("/api/llm/providers/")).json()}
    assert listed[str(found.id)]["residency"]["kind"] == "machines"
    assert listed[str(cloud.id)]["residency"]["kind"] == "external"

    said = await client.put(f"/api/llm/providers/{found.id}/residency", json={"override": "external"})
    assert said.status_code == 200, said.text
    assert said.json()["residency"] == {**said.json()["residency"], "kind": "external", "source": "override"}
    assert said.json()["residency_override"] == "external"

    cleared = await client.put(f"/api/llm/providers/{found.id}/residency", json={"override": None})
    assert cleared.json()["residency"]["source"] == "machine"
    bad = await client.put(f"/api/llm/providers/{found.id}/residency", json={"override": "moon"})
    assert bad.status_code == 422
