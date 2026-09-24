"""Host a model from the marketplace: download it to this server if needed, then deploy it.

One step for the operator, two for the server, in this order and in one
transaction: the model record (joining a download already running, or reusing
a copy already kept) and the deployment. The deployment waits for the files --
the reconciler holds it in "preparing" until the download finishes and the
files are copied to the machines -- so nothing here waits for gigabytes.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

#: Settings that belong to the copy's shape, set from the host form rather
#: than typed as engine flags: the cluster decides these.
SHAPE_KEYS = frozenset({"tensor_parallel_size", "pipeline_parallel_size", "served_model_name", "model", "port", "host"})


class HostError(ValueError):
    """The request cannot be hosted as asked; the message says why."""


@dataclass
class HostRequest:
    repo_id: str
    environment_id: uuid.UUID
    name: str
    alias: str | None
    copies: int
    gpus_per_copy: float
    engine_config: dict[str, Any]
    revision: str | None = None


def deployment_name(repo_id: str) -> str:
    """A name the backend accepts, from the repository's own."""
    base = repo_id.split("/")[-1].lower()
    return re.sub(r"[^a-z0-9-]+", "-", base).strip("-")[:120] or "model"


def build_spec(request: HostRequest) -> dict[str, Any]:
    """The deployment spec for *request*: shape from the form, engine settings as given."""
    engine = {k: v for k, v in (request.engine_config or {}).items() if k not in SHAPE_KEYS and v is not None}
    gpus = float(request.gpus_per_copy)
    if gpus <= 0:
        raise HostError("A copy needs at least part of an accelerator.")
    if gpus >= 1 and not float(gpus).is_integer():
        raise HostError("More than one accelerator per copy must be a whole number.")
    tp = int(gpus) if gpus >= 1 else 1
    if gpus < 1 and "gpu_memory_utilization" in engine and float(engine["gpu_memory_utilization"]) > gpus + 1e-9:
        # A copy told it may use more of the card than it reserved would crowd
        # the others it shares the card with.
        engine["gpu_memory_utilization"] = round(gpus, 2)
    spec: dict[str, Any] = {
        "api_version": "inference.llmport.ai/v1alpha1",
        "engine": {"name": "vllm", "config": engine},
        "scale": {"replicas": max(1, int(request.copies))},
        "resources": {"replica": {"gpus": int(gpus) if gpus >= 1 else round(gpus, 2)}},
        "service": {"path": "/v1", "openai": True, **({"alias": request.alias} if request.alias else {})},
    }
    if tp > 1:
        spec["topology"] = {"tensor_parallel_size": tp}
    if request.revision:
        spec["artifacts"] = {"source": "sync", "revision": request.revision}
    return spec


async def host(session: Any, llm_service: Any, request: HostRequest) -> dict[str, Any]:
    """Keep (or reuse) the model, create the deployment; return both ids and what happened.

    *llm_service* starts the download (the application's shared ``LLMService``).
    """
    from llm_port_backend.db.dao.llm_dao import DownloadJobDAO, ModelDAO  # noqa: PLC0415
    from llm_port_backend.services.inference.service import DeploymentService  # noqa: PLC0415

    if not re.fullmatch(r"[\w.-]+/[\w.-]+", request.repo_id or ""):
        raise HostError("Not a Hugging Face repository id.")
    spec = build_spec(request)

    model_dao = ModelDAO(session)
    kept = await model_dao.find_by_repo(request.repo_id, request.revision)
    model, job = await llm_service.start_download(
        model_dao,
        DownloadJobDAO(session),
        hf_repo_id=request.repo_id,
        hf_revision=request.revision,
        display_name=request.repo_id.split("/")[-1],
        tags=["marketplace"],
    )
    deployment = await DeploymentService(session).create(
        environment_id=request.environment_id,
        model_id=model.id,
        name=request.name.strip() or deployment_name(request.repo_id),
        spec=spec,
    )
    return {
        "deployment_id": str(deployment.id),
        "model_id": str(model.id),
        "download": "kept" if kept is not None and job is None else ("started" if job is not None else "none"),
        "download_error": getattr(job, "error_message", None) if job is not None else None,
        "spec": spec,
    }
