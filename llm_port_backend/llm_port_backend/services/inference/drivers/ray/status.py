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
    """Build condition list for the environment status."""
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

    return conditions

