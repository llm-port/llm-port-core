"""Find the vLLM this machine already runs (Phase 8.1).

A new machine almost always has some: containers somebody started by hand, or
that another tool manages. They are reported with the inventory so LLM.Port
can offer to take them over; the three machines this project runs on had
eleven between them.

Read-only: ``ps`` and ``inspect``, nothing else. Two things are never sent:
environment variables (``HF_TOKEN`` and the like live there) and the value of
any flag whose name says it is a secret. LLM.Port's own containers
(``llm-port-*``) are skipped; they are already known.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from typing import Any

log = logging.getLogger(__name__)

#: At most this many containers are described per report.
MAX_CONTAINERS = 50

_OURS = "llm-port-"
_SECRET_FLAG = re.compile(r"key|token|secret|password", re.IGNORECASE)
_REDACTED = "***"

#: Flags that take no value. A flag not listed here that is followed by a
#: non-flag token is read as taking that token as its value.
_BOOLEAN_FLAGS = frozenset({
    "trust-remote-code", "enforce-eager", "enable-prefix-caching", "no-enable-prefix-caching",
    "enable-chunked-prefill", "disable-log-requests", "disable-log-stats", "enable-auto-tool-choice",
    "disable-frontend-multiprocessing", "enable-lora", "enable-reasoning", "disable-custom-all-reduce",
    "async-scheduling", "enable-expert-parallel", "enable-sleep-mode", "headless",
})

#: vLLM's ``--task`` / ``--runner`` / ``--convert`` values, in LLM.Port's words.
_TASKS = {
    "generate": "chat",
    "embed": "embeddings",
    "embedding": "embeddings",
    "pooling": "embeddings",
    "score": "scoring",
    "classify": "scoring",
    "reward": "scoring",
    "rerank": "scoring",
}


def looks_like_vllm(ps_row: dict[str, Any]) -> bool:
    """Whether a ``docker ps`` row is worth an ``inspect``."""
    name = str(ps_row.get("Names") or "")
    if name.startswith(_OURS):
        return False
    text = f"{ps_row.get('Image') or ''} {ps_row.get('Command') or ''}".lower()
    return "vllm" in text


def _argv(config: dict[str, Any]) -> list[str]:
    """The container's command line, unwrapped from a shell if it is in one."""
    argv = [str(a) for a in (config.get("Entrypoint") or [])] + [str(a) for a in (config.get("Cmd") or [])]
    for i, arg in enumerate(argv):
        if arg == "-c" and i + 1 < len(argv) and "vllm" in argv[i + 1]:
            try:
                return shlex.split(argv[i + 1])
            except ValueError:
                return argv[i + 1].split()
    return argv


def _vllm_args(argv: list[str], image: str) -> list[str] | None:
    """The arguments given to vLLM itself, or ``None`` when it is not vLLM."""
    for i, arg in enumerate(argv):
        if arg.endswith("vllm") and i + 1 < len(argv) and argv[i + 1] == "serve":
            return argv[i + 2:]
        if arg.endswith("vllm.entrypoints.openai.api_server"):
            return argv[i + 1:]
    # The vllm/vllm-openai image's own entrypoint is the API server, so a
    # container that only overrides the command passes it the flags directly.
    # Flags or nothing: the same image running ``sleep infinity`` is not
    # serving a model called "sleep".
    if "vllm" in image.lower() and any(a.startswith("--") for a in argv):
        return [a for a in argv if a not in ("python", "python3", "-m")]
    return None


def parse_args(args: list[str]) -> dict[str, Any]:
    """What a vLLM command line says: model, name, port, task, key settings."""
    flags: dict[str, Any] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            name, eq, value = arg[2:].partition("=")
            if eq:
                flags[name] = value
            elif name in _BOOLEAN_FLAGS or i + 1 >= len(args) or args[i + 1].startswith("-"):
                flags[name] = True
            else:
                # --served-model-name takes several names; the first is the one clients use.
                values = [args[i + 1]]
                i += 1
                while name == "served-model-name" and i + 1 < len(args) and not args[i + 1].startswith("-"):
                    values.append(args[i + 1])
                    i += 1
                flags[name] = values[0] if len(values) == 1 else values
        elif arg.startswith("-") and len(arg) == 3 and i + 1 < len(args):
            flags[{"-tp": "tensor-parallel-size", "-pp": "pipeline-parallel-size"}.get(arg, arg[1:])] = args[i + 1]
            i += 1
        else:
            positional.append(arg)
        i += 1

    served = flags.get("served-model-name")
    served_names = served if isinstance(served, list) else ([served] if served else [])
    task_value = str(flags.get("task") or flags.get("convert") or flags.get("runner") or "").lower()
    model = flags.get("model") or (positional[0] if positional else None)
    port = flags.get("port")
    return {
        "model": model,
        "served_model_names": served_names or ([model] if model else []),
        "port": int(port) if str(port or "").isdigit() else 8000,
        "host": flags.get("host"),
        "task": _TASKS.get(task_value) if task_value else None,
        # ``--api-key ""`` -- which the spark containers pass -- asks for none.
        # A key set through the environment is not seen here; the backend's
        # probe finds that out by being refused.
        "api_key_required": flags.get("api-key") not in (None, "", False),
        "settings": {
            k.replace("-", "_"): v
            for k, v in flags.items()
            if k in (
                "tensor-parallel-size", "pipeline-parallel-size", "max-model-len", "dtype",
                "quantization", "gpu-memory-utilization", "kv-cache-dtype", "max-num-seqs",
            )
        },
    }


def redact(args: list[str]) -> list[str]:
    """The command line with every secret's value replaced by ``***``."""
    out: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            out.append(_REDACTED)
            hide_next = False
            continue
        if arg.startswith("-") and _SECRET_FLAG.search(arg):
            name, eq, _value = arg.partition("=")
            if eq:
                out.append(f"{name}={_REDACTED}")
            else:
                out.append(arg)
                hide_next = True
            continue
        out.append(arg)
    return out


def _host_port(info: dict[str, Any], container_port: int) -> int | None:
    host_config = info.get("HostConfig") or {}
    if str(host_config.get("NetworkMode") or "") == "host":
        return container_port
    key = f"{container_port}/tcp"
    # Live bindings first; a stopped container has none, so fall back to the
    # ones it was created with -- the port it will be on when started again.
    live = ((info.get("NetworkSettings") or {}).get("Ports") or {}).get(key) or []
    configured = (host_config.get("PortBindings") or {}).get(key) or []
    for binding in [*live, *configured]:
        value = str((binding or {}).get("HostPort") or "")
        if value.isdigit():
            return int(value)
    return None


_NAME_TASKS = (
    (re.compile(r"rerank", re.IGNORECASE), "scoring"),
    (re.compile(r"embed", re.IGNORECASE), "embeddings"),
)


#: Label namespaces that come from the image, not from whoever started it.
_IMAGE_LABELS = ("com.nvidia.", "org.opencontainers.", "org.label-schema.", "io.buildah.", "maintainer",
                 "com.docker.compose.", "desktop.docker.")


def _manager(labels: dict[str, Any]) -> tuple[str | None, dict[str, str]]:
    """Which tool started the container, as its labels say, and those labels.

    A Compose project names itself. Otherwise a label outside the image's own
    namespaces was put there by whatever created the container -- spark_manager
    marks its containers ``spark.llm=true`` -- and its first segment names it.
    """
    own = {
        str(k): str(v)[:120]
        for k, v in sorted(labels.items())
        if not str(k).startswith(_IMAGE_LABELS)
    }
    project = labels.get("com.docker.compose.project")
    if project:
        return f"compose:{project}", own
    if own:
        return next(iter(own)).split(".")[0], own
    return None, own


def _task_from_name(model: str | None) -> str | None:
    """A task guessed from the model's name, for models whose flags do not say.

    vLLM decides from the model's architecture, which the command line does
    not show; ``Qwen3-Embedding-0.6B`` runs as an embedding model without a
    ``--task``. The name is only a hint and is reported as one.
    """
    for pattern, task in _NAME_TASKS:
        if model and pattern.search(model):
            return task
    return None


def _gpus(host_config: dict[str, Any]) -> str | None:
    for request in host_config.get("DeviceRequests") or []:
        ids = request.get("DeviceIDs")
        if ids:
            return ",".join(str(i) for i in ids)
        count = request.get("Count")
        if count == -1:
            return "all"
        if count:
            return str(count)
    return "all" if str(host_config.get("Runtime") or "") == "nvidia" else None


def describe(info: dict[str, Any]) -> dict[str, Any] | None:
    """One container, as LLM.Port needs to know it, or ``None`` if it is not vLLM."""
    config = info.get("Config") or {}
    image = str(config.get("Image") or info.get("Image") or "")
    name = str(info.get("Name") or "").lstrip("/")
    if name.startswith(_OURS):
        return None
    args = _vllm_args(_argv(config), image)
    if args is None:
        return None
    parsed = parse_args(args)
    parsed["task_from"] = "flags" if parsed["task"] else None
    if parsed["task"] is None and (guess := _task_from_name(parsed["model"])):
        parsed["task"], parsed["task_from"] = guess, "name"
    state = info.get("State") or {}
    host_config = info.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    managed_by, own_labels = _manager(labels)
    return {
        "name": name,
        "id": str(info.get("Id") or "")[:12],
        "image": image,
        "state": str(state.get("Status") or ""),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "exit_code": state.get("ExitCode"),
        "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name") or None,
        "network_mode": host_config.get("NetworkMode"),
        "host_port": _host_port(info, parsed["port"]),
        "gpus": _gpus(host_config),
        "managed_by": managed_by,
        "labels": dict(list(own_labels.items())[:10]),
        "mounts": [
            {"source": m.get("Source"), "destination": m.get("Destination")}
            for m in (info.get("Mounts") or [])[:10]
            if m.get("Source") and m.get("Destination")
        ],
        "args": redact(args),
        **parsed,
    }


async def discover_vllm(runtime: Any) -> list[dict[str, Any]]:
    """Every vLLM container on this machine that LLM.Port did not start."""
    found: list[dict[str, Any]] = []
    try:
        rows = await runtime.ps(all_=True)
    except Exception as exc:  # noqa: BLE001 - no container runtime is simply nothing found
        log.debug("vLLM discovery: ps failed: %s", exc)
        return found
    for line in rows:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not looks_like_vllm(row):
            continue
        try:
            info = await runtime.inspect(str(row.get("ID") or row.get("Names")))
        except Exception as exc:  # noqa: BLE001 - one container that vanished is not a failure
            log.debug("vLLM discovery: inspect %s failed: %s", row.get("Names"), exc)
            continue
        if not isinstance(info, dict) or info.get("__missing"):
            continue
        described = describe(info)
        if described is not None:
            found.append(described)
        if len(found) >= MAX_CONTAINERS:
            break
    return found
