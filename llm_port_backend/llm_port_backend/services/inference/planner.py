"""Multi-Node Network Fabric Planner and Recommendation Engine.

Analyzes physical network topology reported by Node Agents across all enrolled
nodes assigned to an InferenceEnvironment:
- Correlates shared network subnets / CIDRs across nodes.
- Rejects kernel-virtual, down and address-colliding links before scoring.
- Evaluates interconnect quality (RoCE, InfiniBand, Ethernet, link speeds, MTU).
- Draws the conclusions the agent is not allowed to draw (``is_management``).
- Computes deterministic candidate fingerprints:
    candidate_id = "fabric-" + sha256(...)[:16]
- Optionally certifies a candidate with a cheap agent-to-agent TCP challenge.
- Generates ephemeral InferenceEnvironmentPlan DTOs with recommendation scores,
  warnings and blockers.
- Protects against stale plans via inventory digests (HTTP 409 Conflict).
- Re-derives the plan server-side on apply; the request body is never trusted
  as the source of the bindings that will drive the cluster.
- Binds approved high-speed fabrics into the environment's observed state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode, NodeCommandType
from llm_port_backend.services.inference.service import ConflictError, NotFoundError

log = logging.getLogger(__name__)

# Ephemeral probe port range (Phase3_upgrade.md, "Active Network Probing").
_PROBE_PORT_MIN = 45460
_PROBE_PORT_MAX = 45480
# One dial attempt.  The prober keeps retrying for _PROBE_CONNECT_RETRY_SEC
# while the peer is merely not bound yet: listener and probes are separate
# node commands on separate agent websockets, so their start order is not
# guaranteed and the listener may lose the race.
_PROBE_TIMEOUT_SEC = 5.0
_PROBE_CONNECT_RETRY_SEC = 15.0
# How long the listener waits for its probes - necessarily longer than the
# prober's retry window, or it would give up while a peer is still dialling.
_PROBE_LISTEN_TIMEOUT_SEC = 25.0
# Wall-clock budget for one listen/connect command round trip.
_PROBE_COMMAND_BUDGET_SEC = 45.0
_PROBE_COMMAND_TIMEOUT_SEC = 90

# Link types that can never carry a cluster fabric.  Docker bridges in
# particular exist with *identical* RFC1918 addresses on every host, so they
# satisfy a naive "present on all nodes" test and would bind every Ray node to
# the same 172.17.0.1.
_NON_FABRIC_LINK_TYPES = frozenset({"virtual", "loopback"})


class StalePlanError(ConflictError):
    """Raised when an environment plan is applied against modified node inventory."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stale plan: {detail}")


class NodeFabricBinding(BaseModel):
    """A specific network interface binding for a participating node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    interface: str
    ip: str
    netmask: str | None = None
    speed_gbps: float | None = None
    speed_mbps: int | None = None
    link_type: str = "ethernet"
    rdma_device: str | None = None
    pci_address: str | None = None
    mtu: int = 1500
    is_management: bool = False
    is_up: bool = True


class FabricValidation(BaseModel):
    """Result of the cheap agent-to-agent reachability challenge."""

    model_config = ConfigDict(extra="forbid")

    performed: bool = False
    reachable: bool | None = None
    method: str = "tcp_challenge"
    listener_node_id: str | None = None
    probe_results: list[dict[str, Any]] = Field(default_factory=list)
    detail: str = ""


class RuntimeReadiness(BaseModel):
    """Runtime-bundle compatibility/readiness for the planned node set."""

    model_config = ConfigDict(extra="forbid")

    #: The one bundle every member resolved to, when they agree. ``None``
    #: on a mixed cluster -- which is legal, and is why ``node_bundles``
    #: exists: architecture belongs to a machine, not to a cluster.
    bundle_id: str | None = None
    resolved: bool = False
    compatible: bool | None = None
    node_results: dict[str, str] = Field(default_factory=dict)
    #: node id -> the bundle id certified for that node's platform.
    node_bundles: dict[str, str] = Field(default_factory=dict)
    detail: str = ""


class FabricCandidate(BaseModel):
    """A viable multi-node cluster interconnect fabric."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    fabric_type: str  # "roce", "infiniband", "ethernet", "tcp"
    cidr: str
    speed_gbps: float
    mtu: int
    is_management: bool
    isolation_level: str = "isolated_direct"  # "isolated_direct", "shared_management", "external"
    confidence: str = "high"  # "high", "medium", "low"
    score: int
    recommended: bool = False
    recommendation_reason: str = ""
    reasons: list[str] = Field(default_factory=list)
    node_bindings: dict[str, NodeFabricBinding] = Field(default_factory=dict)
    validation: FabricValidation = Field(default_factory=FabricValidation)


class RejectedFabric(BaseModel):
    """A CIDR that was seen on the nodes but cannot carry the cluster."""

    model_config = ConfigDict(extra="forbid")

    cidr: str
    reason: str
    interfaces: list[str] = Field(default_factory=list)


class InferenceEnvironmentPlan(BaseModel):
    """Ephemeral planning and recommendation document."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    environment_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    # node_id -> digest of that node's planner-relevant network facts.  A
    # content digest, not a timestamp: ``InfraNode.updated_at`` moves on every
    # heartbeat, which would make every plan stale within seconds.
    inventory_revisions: dict[str, str] = Field(default_factory=dict)
    candidates: list[FabricCandidate] = Field(default_factory=list)
    rejected: list[RejectedFabric] = Field(default_factory=list)
    recommended_candidate_id: str | None = None
    recommended_head_node_id: str | None = None
    head_selection_reason: str = ""
    runtime: RuntimeReadiness = Field(default_factory=RuntimeReadiness)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


def compute_fabric_fingerprint(
    cidr: str,
    bindings: list[NodeFabricBinding],
) -> str:
    """Compute deterministic SHA-256 fingerprint for a set of node bindings."""
    canonical_items: list[tuple[str, str, str, str, str]] = []
    for b in bindings:
        canonical_items.append((
            b.node_id,
            b.interface,
            b.ip,
            cidr,
            b.rdma_device or "",
        ))
    canonical_items.sort()
    serialized = json.dumps(canonical_items, sort_keys=True)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"fabric-{digest}"


def score_fabric_candidate(
    *,
    fabric_type: str,
    speed_gbps: float,
    mtu: int,
    is_management: bool,
    bindings: list[NodeFabricBinding],
) -> tuple[int, str, str, str, list[str]]:
    """Calculate deterministic recommendation score, confidence, and human rationale.

    Score = S_type + S_speed + S_mtu - P_management
    """
    score = 0
    reasons: list[str] = []

    # Fabric transport type
    norm_type = fabric_type.lower()
    if norm_type == "infiniband":
        score += 10000
        reasons.append("Native InfiniBand transport (+10000)")
    elif norm_type == "roce":
        score += 8000
        reasons.append("High-speed RoCE RDMA transport (+8000)")
    elif norm_type == "ethernet":
        score += 1000
        reasons.append("Standard Ethernet transport (+1000)")
    else:
        score += 500
        reasons.append(f"Generic transport ({norm_type}) (+500)")

    # Bandwidth score
    bandwidth_pts = int(speed_gbps * 10)
    score += bandwidth_pts
    reasons.append(f"Reported link bandwidth {speed_gbps} Gb/s (+{bandwidth_pts})")

    # Jumbo frames
    if mtu >= 9000:
        score += 500
        reasons.append("Jumbo frames MTU >= 9000 (+500)")

    # Isolation and Management classification
    if is_management:
        score -= 5000
        reasons.append("Carries default gateway / management traffic (-5000)")
        confidence = "low"
        isolation_level = "shared_management"
    elif norm_type in ("roce", "infiniband"):
        confidence = "high"
        isolation_level = "isolated_direct"
        reasons.append("Isolated high-speed interconnect fabric (not default route)")
    else:
        confidence = "medium"
        isolation_level = "isolated_direct"

    return score, "; ".join(reasons), confidence, isolation_level, reasons


def compute_inventory_digest(node: InfraNode) -> str:
    """Digest the planner-relevant network facts of one node.

    Used as the plan's inventory revision.  A *content* digest rather than
    ``updated_at``: the node row is touched on every heartbeat (~15 s), so a
    timestamp revision would declare every plan stale before an operator could
    approve it, while never noticing a fabric that changed between two
    heartbeats within the same second.
    """
    network = (node.capabilities_json or {}).get("network") or {}
    fabrics = network.get("fabrics") or []
    canonical = sorted(
        (
            str(f.get("interface") or ""),
            str(f.get("ip") or ""),
            str(f.get("cidr") or ""),
            str(f.get("link_type") or ""),
            str(f.get("rdma_device") or ""),
            str(f.get("speed_mbps") if f.get("speed_mbps") is not None else f.get("speed_gbps")),
            str(f.get("mtu") or ""),
            str(bool(f.get("is_up", True))),
            str(bool(f.get("has_default_route", f.get("is_management", False)))),
        )
        for f in fabrics
        if isinstance(f, dict)
    )
    serialized = json.dumps(canonical, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32]


def _fabric_is_management(fabric: dict[str, Any]) -> bool:
    """Backend conclusion: does this link carry the host's default route?

    The agent reports ``has_default_route`` (a routing-table fact) and must not
    draw this conclusion itself (4A: "the Node Agent reports facts only").
    ``is_management`` is still read as a fallback so an older agent's payload
    keeps working.
    """
    if "has_default_route" in fabric:
        return bool(fabric["has_default_route"])
    return bool(fabric.get("is_management", False))


def _fabric_is_usable(fabric: dict[str, Any]) -> tuple[bool, str]:
    """Can this interface carry a cluster fabric at all?"""
    link_type = str(fabric.get("link_type") or "ethernet").lower()
    if link_type in _NON_FABRIC_LINK_TYPES:
        return False, f"{link_type} interface cannot carry a cluster fabric"
    if fabric.get("is_virtual"):
        return False, "kernel-virtual interface cannot carry a cluster fabric"
    if not fabric.get("is_up", True):
        return False, "interface is down"
    return True, ""


class MultiNodeFabricPlanner:
    """Discovers network candidates, scores recommendations, and applies plans."""

    def __init__(self, session: AsyncSession, *, gateway: Any = None) -> None:
        self.session = session
        # Optional NodeCommandGateway (or session factory) used for the cheap
        # agent-to-agent reachability challenge.  Absent means passive-only
        # planning, and the plan says so in its warnings.
        self._gateway = gateway

    async def plan_environment(
        self,
        environment_id: uuid.UUID,
        *,
        validate: bool | None = None,
    ) -> InferenceEnvironmentPlan:
        """Analyze participating nodes and generate an ephemeral interconnect plan.

        ``validate`` opts into the cheap TCP challenge between the candidate's
        node bindings.  ``None`` means "run it when a command gateway is
        available"; deep bandwidth/RDMA benchmarks remain a separate
        certification action.
        """
        env = await self.session.get(InferenceEnvironment, environment_id)
        if env is None:
            raise NotFoundError("environment", environment_id)

        # Query assigned environment nodes
        node_assoc_stmt = select(InferenceEnvironmentNode).where(
            InferenceEnvironmentNode.environment_id == environment_id,
        )
        assoc_rows = list((await self.session.execute(node_assoc_stmt)).scalars().all())
        if not assoc_rows:
            return InferenceEnvironmentPlan(
                environment_id=str(environment_id),
                head_selection_reason="No nodes assigned to environment",
                blockers=["No nodes assigned to environment"],
            )

        node_ids = [row.node_id for row in assoc_rows]
        node_stmt = select(InfraNode).where(InfraNode.id.in_(node_ids))
        nodes = list((await self.session.execute(node_stmt)).scalars().all())

        warnings: list[str] = []
        blockers: list[str] = []

        # Content digests for stale-plan protection.
        inventory_revisions = {str(n.id): compute_inventory_digest(n) for n in nodes}

        # Map CIDRs across nodes: cidr -> node_id -> fabric_dict
        cidr_map: dict[str, dict[str, dict[str, Any]]] = {}
        rejections: dict[str, RejectedFabric] = {}
        nodes_without_facts: list[str] = []

        def _reject(cidr: str, reason: str, interface: str | None) -> None:
            entry = rejections.get(cidr)
            if entry is None:
                entry = RejectedFabric(cidr=cidr, reason=reason)
                rejections[cidr] = entry
            if interface and interface not in entry.interfaces:
                entry.interfaces.append(interface)

        for n in nodes:
            node_id_str = str(n.id)
            caps = n.capabilities_json or {}
            net_info = caps.get("network") or {}
            fabrics = net_info.get("fabrics") or []
            if not fabrics:
                nodes_without_facts.append(n.agent_id or node_id_str)
                continue

            for f in fabrics:
                cidr = f.get("cidr")
                if not cidr or not f.get("ip"):
                    continue
                usable, why = _fabric_is_usable(f)
                if not usable:
                    _reject(cidr, why, f.get("interface"))
                    continue
                cidr_map.setdefault(cidr, {})[node_id_str] = f

        if nodes_without_facts:
            blockers.append(
                "No network facts reported for node(s) "
                + ", ".join(sorted(nodes_without_facts))
                + " - the agent has not sent an inventory carrying its network summary yet"
            )

        num_required_nodes = len(nodes)
        candidates: list[FabricCandidate] = []

        for cidr, node_dict in cidr_map.items():
            # A candidate fabric must be present on ALL assigned nodes
            if len(node_dict) != num_required_nodes:
                missing = num_required_nodes - len(node_dict)
                _reject(
                    cidr,
                    f"present on only {len(node_dict)} of {num_required_nodes} nodes "
                    f"({missing} missing)",
                    None,
                )
                continue

            # Every node must hold a *distinct* address on the fabric.  A
            # shared bridge subnet hands every host the same .1 address; a
            # cluster bound to it can never form.
            ips = [str(f.get("ip")) for f in node_dict.values()]
            if len(set(ips)) != len(ips):
                duplicates = sorted({ip for ip in ips if ips.count(ip) > 1})
                _reject(
                    cidr,
                    f"the same address ({', '.join(duplicates)}) is present on more than one node",
                    next(iter(node_dict.values())).get("interface"),
                )
                continue

            bindings: list[NodeFabricBinding] = []
            speeds: list[float] = []
            mtus: list[int] = []
            types: list[str] = []
            is_mgmt_flags: list[bool] = []

            for node_id_str, f in node_dict.items():
                sp_gbps = float(f.get("speed_gbps") or 1.0)
                mtu_val = int(f.get("mtu") or 1500)
                ltype = str(f.get("link_type") or "ethernet").lower()
                is_mgmt = _fabric_is_management(f)

                speeds.append(sp_gbps)
                mtus.append(mtu_val)
                types.append(ltype)
                is_mgmt_flags.append(is_mgmt)

                bindings.append(
                    NodeFabricBinding(
                        node_id=node_id_str,
                        interface=f.get("interface", ""),
                        ip=f.get("ip", ""),
                        netmask=f.get("netmask"),
                        speed_gbps=sp_gbps,
                        speed_mbps=f.get("speed_mbps"),
                        link_type=ltype,
                        rdma_device=f.get("rdma_device"),
                        pci_address=f.get("pci_address"),
                        mtu=mtu_val,
                        is_management=is_mgmt,
                        is_up=bool(f.get("is_up", True)),
                    )
                )

            # Fabric type: if all roce -> roce; if all infiniband -> infiniband; else ethernet
            if all(t == "infiniband" for t in types):
                fabric_type = "infiniband"
            elif all(t == "roce" for t in types):
                fabric_type = "roce"
            else:
                fabric_type = "ethernet"

            min_speed = min(speeds) if speeds else 1.0
            min_mtu = min(mtus) if mtus else 1500
            any_mgmt = any(is_mgmt_flags)

            cand_id = compute_fabric_fingerprint(cidr, bindings)
            score, reason, confidence, iso_level, reasons_list = score_fabric_candidate(
                fabric_type=fabric_type,
                speed_gbps=min_speed,
                mtu=min_mtu,
                is_management=any_mgmt,
                bindings=bindings,
            )

            candidates.append(
                FabricCandidate(
                    candidate_id=cand_id,
                    fabric_type=fabric_type,
                    cidr=cidr,
                    speed_gbps=min_speed,
                    mtu=min_mtu,
                    is_management=any_mgmt,
                    isolation_level=iso_level,
                    confidence=confidence,
                    score=score,
                    recommendation_reason=reason,
                    reasons=reasons_list,
                    node_bindings={b.node_id: b for b in bindings},
                )
            )

        # Sort by score, then CIDR, then fingerprint.  The DGX pair carries two
        # equal-scoring 200 Gb/s RoCE fabrics, so without an explicit tie-break
        # the winner was decided by DB row and sysfs iteration order.  CIDR is
        # the primary tie-break rather than the fingerprint because it is
        # stable across node re-enrollment (the fingerprint hashes node ids)
        # and is the key an operator can read off the plan.
        candidates.sort(key=lambda c: (-c.score, c.cidr, c.candidate_id))

        rec_candidate_id: str | None = None
        if candidates:
            candidates[0].recommended = True
            rec_candidate_id = candidates[0].candidate_id
        elif not blockers:
            blockers.append(
                "No interconnect is present on all assigned nodes; "
                "the environment cannot form a cluster"
            )

        # Determine recommended head node
        # Default: lowest agent_id string sort, scheduler eligible, not draining
        eligible_nodes = [
            n for n in nodes
            if not n.draining and not n.maintenance_mode and n.scheduler_eligible
        ]
        if not eligible_nodes:
            eligible_nodes = nodes
            if nodes:
                warnings.append(
                    "No node is both scheduler-eligible and out of maintenance/draining; "
                    "head selection fell back to the full member list"
                )

        eligible_nodes.sort(key=lambda n: n.agent_id or str(n.id))
        rec_head = eligible_nodes[0] if eligible_nodes else None
        rec_head_id = str(rec_head.id) if rec_head else None
        head_reason = (
            f"Selected stable node {rec_head.agent_id} (not draining, scheduler eligible)"
            if rec_head
            else "No eligible nodes available"
        )

        runtime = self._runtime_readiness(nodes)
        if not runtime.compatible:
            # A warning, not a blocker, deliberately.  Binding a fabric and
            # starting a runtime are separate steps: the network can be
            # applied to a set of machines one of which has no image, and
            # saying otherwise would make the plan screen -- where an
            # operator goes to find out *why* -- refuse to produce one.
            #
            # The refusal lives where the image is actually needed:
            # ``RayEnvironmentManager._bundles_for`` fails the environment,
            # naming the node, before any command is issued.
            warnings.append(runtime.detail)

        plan = InferenceEnvironmentPlan(
            environment_id=str(environment_id),
            inventory_revisions=inventory_revisions,
            candidates=candidates,
            rejected=sorted(rejections.values(), key=lambda r: r.cidr),
            recommended_candidate_id=rec_candidate_id,
            recommended_head_node_id=rec_head_id,
            head_selection_reason=head_reason,
            runtime=runtime,
            warnings=warnings,
            blockers=blockers,
        )

        await self._attach_validation(plan, validate=validate)
        return plan

    # ------------------------------------------------------------------
    # Runtime readiness (4A plan output: "runtime compatibility/readiness")
    # ------------------------------------------------------------------

    @staticmethod
    def _runtime_readiness(nodes: list[InfraNode]) -> RuntimeReadiness:
        """Resolve a runtime bundle for each member node.

        Derived from the machines, never pinned on the cluster. A bundle is an
        image built for a CPU architecture and an accelerator generation, so
        asking "which bundle does this cluster use" has no answer once the
        cluster has two kinds of machine in it -- and the answer that was
        given, whichever one an operator picked in the wizard, was pushed to
        every node including the ones it could not run on.

        A node whose platform no bundle covers makes the set incompatible.
        That is a refusal: there is nothing to start it with.
        """
        from llm_port_backend.services.inference.bundles import (  # noqa: PLC0415
            default_bundle_registry,
        )

        if not nodes:
            return RuntimeReadiness(detail="No member nodes to resolve a runtime for")

        results: dict[str, str] = {}
        node_bundles: dict[str, str] = {}
        unresolved: list[str] = []
        for node in nodes:
            bundle = default_bundle_registry.resolve_for_node(node, driver="ray")
            if bundle is None:
                machine = str((node.capabilities_json or {}).get("machine") or "unknown")
                results[str(node.id)] = (
                    f"No certified Ray runtime bundle for this platform ({machine})"
                )
                unresolved.append(node.agent_id or str(node.id))
                continue
            node_bundles[str(node.id)] = bundle.bundle_id
            results[str(node.id)] = (
                f"{bundle.bundle_id} (image {bundle.container.image})"
            )

        if unresolved:
            return RuntimeReadiness(
                resolved=False,
                compatible=False,
                node_results=results,
                node_bundles=node_bundles,
                detail=(
                    "No certified Ray runtime bundle covers "
                    + ", ".join(unresolved)
                    + ". A node needs an image built for its CPU architecture "
                    "and accelerator before it can join a cluster."
                ),
            )

        distinct = sorted(set(node_bundles.values()))
        return RuntimeReadiness(
            bundle_id=distinct[0] if len(distinct) == 1 else None,
            resolved=True,
            compatible=True,
            node_results=results,
            node_bundles=node_bundles,
            detail=(
                f"Every member node runs {distinct[0]}"
                if len(distinct) == 1
                else "Members span "
                f"{len(distinct)} platforms: {', '.join(distinct)}"
            ),
        )

    # ------------------------------------------------------------------
    # Cheap active validation (4A "cheap validation results")
    # ------------------------------------------------------------------

    async def _attach_validation(
        self, plan: InferenceEnvironmentPlan, *, validate: bool | None
    ) -> None:
        """Certify the recommended candidate with a short TCP challenge."""
        if validate is False:
            return
        recommended = next(
            (c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id),
            None,
        )
        if recommended is None or len(recommended.node_bindings) < 2:
            # Single-node environments have nothing to reach across.
            return
        if self._gateway is None:
            plan.warnings.append(
                "Active validation was requested but no node-command gateway is available; "
                "the recommendation rests on passive facts only"
                if validate
                else "Recommendation rests on passive sysfs facts only "
                "(no active reachability probe was run)"
            )
            return

        result = await self._probe_candidate(recommended)
        recommended.validation = result
        if result.reachable is False:
            plan.blockers.append(
                f"Candidate {recommended.candidate_id} ({recommended.cidr}) failed the "
                f"reachability challenge: {result.detail}"
            )
        elif result.reachable is None:
            plan.warnings.append(
                f"Reachability challenge for {recommended.candidate_id} was "
                f"inconclusive: {result.detail}"
            )

    async def _probe_candidate(self, candidate: FabricCandidate) -> FabricValidation:
        """Run one ephemeral listener and probe it from every other binding."""
        from llm_port_backend.services.inference.drivers.ray.commands import (
            NodeCommandGateway,
        )

        # Accept anything that already speaks the gateway protocol (the real
        # gateway, or a test double); otherwise treat it as a session factory.
        gateway = (
            self._gateway
            if hasattr(self._gateway, "issue") and hasattr(self._gateway, "wait")
            else NodeCommandGateway(self._gateway)
        )

        bindings = sorted(candidate.node_bindings.values(), key=lambda b: b.node_id)
        listener, probers = bindings[0], bindings[1:]
        port = secrets.choice(range(_PROBE_PORT_MIN, _PROBE_PORT_MAX + 1))
        token = secrets.token_hex(16)
        nonce = uuid.uuid4().hex[:12]

        validation = FabricValidation(
            performed=True,
            listener_node_id=listener.node_id,
            detail=f"listener {listener.ip}:{port} on {listener.interface}",
        )

        try:
            listen_cmd = await gateway.issue(
                node_id=listener.node_id,
                command_type=NodeCommandType.VALIDATE_FABRIC_LISTEN.value,
                payload={
                    "ip": listener.ip,
                    "port": port,
                    "probe_token": token,
                    "timeout_sec": _PROBE_LISTEN_TIMEOUT_SEC,
                    # One dial per peer: a listener that closed after the first
                    # would fail every probe but one on a 3+ node environment.
                    "expected_probes": len(probers),
                },
                idempotency_key=f"fabric-probe:{candidate.candidate_id}:{nonce}:listen",
                timeout_sec=_PROBE_COMMAND_TIMEOUT_SEC,
            )
            # The listener accepts exactly one connection and exits, so the
            # probes have to be issued while it is still waiting.
            probe_cmds = []
            for prober in probers:
                probe_cmds.append((
                    prober,
                    await gateway.issue(
                        node_id=prober.node_id,
                        command_type=NodeCommandType.VALIDATE_FABRIC_CONNECT.value,
                        payload={
                            "target_ip": listener.ip,
                            "target_port": port,
                            "source_ip": prober.ip,
                            "probe_token": token,
                            "timeout_sec": _PROBE_TIMEOUT_SEC,
                            "retry_for_sec": _PROBE_CONNECT_RETRY_SEC,
                        },
                        idempotency_key=(
                            f"fabric-probe:{candidate.candidate_id}:{nonce}:connect:{prober.node_id}"
                        ),
                        timeout_sec=_PROBE_COMMAND_TIMEOUT_SEC,
                    ),
                ))

            listen_result, probe_results = await asyncio.gather(
                gateway.wait(listen_cmd.id, budget_sec=_PROBE_COMMAND_BUDGET_SEC),
                asyncio.gather(
                    *(
                        gateway.wait(c.id, budget_sec=_PROBE_COMMAND_BUDGET_SEC)
                        for _, c in probe_cmds
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001 - planning must not fail on a probe
            log.warning("Fabric probe for %s failed to run: %s", candidate.candidate_id, exc)
            validation.detail = f"probe could not be issued: {exc}"
            return validation

        reachable: bool | None = True
        details: list[str] = []
        for (prober, _cmd), result in zip(probe_cmds, probe_results, strict=False):
            payload = dict((result.result_json if result is not None else None) or {})
            entry: dict[str, Any] = {
                "node_id": prober.node_id,
                "source_ip": prober.ip,
                "target_ip": listener.ip,
                "port": port,
                "reachable": bool(payload.get("reachable", False)),
                "rtt_ms": payload.get("rtt_ms"),
                "error": payload.get("error"),
            }
            validation.probe_results.append(entry)
            if result is None:
                reachable = None if reachable is not False else reachable
                details.append(f"{prober.ip}: no result within {_PROBE_COMMAND_BUDGET_SEC:.0f}s")
            elif not entry["reachable"]:
                reachable = False
                err = entry["error"] or "unreachable"
                details.append(f"{prober.ip} -> {listener.ip}:{port}: {err}")

        listen_payload = dict(
            (listen_result.result_json if listen_result is not None else None) or {}
        )
        if listen_result is None:
            reachable = None if reachable is not False else reachable
            details.append("listener produced no result")
        elif not listen_payload.get("listening", False):
            reachable = False
            details.append(
                f"listener could not bind {listener.ip}:{port}: {listen_payload.get('error')}"
            )

        validation.reachable = reachable
        validation.detail = (
            "; ".join(details)
            if details
            else (
                f"{len(validation.probe_results)} probe(s) reached {listener.ip}:{port} "
                f"over {candidate.cidr}"
            )
        )
        return validation

    async def apply_plan(
        self,
        environment_id: uuid.UUID,
        plan: InferenceEnvironmentPlan | None = None,
        *,
        selected_candidate_id: str | None = None,
    ) -> InferenceEnvironment:
        """Apply an approved interconnect plan to the environment.

        The submitted *plan* is an **approval receipt**, never the source of
        the bindings that get written.  Everything that ends up driving the
        cluster is re-derived here from the current node inventory, because the
        body arrives from any caller holding ``inference.environments:operate``
        and its ``node_bindings`` would otherwise bind Ray to arbitrary
        addresses and interface names.

        The receipt is used for exactly two things: asserting it was issued for
        *this* environment, and stale detection against the freshly computed
        inventory digests (which also catches nodes added or removed after the
        plan was generated - a per-node loop over the body cannot).
        """
        env = await self.session.get(InferenceEnvironment, environment_id)
        if env is None:
            raise NotFoundError("environment", environment_id)

        # 1. Re-derive the plan server-side from the live inventory.  Active
        #    validation is skipped: it already ran at plan time and the probe
        #    is not free.
        fresh = await self.plan_environment(environment_id, validate=False)

        # 2. Stale-plan protection against the re-derived inventory digests.
        if plan is not None:
            if plan.environment_id and plan.environment_id != str(environment_id):
                raise ConflictError(
                    f"Plan was generated for environment {plan.environment_id}, "
                    f"not {environment_id}"
                )
            self._assert_not_stale(plan.inventory_revisions, fresh.inventory_revisions)

        # 3. Resolve the candidate out of the *re-derived* candidate set.
        target_cand_id = selected_candidate_id or (
            plan.recommended_candidate_id if plan is not None else None
        ) or fresh.recommended_candidate_id
        if not target_cand_id:
            detail = "; ".join(fresh.blockers) or "no viable interconnect was found"
            raise ConflictError(f"Plan contains no valid candidates to apply: {detail}")

        selected_cand = next(
            (c for c in fresh.candidates if c.candidate_id == target_cand_id),
            None,
        )
        if selected_cand is None:
            if plan is not None and any(
                c.candidate_id == target_cand_id for c in plan.candidates
            ):
                # The operator approved a fabric that the nodes no longer
                # present the same way; the fingerprint is the whole point.
                raise StalePlanError(
                    f"Candidate {target_cand_id} no longer matches the observed topology"
                )
            raise NotFoundError("fabric candidate", target_cand_id)

        if fresh.blockers:
            raise ConflictError(
                "Environment cannot be bound: " + "; ".join(fresh.blockers)
            )

        # 4. Store operator intent in InferenceEnvironment.config_json (Amendment 1)
        cfg = dict(env.config_json or {})
        cfg["interconnect_policy"] = {
            "mode": "auto" if not selected_candidate_id else "explicit",
            "selected_candidate_id": selected_cand.candidate_id,
            "fabric_type": selected_cand.fabric_type,
            "required_speed_gbps": selected_cand.speed_gbps,
            "failover_policy": cfg.get("failover_policy", "manual"),
        }
        resolved_fabric_data = {
            "candidate_id": selected_cand.candidate_id,
            "fabric_type": selected_cand.fabric_type,
            "cidr": selected_cand.cidr,
            "speed_gbps": selected_cand.speed_gbps,
            "mtu": selected_cand.mtu,
            "is_management": selected_cand.is_management,
            "isolation_level": selected_cand.isolation_level,
            "node_bindings": {
                nid: b.model_dump() for nid, b in selected_cand.node_bindings.items()
            },
        }
        # Backward-compat projection in config_json
        cfg["resolved_fabric"] = resolved_fabric_data
        env.config_json = cfg

        # Authoritative storage in observed_status_json (Amendment 1).  The
        # reconcile observer merges rather than replaces, so this survives the
        # pass that the generation bump below schedules.
        obs = dict(env.observed_status_json or {})
        obs["resolved_fabric"] = resolved_fabric_data
        obs["network"] = {
            "selected_candidate_id": selected_cand.candidate_id,
            "node_bindings": resolved_fabric_data["node_bindings"],
        }
        env.observed_status_json = obs

        # 5. Set Head Node & synchronize InferenceEnvironmentNode.role (Amendment 22)
        head_node_id = fresh.recommended_head_node_id
        if head_node_id:
            try:
                head_uuid = uuid.UUID(head_node_id)
            except ValueError:
                head_uuid = None
            if head_uuid is not None:
                env.head_node_id = head_uuid

                # Query and synchronize associated environment nodes
                node_assoc_stmt = select(InferenceEnvironmentNode).where(
                    InferenceEnvironmentNode.environment_id == environment_id,
                )
                assoc_rows = list(
                    (await self.session.execute(node_assoc_stmt)).scalars().all()
                )
                for assoc in assoc_rows:
                    assoc.role = "head" if assoc.node_id == head_uuid else "worker"

        env.generation += 1
        await self.session.flush()
        await self.session.refresh(env)
        return env

    @staticmethod
    def _assert_not_stale(
        approved: dict[str, str],
        current: dict[str, str],
    ) -> None:
        """Compare the approved inventory digests with the current ones.

        Both directions matter: a node whose facts changed invalidates the
        approval, and so does a node *added to* or *removed from* the
        environment after the plan was generated - which a loop over the
        submitted revisions alone can never see.
        """
        if not approved:
            if not current:
                # The environment has no members at all; the re-derived plan's
                # blockers say so far more usefully than a staleness error.
                return
            # An empty receipt against a populated environment is not an
            # opt-out of validation - it is a plan this planner did not issue.
            raise StalePlanError("plan carries no inventory revisions")

        added = sorted(set(current) - set(approved))
        if added:
            raise StalePlanError(
                f"Node(s) {', '.join(added)} joined the environment after plan generation"
            )
        removed = sorted(set(approved) - set(current))
        if removed:
            raise StalePlanError(
                f"Node(s) {', '.join(removed)} left the environment after plan generation"
            )
        changed = sorted(nid for nid, digest in approved.items() if current[nid] != digest)
        if changed:
            raise StalePlanError(
                f"Node(s) {', '.join(changed)} reported new network inventory "
                "after plan generation"
            )
