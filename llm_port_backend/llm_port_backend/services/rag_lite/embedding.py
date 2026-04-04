"""OpenAI-compatible embedding client for RAG Lite.

Auto-detects embedding providers from configured LLM providers that have
``supports_embeddings=True`` in their capabilities JSON.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from llm_port_backend.db.models.rag_lite import MAX_EMBEDDING_DIM

log = logging.getLogger(__name__)

# Maximum texts per single API call to avoid OOM on the provider side.
_BATCH_SIZE = 64


class EmbeddingClient:
    """Call an OpenAI-compatible ``/v1/embeddings`` endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        dim: int = 768,
        timeout: float = 120.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = dim
        self.timeout = timeout
        self._shared_client = http_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch-embed *texts*.  Returns a list of vectors, zero-padded to
        ``MAX_EMBEDDING_DIM`` for the pgvector column width.
        """
        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            vectors = await self._call_api(batch)
            all_vectors.extend(vectors)
        return all_vectors

    async def health_check(self) -> bool:
        """Quick probe to verify the embedding provider is reachable."""
        try:
            if self._shared_client:
                resp = await self._shared_client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                    timeout=10.0,
                )
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{self.base_url}/models",
                        headers=self._headers(),
                    )
            return resp.status_code == 200  # noqa: PLR2004
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def from_provider(
        cls,
        provider: Any,
        *,
        model_override: str | None = None,
        base_url_override: str | None = None,
        dim: int = 768,
        crypto: Any | None = None,
    ) -> EmbeddingClient:
        """Build an ``EmbeddingClient`` from an ``LLMProvider`` DB row.

        Parameters
        ----------
        provider:
            An ``LLMProvider`` instance (from ``llm_port_backend.db.models.llm``).
        model_override:
            Explicit model name.  Falls back to ``provider.capabilities["remote_model"]``.
        base_url_override:
            Explicit base URL (e.g. from a running runtime's endpoint_url).
        dim:
            Actual embedding dimension (for zero-padding).
        crypto:
            ``SettingsCrypto`` instance for decrypting the API key.
        """
        base_url = base_url_override or provider.endpoint_url or ""
        caps = provider.capabilities or {}

        model = model_override or caps.get("remote_model", "")
        if not model:
            raise ValueError(
                f"Provider {provider.name!r} has no remote_model in capabilities "
                "and no model_override was given.",
            )

        api_key: str | None = None
        if provider.api_key_encrypted and crypto:
            api_key = crypto.decrypt(provider.api_key_encrypted)

        return cls(
            base_url=base_url,
            model=model,
            api_key=api_key,
            dim=dim,
        )

    @classmethod
    async def auto_detect(
        cls,
        session: Any,
        *,
        preferred_provider_id: uuid.UUID | None = None,
        model_override: str | None = None,
        dim: int = 768,
        crypto: Any | None = None,
    ) -> EmbeddingClient:
        """Auto-detect an embedding provider from DB.

        If *preferred_provider_id* is set, uses that provider.
        Otherwise, picks the first embedding-capable provider.
        """
        from llm_port_backend.db.dao.llm_dao import (  # noqa: PLC0415
            ModelDAO,
            ProviderDAO,
            RuntimeDAO,
        )

        dao = ProviderDAO(session)

        if preferred_provider_id:
            provider = await dao.get(preferred_provider_id)
            if provider is None:
                raise ValueError(
                    f"Preferred embedding provider {preferred_provider_id} not found.",
                )
        else:
            providers = await dao.list_embedding_capable()
            if not providers:
                raise ValueError(
                    "No embedding-capable providers configured. "
                    "Register a provider with supports_embeddings=true.",
                )
            provider = providers[0]

        # For local Docker providers, resolve model name and endpoint URL
        # from the running runtime when not set on the provider itself.
        effective_model = model_override
        effective_base_url: str | None = None
        if not effective_model:
            caps = provider.capabilities or {}
            effective_model = caps.get("remote_model") or None
        if not effective_model or not provider.endpoint_url:
            runtime_dao = RuntimeDAO(session)
            model_dao = ModelDAO(session)
            runtimes = await runtime_dao.list_by_provider(provider.id)
            for rt in runtimes:
                if rt.status.value == "running":
                    if not effective_model:
                        m = await model_dao.get(rt.model_id)
                        if m:
                            effective_model = m.hf_repo_id or m.display_name
                    if not provider.endpoint_url and rt.endpoint_url:
                        # Runtime endpoint_url is bare http://host:port;
                        # the OpenAI-compat API lives under /v1.
                        effective_base_url = rt.endpoint_url.rstrip("/") + "/v1"
                    break

        return await cls.from_provider(
            provider,
            model_override=effective_model,
            base_url_override=effective_base_url,
            dim=dim,
            crypto=crypto,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _zero_pad(self, vector: list[float]) -> list[float]:
        """Pad *vector* to ``MAX_EMBEDDING_DIM`` for pgvector storage."""
        if len(vector) >= MAX_EMBEDDING_DIM:
            return vector[:MAX_EMBEDDING_DIM]
        return vector + [0.0] * (MAX_EMBEDDING_DIM - len(vector))

    async def _call_api(self, texts: list[str]) -> list[list[float]]:
        """Single batch call to the embeddings endpoint."""
        payload = {"model": self.model, "input": texts}
        if self._shared_client:
            resp = await self._shared_client.post(
                f"{self.base_url}/embeddings",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/embeddings",
                    json=payload,
                    headers=self._headers(),
                )
        resp.raise_for_status()
        data = resp.json()

        # OpenAI-compatible response: { "data": [{"embedding": [...], "index": 0}, ...] }
        embeddings = sorted(data["data"], key=lambda x: x["index"])
        return [self._zero_pad(e["embedding"]) for e in embeddings]
