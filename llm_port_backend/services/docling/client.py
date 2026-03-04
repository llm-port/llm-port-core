"""Async HTTP client for the Docling document conversion service.

Usage::

    from llm_port_backend.services.docling import DoclingClient

    async with DoclingClient() as client:
        result = await client.convert(
            file_bytes=raw,
            filename="report.pdf",
            chunk=True,
        )
        print(result["content"])
        for chunk in result["chunks"]:
            print(chunk["text"])
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from llm_port_backend.settings import settings

logger = logging.getLogger(__name__)

# ── Timeout budget ────────────────────────────────────────────────────
# Document conversion (especially OCR on large PDFs) can be slow.
_CONNECT_TIMEOUT = 10.0  # seconds
_CONVERT_TIMEOUT = 300.0  # 5 min ceiling for huge documents
_HEALTH_TIMEOUT = 5.0


class DoclingClient:
    """Thin async wrapper around the Docling microservice REST API."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.docling_service_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # -- Context manager ----------------------------------------------------

    async def __aenter__(self) -> "DoclingClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_CONVERT_TIMEOUT,
                write=_CONVERT_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- Convenience for one-shot calls -------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    connect=_CONNECT_TIMEOUT,
                    read=_CONVERT_TIMEOUT,
                    write=_CONVERT_TIMEOUT,
                    pool=_CONNECT_TIMEOUT,
                ),
            )
        return self._client

    # -- Public API ---------------------------------------------------------

    async def convert(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        format: str = "markdown",  # noqa: A002
        ocr: bool = True,
        table_mode: str = "fast",
        chunk: bool = False,
        chunk_max_tokens: int = 512,
        chunk_overlap: int = 64,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        """Send a file to the Docling service for conversion.

        Returns the parsed JSON response with ``content``, ``metadata``,
        and ``chunks`` keys.
        """
        import json  # noqa: PLC0415

        client = self._ensure_client()

        options = {
            "format": format,
            "ocr": ocr,
            "table_mode": table_mode,
            "chunk": chunk,
            "chunk_max_tokens": chunk_max_tokens,
            "chunk_overlap": chunk_overlap,
        }
        if max_pages is not None:
            options["max_pages"] = max_pages

        resp = await client.post(
            "/api/v1/convert",
            files={"file": (filename, file_bytes)},
            data={"options": json.dumps(options)},
        )
        resp.raise_for_status()
        return resp.json()

    async def formats(self) -> list[dict[str, str]]:
        """Return the list of supported input formats."""
        client = self._ensure_client()
        resp = await client.get("/api/v1/formats")
        resp.raise_for_status()
        return resp.json().get("formats", [])

    async def healthy(self) -> bool:
        """Return *True* if the Docling service is reachable and healthy."""
        try:
            client = self._ensure_client()
            resp = await client.get(
                "/api/v1/health",
                timeout=_HEALTH_TIMEOUT,
            )
            return resp.status_code == 200
        except Exception:
            logger.debug("Docling health-check failed", exc_info=True)
            return False

    async def close(self) -> None:
        """Explicitly close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
