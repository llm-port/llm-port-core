"""Neutral inference domain services (Ray-first, vendor-agnostic).

Phase 1 surface: versioned :mod:`schemas`, :mod:`capabilities`, driver
:mod:`contracts` + :mod:`registry`, and the desired-state :mod:`service`
(orchestration over the DAOs).  No backend driver is registered and no live
reconciliation is performed; see :mod:`reconciliation` for the seams Phase 2
fills in.
"""

from llm_port_backend.services.inference.capabilities import (
    CAPABILITY_SECTIONS,
    DEFAULT_CAPABILITY_SECTIONS,
    CapabilityDocument,
)
from llm_port_backend.services.inference.contracts import (
    DeploymentOrchestrator,
    EnvironmentManager,
    InferenceDriver,
)
from llm_port_backend.services.inference.registry import DriverRegistry, registry
from llm_port_backend.services.inference.schemas import (
    API_VERSION_V1ALPHA1,
    InferenceDeploymentSpecV1Alpha1,
    KnownSpecVersions,
    parse_inference_deployment_spec,
)
from llm_port_backend.services.inference.service import (
    ConflictError,
    ControlPlaneService,
    DeploymentService,
    EnvironmentService,
    InferenceError,
    NotFoundError,
)

__all__ = [
    "API_VERSION_V1ALPHA1",
    "CAPABILITY_SECTIONS",
    "DEFAULT_CAPABILITY_SECTIONS",
    "CapabilityDocument",
    "ConflictError",
    "ControlPlaneService",
    "DeploymentOrchestrator",
    "DeploymentService",
    "DriverRegistry",
    "EnvironmentManager",
    "EnvironmentService",
    "InferenceDeploymentSpecV1Alpha1",
    "InferenceDriver",
    "InferenceError",
    "KnownSpecVersions",
    "NotFoundError",
    "parse_inference_deployment_spec",
    "registry",
]
