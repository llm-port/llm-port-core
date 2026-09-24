"""Reranking search candidates with a scoring model.

Vector and keyword search rank by resemblance; a reranker reads the query
and each candidate together and scores how well the candidate answers it.
It re-orders the fused candidates before the top few are returned.

Some rerankers need their input wrapped. Qwen3-Reranker is a chat model
turned classifier: sent the bare query and document, it scored "Bananas are
yellow." above the passage that answered the question (0.90 against 0.80);
wrapped in its instruction template, 0.0001 against 0.98. vLLM's /rerank does
not wrap it, so the client does, by model name or by setting.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from llm_port_backend.services.rag_lite.embedding import api_base
from llm_port_backend.services.tls import default_httpx_verify

log = logging.getLogger(__name__)

NONE, QWEN3 = "none", "qwen3"

#: Qwen3-Reranker's template, as its model card and vLLM's example give it.
_QWEN3_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the "
    'Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
)
_QWEN3_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_QWEN3_INSTRUCTION = "Given a question, retrieve passages that answer the question"


def template_for(model: str, setting: str | None) -> str:
    """The template to wrap input in: the setting's, or by model name for ``auto``."""
    chosen = (setting or "auto").strip().lower()
    if chosen in (NONE, QWEN3):
        return chosen
    name = model.lower()
    return QWEN3 if "qwen3" in name and "rerank" in name else NONE


class RerankClient:
    """Calls an OpenAI-style ``/v1/rerank`` endpoint (vLLM, Jina, Cohere shape)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        template: str = NONE,
        api_key: str | None = None,
        timeout: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = api_base(base_url)
        self.model = model
        self.template = template
        self.api_key = api_key
        self.timeout = timeout
        self._http = http_client

    def wrap(self, query: str, documents: list[str]) -> tuple[str, list[str]]:
        """The query and documents as the model expects them."""
        if self.template == QWEN3:
            return (
                f"{_QWEN3_PREFIX}<Instruct>: {_QWEN3_INSTRUCTION}\n<Query>: {query}\n",
                [f"<Document>: {d}{_QWEN3_SUFFIX}" for d in documents],
            )
        return query, documents

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """A relevance score for each of *documents*, in their order."""
        if not documents:
            return []
        wrapped_query, wrapped = self.wrap(query, documents)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {"model": self.model, "query": wrapped_query, "documents": wrapped}
        if self._http is not None:
            resp = await self._http.post(f"{self.base_url}/rerank", json=body, headers=headers, timeout=self.timeout)
        else:
            # The shared SSL context: a default client loads the CA bundle
            # into a new one, ~500 ms here, on the event loop -- half of what
            # a reranked search took.
            async with httpx.AsyncClient(verify=default_httpx_verify(), timeout=self.timeout) as http:
                resp = await http.post(f"{self.base_url}/rerank", json=body, headers=headers)
        resp.raise_for_status()
        scores = [0.0] * len(documents)
        for result in resp.json().get("results", []):
            index = result.get("index")
            if isinstance(index, int) and 0 <= index < len(scores):
                scores[index] = float(result.get("relevance_score", 0.0))
        return scores

    @classmethod
    async def from_settings(
        cls,
        session: Any,
        *,
        crypto: Any | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> RerankClient | None:
        """The reranker RAG Lite is set to use, or ``None`` when none is."""
        from llm_port_backend.db.dao.llm_dao import ProviderDAO  # noqa: PLC0415
        from llm_port_backend.settings import settings  # noqa: PLC0415

        provider_id = (settings.rag_lite_rerank_provider_id or "").strip()
        if not provider_id:
            return None
        try:
            provider = await ProviderDAO(session).get(uuid.UUID(provider_id))
        except ValueError:
            provider = None
        if provider is None or not provider.endpoint_url:
            log.warning("Reranker provider %s not found or has no endpoint; not reranking", provider_id)
            return None
        caps = provider.capabilities or {}
        model = settings.rag_lite_rerank_model or provider.litellm_model or caps.get("remote_model") or ""
        if not model:
            log.warning("Reranker provider %s names no model; not reranking", provider.name)
            return None
        api_key = crypto.decrypt(provider.api_key_encrypted) if provider.api_key_encrypted and crypto else None
        return cls(
            provider.endpoint_url,
            model,
            template=template_for(model, settings.rag_lite_rerank_template),
            api_key=api_key,
            http_client=http_client,
        )


def fuse(*rankings: list[dict[str, Any]], k: int = 60) -> list[dict[str, Any]]:
    """Reciprocal rank fusion: one ranking from several, by rank alone.

    Keyword scores and cosine similarities do not compare; ranks do. ``k=60``
    is the constant the RRF paper and most search engines use.
    """
    fused: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            key = item["chunk_id"]
            fused.setdefault(key, item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    ordered = sorted(fused, key=lambda key: scores[key], reverse=True)
    return [{**fused[key], "score": scores[key]} for key in ordered]
