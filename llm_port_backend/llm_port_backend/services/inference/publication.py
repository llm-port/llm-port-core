"""Generic inference publication layer (Phase 5).

Translates normalized :class:`~llm_port_backend.db.models.inference.InferenceDeployment`
and :class:`~llm_port_backend.db.models.inference.InferenceEndpoint` states into
generic gateway provider availability, completely separated from driver-specific
runtimes (Ray, Dynamo, vLLM).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from llm_port_backend.db.dao.inference_dao import EndpointDAO
from llm_port_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    InferenceDeployment,
    InferenceEndpoint,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from llm_port_backend.services.llm.gateway_sync import GatewaySyncService

log = logging.getLogger(__name__)


class InferencePublicationCoordinator:
    """Synchronizes normalized inference endpoint status to the Gateway."""

    def __init__(
        self,
        session: AsyncSession,
        gateway_sync: GatewaySyncService | None = None,
    ) -> None:
        self.session = session
        self.gateway_sync = gateway_sync
        self.endpoint_dao = EndpointDAO(session)

    async def reconcile_deployment_publication(
        self,
        deployment: InferenceDeployment,
    ) -> None:
        """Reconcile the gateway provider representation of this deployment.

        Adheres strictly to the Phase 5 publication invariants:
        - 1 Deployment = 1 logical LLMProviderInstance
        - Publication is driven by normalized InferenceEndpoint state, not raw engine state
        - Degraded deployment with healthy endpoint and ready_replicas > 0 remains routable
        - Stopping a deployment deactivates the source without deleting alias or membership
        - Deleting a deployment retires the source
        """
        if self.gateway_sync is None or not self.gateway_sync.enabled:
            return

        source_id = deployment.id
        source_kind = "inference_deployment"

        # 1. Handle explicit desired or observed stopped state
        if (
            deployment.desired_state == DeploymentDesiredState.STOPPED
            or deployment.phase == DeploymentPhase.STOPPED
        ):
            await self.gateway_sync.deactivate_source(
                source_kind=source_kind,
                source_id=source_id,
            )
            return

        # 2. Handle explicit desired or observed deleted state
        if (
            deployment.desired_state == DeploymentDesiredState.DELETED
            or deployment.phase == DeploymentPhase.DELETED
        ):
            await self.gateway_sync.retire_source(
                source_kind=source_kind,
                source_id=source_id,
            )
            return

        # 3. Retrieve normalized primary inference endpoint
        endpoints = await self.endpoint_dao.list_for_deployment(deployment.id)
        primary_endpoint: InferenceEndpoint | None = next(
            (e for e in endpoints if e.name == "openai"),
            endpoints[0] if endpoints else None,
        )

        if primary_endpoint is None:
            # No endpoint yet (pending deployment)
            return

        # 4. Evaluate endpoint readiness and routability
        endpoint_published = primary_endpoint.status == EndpointStatus.PUBLISHED
        ready_replicas = deployment.ready_replicas

        # Base URL from endpoint address + path, or published_json
        address = primary_endpoint.address
        path = primary_endpoint.path or "/v1"
        base_url = (primary_endpoint.published_json or {}).get("base_url") or f"{address}{path}"
        served_model_name = (primary_endpoint.published_json or {}).get("model")

        # Determine alias: spec_json service.alias, or deployment.name
        spec_service = (deployment.spec_json or {}).get("service") or {}
        alias = spec_service.get("alias") or deployment.name

        # A deployment is routable if its endpoint is PUBLISHED and has at least 1 ready replica
        # Even if deployment phase is DEGRADED, as long as ready_replicas > 0, it stays routable
        if endpoint_published and ready_replicas > 0:
            is_routable = True
            health_status = "healthy"

            await self.gateway_sync.publish_inference_endpoint(
                deployment_id=deployment.id,
                endpoint_id=primary_endpoint.id,
                base_url=base_url,
                alias=alias,
                served_model_name=served_model_name,
                backend_provider_type="vllm",
                health_status=health_status,
                is_routable=is_routable,
            )
        elif primary_endpoint.status in (EndpointStatus.FAILED, EndpointStatus.RETIRED) or ready_replicas == 0:
            # Endpoint is unhealthy or has 0 ready replicas
            await self.gateway_sync.set_source_health(
                source_kind=source_kind,
                source_id=source_id,
                health_status="unhealthy",
                enabled=False,
            )

