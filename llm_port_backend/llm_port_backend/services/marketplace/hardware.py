"""What each cluster has to run models on, from what its machines report.

Read from each member's latest utilization report: the accelerators' names,
their memory, and how much of it is free. A machine that has not reported is
listed with no accelerators rather than guessed at -- the fit check then says
"no accelerators", which is the truth about what the server knows.
"""

from __future__ import annotations

from typing import Any

from llm_port_backend.services.marketplace.fit import ClusterHardware, Gpu, Machine

MIB = 1024**2


def gpus_from_utilization(utilization: dict[str, Any] | None) -> list[Gpu]:
    """The accelerators in one machine's utilization report."""
    gpu = (utilization or {}).get("gpu") or {}
    devices = gpu.get("devices") if isinstance(gpu, dict) else None
    found: list[Gpu] = []
    for device in devices or []:
        if not isinstance(device, dict):
            continue
        total_mib = device.get("memory_total_mib")
        if not total_mib:
            continue
        used_mib = device.get("memory_used_mib")
        free = None if used_mib is None else max(0, int((float(total_mib) - float(used_mib)) * MIB))
        found.append(Gpu(
            name=str(device.get("name") or device.get("vendor") or "accelerator"),
            total_bytes=int(float(total_mib) * MIB),
            free_bytes=free,
        ))
    if found:
        return found
    # Older agents: only totals for the whole machine.
    total = gpu.get("total_vram_bytes") if isinstance(gpu, dict) else None
    count = int(gpu.get("count") or 1) if isinstance(gpu, dict) else 1
    if total:
        free = gpu.get("free_vram_bytes")
        return [
            Gpu(name="accelerator", total_bytes=int(total) // count,
                free_bytes=None if free is None else int(free) // count)
            for _ in range(count)
        ]
    return []


async def cluster_hardware(session: Any) -> list[ClusterHardware]:
    """Every cluster this server manages, with its machines' accelerators."""
    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO  # noqa: PLC0415
    from llm_port_backend.db.models.inference import (  # noqa: PLC0415
        InferenceEnvironment,
        InferenceEnvironmentNode,
    )
    from llm_port_backend.db.models.node_control import InfraNode  # noqa: PLC0415
    from llm_port_backend.services.inference.bundles import default_bundle_registry  # noqa: PLC0415

    environments = list((await session.execute(
        select(InferenceEnvironment).order_by(InferenceEnvironment.name),
    )).scalars())
    members = list((await session.execute(select(InferenceEnvironmentNode))).scalars())
    node_ids = {m.node_id for m in members}
    nodes = {n.id: n for n in (await session.execute(select(InfraNode))).scalars() if n.id in node_ids}
    snapshots = await NodeControlDAO(session).latest_inventory_snapshots(node_ids=list(node_ids)) if node_ids else {}

    clusters: list[ClusterHardware] = []
    for env in environments:
        machines: list[Machine] = []
        version: str | None = None
        for member in (m for m in members if m.environment_id == env.id):
            node = nodes.get(member.node_id)
            if node is None:
                continue
            snapshot = snapshots.get(node.id)
            machines.append(Machine(
                node_id=str(node.id),
                name=node.agent_id or node.host,
                gpus=gpus_from_utilization(getattr(snapshot, "utilization_json", None)),
            ))
            if version is None:
                try:
                    bundle = default_bundle_registry.resolve_for_node(node, driver="ray")
                    version = bundle.compatibility_matrix.vllm_version if bundle is not None else None
                except Exception:  # noqa: BLE001 - the version is a nicety, never a blocker
                    version = None
        clusters.append(ClusterHardware(
            environment_id=str(env.id),
            name=env.name,
            status=env.status,
            machines=machines,
            vllm_version=version or None,
        ))
    return clusters
