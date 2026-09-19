"""Ray-migration live smoke harness (host side).

Runs with the llm_port_backend venv against the isolated smoke database
(``LLM_PORT_BACKEND_DB_BASE=llm_port_smoke``); see README.md in this
directory for the full runbook.  Each subcommand is one stage of

    enroll -> cluster up -> ModelAvailability -> deployment
           -> RUNNING -> OpenAI endpoint -> delete -> stop

so the node agent can be observed between stages.  Stage ids are persisted in
``state.json`` under ``$RAY_SMOKE_WORKDIR`` (default: ``.work/`` next to this
file).

The default path is the **product path**: the harness only creates rows and
watches; the backend's own reconciler loop does all the work.  Two diagnostic
modes remain for isolating failures:

* ``reconcile-dep-product`` calls the shipped
  ``reconciliation.reconcile_deployment`` seam directly (with a timeout);
* ``reconcile-env-shim`` / ``reconcile-dep-shim`` drive the *same* real
  managers with a ``NodeControlService`` that commits each issued command and
  re-reads command rows fresh, to separate node-agent / Ray problems from the
  backend's transaction handling.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from llm_port_backend.db.models import load_all_models

load_all_models()

from llm_port_backend.db.dao.inference_dao import EndpointDAO, ModelAvailabilityDAO  # noqa: E402
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO  # noqa: E402
from llm_port_backend.db.models.inference import (  # noqa: E402
    InferenceDeployment,
    InferenceEnvironment,
    ModelAvailabilityStatus,
    ModelSourceKind,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus  # noqa: E402
from llm_port_backend.db.models.node_control import InfraNode, InfraNodeCommand  # noqa: E402
from llm_port_backend.services.inference.drivers.ray.driver import RayDriver  # noqa: E402
from llm_port_backend.services.inference.reconciliation import (  # noqa: E402
    ReconciliationContext,
    reconcile_deployment,
)
from llm_port_backend.services.inference.service import (  # noqa: E402
    ControlPlaneService,
    DeploymentService,
    EnvironmentService,
)
from llm_port_backend.services.nodes.service import NodeControlService  # noqa: E402
from llm_port_backend.settings import settings  # noqa: E402

WORKDIR = Path(os.environ.get("RAY_SMOKE_WORKDIR") or Path(__file__).with_name(".work"))
WORKDIR.mkdir(parents=True, exist_ok=True)
STATE = WORKDIR / "state.json"
MODEL_REPO = "Qwen/Qwen2.5-0.5B-Instruct"
MODEL_ROOT = "/models/qwen2.5-0.5b-instruct"  # on the head node (agent container)

engine = create_async_engine(str(settings.db_url), pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(**updates: Any) -> dict[str, Any]:
    state = load_state() | {k: str(v) for k, v in updates.items()}
    STATE.write_text(json.dumps(state, indent=2))
    return state


def ts() -> str:
    return datetime.now(tz=UTC).strftime("%H:%M:%S")


def out(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def node_control(session) -> NodeControlService:
    return NodeControlService(
        dao=NodeControlDAO(session),
        pepper=settings.settings_master_key,
        enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
        default_command_timeout_sec=settings.node_command_default_timeout_sec,
    )


class CommittingNodeControl(NodeControlService):
    """Harness shim: make issued commands visible and read them fresh.

    Shares the manager's session.  ``issue_command`` commits so the stream
    handler (a different session) can dispatch the row; ``get_command``
    re-selects with ``populate_existing`` so a status written by the stream
    handler is actually seen instead of the identity-map copy.
    """

    async def issue_command(self, **kwargs: Any) -> InfraNodeCommand:
        command = await super().issue_command(**kwargs)
        await self._dao.session.commit()
        print(f"  [{ts()}] issued+committed {kwargs['command_type']} -> {command.id}")
        return command

    async def get_command(self, *, command_id: uuid.UUID) -> InfraNodeCommand | None:
        result = await self._dao.session.execute(
            select(InfraNodeCommand)
            .where(InfraNodeCommand.id == command_id)
            .execution_options(populate_existing=True),
        )
        return result.scalar_one_or_none()


def shim_control(session) -> CommittingNodeControl:
    return CommittingNodeControl(
        dao=NodeControlDAO(session),
        pepper=settings.settings_master_key,
        enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
        default_command_timeout_sec=settings.node_command_default_timeout_sec,
    )


async def command_rows(session, node_id: uuid.UUID, since: datetime | None = None) -> list[dict]:
    q = select(InfraNodeCommand).where(InfraNodeCommand.node_id == node_id)
    if since is not None:
        q = q.where(InfraNodeCommand.issued_at >= since)
    q = q.order_by(InfraNodeCommand.issued_at).execution_options(populate_existing=True)
    rows = (await session.execute(q)).scalars().all()
    return [
        {
            "type": r.command_type,
            "status": r.status,
            "issued": r.issued_at.strftime("%H:%M:%S") if r.issued_at else None,
            "dispatched": r.dispatched_at.strftime("%H:%M:%S") if r.dispatched_at else None,
            "completed": r.completed_at.strftime("%H:%M:%S") if r.completed_at else None,
            "error": (r.error_message or "")[:300] or None,
        }
        for r in rows
    ]


def env_view(env: InferenceEnvironment) -> dict:
    obs = (env.observed_status_json or {}).get("observation") or {}
    cluster = (env.observed_status_json or {}).get("cluster") or {}
    return {
        "status": env.status,
        "generation": env.generation,
        "observed_generation": env.observed_generation,
        "address": env.address,
        "reason": obs.get("reason"),
        "cluster_alive": cluster.get("alive"),
        "num_nodes": cluster.get("num_nodes"),
        "gpus": cluster.get("total_gpus"),
        "conditions": [
            f"{c['type']}={c['status']}"
            for c in (env.observed_status_json or {}).get("conditions") or []
        ],
    }


def dep_view(dep: InferenceDeployment) -> dict:
    return {
        "phase": dep.phase,
        "message": (dep.phase_message or "")[:400],
        "generation": dep.generation,
        "observed_generation": dep.observed_generation,
        "ready_replicas": dep.ready_replicas,
        "observation": (dep.observed_status_json or {}).get("observation"),
    }


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


async def cmd_enroll_token(_: argparse.Namespace) -> None:
    async with Session() as s:
        payload = await node_control(s).create_enrollment_token(issued_by=None, note="ray smoke")
        await s.commit()
    save_state(enrollment_token_id=payload["id"])
    print(payload["token"])


async def cmd_wait_node(args: argparse.Namespace) -> None:
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        async with Session() as s:
            node = (
                await s.execute(select(InfraNode).order_by(InfraNode.created_at.desc()).limit(1))
            ).scalar_one_or_none()
            if node is not None and node.last_seen is not None:
                save_state(node_id=node.id)
                out({"node_id": node.id, "agent_id": node.agent_id, "host": node.host,
                     "status": node.status, "last_seen": node.last_seen})
                return
        await asyncio.sleep(3)
    sys.exit("no enrolled node with a heartbeat before timeout")


async def cmd_create_env(_: argparse.Namespace) -> None:
    st = load_state()
    node_id = uuid.UUID(st["node_id"])
    async with Session() as s:
        cp = await ControlPlaneService(s).create(name="smoke-ray", driver="ray")
        envs = EnvironmentService(s)
        env = await envs.create(
            control_plane_id=cp.id,
            name="smoke-env",
            ray_version="2.58.0",
            head_node_id=node_id,
            config={"ray_version": "2.58.0", "head_port": 6379,
                    "dashboard_port": 8265, "dashboard_host": "127.0.0.1",
                    # Platform flag via the product (F28 node-level path):
                    # vLLM >= 0.26 needs pinned memory/UVA on WSL2 hosts.
                    "node_env_vars": {"VLLM_WSL2_ENABLE_PIN_MEMORY": "1"}},
        )
        await envs.add_node(env.id, node_id, role="head")
        await s.commit()
        env = await s.get(InferenceEnvironment, env.id)
        save_state(control_plane_id=cp.id, environment_id=env.id, env_created_at=datetime.now(tz=UTC).isoformat())
        out({"control_plane_id": cp.id, "environment_id": env.id, **env_view(env)})


async def cmd_watch_env(args: argparse.Namespace) -> None:
    """Observe the environment + node command rows (product loop does the work)."""
    st = load_state()
    env_id, node_id = uuid.UUID(st["environment_id"]), uuid.UUID(st["node_id"])
    since = datetime.fromisoformat(st["env_created_at"])
    deadline = time.monotonic() + args.seconds
    last = None
    while True:
        async with Session() as s:
            env = await s.get(InferenceEnvironment, env_id)
            snap = {"env": env_view(env), "commands": await command_rows(s, node_id, since)}
        if snap != last:
            print(f"--- {ts()}")
            out(snap)
            last = snap
        converged = snap["env"]["observed_generation"] == snap["env"]["generation"]
        if (args.until and converged and snap["env"]["status"] in args.until) or time.monotonic() > deadline:
            return
        await asyncio.sleep(5)


async def cmd_watch_dep(args: argparse.Namespace) -> None:
    """Observe the deployment + its commands while the backend loop drives it."""
    st = load_state()
    dep_id, node_id = uuid.UUID(st["deployment_id"]), uuid.UUID(st["node_id"])
    since = datetime.fromisoformat(st["dep_created_at"])
    deadline = time.monotonic() + args.seconds
    last = None
    while True:
        async with Session() as s:
            dep = await s.get(InferenceDeployment, dep_id)
            view = dep_view(dep)
            snap = {"dep": {k: view[k] for k in ("phase", "message", "ready_replicas",
                                                   "generation", "observed_generation")},
                    "commands": [(c["type"], c["status"]) for c in await command_rows(s, node_id, since)]}
        if snap != last:
            print(f"--- {ts()}")
            out(snap)
            last = snap
        # Only a phase the loop has observed at the *current* generation counts:
        # right after set-desired the old phase is still shown.
        observed = view["observed_generation"] == view["generation"]
        if args.until_observed:
            done = observed  # e.g. after request-reconcile: the phase does not change
        else:
            done = observed and view["phase"] in args.until
        if done or time.monotonic() > deadline:
            return
        await asyncio.sleep(5)


async def cmd_reconcile_env_shim(_: argparse.Namespace) -> None:
    st = load_state()
    env_id = uuid.UUID(st["environment_id"])
    async with Session() as s:
        env = await s.get(InferenceEnvironment, env_id)
        t0 = time.monotonic()
        await RayDriver().environment_manager.reconcile_environment(
            s, env, node_control=shim_control(s),
        )
        await s.commit()
        print(f"pass took {time.monotonic() - t0:.1f}s")
        out(env_view(env))
        out((env.observed_status_json or {}).get("cluster"))


async def cmd_seed_model(_: argparse.Namespace) -> None:
    st = load_state()
    node_id = uuid.UUID(st["node_id"])
    async with Session() as s:
        model = LLMModel(
            display_name="Qwen2.5-0.5B-Instruct",
            source=ModelSource.HUGGINGFACE,
            hf_repo_id=MODEL_REPO,
            status=ModelStatus.AVAILABLE,
        )
        s.add(model)
        await s.flush()
        row = await ModelAvailabilityDAO(s).upsert(
            model.id,
            node_id,
            status=ModelAvailabilityStatus.READY.value,
            source_kind=ModelSourceKind.PRE_EXISTING.value,
            root_path=MODEL_ROOT,
            progress=1.0,
            status_message="seeded by smoke harness (weights pulled directly with hf download)",
            ready_at=datetime.now(tz=UTC),
        )
        await s.commit()
        save_state(model_id=model.id)
        out({"model_id": model.id, "availability": {"status": row.status, "root_path": row.root_path}})


def smoke_spec() -> dict[str, Any]:
    return {
        "api_version": "inference.llmport.ai/v1alpha1",
        "engine": {
            "name": "vllm",
            # TITAN RTX is Turing (sm_75): no bf16; share the GPU with the
            # workstation's embed/rerank containers.
            "config": {"dtype": "half", "max_model_len": 2048,
                       "gpu_memory_utilization": 0.3, "enforce_eager": True},
        },
        "scale": {"replicas": 1},
        "resources": {"replica": {"gpus": 1}},
        "artifacts": {"source": "sync"},
        "service": {"path": "/v1"},
    }


async def cmd_create_deployment(_: argparse.Namespace) -> None:
    st = load_state()
    async with Session() as s:
        dep = await DeploymentService(s).create(
            environment_id=uuid.UUID(st["environment_id"]),
            model_id=uuid.UUID(st["model_id"]),
            name="smoke-qwen",
            spec=smoke_spec(),
        )
        await s.commit()
        dep = await s.get(InferenceDeployment, dep.id)
        save_state(deployment_id=dep.id, dep_created_at=datetime.now(tz=UTC).isoformat())
        out({"deployment_id": dep.id, "desired_state": dep.desired_state, **dep_view(dep)})


async def cmd_request_reconcile(_: argparse.Namespace) -> None:
    """Call what ``POST /api/inference/deployments/{id}/reconcile`` calls.

    A healthy deployment must only be re-observed by the next loop pass: no
    ``run_serve_app`` and no replica restart.
    """
    dep_id = uuid.UUID(load_state()["deployment_id"])
    async with Session() as s:
        dep = await DeploymentService(s).request_reconcile(dep_id)
        await s.commit()
        out({
            "phase": dep.phase,
            "generation": dep.generation,
            "observed_generation": dep.observed_generation,
            "applied_config_hash_kept": bool((dep.observed_status_json or {}).get("applied_config_hash")),
        })


async def cmd_reconcile_dep_product(args: argparse.Namespace) -> None:
    """The shipped seam, unshimmed: what a deployment loop would call."""
    st = load_state()
    dep_id, node_id = uuid.UUID(st["deployment_id"]), uuid.UUID(st["node_id"])
    started = datetime.now(tz=UTC)
    async with Session() as s:
        dep = await s.get(InferenceDeployment, dep_id)
        context = ReconciliationContext.for_session(s)
        try:
            result = await asyncio.wait_for(reconcile_deployment(context, dep), timeout=args.timeout)
            await s.commit()
            print("returned:")
            out(result)
            out(dep_view(dep))
        except TimeoutError:
            print(f"TIMEOUT after {args.timeout}s inside reconcile_deployment")
            async with Session() as other:  # what the stream handler can see
                print("command rows visible to another session during the pass:")
                out(await command_rows(other, node_id, started))
            await s.rollback()


async def cmd_reconcile_dep_shim(args: argparse.Namespace) -> None:
    """Drive the real RayDeploymentManager pass-by-pass (stands in for the missing loop)."""
    st = load_state()
    dep_id = uuid.UUID(st["deployment_id"])
    for n in range(1, args.max_passes + 1):
        async with Session() as s:
            dep = await s.get(InferenceDeployment, dep_id)
            t0 = time.monotonic()
            print(f"=== pass {n} [{ts()}] desired={dep.desired_state} phase(before)={dep.phase}")
            await RayDriver().deployment_manager.reconcile_deployment(
                s, dep, node_control=shim_control(s),
            )
            await s.commit()
            view = dep_view(dep)
            print(f"pass {n} took {time.monotonic() - t0:.1f}s")
            out(view)
            if view["phase"] in args.stop_on:
                break
        await asyncio.sleep(args.gap)


async def cmd_set_desired(args: argparse.Namespace) -> None:
    st = load_state()
    async with Session() as s:
        if args.kind == "deployment":
            row = await s.get(InferenceDeployment, uuid.UUID(st["deployment_id"]))
        else:
            row = await s.get(InferenceEnvironment, uuid.UUID(st["environment_id"]))
        row.desired_state = args.state
        row.generation += 1
        await s.commit()
        print(f"{args.kind} desired_state={args.state} generation={row.generation}")


async def cmd_show(_: argparse.Namespace) -> None:
    st = load_state()
    async with Session() as s:
        res: dict[str, Any] = {"state": st}
        if "environment_id" in st:
            env = await s.get(InferenceEnvironment, uuid.UUID(st["environment_id"]))
            res["environment"] = env_view(env)
        if "deployment_id" in st:
            dep = await s.get(InferenceDeployment, uuid.UUID(st["deployment_id"]))
            res["deployment"] = dep_view(dep)
            res["endpoints"] = [
                {"name": e.name, "address": e.address, "path": e.path, "status": e.status,
                 "published": e.published_json}
                for e in await EndpointDAO(s).list_for_deployment(dep.id)
            ]
        out(res)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("enroll-token")
    w = sub.add_parser("wait-node")
    w.add_argument("--timeout", type=int, default=120)
    sub.add_parser("create-env")
    we = sub.add_parser("watch-env")
    we.add_argument("--seconds", type=int, default=180)
    we.add_argument("--until", nargs="*", default=[], help="stop once observed in one of these statuses")
    wd = sub.add_parser("watch-dep")
    wd.add_argument("--seconds", type=int, default=600)
    wd.add_argument("--until", nargs="*", default=["running", "failed", "deleted", "stopped"])
    wd.add_argument("--until-observed", action="store_true",
                    help="stop once the loop has observed the current generation")
    sub.add_parser("reconcile-env-shim")
    sub.add_parser("seed-model")
    sub.add_parser("create-deployment")
    sub.add_parser("request-reconcile")
    rp = sub.add_parser("reconcile-dep-product")
    rp.add_argument("--timeout", type=int, default=150)
    rs = sub.add_parser("reconcile-dep-shim")
    rs.add_argument("--max-passes", type=int, default=12)
    rs.add_argument("--gap", type=float, default=5.0)
    rs.add_argument("--stop-on", nargs="*", default=["running", "failed", "deleted", "stopped"])
    sd = sub.add_parser("set-desired")
    sd.add_argument("kind", choices=["deployment", "environment"])
    sd.add_argument("state")
    sub.add_parser("show")
    args = p.parse_args()
    handler = {
        "enroll-token": cmd_enroll_token,
        "wait-node": cmd_wait_node,
        "create-env": cmd_create_env,
        "watch-env": cmd_watch_env,
        "watch-dep": cmd_watch_dep,
        "reconcile-env-shim": cmd_reconcile_env_shim,
        "seed-model": cmd_seed_model,
        "create-deployment": cmd_create_deployment,
        "request-reconcile": cmd_request_reconcile,
        "reconcile-dep-product": cmd_reconcile_dep_product,
        "reconcile-dep-shim": cmd_reconcile_dep_shim,
        "set-desired": cmd_set_desired,
        "show": cmd_show,
    }[args.cmd]
    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
