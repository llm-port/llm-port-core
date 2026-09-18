"""Status normalization functions for Ray cluster state."""

from llm_port_backend.db.models.inference import EnvironmentStatus
from llm_port_backend.services.inference.drivers.ray.schemas import RayClusterStatus


def map_cluster_to_environment_status(status: RayClusterStatus) -> EnvironmentStatus:
    """Map Ray cluster health to inference environment status."""
    if not status.alive:
        return EnvironmentStatus.FAILED
    if status.num_nodes == 0:
        return EnvironmentStatus.PREPARING
    if status.all_healthy:
        return EnvironmentStatus.READY
    return EnvironmentStatus.DEGRADED


def build_environment_conditions(
    status: RayClusterStatus, expected_nodes: int
) -> list[dict[str, str]]:
    """Build condition list for the environment status.

    HeadActive/WorkersJoined are cluster-tier conditions (they reflect
    liveness and membership).  ServeReady/MetricsDiscovery are additive
    tier conditions — they surface the state of the Serve and metrics
    tiers without ever changing the environment status (spec: tiers must
    not gate core health).
    """
    conditions = []

    # Head Active Condition
    if status.alive:
        conditions.append({
            "type": "HeadActive",
            "status": "True",
            "reason": "HeadResponding",
            "message": "Ray head node is alive and responding.",
        })
    else:
        conditions.append({
            "type": "HeadActive",
            "status": "False",
            "reason": "HeadUnreachable",
            "message": "Ray head node is unreachable.",
        })

    # Workers Joined Condition
    if status.num_nodes >= expected_nodes and expected_nodes > 0:
        conditions.append({
            "type": "WorkersJoined",
            "status": "True",
            "reason": "AllWorkersJoined",
            "message": f"All {expected_nodes} nodes have joined.",
        })
    elif status.num_nodes > 1:
         conditions.append({
            "type": "WorkersJoined",
            "status": "False",
            "reason": "PartialWorkersJoined",
            "message": f"Only {status.num_nodes} of {expected_nodes} nodes joined.",
        })

    # Serve Ready condition (additive tier, non-gating).  Only reported when
    # the probe actually carried the Serve tier (None = tier not present for
    # this probe); a dead cluster cannot report Serve state.
    if status.alive and status.serve is not None:
        if status.serve_available:
            conditions.append({
                "type": "ServeReady",
                "status": "True",
                "reason": "ServeAvailable",
                "message": "Ray Serve control plane is available.",
            })
        else:
            conditions.append({
                "type": "ServeReady",
                "status": "False",
                "reason": "ServeNotObserved",
                "message": "Ray Serve control plane is not available on this cluster.",
            })

    # Metrics discovery condition (additive tier, non-gating) — same
    # presence rule as ServeReady.
    if status.alive and status.metrics is not None:
        if status.metrics_enabled:
            conditions.append({
                "type": "MetricsDiscovery",
                "status": "True",
                "reason": "TargetsDiscovered",
                "message": "Prometheus scrape targets discovered from ray.nodes().",
            })
        else:
            conditions.append({
                "type": "MetricsDiscovery",
                "status": "False",
                "reason": "NoTargets",
                "message": "No Prometheus scrape targets discovered.",
            })

    return conditions

