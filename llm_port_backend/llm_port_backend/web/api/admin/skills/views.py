"""Admin Skills proxy routes.

Proxies skills management requests to the Skills micro-service.
All endpoints require superuser privileges.

The Skills micro-service uses a YAML-frontmatter + markdown document format
for creation/body-update.  This proxy accepts structured JSON from the
frontend and composes/decomposes documents transparently.
"""

from __future__ import annotations

from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Body, Depends, Path, Query

from llm_port_backend.db.models.users import User
from llm_port_backend.services.skills.client import SkillsServiceClient, get_skills_client
from llm_port_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()

# Fields that belong in YAML frontmatter (not the markdown body or name).
_FRONTMATTER_KEYS = {
    "description", "scope", "priority", "tags",
    "allowed_tools", "preferred_tools", "forbidden_tools",
    "knowledge_sources", "trigger_rules",
}


def _compose_content(payload: dict[str, Any]) -> str:
    """Build a ``---\\nfrontmatter\\n---\\nbody`` document from flat fields."""
    fm: dict[str, Any] = {}
    if payload.get("name"):
        fm["name"] = payload["name"]
    for key in _FRONTMATTER_KEYS:
        if key in payload and payload[key] is not None:
            fm[key] = payload[key]
    body = payload.get("body_markdown", "")
    if fm:
        fm_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm_yaml}\n---\n\n{body}"
    return body


# ── Skills CRUD ──────────────────────────────────────────────────────────────


@router.get("")
async def list_skills(
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
    status: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if status:
        params["status"] = status
    if scope:
        params["scope"] = scope
    if tag:
        params["tag"] = tag
    data = await client.list_skills(params=params)
    if isinstance(data, dict):
        return data.get("items", [])
    return data


@router.post("", status_code=201)
async def create_skill(
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    # Compose frontmatter document expected by the Skills micro-service.
    content = _compose_content(payload)
    return await client.create_skill({
        "content": content,
        "name": payload.get("name"),
    })


@router.get("/completions")
async def completions(
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
    field: Annotated[str | None, Query()] = None,
    prefix: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if field:
        params["field"] = field
    if prefix:
        params["prefix"] = prefix
    return await client.completions(params=params)


@router.get("/{skill_id}")
async def get_skill(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> dict[str, Any]:
    return await client.get_skill(skill_id)


@router.patch("/{skill_id}")
async def update_skill(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    # Separate metadata-only updates from body updates.
    has_body = "body_markdown" in payload
    metadata = {k: v for k, v in payload.items() if k not in ("body_markdown", "change_note")}
    # Rename frontend field names to microservice field names.
    if "tools" in metadata:
        metadata["allowed_tools"] = metadata.pop("tools")

    result: dict[str, Any] = {}
    if metadata:
        result = await client.update_skill_metadata(skill_id, metadata)
    if has_body:
        content = _compose_content(payload)
        await client.update_skill_body(skill_id, {
            "content": content,
            "change_note": payload.get("change_note"),
        })
        # Refresh the full skill to return updated state.
        result = await client.get_skill(skill_id)
    return result


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> None:
    await client.delete_skill(skill_id)


# ── Lifecycle ────────────────────────────────────────────────────────────────


@router.post("/{skill_id}/publish")
async def publish_skill(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> dict[str, Any]:
    return await client.publish_skill(skill_id)


@router.post("/{skill_id}/archive")
async def archive_skill(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> dict[str, Any]:
    return await client.archive_skill(skill_id)


# ── Versions ─────────────────────────────────────────────────────────────────


@router.get("/{skill_id}/versions")
async def list_versions(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> list[dict[str, Any]]:
    return await client.list_versions(skill_id)


@router.get("/{skill_id}/versions/{version}")
async def get_version(
    skill_id: Annotated[str, Path()],
    version: Annotated[int, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> dict[str, Any]:
    return await client.get_version(skill_id, version)


# ── Assignments ──────────────────────────────────────────────────────────────


@router.get("/{skill_id}/assignments")
async def list_assignments(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> list[dict[str, Any]]:
    return await client.list_assignments(skill_id)


@router.post("/{skill_id}/assignments", status_code=201)
async def create_assignment(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return await client.create_assignment(skill_id, payload)


@router.delete("/{skill_id}/assignments/{assignment_id}", status_code=204)
async def delete_assignment(
    skill_id: Annotated[str, Path()],
    assignment_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> None:
    await client.delete_assignment(skill_id, assignment_id)


# ── Import / Export ──────────────────────────────────────────────────────────


@router.get("/{skill_id}/export")
async def export_skill(
    skill_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> dict[str, Any]:
    return await client.export_skill(skill_id)


@router.post("/import", status_code=201)
async def import_skill(
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return await client.import_skill(payload)
