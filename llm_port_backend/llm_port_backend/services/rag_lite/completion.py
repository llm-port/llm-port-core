"""Lightweight OpenAI-compatible chat completion client for RAG Lite.

Used to generate AI summaries for collections and documents via any
chat-capable LLM provider registered in the system.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Maximum characters of document content to include in the prompt.
_MAX_CONTENT_CHARS = 8000


class CompletionClient:
    """Call an OpenAI-compatible ``/v1/chat/completions`` endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._shared_client = http_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
        """Send a single-turn prompt and return the assistant response."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": False,
        }
        if self._shared_client:
            resp = await self._shared_client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "").strip()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def auto_detect(
        cls,
        session: Any,
        *,
        crypto: Any | None = None,
    ) -> CompletionClient:
        """Find a chat-capable provider with a running runtime."""
        from llm_port_backend.db.dao.llm_dao import (  # noqa: PLC0415
            ModelDAO,
            ProviderDAO,
            RuntimeDAO,
        )

        provider_dao = ProviderDAO(session)
        runtime_dao = RuntimeDAO(session)
        model_dao = ModelDAO(session)

        providers = await provider_dao.list_all()

        # Prefer providers that are NOT embedding-only
        embedding_ids: set[uuid.UUID] = set()
        chat_providers = []
        for p in providers:
            caps = p.capabilities or {}
            if caps.get("supports_embeddings"):
                embedding_ids.add(p.id)
            else:
                chat_providers.append(p)

        # Fall back to all providers if no chat-specific ones found
        candidates = chat_providers or providers

        for provider in candidates:
            runtimes = await runtime_dao.list_by_provider(provider.id)
            for rt in runtimes:
                if rt.status.value != "running":
                    continue
                model = await model_dao.get(rt.model_id)
                if model is None:
                    continue
                model_name = model.hf_repo_id or model.display_name

                # Resolve base URL
                base_url = provider.endpoint_url
                if not base_url and rt.endpoint_url:
                    base_url = rt.endpoint_url.rstrip("/") + "/v1"
                if not base_url:
                    continue

                # Resolve API key
                api_key: str | None = None
                if provider.api_key_encrypted and crypto:
                    api_key = crypto.decrypt(provider.api_key_encrypted)

                return cls(
                    base_url=base_url,
                    model=model_name,
                    api_key=api_key,
                )

        raise ValueError(
            "No chat-capable provider with a running runtime found. "
            "Please start a chat model runtime to use AI summary generation.",
        )

    # ------------------------------------------------------------------
    # Summary generation helpers
    # ------------------------------------------------------------------

    async def generate_collection_summary(
        self,
        collection_name: str,
        document_names: list[str],
        document_summaries: list[str | None],
    ) -> str:
        """Generate a concise summary for a collection."""
        doc_lines: list[str] = []
        for i, name in enumerate(document_names):
            summary = (
                document_summaries[i]
                if i < len(document_summaries) and document_summaries[i]
                else None
            )
            if summary:
                doc_lines.append(f"- {name}: {summary}")
            else:
                doc_lines.append(f"- {name}")

        docs_text = "\n".join(doc_lines) if doc_lines else "(empty collection)"

        prompt = (
            f'The collection "{collection_name}" contains these documents:\n'
            f"{docs_text}\n\n"
            "Write a concise 1-3 sentence summary of what this collection "
            "covers. Focus on the topics, themes, and type of information "
            "contained. Be specific and factual."
        )
        return await self.generate(prompt, max_tokens=256)

    async def generate_document_summary(
        self,
        filename: str,
        content_text: str | None,
    ) -> str:
        """Generate a concise summary for a document."""
        if content_text:
            snippet = content_text[:_MAX_CONTENT_CHARS]
            truncated = " (truncated)" if len(content_text) > _MAX_CONTENT_CHARS else ""
            prompt = (
                f"Summarize the following document ({filename}) in 1-3 "
                f"sentences. Focus on the key topics and information.\n\n"
                f"---\n{snippet}{truncated}\n---"
            )
        else:
            prompt = (
                f"Based on the filename \"{filename}\", write a one-sentence "
                "placeholder description of what this document likely contains."
            )
        return await self.generate(prompt, max_tokens=256)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
