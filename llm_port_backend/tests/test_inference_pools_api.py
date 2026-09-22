"""The two read-only endpoints the cluster UI builds its choices from.

Neither was covered, and that is how a DTO rename shipped a broken
``/runtime-bundles``: the field was renamed on the schema and still passed by
its old keyword at the call site, which only fails when the route is actually
invoked.

Both endpoints exist to keep the UI from re-deriving anything: compatibility
comes from ``validate_node_compatibility``, pools come from the same
derivation the reconciler uses.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.node_control import InfraNode

from tests.test_inference_api import (  # noqa: F401  (fixtures)
    API,
    authed_fapp,
    make_control_plane,
    make_environment,
    superuser,
)


def _caps(vendor: str, family: str, machine: str) -> dict[str, Any]:
    return {
        "machine": machine,
        "gpu_count": 1,
        "gpu": {"vendor": vendor, "family": family},
    }


async def _node(session: AsyncSession, caps: dict[str, Any]) -> uuid.UUID:
    node = InfraNode(
        agent_id=f"agent-{uuid.uuid4().hex[:8]}",
        host="10.88.10.49",
        status="healthy",
        capabilities_json=caps,
    )
    session.add(node)
    await session.flush()
    return node.id


@pytest.mark.anyio()
async def test_runtime_bundles_render(client: AsyncClient, authed_fapp: FastAPI) -> None:
    r = await client.get(f"{API}/runtime-bundles")
    assert r.status_code == 200, r.text
    bundles = r.json()
    assert bundles, "the certified catalog must not be empty"
    for bundle in bundles:
        # Neutral field names at the API boundary: the bundle's own
        # compatibility matrix still says ``ray_version``, the DTO does not.
        assert "runtime_version" in bundle
        assert "ray_version" not in bundle
        assert bundle["accelerator_vendor"]
        assert bundle["cpu_architecture"]


@pytest.mark.anyio()
async def test_runtime_bundles_report_per_node_compatibility(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    good = await _node(dbsession, _caps("nvidia", "GB10", "aarch64"))
    wrong = await _node(dbsession, _caps("amd", "MI300X", "x86_64"))

    r = await client.get(f"{API}/runtime-bundles", params={"node_id": [str(good), str(wrong)]})
    assert r.status_code == 200, r.text
    bundles = r.json()

    # The AMD box cannot run an aarch64/NVIDIA bundle, and the reason must be
    # stated rather than the node silently missing from the compatible list.
    for bundle in bundles:
        if str(wrong) in bundle["incompatible"]:
            assert bundle["incompatible"][str(wrong)].strip()
    assert any(str(wrong) in b["incompatible"] for b in bundles)


@pytest.mark.anyio()
async def test_pools_endpoint_reflects_who_joined(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")

    r = await client.get(f"{API}/environments/{env['id']}/pools")
    assert r.status_code == 200
    assert r.json() == [], "an empty cluster has no pools to configure"

    head = await _node(dbsession, _caps("nvidia", "GB10", "aarch64"))
    r = await client.post(
        f"{API}/environments/{env['id']}/nodes", json={"node_id": str(head), "role": "head"}
    )
    assert r.status_code == 201, r.text

    r = await client.get(f"{API}/environments/{env['id']}/pools")
    assert r.status_code == 200
    pools = r.json()
    assert len(pools) == 1
    assert pools[0]["signature"] == "nvidia/aarch64/gb10"
    assert pools[0]["member_count"] == 1
    assert pools[0]["managed"] is False

    # A second machine of the same class joins the same pool -- the cluster
    # stays "one pool" and the operator was never asked to name anything.
    worker = await _node(dbsession, _caps("nvidia", "GB10", "aarch64"))
    r = await client.post(
        f"{API}/environments/{env['id']}/nodes", json={"node_id": str(worker), "role": "worker"}
    )
    assert r.status_code == 201, r.text

    pools = (await client.get(f"{API}/environments/{env['id']}/pools")).json()
    assert len(pools) == 1
    assert pools[0]["member_count"] == 2


@pytest.mark.anyio()
async def test_membership_carries_its_pool(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    head = await _node(dbsession, _caps("nvidia", "GB10", "aarch64"))
    await client.post(
        f"{API}/environments/{env['id']}/nodes", json={"node_id": str(head), "role": "head"}
    )

    members = (await client.get(f"{API}/environments/{env['id']}/nodes")).json()
    assert len(members) == 1
    pools = (await client.get(f"{API}/environments/{env['id']}/pools")).json()
    assert members[0]["compute_pool_id"] == pools[0]["id"]
    # Neutral name at the boundary; the Ray driver keeps its own vocabulary.
    assert "member_status" in members[0]
    assert "ray_status" not in members[0]


@pytest.mark.anyio()
async def test_pools_for_a_missing_environment_404(
    client: AsyncClient, authed_fapp: FastAPI
) -> None:
    r = await client.get(f"{API}/environments/{uuid.uuid4()}/pools")
    assert r.status_code == 404
