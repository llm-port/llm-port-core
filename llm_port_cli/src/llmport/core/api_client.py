"""HTTP client for the llm.port backend REST API.

Used by commands that need to interact with a running backend instance
(``provider``, ``user``, ``model``, ``config``, etc.).  The client
reads ``api_url`` and ``api_token`` from the persisted config.
"""

from __future__ import annotations

from typing import Any

import httpx

from llmport.core.settings import LlmportConfig


class ApiClient:
    """Thin wrapper around ``httpx.Client`` targeting the backend API."""

    def __init__(self, cfg: LlmportConfig, *, timeout: float = 30.0) -> None:
        self.base_url = cfg.api_url.rstrip("/")
        self.token = cfg.api_token
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    # ── HTTP verbs ────────────────────────────────────────────

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a GET request."""
        return self._client.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a POST request."""
        return self._client.post(path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a PUT request."""
        return self._client.put(path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a PATCH request."""
        return self._client.patch(path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a DELETE request."""
        return self._client.delete(path, **kwargs)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "ApiClient":
        """Support context manager protocol."""
        return self

    def __exit__(self, *_: Any) -> None:
        """Close on exit."""
        self.close()

    # ── Health check ──────────────────────────────────────────

    def healthy(self) -> bool:
        """Check if the backend is reachable and healthy."""
        try:
            resp = self.get("/api/health")
            return resp.status_code == 200  # noqa: PLR2004
        except httpx.HTTPError:
            return False
