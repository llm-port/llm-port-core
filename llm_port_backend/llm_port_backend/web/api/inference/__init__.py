"""Inference API route module - aggregates control-plane, environment, and deployment routes."""

import uuid

from fastapi import APIRouter, Depends, Query

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.users import User
from llm_port_backend.services.inference.bundles import default_bundle_registry
from llm_port_backend.services.inference.registry import registry
from llm_port_backend.web.api.inference.schema import RuntimeBundleDTO
from llm_port_backend.web.api.rbac import require_permission

from llm_port_backend.web.api.inference.control_planes import (
    router as control_planes_router,
)
from llm_port_backend.web.api.inference.deployments import router as deployments_router
from llm_port_backend.web.api.inference.environments import (
    router as environments_router,
)

inference_router = APIRouter()
inference_router.include_router(
    control_planes_router,
    prefix="/control-planes",
    tags=["inference-control-planes"],
)
inference_router.include_router(
    environments_router,
    prefix="/environments",
    tags=["inference-environments"],
)
inference_router.include_router(
    deployments_router,
    prefix="/deployments",
    tags=["inference-deployments"],
)


@inference_router.get("/drivers", response_model=list[str], tags=["inference"])
async def list_drivers(
    _user: User = Depends(require_permission("inference.control_planes", "read")),
) -> list[str]:
    """List the driver keys registered in this process.

    A control plane may name any driver - the server stays permissive so a
    driver can register later - but an operator choosing from a list cannot
    accidentally create one whose driver will answer 501 to every call.
    """
    return registry.keys()


@inference_router.get(
    "/runtime-bundles",
    response_model=list[RuntimeBundleDTO],
    tags=["inference"],
)
async def list_runtime_bundles(
    node_id: list[uuid.UUID] = Query(
        default_factory=list,
        description="Report compatibility against these nodes.",
    ),
    _user: User = Depends(require_permission("inference.environments", "read")),
    dao: NodeControlDAO = Depends(),
) -> list[RuntimeBundleDTO]:
    """List the certified runtime images and which nodes will run each.

    Read-only.  The assignment is delegated to
    ``RuntimeBundleRegistry.resolve_for_node`` -- the same call the cluster
    lifecycle makes -- rather than re-derived here, so what this screen shows
    is what will actually be started.
    """
    nodes = []
    for identifier in node_id:
        node = await dao.get_node_by_id(identifier)
        if node is not None:
            nodes.append(node)

    # Resolve once per node rather than once per (bundle, node): resolution
    # picks a winner among the bundles that fit, so asking bundle by bundle
    # cannot see the answer.
    resolved = {
        node.id: default_bundle_registry.resolve_for_node(node, driver="ray")
        for node in nodes
    }

    result: list[RuntimeBundleDTO] = []
    for bundle in default_bundle_registry.list_bundles():
        compatible: list[uuid.UUID] = []
        incompatible: dict[str, str] = {}
        for node in nodes:
            chosen = resolved.get(node.id)
            if chosen is not None and chosen.bundle_id == bundle.bundle_id:
                compatible.append(node.id)
                continue
            ok, reason = default_bundle_registry.validate_node_compatibility(bundle, node)
            if not ok:
                incompatible[str(node.id)] = reason
            elif chosen is not None:
                # It would run, but something more specific was chosen.  Say
                # so rather than reporting it as an incompatibility, which
                # would read as a fault on a node that is perfectly fine.
                incompatible[str(node.id)] = (
                    f"{chosen.display_name} is the closer match for this machine"
                )
            else:
                machine = str((node.capabilities_json or {}).get("machine") or "")
                incompatible[str(node.id)] = (
                    "This machine has not reported its platform yet"
                    if not machine
                    else f"No certified image resolves for {machine}"
                )
        result.append(
            RuntimeBundleDTO(
                bundle_id=bundle.bundle_id,
                display_name=bundle.display_name,
                description=bundle.description,
                image=bundle.container.image,
                cpu_architecture=bundle.target_architecture.cpu,
                accelerator_vendor=bundle.target_architecture.accelerator.vendor,
                runtime_version=bundle.compatibility_matrix.ray_version,
                vllm_version=bundle.compatibility_matrix.vllm_version,
                certification_status=bundle.certification.status,
                compatible_node_ids=compatible,
                incompatible=incompatible,
            )
        )
    return result
