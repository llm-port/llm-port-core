"""HTTP client for llm_port_skills admin APIs."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException
from starlette import status

from llm_port_backend.settings import settings


class SkillsServiceClient:
    """Typed proxy for the Skills micro-service admin endpoints."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        timeout_sec: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout_sec = timeout_sec

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not self.service_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Skills integration is not configured (missing service token).",
            )

        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json_body,
                    params=params,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"Skills service timed out calling {path}.",
                ) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to reach Skills service: {exc}",
                ) from exc

        if response.status_code >= 400:
            detail = response.text
            try:
                payload = response.json()
                if isinstance(payload, dict) and "detail" in payload:
                    detail = str(payload["detail"])
            except ValueError:
                pass
            raise HTTPException(status_code=response.status_code, detail=detail)

        if response.status_code == status.HTTP_204_NO_CONTENT:
            return None
        return response.json()

    # ── Skills CRUD ──────────────────────────────────────────────────────

    async def list_skills(
        self, *, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/api/admin/skills", params=params,
        )

    async def get_skill(self, skill_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/admin/skills/{skill_id}")

    async def create_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/admin/skills", json_body=payload)


    async def update_skill_metadata(
        self, skill_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "PUT", f"/api/admin/skills/{skill_id}", json_body=payload,
        )

    async def update_skill_body(
        self, skill_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "PUT", f"/api/admin/skills/{skill_id}/body", json_body=payload,
        )

    async def delete_skill(self, skill_id: str) -> None:
        await self._request("DELETE", f"/api/admin/skills/{skill_id}")

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def publish_skill(self, skill_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/admin/skills/{skill_id}/publish")

    async def archive_skill(self, skill_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/admin/skills/{skill_id}/archive")

    # ── Versions ─────────────────────────────────────────────────────────

    async def list_versions(self, skill_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/api/admin/skills/{skill_id}/versions")

    async def get_version(
        self, skill_id: str, version: int,
    ) -> dict[str, Any]:
        return await self._request(
            "GET", f"/api/admin/skills/{skill_id}/versions/{version}",
        )

    # ── Assignments ──────────────────────────────────────────────────────

    async def list_assignments(self, skill_id: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"/api/admin/skills/{skill_id}/assignments",
        )

    async def create_assignment(
        self, skill_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"/api/admin/skills/{skill_id}/assignments",
            json_body=payload,
        )

    async def delete_assignment(
        self, skill_id: str, assignment_id: str,
    ) -> None:
        await self._request(
            "DELETE",
            f"/api/admin/skills/{skill_id}/assignments/{assignment_id}",
        )

    # ── Import / Export ──────────────────────────────────────────────────

    async def export_skill(self, skill_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/admin/skills/{skill_id}/export")

    async def import_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/admin/skills/import", json_body=payload)

    # ── Completions (intellisense) ───────────────────────────────────────

    async def completions(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", "/api/admin/skills/completions", params=params)

    # ── Health ───────────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/health")


def get_skills_client() -> SkillsServiceClient:
    """Factory — raises 503 when the Skills module is disabled."""
    if not settings.skills_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Skills module is not enabled.",
        )
    return SkillsServiceClient(
        base_url=settings.skills_service_url,
        service_token=settings.skills_service_token,
    )
