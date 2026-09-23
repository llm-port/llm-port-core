"""HTTP client for RAG Lite on the backend: search, and read a passage.

Used by the knowledge tools the model calls (``knowledge.py``), as the user
who asked.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RagLiteClient:
    """Async client wrapping the backend RAG Lite search endpoint."""

    def __init__(self, *, base_url: str, http_client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._http = http_client

    async def search(
        self,
        *,
        query: str,
        top_k: int = 5,
        collection_ids: list[str] | None = None,
        api_token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Call ``POST /api/admin/rag/search`` on the backend.

        Returns a list of result dicts with ``chunk_text``, ``filename``,
        ``score``, etc.
        """
        body: dict[str, Any] = {"query": query, "top_k": top_k}
        if collection_ids:
            body["collection_ids"] = collection_ids

        headers: dict[str, str] = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        # Raises on failure. The model is told the search failed; answered
        # with an empty list, it took "nothing found" for an answer.
        resp = await self._http.post(
            f"{self._base}/api/admin/rag/search",
            json=body,
            timeout=15.0,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    async def passage(
        self,
        *,
        document_id: str,
        chunk: int,
        around: int = 2,
        api_token: str | None = None,
    ) -> dict[str, Any]:
        """Call ``GET /api/admin/rag/documents/{id}/passage`` on the backend.

        The text of a document around one chunk: ``filename``, ``chunk_count``
        and ``chunks`` (``chunk_index``, ``text``). Raises on failure: the
        model is told, rather than handed an empty passage.
        """
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
        resp = await self._http.get(
            f"{self._base}/api/admin/rag/documents/{document_id}/passage",
            params={"chunk": chunk, "around": around},
            timeout=15.0,
            headers=headers,
        )
        if resp.status_code == 404:
            raise LookupError(f"No document {document_id}.")
        resp.raise_for_status()
        return resp.json()

