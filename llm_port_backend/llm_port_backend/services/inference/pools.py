"""Compute pools — derived, not configured.

A pool is the *persisted equivalence class* of nodes under the same rules a
runtime bundle is matched against: accelerator vendor, CPU architecture and
accelerator family.  It is not new information, which is why it can be
derived rather than asked for.

Why it exists at all: without it the environment is the only grouping, so a
cluster is implicitly homogeneous.  That holds exactly as long as every node
is NVIDIA.  The moment a ROCm box, an Intel box, or an Apple group behind a
peer-to-peer backend joins, something has to express "these machines are
interchangeable and those are not" -- and scheduling, placement and the
runtime-bundle match all need the same answer.

Derivation writes ``name`` only while a pool is unmanaged.  Once an operator
edits one, ``managed`` is set and derivation stops renaming it but keeps
assigning members to it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    InferenceComputePool,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode

log = logging.getLogger(__name__)

#: Reported when a node has no accelerator at all -- a CPU-only utility node
#: is still a scheduling class, just not an interesting one.
_NO_ACCELERATOR = "none"


def _capability(node: InfraNode) -> dict[str, Any]:
    return node.capabilities_json or {}


def accelerator_vendor(node: InfraNode) -> str:
    """The accelerator vendor the node reported, normalized.

    The agent's collectors already report ``nvidia`` / ``amd`` / ``apple``
    (``llm_port_node_agent/gpu/``), so this reads what is there rather than
    inferring from a driver name.
    """
    caps = _capability(node)
    gpu = caps.get("gpu") if isinstance(caps.get("gpu"), dict) else {}
    vendor = (gpu.get("vendor") or caps.get("gpu_vendor") or "").strip().lower()
    if not vendor:
        count = int(caps.get("gpu_count") or 0)
        return "unknown" if count > 0 else _NO_ACCELERATOR
    return vendor


def accelerator_family(node: InfraNode) -> str | None:
    """Model family (``GB10``, ``MI300X``, ``M3-Ultra``), when reported."""
    caps = _capability(node)
    gpu = caps.get("gpu") if isinstance(caps.get("gpu"), dict) else {}
    for key in ("family", "model", "name", "product"):
        value = gpu.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    devices = gpu.get("devices")
    if isinstance(devices, list) and devices:
        first = devices[0]
        if isinstance(first, dict):
            for key in ("family", "model", "name", "product"):
                value = first.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def cpu_architecture(node: InfraNode) -> str:
    caps = _capability(node)
    machine = str(caps.get("machine") or "").strip().lower()
    # aarch64/arm64 and x86_64/amd64 are the same class under different names;
    # collapsing them stops one cluster growing two pools for one architecture.
    if machine in ("arm64", "aarch64"):
        return "aarch64"
    if machine in ("amd64", "x86_64"):
        return "x86_64"
    return machine or "unknown"


def pool_signature(node: InfraNode) -> str:
    """Stable identity of a node's compatibility class.

    Matching is always on this, never on the display name, so renaming a pool
    cannot split it.
    """
    family = (accelerator_family(node) or "").lower().replace(" ", "-")
    return f"{accelerator_vendor(node)}/{cpu_architecture(node)}/{family or 'any'}"


def suggest_pool_name(node: InfraNode) -> str:
    """A readable name for a derived pool: family if known, else vendor."""
    family = accelerator_family(node)
    if family:
        return family.lower().replace(" ", "-").replace("_", "-")
    vendor = accelerator_vendor(node)
    if vendor == _NO_ACCELERATOR:
        return "cpu-only"
    return f"{vendor}-{cpu_architecture(node)}"


class ComputePoolCoordinator:
    """Keeps every environment member assigned to the right pool."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assign(
        self, *, environment_id: uuid.UUID, node: InfraNode, member: InferenceEnvironmentNode
    ) -> InferenceComputePool:
        """Place *member* in the pool matching its node, creating it if needed."""
        signature = pool_signature(node)
        result = await self.session.execute(
            select(InferenceComputePool).where(
                InferenceComputePool.environment_id == environment_id,
                InferenceComputePool.signature == signature,
            )
        )
        pool = result.scalar_one_or_none()

        if pool is None:
            pool = InferenceComputePool(
                id=uuid.uuid4(),
                environment_id=environment_id,
                name=await self._unique_name(environment_id, suggest_pool_name(node)),
                signature=signature,
                accelerator_vendor=accelerator_vendor(node),
                accelerator_family=accelerator_family(node),
                cpu_architecture=cpu_architecture(node),
            )
            self.session.add(pool)
            await self.session.flush()
            log.info(
                "Derived compute pool %s (%s) in environment %s",
                pool.name, signature, environment_id,
            )

        member.compute_pool_id = pool.id
        return pool

    async def _unique_name(self, environment_id: uuid.UUID, base: str) -> str:
        """``gb10``, then ``gb10-2`` — the name is cosmetic, the signature is not."""
        result = await self.session.execute(
            select(InferenceComputePool.name).where(
                InferenceComputePool.environment_id == environment_id
            )
        )
        taken = {row for row in result.scalars().all()}
        if base not in taken:
            return base
        for suffix in range(2, 100):
            candidate = f"{base}-{suffix}"
            if candidate not in taken:
                return candidate
        return f"{base}-{uuid.uuid4().hex[:6]}"

    async def list_for_environment(
        self, environment_id: uuid.UUID
    ) -> list[InferenceComputePool]:
        result = await self.session.execute(
            select(InferenceComputePool)
            .where(InferenceComputePool.environment_id == environment_id)
            .order_by(InferenceComputePool.name)
        )
        return list(result.scalars().all())

    async def member_counts(self, environment_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """How many members sit in each pool of this environment."""
        result = await self.session.execute(
            select(InferenceEnvironmentNode.compute_pool_id).where(
                InferenceEnvironmentNode.environment_id == environment_id
            )
        )
        counts: dict[uuid.UUID, int] = {}
        for pool_id in result.scalars().all():
            if pool_id is not None:
                counts[pool_id] = counts.get(pool_id, 0) + 1
        return counts
