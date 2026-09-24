"""Taking over Ray clusters a server's machines still run, after the server lost them.

When an LLM.Port server is rebuilt without its database -- or with an old
backup -- its machines go on running the clusters and models the old server
started. Recreating them restarts every model, and while the old serving apps
run they hold their GPUs, so a recreated deployment often cannot even fit.
This module lets the new server take them over as they are.

The machines' agents report whether they run LLM.Port's Ray runtime; for one
that does, the agent reads the cluster from Ray itself (``inspect.py`` on the
agent): its members, and for each model it serves the ``LLMConfig`` it was
deployed with. From that this module rebuilds what the old server had --
cluster, members, deployments under their original ids (the apps are named
after them), their models -- and before recording a deployment as applied it
has the agent check, with Ray's own configuration model, that what this
server would deploy is exactly what runs. The reconciler, which redeploys
whenever the two differ, then observes and restarts nothing.

What cannot be read back is what only the old server knew: the chat alias, the
names shown in the console. Those the admin gives when taking over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from llm_port_backend.services.inference.drivers.ray.compiler import (
    DEFAULT_LOGGING_CONFIG,
)

#: Apps LLM.Port deploys are named ``llmport-<deployment id>``.
APP_PREFIX = "llmport-"
_APP = re.compile(r"^llmport-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
#: A Hugging Face cache directory: ``models--<org>--<name>``.
_HF_CACHE_DIR = re.compile(r"models--([^/]+?)--([^/]+)")

_TOPOLOGY_KEYS = {"tensor_parallel_size", "pipeline_parallel_size"}


def deployment_id_of(app_name: str) -> str | None:
    """The deployment id an app is named after, or ``None`` for an app LLM.Port did not deploy."""
    match = _APP.match(app_name or "")
    return match.group(1) if match else None


def hf_repo_of(model_source: str) -> str | None:
    """The Hugging Face repository a cached snapshot path belongs to, if it is one."""
    match = _HF_CACHE_DIR.search(model_source or "")
    return f"{match.group(1)}/{match.group(2)}" if match else None


@dataclass
class RecoveredDeployment:
    """One served model, as it can be rebuilt from its running ``LLMConfig``."""

    app_name: str
    deployment_id: str
    #: The name the model is served as (``model_loading_config.model_id``).
    model_id: str
    #: Where the engine loads the weights from, on the machines.
    model_source: str
    hf_repo_id: str | None
    spec: dict[str, Any]
    #: What could not be carried into the spec, and why -- shown to the admin.
    notes: list[str] = field(default_factory=list)


def spec_from_llm_config(llm_config: dict[str, Any], *, alias: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """The deployment spec that compiles to *llm_config*, and what it could not carry.

    The inverse of ``compiler.compile_spec`` for what LLM.Port itself emits.
    Whether it truly compiles back to the same configuration is checked by the
    agent against the running app before anything is recorded as applied --
    this function need not be trusted blindly.
    """
    notes: list[str] = []
    engine_kwargs = dict(llm_config.get("engine_kwargs") or {})
    topology: dict[str, Any] = {}
    for key in _TOPOLOGY_KEYS:
        if key in engine_kwargs:
            topology[key] = int(engine_kwargs.pop(key))
    devices = topology.get("tensor_parallel_size", 1) * topology.get("pipeline_parallel_size", 1)

    # Copies: fixed, or autoscaled between bounds.
    deployment_config = dict(llm_config.get("deployment_config") or {})
    scale: dict[str, Any]
    extensions: dict[str, Any] = {}
    autoscaling = deployment_config.get("autoscaling_config")
    if autoscaling:
        scale = {"autoscale": {
            "min_replicas": int(autoscaling.get("min_replicas", 1)),
            "max_replicas": int(autoscaling.get("max_replicas", 1)),
        }}
        if autoscaling.get("upscale_delay_s") is not None:
            scale["autoscale"]["scale_up_timeout"] = float(autoscaling["upscale_delay_s"])
        if autoscaling.get("downscale_delay_s") is not None:
            scale["autoscale"]["scale_down_timeout"] = float(autoscaling["downscale_delay_s"])
        if autoscaling.get("target_ongoing_requests") is not None:
            extensions.setdefault("ray", {})["target_ongoing_requests"] = autoscaling["target_ongoing_requests"]
    else:
        replicas = int(deployment_config.get("num_replicas") or 1)
        scale = {"replicas": max(replicas, 1)}
        if replicas == 0:
            notes.append("It runs with no copies (stopped); taken over as one copy.")
    if deployment_config.get("logging_config") not in (None, DEFAULT_LOGGING_CONFIG):
        notes.append("Its logging settings are not LLM.Port's; they will be LLM.Port's on the next deploy.")

    # Resources: one GPU per device unless a placement says otherwise.
    replica: dict[str, Any] = {"gpus": devices}
    resources: dict[str, Any] = {"replica": replica}
    placement = llm_config.get("placement_group_config")
    if placement:
        bundle = dict((placement.get("bundle_per_worker") or {}))
        if "GPU" in bundle:
            replica["gpus"] = float(bundle["GPU"]) * devices
            if replica["gpus"].is_integer():
                replica["gpus"] = int(replica["gpus"])
        if "CPU" in bundle:
            replica["cpu"] = float(bundle["CPU"]) * devices
        strategy = placement.get("strategy")
        if strategy:
            resources["placement"] = strategy
    if llm_config.get("accelerator_type"):
        replica["accelerator"] = llm_config["accelerator_type"]

    env_vars = ((llm_config.get("runtime_env") or {}).get("env_vars") or {})
    if env_vars:
        extensions["env_vars"] = dict(env_vars)

    loading = llm_config.get("model_loading_config") or {}
    source = str(loading.get("model_source") or "")
    artifacts = (
        {"source": "local_path", "root_path": source}
        if source.startswith("/")
        else {"source": "remote", "root_path": source}
    )

    spec: dict[str, Any] = {
        "api_version": "inference.llmport.ai/v1alpha1",
        "engine": {"name": str(llm_config.get("llm_engine") or "vllm").lower(), "config": engine_kwargs},
        "scale": scale,
        "resources": resources,
        "artifacts": artifacts,
        "service": {"path": "/v1", "openai": True, **({"alias": alias} if alias else {})},
    }
    if topology:
        spec["topology"] = topology
    if extensions:
        spec["extensions"] = extensions
    return spec, notes


def recover_deployment(app: dict[str, Any], *, alias: str | None = None) -> RecoveredDeployment | None:
    """A served app as a deployment to take over, or ``None`` when it is not one of LLM.Port's."""
    deployment_id = deployment_id_of(app.get("name", ""))
    configs = app.get("llm_configs") or []
    if deployment_id is None or not configs:
        return None
    notes: list[str] = []
    if len(configs) > 1:
        notes.append(f"It serves {len(configs)} models in one app; only the first is taken over.")
    llm_config = configs[0]
    spec, spec_notes = spec_from_llm_config(llm_config, alias=alias)
    loading = llm_config.get("model_loading_config") or {}
    source = str(loading.get("model_source") or "")
    return RecoveredDeployment(
        app_name=app["name"],
        deployment_id=deployment_id,
        model_id=str(loading.get("model_id") or ""),
        model_source=source,
        hf_repo_id=hf_repo_of(source),
        spec=spec,
        notes=notes + spec_notes,
    )


# ---------------------------------------------------------------------------
# Finding the clusters, and taking one over
# ---------------------------------------------------------------------------


class TakeoverError(Exception):
    """A step that cannot be taken; the message says why, for the operator."""


#: Machines asked at once when looking for clusters.
_CONCURRENT_DESCRIBES = 4


def _addresses(node: Any) -> set[str]:
    """Every address a machine is known by: its host, and each network it reported."""
    found: set[str] = set()
    host = str(getattr(node, "host", "") or "").strip()
    if host.startswith("host="):
        host = host[len("host="):]
    if host:
        found.add(host)
    network = (getattr(node, "capabilities_json", None) or {}).get("network") or {}
    for fabric in network.get("fabrics") or []:
        if isinstance(fabric, dict) and fabric.get("ip"):
            found.add(str(fabric["ip"]))
    return found


def _machine_for(ip: str | None, machines: list[Any]) -> Any | None:
    if not ip:
        return None
    for machine in machines:
        if ip in _addresses(machine):
            return machine
    return None


def _replicas(spec: dict[str, Any]) -> int:
    scale = spec.get("scale") or {}
    if scale.get("autoscale"):
        return int(scale["autoscale"].get("min_replicas") or 1)
    return int(scale.get("replicas") or 1)


def _running_copies(app: dict[str, Any]) -> int:
    return sum(
        int(d.get("running_replicas") or 0)
        for d in app.get("deployments") or []
        if str(d.get("name", "")).startswith("LLMServer")
    )


def view(
    doc: dict[str, Any],
    *,
    machines: list[Any],
    members_elsewhere: set[Any],
    existing_deployments: set[str],
) -> dict[str, Any]:
    """A cluster as a machine described it, and whether it can be taken over."""
    members: list[dict[str, Any]] = []
    unknown: list[str] = []
    for node in doc.get("nodes") or []:
        if not node.get("alive"):
            continue  # Ray keeps records of nodes long gone
        machine = _machine_for(node.get("ip"), machines)
        entry = {
            "ip": node.get("ip"),
            "hostname": node.get("hostname"),
            "role": "head" if node.get("is_head") else "worker",
            "gpus": node.get("gpus"),
            "runtime_node_id": node.get("node_id"),
            "node_id": str(machine.id) if machine is not None else None,
            "name": (machine.agent_id if machine is not None else None) or node.get("hostname"),
        }
        if machine is None:
            unknown.append(str(node.get("hostname") or node.get("ip")))
        members.append(entry)

    apps: list[dict[str, Any]] = []
    other_apps: list[str] = []
    for app in doc.get("apps") or []:
        recovered = recover_deployment(app)
        if recovered is None:
            other_apps.append(str(app.get("name")))
            continue
        apps.append({
            "app_name": recovered.app_name,
            "deployment_id": recovered.deployment_id,
            "model_id": recovered.model_id,
            "model_source": recovered.model_source,
            "hf_repo_id": recovered.hf_repo_id,
            "copies": _replicas(recovered.spec),
            "gpus_per_copy": ((recovered.spec.get("resources") or {}).get("replica") or {}).get("gpus"),
            "engine": (recovered.spec.get("engine") or {}).get("config") or {},
            "status": app.get("status"),
            "running_copies": _running_copies(app),
            "suggested_alias": recovered.model_id.lower(),
            "notes": recovered.notes,
            "already_known": recovered.deployment_id in existing_deployments,
        })

    blockers: list[str] = []
    head = next((m for m in members if m["role"] == "head"), None)
    if head is None:
        blockers.append("No live head in the cluster.")
    if unknown:
        blockers.append(
            f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} in the cluster but not in this "
            "fleet. Approve the machine first: its agent files a join request once it reaches this server."
        )
    ours = {str(n) for n in members_elsewhere}
    elsewhere = [m["name"] for m in members if m["node_id"] and m["node_id"] in ours]
    if elsewhere:
        blockers.append(f"{', '.join(elsewhere)} already belong to a cluster this server manages.")
    known = [a["model_id"] for a in apps if a["already_known"]]
    if known:
        blockers.append(f"This server already has the deployment of {', '.join(known)}.")
    return {
        "address": doc.get("gcs_address"),
        "runtime_version": doc.get("ray_version"),
        "image": (doc.get("container") or {}).get("image"),
        "head": head,
        "members": members,
        "apps": apps,
        "other_apps": other_apps,
        "can_take_over": not blockers,
        "blockers": blockers,
        "errors": doc.get("errors") or [],
    }


async def _machines(session: Any) -> list[Any]:
    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.models.node_control import InfraNode  # noqa: PLC0415

    return list((await session.execute(select(InfraNode))).scalars())


async def _members_elsewhere(session: Any) -> set[Any]:
    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.models.inference import InferenceEnvironmentNode  # noqa: PLC0415

    return set((await session.execute(select(InferenceEnvironmentNode.node_id))).scalars())


async def _deployment_ids(session: Any) -> set[str]:
    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.models.inference import InferenceDeployment  # noqa: PLC0415

    return {str(i) for i in (await session.execute(select(InferenceDeployment.id))).scalars()}


def _client(gateway: Any) -> Any:
    from llm_port_backend.services.inference.drivers.ray.client import RayClusterClient  # noqa: PLC0415

    return RayClusterClient(gateway)


async def find(session: Any, gateway: Any) -> dict[str, list[dict[str, Any]]]:
    """The Ray clusters this server's machines run and it does not manage.

    Asks each machine that reports LLM.Port's Ray runtime running, and is in
    no cluster here, what its cluster serves; machines in the same cluster
    are listed once. Machines that could not say are listed apart
    (``unreadable``), with why.
    """
    import asyncio  # noqa: PLC0415

    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO  # noqa: PLC0415

    machines = await _machines(session)
    elsewhere = await _members_elsewhere(session)
    snapshots = await NodeControlDAO(session).latest_inventory_snapshots(node_ids=[m.id for m in machines])
    candidates = [
        m for m in machines
        if m.id not in elsewhere
        and ((getattr(snapshots.get(m.id), "inventory_json", None) or {}).get("ray_runtime") or {}).get("running")
    ]
    client = _client(gateway)
    gate = asyncio.Semaphore(_CONCURRENT_DESCRIBES)

    async def describe(machine: Any) -> dict[str, Any] | None:
        async with gate:
            try:
                return await client.describe_cluster(node_id=machine.id, budget_sec=60)
            except Exception as exc:  # noqa: BLE001 - one machine failing is reported, not fatal
                return {"attached": False, "error": str(exc)}

    docs = await asyncio.gather(*(describe(m) for m in candidates))
    existing = await _deployment_ids(session)
    clusters: dict[str, dict[str, Any]] = {}
    unreadable: list[dict[str, Any]] = []
    for machine, doc in zip(candidates, docs, strict=True):
        if not doc or not doc.get("attached"):
            unreadable.append({
                "node_id": str(machine.id),
                "name": machine.agent_id,
                "error": (doc or {}).get("error")
                or "The machine did not answer (its agent may be older than 0.1.12).",
            })
            continue
        key = str(doc.get("gcs_address") or machine.id)
        if key in clusters:
            continue  # another member described the same cluster
        clusters[key] = view(doc, machines=machines, members_elsewhere=elsewhere, existing_deployments=existing)
        clusters[key]["described_by"] = str(machine.id)
    return {"clusters": list(clusters.values()), "unreadable": unreadable}


def serving_args(recovered: RecoveredDeployment, model: Any) -> dict[str, Any]:
    """What the deployment reconciler will compile for this deployment -- the same call."""
    from llm_port_backend.services.inference.drivers.ray.compiler import compile_deployment  # noqa: PLC0415

    return compile_deployment(
        spec_data=recovered.spec,
        model_display_name=model.display_name,
        model_source=getattr(model, "source", "huggingface") or "huggingface",
        hf_repo_id=model.hf_repo_id,
        hf_revision=model.hf_revision,
        availability_root_path=None,
        desired_state="active",
    )


def config_hash(args: dict[str, Any]) -> str:
    """The deployment reconciler's hash of what it applied."""
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    return hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()


async def _model_for(session: Any, recovered: RecoveredDeployment) -> Any:
    """The catalogue's record of the model, or a new one that compiles to the same served name."""
    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus  # noqa: PLC0415
    from llm_port_backend.services.inference.drivers.ray.compiler import _sanitize_model_id  # noqa: PLC0415

    for model in (await session.execute(select(LLMModel))).scalars():
        same_repo = recovered.hf_repo_id is None or model.hf_repo_id == recovered.hf_repo_id
        try:
            served_as = _sanitize_model_id(model.display_name, model.hf_repo_id)
        except Exception:  # noqa: BLE001 - a record with neither name cannot be this model
            continue
        if same_repo and served_as == recovered.model_id:
            return model
    model = LLMModel(
        display_name=recovered.model_id,
        source=ModelSource.HUGGINGFACE if recovered.hf_repo_id else ModelSource.LOCAL_PATH,
        hf_repo_id=recovered.hf_repo_id,
        hf_revision=None,
        status=ModelStatus.AVAILABLE,
    )
    session.add(model)
    await session.flush()
    return model


async def _control_plane_for(session: Any, name: str, token: str) -> Any:
    """A Ray control plane holding *token*: the first one if it has none yet, else one of its own."""
    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.models.inference import ControlPlaneStatus, InferenceControlPlane  # noqa: PLC0415
    from llm_port_backend.services.inference.drivers.ray.secrets import (  # noqa: PLC0415
        retrieve_cluster_token,
        store_cluster_token,
    )

    planes = list((await session.execute(
        select(InferenceControlPlane)
        .where(InferenceControlPlane.driver == "ray")
        .order_by(InferenceControlPlane.created_at),
    )).scalars())
    for plane in planes:
        if not plane.credential_ref:
            plane.credential_ref = await store_cluster_token(session, plane.id, token)
            return plane
        if await retrieve_cluster_token(session, plane.credential_ref) == token:
            return plane
    names = {p.name for p in planes}
    plane = InferenceControlPlane(
        name="Default-Ray" if "Default-Ray" not in names else f"Ray ({name})",
        driver="ray",
        status=ControlPlaneStatus.PENDING.value,
        config_json={},
    )
    session.add(plane)
    await session.flush()
    plane.credential_ref = await store_cluster_token(session, plane.id, token)
    return plane


def cluster_status(doc: dict[str, Any]) -> Any:
    """The cluster as the environment reconciler records an observation of it."""
    from llm_port_backend.services.inference.drivers.ray.schemas import RayClusterStatus  # noqa: PLC0415

    alive = [n for n in doc.get("nodes") or [] if n.get("alive")]
    return RayClusterStatus(
        alive=True,
        version=doc.get("ray_version"),
        num_nodes=len(alive),
        nodes=[{
            "node_id": n.get("node_id"),
            "node_ip": n.get("ip"),
            "node_manager_address": n.get("ip"),
            "node_name": n.get("hostname"),
            "alive": True,
            "is_head": bool(n.get("is_head")),
        } for n in alive],
        total_gpus=sum(float(n.get("gpus") or 0) for n in alive),
        cluster_address=doc.get("gcs_address"),
        head_address=doc.get("gcs_address"),
    )


async def take_over(
    session: Any,
    gateway: Any,
    *,
    node_id: Any,
    name: str,
    aliases: dict[str, str] | None = None,
    user_id: Any = None,
) -> dict[str, Any]:
    """Take over the cluster *node_id* is in, and every model it serves, as they run.

    Nothing is recorded unless every model's rebuilt configuration is what
    runs: otherwise the reconciler would redeploy it -- a restart -- and the
    difference is reported instead.
    """
    import uuid as _uuid  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.models.inference import (  # noqa: PLC0415
        DeploymentDesiredState,
        DeploymentPhase,
        EnvironmentDesiredState,
        EnvironmentNodeRole,
        EnvironmentStatus,
        InferenceDeployment,
        InferenceEnvironment,
        InferenceEnvironmentNode,
    )
    from llm_port_backend.services.inference.drivers.ray.driver import RayDriver  # noqa: PLC0415
    from llm_port_backend.services.inference.drivers.ray.secrets import unseal_token  # noqa: PLC0415
    from llm_port_backend.services.inference.drivers.ray.status import (  # noqa: PLC0415
        build_environment_conditions,
    )
    from llm_port_backend.services.inference.planner import MultiNodeFabricPlanner  # noqa: PLC0415
    from llm_port_backend.services.inference.pools import ComputePoolCoordinator  # noqa: PLC0415
    from llm_port_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    name = (name or "").strip()
    if not name:
        raise TakeoverError("Give the cluster a name.")
    taken_name = await session.execute(select(InferenceEnvironment.id).where(InferenceEnvironment.name == name))
    if taken_name.scalar_one_or_none() is not None:
        raise TakeoverError(f"A cluster named {name} already exists.")
    aliases = dict(aliases or {})
    client = _client(gateway)

    # 1. What runs, from the machine.
    doc = await client.describe_cluster(node_id=node_id)
    if not doc or not doc.get("attached"):
        raise TakeoverError((doc or {}).get("error") or "The machine did not describe its cluster.")
    machines = await _machines(session)
    seen = view(
        doc, machines=machines,
        members_elsewhere=await _members_elsewhere(session),
        existing_deployments=await _deployment_ids(session),
    )
    if not seen["can_take_over"]:
        raise TakeoverError(" ".join(seen["blockers"]))

    # 2. What this server would deploy for each model, and whether that is it.
    plans = []
    for app in doc.get("apps") or []:
        alias = aliases.get(str(app.get("name")))
        recovered = recover_deployment(app, alias=alias)
        if recovered is None:
            continue
        alias = alias or recovered.model_id.lower()
        recovered.spec.setdefault("service", {})["alias"] = alias
        model = await _model_for(session, recovered)
        plans.append((recovered, model, serving_args(recovered, model), alias, app))
    checked = await client.describe_cluster(
        node_id=node_id,
        verify={recovered.app_name: args for recovered, _m, args, _a, _app in plans},
        hand_over_token=True,
    )
    if not checked or not checked.get("attached"):
        raise TakeoverError((checked or {}).get("error") or "The machine did not answer the check.")
    by_app = {a.get("name"): a for a in checked.get("apps") or []}
    differ = []
    for recovered, *_rest in plans:
        verdict = (by_app.get(recovered.app_name) or {}).get("verify") or {}
        if not verdict.get("equal"):
            detail = verdict.get("error") or "; ".join(
                f"{d['path']}: running {d.get('running')!r}, would deploy {d.get('candidate')!r}"
                for d in (verdict.get("diff") or [])[:5]
            ) or "no answer"
            differ.append(f"{recovered.model_id} ({detail})")
    if differ:
        await session.rollback()
        raise TakeoverError(
            "Taking over would restart " + "; ".join(differ)
            + ": what this server would deploy is not exactly what runs. Nothing was changed."
        )
    sealed = checked.get("cluster_token_sealed")
    token = unseal_token(sealed) if sealed else None
    if not token:
        await session.rollback()
        raise TakeoverError(checked.get("token_error") or "The machine did not hand over the cluster's token.")

    # 3. The cluster and its members, recorded as already up.
    plane = await _control_plane_for(session, name, token)
    head = seen["head"]
    now = datetime.now(tz=UTC)
    environment = InferenceEnvironment(
        control_plane_id=plane.id,
        name=name,
        status=EnvironmentStatus.READY.value,
        desired_state=EnvironmentDesiredState.RUNNING.value,
        runtime_version=doc.get("ray_version"),
        head_node_id=_uuid.UUID(head["node_id"]),
        address=doc.get("gcs_address"),
        config_json={},
        capabilities_json={},
        observed_status_json={},
        generation=1,
        observed_generation=1,
    )
    session.add(environment)
    await session.flush()
    pools = ComputePoolCoordinator(session)
    by_id = {str(m.id): m for m in machines}
    for member in seen["members"]:
        row = InferenceEnvironmentNode(
            environment_id=environment.id,
            node_id=_uuid.UUID(member["node_id"]),
            role=(EnvironmentNodeRole.HEAD if member["role"] == "head" else EnvironmentNodeRole.WORKER).value,
            member_status="alive",
            joined_at=now,
        )
        session.add(row)
        await session.flush()
        await pools.assign(environment_id=environment.id, node=by_id[member["node_id"]], member=row)

    # The network each machine is in the cluster by. The health check finds
    # members by these addresses: without them a cluster on its own fabric
    # (the DGX pair: RoCE) would read as having lost every machine, and be
    # re-formed -- every model restarted.
    observed: dict[str, Any] = {"converged_generation": environment.generation}
    ray_ips = {m["node_id"]: m["ip"] for m in seen["members"]}
    if not all(ip == by_id[nid].host for nid, ip in ray_ips.items()):
        plan = await MultiNodeFabricPlanner(session).plan_environment(environment.id, validate=False)
        chosen = next(
            (c for c in plan.candidates
             if all(c.node_bindings.get(nid) is not None and c.node_bindings[nid].ip == ip
                    for nid, ip in ray_ips.items())),
            None,
        )
        if chosen is None:
            await session.rollback()
            raise TakeoverError(
                "Cannot tell which network the cluster runs on: its machines' addresses in Ray "
                f"({', '.join(sorted(ray_ips.values()))}) are not on one network they reported. Nothing was changed."
            )
        bindings = {nid: b.model_dump() for nid, b in chosen.node_bindings.items()}
        environment.config_json = {"interconnect_policy": {
            "mode": "explicit",
            "selected_candidate_id": chosen.candidate_id,
            "fabric_type": chosen.fabric_type,
            "required_speed_gbps": chosen.speed_gbps,
            "failover_policy": "manual",
        }}
        observed["resolved_fabric"] = {
            "candidate_id": chosen.candidate_id,
            "fabric_type": chosen.fabric_type,
            "cidr": chosen.cidr,
            "speed_gbps": chosen.speed_gbps,
            "mtu": chosen.mtu,
            "is_management": chosen.is_management,
            "isolation_level": chosen.isolation_level,
            "node_bindings": bindings,
        }
        observed["network"] = {"selected_candidate_id": chosen.candidate_id, "node_bindings": bindings}
    status = cluster_status(doc)
    observed["cluster"] = status.model_dump()
    observed["conditions"] = build_environment_conditions(status, len(seen["members"]))
    observed["takeover"] = {
        "at": now.isoformat(),
        "by": str(user_id) if user_id else None,
        "from_machine": head.get("name"),
        "gcs_address": doc.get("gcs_address"),
    }
    environment.observed_status_json = observed
    try:
        environment.capabilities_json = (await RayDriver().capabilities(environment)).raw or {}
    except Exception:  # noqa: BLE001 - the reconciler writes it on its first pass anyway
        environment.capabilities_json = {}

    # 4. The models, under their own ids, recorded as applied on this head.
    head_ray_id = head.get("runtime_node_id")
    taken = []
    names = set((await session.execute(select(InferenceDeployment.name))).scalars())
    for recovered, model, args, alias, app in plans:
        deployment_name = alias if alias not in names else f"{alias}-{recovered.deployment_id[:8]}"
        names.add(deployment_name)
        session.add(InferenceDeployment(
            id=_uuid.UUID(recovered.deployment_id),
            environment_id=environment.id,
            model_id=model.id,
            name=deployment_name,
            spec_json=recovered.spec,
            desired_state=DeploymentDesiredState.ACTIVE.value,
            phase=DeploymentPhase.RUNNING.value,
            phase_message="Taken over as it ran.",
            generation=1,
            observed_generation=1,
            ready_replicas=_running_copies(app),
            total_replicas=_replicas(recovered.spec),
            observed_status_json={
                "applied_config_hash": config_hash(args),
                "applied_head": head_ray_id,
                # Where Ray mounted it: the endpoint is published there.
                "observation": {"run": {
                    "app": recovered.app_name, "deployed": True, "cached": True,
                    **({"route_prefix": app["route_prefix"]} if app.get("route_prefix") else {}),
                }},
                "takeover": {"at": now.isoformat(), "notes": recovered.notes},
            },
        ))
        taken.append({
            "deployment_id": recovered.deployment_id,
            "name": deployment_name,
            "alias": alias,
            "model": recovered.model_id,
            "notes": recovered.notes,
        })
    wake_reconciler_after_commit(session)
    await session.commit()
    return {
        "environment_id": str(environment.id),
        "name": name,
        "control_plane": plane.name,
        "members": seen["members"],
        "deployments": taken,
    }
