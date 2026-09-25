"""Whether the endpoints of remote runtimes answer.

A remote runtime -- an OpenAI-compatible server somewhere, a cloud API -- was
marked running when it was added and never looked at again. The runtimes list
and the data residency map said "running" for an endpoint whose host no longer
resolved, and the gateway kept routing to it. Container runtimes are held to
Docker, cluster deployments and found containers to their machines; this holds
what has only an address to that address.

The question is reachability, not the API: any HTTP answer below 500 -- a 404
from a server without that path, a 401 from a cloud API asked without a key --
means something is there. Connection errors, timeouts and 5xx mean nothing
usable is. One failure does not take a route away: an endpoint is down after
``FAILURES_TO_GO_DOWN`` in a row, and back on its first answer.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.llm import LLMProvider, LLMRuntime, ProviderTarget, RuntimeStatus
from llm_port_backend.services.tls import default_httpx_verify

log = logging.getLogger(__name__)

#: Where the APIs LiteLLM routes to -- a provider with no endpoint of its own -- answer.
KNOWN_API_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "deepseek": "https://api.deepseek.com/v1/models",
    "cohere": "https://api.cohere.com/v2/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
}

FAILURES_TO_GO_DOWN = 2
PROBE_TIMEOUT_SEC = 5.0

#: runtime id -> failed probes in a row, in this process.
_failures: dict[uuid.UUID, int] = {}

#: What a remote runtime can be in while it is meant to serve.
_WATCHED = (RuntimeStatus.RUNNING, RuntimeStatus.ERROR)


@dataclass(frozen=True)
class Probe:
    """One look at an endpoint."""

    reachable: bool
    detail: str


def probe_url(endpoint_url: str | None, litellm_provider: str | None) -> str | None:
    """The URL that shows whether a remote runtime's endpoint is there; None when nothing can."""
    url = (endpoint_url or "").strip()
    if url.startswith("litellm://"):
        return KNOWN_API_URLS.get(litellm_provider or url.removeprefix("litellm://"))
    if url.startswith(("http://", "https://")):
        return url
    return None


async def probe(url: str, client: httpx.AsyncClient) -> Probe:
    """Whether *url* answers at all."""
    try:
        resp = await client.get(url)
    except httpx.TimeoutException:
        return Probe(reachable=False, detail=f"no answer within {PROBE_TIMEOUT_SEC:g} s")
    except Exception as exc:  # failing to get any answer at all is the finding
        return Probe(reachable=False, detail=str(exc) or type(exc).__name__)
    if resp.status_code >= 500:
        return Probe(reachable=False, detail=f"HTTP {resp.status_code}")
    return Probe(reachable=True, detail=f"HTTP {resp.status_code}")


def forget(runtime_id: uuid.UUID) -> None:
    """Start counting failures afresh: the runtime was started, or its endpoint changed."""
    _failures.pop(runtime_id, None)


async def follow_remote_runtimes(session: AsyncSession, gateway_sync: Any) -> int:
    """Probe every remote runtime meant to serve and hold its status and route to the answer.

    Returns how many runtimes went down or came back. The gateway is told the
    health on every probe, not only on a change: a route another path left
    unhealthy (a stop, then a restart) comes back without waiting for a change.
    """
    rows = await session.execute(
        select(LLMRuntime.id, LLMRuntime.endpoint_url, LLMProvider.litellm_provider)
        .join(LLMProvider, LLMProvider.id == LLMRuntime.provider_id)
        .where(
            LLMProvider.target == ProviderTarget.REMOTE_ENDPOINT,
            LLMRuntime.status.in_(_WATCHED),
        ),
    )
    targets = [
        (runtime_id, url)
        for runtime_id, endpoint_url, litellm_provider in rows.all()
        if (url := probe_url(endpoint_url, litellm_provider)) is not None
    ]
    await session.commit()  # no transaction held open across the network
    if not targets:
        return 0

    async with httpx.AsyncClient(verify=default_httpx_verify(), timeout=PROBE_TIMEOUT_SEC) as client:
        results = await asyncio.gather(*(probe(url, client) for _, url in targets))

    changed = 0
    for (runtime_id, url), result in zip(targets, results, strict=True):
        runtime = await session.get(LLMRuntime, runtime_id)
        if runtime is None or runtime.status not in _WATCHED:
            continue  # deleted or stopped while we looked
        if result.reachable:
            _failures.pop(runtime_id, None)
            if runtime.status == RuntimeStatus.ERROR:
                runtime.status = RuntimeStatus.RUNNING
                runtime.status_message = None
                changed += 1
                log.info("Remote runtime %r answers again at %s (%s)", runtime.name, url, result.detail)
            await _tell_gateway(gateway_sync, runtime_id, "healthy")
            continue
        failures = _failures.get(runtime_id, 0) + 1
        _failures[runtime_id] = failures
        if failures < FAILURES_TO_GO_DOWN:
            continue
        if runtime.status != RuntimeStatus.ERROR:
            changed += 1
            log.warning(
                "Remote runtime %r does not answer at %s (%s); taken out of routing",
                runtime.name,
                url,
                result.detail,
            )
        runtime.status = RuntimeStatus.ERROR
        runtime.status_message = f"Endpoint not answering: {result.detail}"[:1000]
        await _tell_gateway(gateway_sync, runtime_id, "unhealthy")
    await session.commit()
    return changed


async def _tell_gateway(gateway_sync: Any, runtime_id: uuid.UUID, health: str) -> None:
    if gateway_sync is None or not getattr(gateway_sync, "enabled", False):
        return
    await gateway_sync.set_instance_health(runtime_id=runtime_id, health_status=health)
