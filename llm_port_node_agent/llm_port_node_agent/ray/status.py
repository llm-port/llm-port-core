"""Ray cluster status via the CLI's structured node listing."""

import asyncio
import json
import logging
from typing import Any

from llm_port_node_agent.ray.process import RayProcessManager
from llm_port_node_agent.ray.schemas import RayStatusResult

log = logging.getLogger(__name__)


async def get_ray_status(
    process_manager: RayProcessManager, version: str, address: str | None = None
) -> RayStatusResult:
    """Query live Ray nodes with ``ray list nodes --format json``.

    ``ray status`` is unstructured text; the state-API CLI (``ray list``)
    returns JSON with ``node_id``/``node_ip``/``state``/``is_head_node``/
    ``resources_total`` per node, which is what the reconciler needs.
    """
    ray_bin = process_manager.ray_binary_path(version)
    if not ray_bin.exists():
        return RayStatusResult(alive=False)

    cmd = [str(ray_bin), "list", "nodes", "--format", "json"]
    if address:
        cmd.extend(["--address", address])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("ray list nodes failed to launch: %s", exc)
        return RayStatusResult(alive=False)

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        log.info("ray list nodes exited %s: %s", proc.returncode, err)
        return RayStatusResult(alive=False)

    try:
        records = json.loads(stdout.decode(errors="replace") or "[]")
    except json.JSONDecodeError as exc:
        log.warning("ray list nodes returned non-JSON: %s", exc)
        return RayStatusResult(alive=False)

    if not isinstance(records, list):
        return RayStatusResult(alive=False)

    nodes: list[dict[str, Any]] = []
    total_gpus = 0.0
    cluster_address: str | None = None
    head_seen = False
    for rec in records:
        if not isinstance(rec, dict):
            continue
        state = str(rec.get("state") or "").upper()
        if state != "ALIVE":
            continue
        node_ip = str(rec.get("node_ip") or rec.get("node_manager_address") or "")
        is_head = bool(rec.get("is_head_node"))
        resources = rec.get("resources_total") or {}
        gpus = float(resources.get("GPU") or 0.0)
        total_gpus += gpus
        nodes.append(
            {
                "node_id": rec.get("node_id"),
                "ip": node_ip,
                "node_ip": node_ip,
                "state": state,
                "is_head": is_head,
                "is_head_node": is_head,
                "gpus": gpus,
                "cpus": resources.get("CPU"),
            }
        )
        if is_head and not head_seen and node_ip:
            head_seen = True
            port = rec.get("node_manager_port")
            cluster_address = f"{node_ip}:{port}" if port else node_ip

    return RayStatusResult(
        alive=True,
        version=version,
        num_nodes=len(nodes),
        nodes=nodes,
        total_gpus=total_gpus,
        available_gpus=total_gpus,
        cluster_address=cluster_address,
    )

