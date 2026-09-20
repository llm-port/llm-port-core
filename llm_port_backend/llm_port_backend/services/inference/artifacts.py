"""Inference model artifact readiness coordination and lifecycle service (Phase 4C, WI-3).

Coordinates per-node model artifact readiness for inference environments:
- Resolves cache manifests and canonical manifest digests.
- Reconciles per-node ModelAvailability rows (MISSING / STALE / READY / FAILED).
- Evaluates artifact readiness across eligible environment nodes.
- Dispatches idempotent SYNC_MODEL commands via NodeCommandGateway.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from llm_port_backend.db.dao.inference_dao import ModelAvailabilityDAO
from llm_port_backend.db.models.inference import (
    InferenceEnvironment,
    InferenceEnvironmentNode,
    ModelAvailability,
    ModelAvailabilityStatus,
)
from llm_port_backend.db.models.node_control import InfraNode, NodeCommandType
from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway
from llm_port_backend.services.llm.artifacts import (
    build_cache_manifest,
    build_model_sync_payload,
    model_cache_dir,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from llm_port_backend.db.models.llm import LLMModel

log = logging.getLogger(__name__)


class ArtifactReadiness(BaseModel):
    """Report of model artifact readiness across an environment's nodes."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    desired_revision: str | None = None
    manifest_sha256: str | None = None
    ready_node_ids: list[str] = Field(default_factory=list)
    pending_node_ids: list[str] = Field(default_factory=list)
    failed_node_ids: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    root_paths: dict[str, str] = Field(default_factory=dict)  # node_id -> host root path
    all_ready: bool = False


def is_ready_for(row: ModelAvailability | None, desired_digest: str | None) -> bool:
    """Check if *row* represents a verified, loadable artifact for *desired_digest*."""
    if row is None:
        return False
    if row.status != ModelAvailabilityStatus.READY.value:
        return False
    if not row.root_path:
        return False
    if desired_digest and row.manifest_sha256 != desired_digest:
        return False
    return True


class ModelArtifactCoordinator:
    """Coordinates model artifact synchronization and readiness for inference environments."""

    def __init__(self, session: AsyncSession, *, gateway: Any = None) -> None:
        self.session = session
        self._dao = ModelAvailabilityDAO(session)
        self._gateway = NodeCommandGateway(gateway) if gateway is not None else None

    async def eligible_nodes(self, environment: InferenceEnvironment) -> list[InfraNode]:
        """Resolve member nodes eligible for scheduling workloads in *environment*.

        Filters out nodes that are in maintenance, draining, or ineligible.
        """
        stmt = (
            select(InfraNode)
            .join(InferenceEnvironmentNode, InferenceEnvironmentNode.node_id == InfraNode.id)
            .where(InferenceEnvironmentNode.environment_id == environment.id)
        )
        res = await self.session.execute(stmt)
        nodes = list(res.scalars().all())

        eligible: list[InfraNode] = []
        for node in nodes:
            if not node.scheduler_eligible:
                continue
            if node.maintenance_mode:
                continue
            if node.draining:
                continue
            status = str(node.status or "").lower()
            if status in ("offline", "disabled"):
                continue
            eligible.append(node)
        return eligible

    async def evaluate(
        self,
        *,
        model: LLMModel,
        environment: InferenceEnvironment,
    ) -> ArtifactReadiness:
        """Evaluate artifact readiness for *model* across eligible nodes in *environment*.

        Reconciles ModelAvailability rows (marks MISSING or STALE when detected).
        Does NOT issue commands.
        """
        desired_revision = model.hf_revision
        manifest_sha256: str | None = None

        if model.hf_repo_id:
            m_dir = model_cache_dir(model.hf_repo_id)
            if m_dir is not None:
                manifest = build_cache_manifest(m_dir)
                manifest_sha256 = manifest.get("manifest_sha256")
                if not desired_revision and manifest.get("refs"):
                    desired_revision = manifest["refs"][0].get("commit")

        nodes = await self.eligible_nodes(environment)
        blockers: list[str] = []
        if not nodes:
            blockers.append("No eligible scheduler nodes in environment")

        node_ids = [n.id for n in nodes]
        rows = await self._dao.list_for_nodes(model.id, node_ids)
        row_by_node = {r.node_id: r for r in rows}

        ready_node_ids: list[str] = []
        pending_node_ids: list[str] = []
        failed_node_ids: list[str] = []
        root_paths: dict[str, str] = {}

        for node in nodes:
            node_str = str(node.id)
            row = row_by_node.get(node.id)
            if row is None:
                # Probed and absent -> record MISSING
                await self._dao.mark(
                    model.id,
                    node.id,
                    ModelAvailabilityStatus.MISSING,
                    status_message="Artifact missing from node",
                )
                pending_node_ids.append(node_str)
            elif is_ready_for(row, manifest_sha256):
                ready_node_ids.append(node_str)
                if row.root_path:
                    root_paths[node_str] = row.root_path
            elif (
                row.status == ModelAvailabilityStatus.READY.value
                and manifest_sha256
                and row.manifest_sha256 != manifest_sha256
            ):
                # Manifest digest differs from current desired digest -> STALE
                await self._dao.mark(
                    model.id,
                    node.id,
                    ModelAvailabilityStatus.STALE,
                    status_message=(
                        f"Artifact digest {row.manifest_sha256[:12] if row.manifest_sha256 else 'none'} "
                        f"differs from desired {manifest_sha256[:12]}"
                    ),
                )
                pending_node_ids.append(node_str)
            elif row.status == ModelAvailabilityStatus.FAILED.value:
                failed_node_ids.append(node_str)
                blockers.append(
                    f"Artifact sync failed on node {node.host or node_str}: {row.status_message or 'unknown error'}"
                )
            else:
                # SYNCING, PENDING, MISSING, STALE, UNKNOWN
                pending_node_ids.append(node_str)

        all_ready = len(ready_node_ids) == len(nodes) and len(nodes) > 0

        return ArtifactReadiness(
            model_id=str(model.id),
            desired_revision=desired_revision,
            manifest_sha256=manifest_sha256,
            ready_node_ids=ready_node_ids,
            pending_node_ids=pending_node_ids,
            failed_node_ids=failed_node_ids,
            blockers=blockers,
            root_paths=root_paths,
            all_ready=all_ready,
        )

    async def ensure(
        self,
        *,
        model: LLMModel,
        environment: InferenceEnvironment,
        gateway: Any = None,
    ) -> ArtifactReadiness:
        """Evaluate readiness and issue SYNC_MODEL commands for non-ready nodes.

        Idempotent: does not re-issue commands already in flight for the desired digest.
        """
        readiness = await self.evaluate(model=model, environment=environment)
        if readiness.all_ready:
            return readiness

        sync_payload = build_model_sync_payload(model, source="sync_from_server")
        if sync_payload is None:
            readiness.blockers.append(f"Model {model.id} has no repository or cache manifest to sync")
            return readiness

        gw = (
            gateway
            if isinstance(gateway, NodeCommandGateway)
            else (NodeCommandGateway(gateway) if gateway is not None else self._gateway)
        )
        if gw is None:
            readiness.blockers.append("No node command gateway available to issue SYNC_MODEL")
            return readiness

        target_nodes = set(readiness.pending_node_ids) | set(readiness.failed_node_ids)
        digest_tag = readiness.manifest_sha256 or "latest"

        for node_id_str in target_nodes:
            try:
                node_uuid = uuid.UUID(node_id_str)
            except ValueError:
                continue

            idem_key = f"artifact:{model.id}:{digest_tag}:{node_id_str}"

            # Mark state machine PENDING prior to dispatch
            await self._dao.mark(
                model.id,
                node_uuid,
                ModelAvailabilityStatus.PENDING,
                manifest_sha256=readiness.manifest_sha256,
                revision=readiness.desired_revision,
                status_message="Sync command queued",
            )

            try:
                await gw.issue(
                    node_id=node_uuid,
                    command_type=NodeCommandType.SYNC_MODEL.value,
                    payload={"model_sync": sync_payload},
                    idempotency_key=idem_key,
                    timeout_sec=1800,
                )
            except Exception as exc:
                log.warning("Failed issuing SYNC_MODEL to node %s: %s", node_id_str, exc)
                readiness.blockers.append(f"Failed issuing SYNC_MODEL to node {node_id_str}: {exc}")

        return readiness
