"""Runtime translation bundle endpoints."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette import status

from llm_port_backend.settings import settings

router = APIRouter()

_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_LANGUAGE_NAMES = {
    "en": "English",
    "es": "Espanol",
    "fr": "Francais",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Portugues",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
}


def _assert_safe_segment(value: str, field_name: str) -> None:
    if not _SAFE_SEGMENT_RE.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}.",
        )


def _i18n_base_dir() -> Path:
    base = settings.i18n_path
    return base


@router.get("/languages", name="i18n_languages")
async def list_languages() -> JSONResponse:
    """List available runtime languages from translation directory."""
    base = _i18n_base_dir()
    if not base.exists():
        return JSONResponse(
            {"languages": []},
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    languages: list[dict[str, str]] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        has_namespace = any(child.suffix == ".json" for child in entry.iterdir() if child.is_file())
        if not has_namespace:
            continue
        code = entry.name
        languages.append(
            {
                "code": code,
                "name": _LANGUAGE_NAMES.get(code.lower(), code),
            },
        )
    return JSONResponse(
        {"languages": languages},
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/{lang}/{namespace}", name="i18n_bundle")
async def get_bundle(
    lang: str,
    namespace: str,
) -> JSONResponse:
    """Return one language namespace JSON bundle."""
    _assert_safe_segment(lang, "language code")
    _assert_safe_segment(namespace, "namespace")

    bundle_path = _i18n_base_dir() / lang / f"{namespace}.json"
    if not bundle_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Translation bundle not found.")

    try:
        content = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Translation bundle is unreadable.",
        ) from exc

    if not isinstance(content, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Translation bundle must be a JSON object.",
        )
    return JSONResponse(
        content,
        headers={"Cache-Control": "no-store, max-age=0"},
    )
