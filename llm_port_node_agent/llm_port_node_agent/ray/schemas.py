"""Pydantic schemas for Ray node command payloads.

The status *result* contract is the enriched
:class:`~llm_port_node_agent.ray.models.RayEnvironmentStatus` produced by the
SDK layer; :class:`RayStatusResult` remains for importers/tests and is a
backwards-compatible projection of the flat fields.
"""

from typing import Any

from pydantic import BaseModel

from llm_port_node_agent.ray.models import RayEnvironmentStatus


class RuntimeBundlePayload(BaseModel):
    """Container contract for the Phase 4B runtime-bundle bootstrap path.

    Its presence on a lifecycle command selects the containerized path: the
    pinned image is verified/loaded and ``ray start`` is exec'd inside it, so
    the host needs no Ray Python distribution at all.  ``requirements`` are
    *semantic* (``network_mode``, ``ipc_mode``, devices, ...); mapping them to
    handler flags is the agent's job.
    """

    name: str = "llm-port-ray-runtime"
    image: str
    digest: str
    repo_digest: str | None = None
    runtime_handler: str = "docker"
    requirements: dict[str, Any] = {}
    mounts: list[dict[str, Any]] = []
    env: dict[str, str] = {}


class EnsureRayRuntimePayload(BaseModel):
    version: str = "2.58.0"
    runtime_bundle: RuntimeBundlePayload | None = None


class EnsureRuntimeImagePayload(BaseModel):
    """``ENSURE_RUNTIME_IMAGE``: make a pinned OCI image present and verified."""

    runtime_bundle: RuntimeBundlePayload
    # Also create/start the runtime container once the image is verified.
    ensure_container: bool = False


class ValidateFabricListenPayload(BaseModel):
    """``VALIDATE_FABRIC_LISTEN``: one-shot ephemeral probe listener."""

    ip: str
    port: int
    probe_token: str = ""
    timeout_sec: float = 5.0


class ValidateFabricConnectPayload(BaseModel):
    """``VALIDATE_FABRIC_CONNECT``: one-shot probe against a listener."""

    target_ip: str
    target_port: int
    source_ip: str | None = None
    probe_token: str = ""
    timeout_sec: float = 5.0


class StartRayHeadPayload(BaseModel):
    credential_ref: str
    version: str = "2.58.0"
    port: int = 6379
    dashboard_port: int = 8265
    dashboard_host: str = "127.0.0.1"
    node_ip_address: str | None = None
    num_cpus: int | None = None
    num_gpus: int | None = None
    resources: dict[str, float] = {}
    # The dashboard is an optional component.  When False the head boots with
    # ``--include-dashboard=false`` (GCS + raylet only) — the SDK status path
    # does not need the dashboard.
    include_dashboard: bool = True
    env: dict[str, str] = {}
    runtime_bundle: RuntimeBundlePayload | None = None


class JoinRayClusterPayload(BaseModel):
    head_address: str  # "<ip>:<port>"
    credential_ref: str
    version: str = "2.58.0"
    node_ip_address: str | None = None
    num_cpus: int | None = None
    num_gpus: int | None = None
    resources: dict[str, float] = {}
    env: dict[str, str] = {}
    runtime_bundle: RuntimeBundlePayload | None = None


class StopRayPayload(BaseModel):
    force: bool = False
    version: str = "2.58.0"
    runtime_bundle: RuntimeBundlePayload | None = None


class GetRayStatusPayload(BaseModel):
    """``GET_RAY_STATUS`` payload.

    All fields are optional: the probe attaches in-process to the *local*
    cluster (``ray.init(address="auto")``) — there is no remote ``address`` to
    dial into.  ``expected_version`` selects the intended cluster version for
    the parity check; ``include_serve``/``include_metrics``/``include_state``
    opt into the additive sub-tiers (Serve / metrics targets / State API) so a
    plain probe stays cheap.
    """

    expected_version: str | None = None
    credential_ref: str | None = None
    include_serve: bool = True
    include_metrics: bool = False
    include_state: bool = False


class GetRayServeStatusPayload(BaseModel):
    """``GET_RAY_SERVE_STATUS`` payload (additive Serve-tier command)."""

    app_name: str | None = None  # optional: report a single app
    credential_ref: str | None = None


class RunServeAppPayload(BaseModel):
    """``RUN_SERVE_APP`` payload (Phase 3: deploy an LLM Serve application).

    ``app_name`` is the *explicit* Ray Serve application name.  Deploying with
    a name makes ``serve.run`` a per-application operation: it replaces only
    this application and never touches other named apps on the cluster.

    ``llm_serving_args`` is the ``LLMServingArgs``-shaped document compiled by
    the backend (``llm_configs`` + ``ingress_cls_config``); it is validated by
    Ray's ``build_openai_app`` on the agent side.
    """

    app_name: str
    llm_serving_args: dict[str, Any]
    # Serve HTTP proxy placement, applied when Serve first starts on the
    # cluster (``serve.start``); ignored by Ray once Serve is running.
    proxy_location: str | None = None
    http_options: dict[str, Any] | None = None


class DeleteServeAppPayload(BaseModel):
    """``DELETE_SERVE_APP`` payload (Phase 3: delete a named application)."""

    app_name: str


class RayServeStatusResult(BaseModel):
    """Wire result for ``GET_RAY_SERVE_STATUS``."""

    alive: bool = False
    serve: dict[str, Any] = {}


class RayStatusResult(BaseModel):
    """Backwards-compatible flat status (legacy consumers/tests).

    New consumers should use
    :class:`~llm_port_node_agent.ray.models.RayEnvironmentStatus` directly.
    """

    alive: bool
    version: str | None = None
    num_nodes: int = 0
    nodes: list[dict[str, Any]] = []
    total_gpus: float = 0
    available_gpus: float = 0
    cluster_address: str | None = None

    @classmethod
    def from_environment_status(cls, status: RayEnvironmentStatus) -> "RayStatusResult":
        """Project the flat fields out of the enriched status DTO."""
        return cls(
            alive=status.alive,
            version=status.version,
            num_nodes=status.num_nodes,
            nodes=list(status.nodes),
            total_gpus=status.total_gpus,
            available_gpus=status.available_gpus,
            cluster_address=status.cluster_address,
        )

