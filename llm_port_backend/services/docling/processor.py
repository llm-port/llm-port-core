"""Unified document processing facade.

Abstracts over two backends:

1. **Docling service** — high-quality, layout-aware conversion with OCR
   and table extraction.  Requires the ``docling`` Docker profile to be
   active and ``docling_enabled=True``.

2. **Fallback extractor** — lightweight, dependency-light text
   extraction using PyMuPDF / python-docx / etc.  No OCR, no table
   structure, no heading hierarchy.  Always available.

Usage::

    from llm_port_backend.services.docling.processor import DocumentProcessor

    processor = DocumentProcessor()
    result = await processor.process(
        file_bytes=raw,
        filename="report.pdf",
        chunk=True,
    )
    print(result["content"])
"""

from __future__ import annotations

import logging
import time
from asyncio import to_thread
from typing import Any

from llm_port_backend.settings import settings

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Route document conversion to Docling or the local fallback."""

    async def process(
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
        """Process a document, choosing the best available backend.

        Returns a dict with ``content``, ``metadata``, and ``chunks``
        keys — identical shape regardless of which backend was used.
        """
        if settings.docling_enabled:
            try:
                return await self._via_docling(
                    file_bytes,
                    filename,
                    format=format,
                    ocr=ocr,
                    table_mode=table_mode,
                    chunk=chunk,
                    chunk_max_tokens=chunk_max_tokens,
                    chunk_overlap=chunk_overlap,
                    max_pages=max_pages,
                )
            except Exception:
                logger.warning(
                    "Docling service call failed — falling back to local extraction",
                    exc_info=True,
                )

        return await self._via_fallback(file_bytes, filename)

    # ── Docling path ──────────────────────────────────────────────

    async def _via_docling(
        self,
        file_bytes: bytes,
        filename: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from llm_port_backend.services.docling.client import DoclingClient  # noqa: PLC0415

        async with DoclingClient() as client:
            result = await client.convert(file_bytes, filename, **kwargs)
            result["metadata"]["backend"] = "docling"
            return result

    # ── Fallback path ─────────────────────────────────────────────

    async def _via_fallback(
        self,
        file_bytes: bytes,
        filename: str,
    ) -> dict[str, Any]:
        from llm_port_backend.services.docling.fallback import extract_text  # noqa: PLC0415

        t0 = time.perf_counter()
        result = await to_thread(extract_text, file_bytes, filename)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        data = result.to_dict()
        data["metadata"]["processing_time_ms"] = elapsed_ms
        data["metadata"]["backend"] = "fallback"
        return data

    # ── Health / info ─────────────────────────────────────────────

    async def backend_info(self) -> dict[str, Any]:
        """Return info about which backend is active."""
        info: dict[str, Any] = {
            "docling_enabled": settings.docling_enabled,
            "active_backend": "docling" if settings.docling_enabled else "fallback",
        }
        if settings.docling_enabled:
            from llm_port_backend.services.docling.client import DoclingClient  # noqa: PLC0415

            async with DoclingClient() as client:
                info["docling_healthy"] = await client.healthy()
        return info
