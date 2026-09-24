"""What a running Ray cluster is serving, read from Ray itself -- for a takeover.

A server that lost its database can take over the clusters its machines still
run, without restarting them, only if it can learn what they run: which
machines form the cluster, and for each model it serves the configuration it
was deployed with. Ray's public status gives app names and health, not that
configuration. Ray Serve's controller keeps it, though: every deployment's
init arguments, which for an LLM app hold the ``LLMConfig`` -- model, engine
settings, copies, GPUs.

So a small script runs *inside* the runtime container (``python3 -``, fed on
stdin), against the exact Ray version the cluster runs, attached with the
token the container was started with. It uses ``get_deployment_info`` and
``get_serve_details`` from Serve's client, which are internal to Ray: the
runtime images pin Ray, so a change there shows up when an image is built,
not in the field.

Given candidate ``llm_serving_args`` (``verify``), it also says whether they
match what runs, both validated through Ray's own ``LLMConfig`` -- so defaults
compare equal the way Ray sees them. A takeover that matches restarts
nothing; one that does not is shown the difference.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Apps LLM.Port deploys are named ``llmport-<deployment id>``.
APP_PREFIX = "llmport-"

_SCRIPT = r'''
import json, sys, logging, warnings
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
VERIFY = json.loads(__VERIFY__)
APP_PREFIX = __APP_PREFIX__
out = {"errors": []}

def jsonable(value):
    return json.loads(json.dumps(value, default=str))

def dump(cfg):
    try:
        return jsonable(cfg.model_dump(mode="json"))
    except Exception:
        return jsonable(cfg.model_dump())

try:
    import ray
    out["ray_version"] = ray.__version__
    ray.init(address="auto", logging_level="ERROR", log_to_driver=False)
except Exception as exc:
    print(json.dumps({"attached": False, "error": "attach: %s" % exc}))
    sys.exit(0)
out["attached"] = True

try:
    ctx = ray.get_runtime_context()
    out["gcs_address"] = getattr(ctx, "gcs_address", None)
    nodes = []
    for n in ray.nodes():
        resources = n.get("Resources") or {}
        nodes.append({
            "node_id": n.get("NodeID"),
            "ip": n.get("NodeManagerAddress"),
            "hostname": n.get("NodeManagerHostname"),
            "alive": bool(n.get("Alive")),
            "is_head": "node:__internal_head__" in resources,
            "gpus": float(resources.get("GPU", 0) or 0),
            "cpus": float(resources.get("CPU", 0) or 0),
            "accelerators": {k: v for k, v in resources.items() if k.startswith("accelerator_type:")},
        })
    out["nodes"] = nodes
except Exception as exc:
    out["errors"].append("nodes: %s" % exc)

apps = []
try:
    import pydantic
    from ray.serve.context import _get_global_client
    from ray.serve.llm import LLMConfig
    client = _get_global_client(raise_if_no_controller_running=False)
    details = client.get_serve_details() if client is not None else {}
    out["serve"] = {"running": client is not None, "http_options": jsonable(details.get("http_options") or {})}

    def llm_configs(value, found):
        # A pydantic model with the LLMConfig's fields -- not isinstance(): the
        # public ray.serve.llm.LLMConfig is not the internal class Serve
        # stores. Not hasattr() either: a DeploymentHandle (in the ingress's
        # arguments) answers every attribute name with a method.
        if isinstance(value, pydantic.BaseModel) and "model_loading_config" in type(value).model_fields:
            found.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                llm_configs(v, found)
        elif isinstance(value, (list, tuple)):
            for v in value:
                llm_configs(v, found)
        return found

    for app_name, app in (details.get("applications") or {}).items():
        entry = {
            "name": app_name,
            "route_prefix": app.get("route_prefix"),
            "status": app.get("status"),
            "message": app.get("message") or "",
            "deployments": [],
            "llm_configs": [],
        }
        running = []
        for dep_name, dep in (app.get("deployments") or {}).items():
            replicas = dep.get("replicas") or []
            d = {
                "name": dep_name,
                "status": dep.get("status"),
                "running_replicas": sum(1 for r in replicas if (r.get("state") or "") == "RUNNING"),
                "deployment_config": jsonable(dep.get("deployment_config") or {}),
            }
            if app_name.startswith(APP_PREFIX):
                try:
                    info, _route = client.get_deployment_info(dep_name, app_name)
                    rc = info.replica_config
                    running.extend(llm_configs([rc.init_args, rc.init_kwargs], []))
                except Exception as exc:
                    d["error"] = "deployment info: %s" % exc
            entry["deployments"].append(d)
        entry["llm_configs"] = [dump(c) for c in running]
        candidate = (VERIFY or {}).get(app_name)
        if candidate is not None:
            try:
                # Validated by the class Serve itself holds, so defaults fill in alike.
                cls = type(running[0]) if running else LLMConfig
                wanted = [dump(cls.model_validate(c)) for c in candidate.get("llm_configs") or []]
                have = entry["llm_configs"]
                diff = []
                def walk(a, b, path):
                    if isinstance(a, dict) and isinstance(b, dict):
                        for k in sorted(set(a) | set(b)):
                            walk(a.get(k), b.get(k), path + [str(k)])
                    elif a != b:
                        diff.append({"path": ".".join(path), "running": a, "candidate": b})
                if len(wanted) != len(have):
                    diff.append({"path": "llm_configs", "running": len(have), "candidate": len(wanted)})
                for i, (a, b) in enumerate(zip(have, wanted)):
                    walk(a, b, ["llm_configs", str(i)])
                entry["verify"] = {"equal": not diff, "diff": diff[:50]}
            except Exception as exc:
                entry["verify"] = {"equal": False, "error": "verify: %s" % exc}
        apps.append(entry)
except Exception as exc:
    out["errors"].append("serve: %s" % exc)
out["apps"] = apps
print("__INSPECT_BEGIN__" + json.dumps(out) + "__INSPECT_END__")
'''


def script(verify: dict[str, Any] | None = None) -> str:
    """The inspection script, with the candidate configurations to check."""
    return (
        _SCRIPT.replace("__VERIFY__", repr(json.dumps(verify or {})))
        .replace("__APP_PREFIX__", repr(APP_PREFIX))
    )


def parse(stdout: str) -> dict[str, Any] | None:
    """The script's document from its output (Ray prints lines of its own)."""
    text = stdout or ""
    start, end = text.rfind("__INSPECT_BEGIN__"), text.rfind("__INSPECT_END__")
    if start < 0 or end < start:
        # Attach failures print a bare document.
        for line in reversed(text.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except ValueError:
                    return None
        return None
    try:
        return json.loads(text[start + len("__INSPECT_BEGIN__"):end])
    except ValueError:
        return None


async def runtime_summary(runtime: Any, container_name: str) -> dict[str, Any] | None:
    """Whether this machine runs LLM.Port's Ray runtime container, cheaply, for the inventory.

    ``None`` when there is no such container at all.
    """
    try:
        info = await runtime.inspect(container_name)
    except Exception as exc:  # noqa: BLE001 - no container runtime is simply nothing found
        log.debug("Ray runtime summary skipped: %s", exc)
        return None
    if not isinstance(info, dict) or info.get("__missing") or not info:
        return None
    state = info.get("State") or {}
    config = info.get("Config") or {}
    return {
        "container": container_name,
        "running": bool(state.get("Running")),
        "started_at": state.get("StartedAt"),
        "image": config.get("Image"),
        "image_id": info.get("Image"),
    }
