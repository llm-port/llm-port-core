"""Ray Core Python-API client — the single SDK compatibility boundary.

Spec rule (section 2): live cluster status/controls go through Ray **Core
Python APIs**; the CLI is reserved for bootstrap/process (``ray start`` /
``ray stop``) in :mod:`llm_port_node_agent.ray.runtime`.

This module is the ONLY place in the agent that calls into the Ray SDK for
cluster state.  Everything else (``manager``/``serve``/``metrics``) consumes
the DTOs normalized here, so a Ray-version change touches one file.

Design constraints (all verified against installed ``ray==2.58.0``):

* **No Dashboard dependency.**  ``ray.nodes()`` / ``ray.cluster_resources()`` /
  ``ray.available_resources()`` are ``@DeveloperAPI`` but they resolve through
  the ``ray.state`` accessor to the C++ **GCS accessor** — a direct GCS gRPC
  round trip.  No Dashboard component is involved.  (The separate
  ``ray.util.state`` *SDK* DOES require Dashboard; it is quarantined in
  :mod:`.state` as Tier B.)
* **In-process attach, never Ray Client.**  ``ray.init(address="auto",
  ignore_reinit_error=True)`` attaches the *local* worker to the local cluster
  over ``session_local``.  No ``ray://`` URI.
* **Idempotent attach.**  ``ensure_attached`` is safe to call from every
  heartbeat; it does not init/shutdown per call.  A stale attach (cluster
  stopped underneath us) is detected by a GCS round-trip and one reconnect
  attempt.
* **No ``ray._private.*``.**  Only ``ray`` public surface: ``ray.init``,
  ``ray.shutdown``, ``ray.is_initialized``, ``ray.nodes``,
  ``ray.cluster_resources``, ``ray.available_resources``,
  ``ray.get_runtime_context``, ``ray.__version__``.

Node-record keys in use were verified live on a
``--include-dashboard=false`` cluster (2026-09-17): ``NodeID``, ``Alive``,
``NodeManagerAddress``, ``NodeManagerPort``, ``NodeName``, ``Resources``,
``MetricsExportPort``.  Head detection uses the ``node:__internal_head__``
resource label; the recorded Ray version is the *agent's packaged* SDK
version (the node table carries no version field in 2.58).
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Any, Callable, Mapping

from llm_port_node_agent.ray import errors, models
from llm_port_node_agent.ray.models import RayNodeStatus

log = logging.getLogger(__name__)

#: Resource key that marks the head node (present only on the head's record).
_HEAD_RESOURCE_KEY = "node:__internal_head__"
#: ``accelerator_type:...`` resources are accelerator labels, not CPU/GPU.
_ACCELERATOR_PREFIX = "accelerator_type:"


class RayCoreClient:
    """Idempotent in-process attach + normalization of Ray Core state."""

    def __init__(self, *, ray_module: ModuleType | None = None) -> None:
        # Injectable for tests; production uses the real ``ray`` module.
        self._ray = ray_module

    # ------------------------------------------------------------------
    # module access
    # ------------------------------------------------------------------

    @property
    def ray(self) -> ModuleType:
        ray = self._ray
        if ray is None:
            import ray  # noqa: PLC0415  (lazy: keep module import cheap)

            self._ray = ray
            ray = ray
        return ray

    # ------------------------------------------------------------------
    # attach / detach
    # ------------------------------------------------------------------

    def ensure_attached(self) -> None:
        """Attach in-process to the local cluster, idempotently.

        Raises:
            errors.RayAttachError: no local cluster reachable
                (including ``address="auto"`` finding nothing after the
                reconnect attempt).
            errors.RayVersionMismatchError: attached cluster version
                differs from the packaged SDK version.
        """
        ray = self.ray
        try:
            if not ray.is_initialized():
                ray.init(address="auto", ignore_reinit_error=True)
            # GCS round-trip: proves the attach is live.  On a stale attach
            # this raises; fall through to one reconnect attempt.
            ray.nodes()
        except errors.RayAttachError:
            raise
        except Exception as exc:
            self._reconnect_once(exc)

    def _reconnect_once(self, original: Exception) -> None:
        ray = self.ray
        try:
            ray.shutdown()
        except Exception:  # pragma: no cover - best effort
            log.debug("shutdown during reconnect failed", exc_info=True)
        try:
            ray.init(address="auto", ignore_reinit_error=True)
            ray.nodes()
        except Exception as reconnect_exc:
            raise errors.RayAttachError(
                "Could not attach to a local Ray cluster "
                "(no cluster running, or GCS unreachable)",
                detail=f"init: {original!r}; reconnect: {reconnect_exc!r}",
            ) from reconnect_exc

    def check_version(self, expected_version: str | None) -> None:
        """Validate the intended Ray version against the packaged SDK.

        The bootstrap CLI and the SDK come from the *same* wheel (the agent
        ships ``ray[serve]==2.58.0``), so ``ray.__version__`` is the version of
        the Ray that started this node.  Ray 2.58 does not expose the running
        cluster's version through any *public* SDK surface (``ray.nodes()``
        carries no version field and ``RuntimeContext`` has no version
        property), so version parity is checked here against the *intended*
        version supplied by the environment/payload.  A divergence flags a
        configuration/parity problem (e.g. a stray system Ray was bootstrapped
        instead of the packaged one).
        """
        ray = self.ray
        sdk = getattr(ray, "__version__", None)
        if not expected_version or not sdk:
            return
        if expected_version != sdk:
            raise errors.RayVersionMismatchError(
                f"Intended Ray version {expected_version!r} does not match the "
                f"packaged SDK version {sdk!r}; bootstrap CLI and SDK come from "
                "the same wheel, so this indicates a stray/other Ray "
                "installation was used to start this node",
                detail=f"intended={expected_version} sdk={sdk}",
            )

    def disconnect(self) -> None:
        """Detach this process from the cluster.

        This is ``ray.shutdown()`` only — it releases the local driver
        connection.  It does NOT and must NOT run ``ray stop`` (the cluster
        keeps running for other consumers).
        """
        try:
            if self.ray.is_initialized():
                self.ray.shutdown()
        except Exception:  # pragma: no cover - best effort
            log.debug("ray.shutdown() failed", exc_info=True)

    # ------------------------------------------------------------------
    # cluster state (GCS-direct, Dashboard-independent)
    # ------------------------------------------------------------------

    def probe(self, *, expected_version: str | None = None) -> models.RayEnvironmentStatus:
        """Produce the cluster-tier (Tier A) status DTO.

        This is what ``GET_RAY_STATUS`` answers with (the manager folds the
        Serve/metrics/state tiers on top).  On any attach failure it returns
        ``alive=False`` rather than raising — a dead/absent cluster is a
        *state*, not an error.
        """
        ray = self.ray
        try:
            self.ensure_attached()
            self.check_version(expected_version)
        except errors.RayVersionMismatchError as exc:
            # A version mismatch is a reported state, not a crash: surface it
            # but do not claim the cluster is alive.
            log.warning("attach failed: %s", exc)
            return models.RayEnvironmentStatus(alive=False, version=exc.detail)
        except errors.RayAttachError:
            return models.RayEnvironmentStatus(alive=False)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("probe failed: %s", exc)
            return models.RayEnvironmentStatus(alive=False)

        try:
            raw_nodes = ray.nodes()
            raw_total = ray.cluster_resources() or {}
            raw_avail = ray.available_resources() or {}
        except Exception as exc:
            # Attached but the state round-trip failed — treat as not alive.
            log.warning("cluster state round-trip failed: %s", exc)
            return models.RayEnvironmentStatus(alive=False)

        sdk_version = getattr(ray, "__version__", None)
        ray_version = expected_version or sdk_version
        nodes, head_ip, head_port = self._normalize_nodes(raw_nodes, ray_version)
        alive = any(n.alive for n in nodes)

        cluster_address = None
        try:
            gcs = ray.get_runtime_context().gcs_address
            if gcs:
                cluster_address = str(gcs)
        except Exception:
            cluster_address = None

        resources = models.RayResourceTotals.model_validate(
            self._normalize_resources(raw_total)
        )
        available = models.RayAvailableResources.model_validate(
            self._normalize_resources(raw_avail, available_shape=True)
        )

        return models.RayEnvironmentStatus(
            alive=alive,
            version=ray_version,
            num_nodes=len(nodes),
            nodes=[self._node_to_wire(n) for n in nodes],
            resources=resources,
            available=available,
            total_gpus=resources.gpu,
            available_gpus=available.gpu,
            total_cpus=resources.cpu,
            cluster_address=cluster_address,
            head_address=head_ip,
            capabilities=models.RayCapabilities(cluster_sdk=True),
        )

    # ------------------------------------------------------------------
    # normalization helpers
    # ------------------------------------------------------------------

    def _normalize_nodes(
        self, raw_nodes: list[dict[str, Any]], ray_version: str | None
    ) -> tuple[list[RayNodeStatus], str | None, int | None]:
        """Map raw ``ray.nodes()`` dicts to DTOs; return (nodes, head_ip, head_port)."""
        nodes: list[RayNodeStatus] = []
        head_ip: str | None = None
        head_port: int | None = None
        for rec in raw_nodes or []:
            if not isinstance(rec, dict):
                continue
            node_id = str(rec.get("NodeID", ""))
            if not node_id:
                continue
            resources = self._node_resources(rec.get("Resources"))
            is_head = _HEAD_RESOURCE_KEY in resources
            node_ip = str(rec.get("NodeManagerAddress", "") or "")
            node_manager_port = self._as_int(rec.get("NodeManagerPort"))
            node = RayNodeStatus(
                node_id=node_id,
                node_ip=node_ip,
                node_manager_address=node_ip,
                node_manager_port=node_manager_port,
                node_name=rec.get("NodeName"),
                alive=bool(rec.get("Alive")),
                is_head=is_head,
                ray_version=ray_version,
                resources={k: float(v) for k, v in resources.items() if self._is_num(v)},
                metrics_export_port=self._as_int(rec.get("MetricsExportPort")),
            )
            if is_head and node.alive and head_ip is None and node_ip:
                head_ip = node_ip
                head_port = node_manager_port
            nodes.append(node)
        return nodes, head_ip, head_port

    def _node_resources(self, raw: Any) -> dict[str, float]:
        if not isinstance(raw, dict):
            return {}
        return {str(k): self._as_float(v) for k, v in raw.items()}

    def _normalize_resources(
        self, raw: dict[str, Any], *, available_shape: bool = False
    ) -> dict[str, Any]:
        total: dict[str, float] = self._node_resources(raw)
        accelerators = {
            k: v for k, v in total.items() if k.startswith(_ACCELERATOR_PREFIX)
        }
        return {
            "cpu": total.get("CPU", 0.0),
            "gpu": total.get("GPU", 0.0),
            "memory": total.get("memory", 0.0),
            "object_store_memory": total.get("object_store_memory", 0.0),
            "accelerators": accelerators,
            "other": {
                k: v
                for k, v in total.items()
                if k not in ("CPU", "GPU", "memory", "object_store_memory")
                and not k.startswith(_ACCELERATOR_PREFIX)
                and not k.startswith("node:")
            },
        }

    def _node_to_wire(self, node: RayNodeStatus) -> dict[str, Any]:
        """Backwards-compatible node dict: old keys + new ``ray_version`` /
        ``metrics_export_port`` (additive).  ``cpus`` is float like before."""
        return {
            "node_id": node.node_id,
            "ip": node.node_ip,
            "node_ip": node.node_ip,
            "node_manager_address": node.node_manager_address,
            "node_manager_port": node.node_manager_port,
            "state": "ALIVE" if node.alive else "DEAD",
            "is_head": node.is_head,
            "is_head_node": node.is_head,
            "gpus": node.resources.get("GPU", 0.0),
            "cpus": node.resources.get("CPU"),
            "ray_version": node.ray_version,
            "metrics_export_port": node.metrics_export_port,
        }

    @staticmethod
    def _is_num(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    @staticmethod
    def _as_float(v: Any) -> float:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _as_int(v: Any) -> int | None:
        if v is None or isinstance(v, bool):
            return None
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return None
        return iv if iv > 0 else None
