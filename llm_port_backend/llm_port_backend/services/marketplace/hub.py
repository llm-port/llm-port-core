"""The Hugging Face Hub, as the marketplace sees it: cached, and honest when unreachable.

Every call runs in a worker thread (``huggingface_hub`` is synchronous) and its
answer is kept for a while: the marketplace page asks the same questions as
people browse, and the Hub rate-limits anonymous clients.

A server without internet access is a normal installation, not an error:
:class:`HubUnavailable` carries that, and the API turns it into "showing what
this server has" instead of a failure.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from llm_port_backend.services.marketplace import facts

log = logging.getLogger(__name__)

#: How long answers are kept.
SEARCH_TTL_SEC = 10 * 60
DETAIL_TTL_SEC = 60 * 60

#: Sorts the marketplace offers -> the Hub's names for them.
SORTS = {
    "downloads": "downloads",
    "trending": "trending_score",
    "likes": "likes",
    "recent": "created_at",
}

#: Tasks -> the Hub pipeline tags they are filed under.
#: The Hub queries behind each task filter.
#:
#: Chat asks for the ``conversational`` tag rather than the text-generation
#: pipeline: the current chat models (Qwen3.5 onwards, Gemma 4) read images
#: too and are filed under image-text-to-text, so a pipeline filter hid the
#: most used chat models there are.
TASK_QUERIES: dict[str, tuple[dict[str, str], ...]] = {
    "chat": ({"filter": "conversational"},),
    "embedding": ({"pipeline_tag": "feature-extraction"}, {"pipeline_tag": "sentence-similarity"}),
    "vision": ({"pipeline_tag": "image-text-to-text"},),
}

#: A wildcard search fetches this many per literal piece, then filters.
WILDCARD_FETCH = 100


def wildcard_parts(query: str) -> tuple[str, list[str]] | None:
    """``qwen3*fp8`` -> the glob and the literal pieces to ask the Hub for; None without wildcards.

    ``*`` is any text and ``?`` one character. The glob matches anywhere in
    ``author/name``, like the plain search does. The Hub has no wildcards: each
    of the (at most three longest) literal pieces is searched for, and the
    union filtered by the glob.
    """
    q = query.strip().lower()
    if "*" not in q and "?" not in q:
        return None
    pieces = sorted(dict.fromkeys(p for p in re.split(r"[*?]+", q) if p), key=len, reverse=True)[:3]
    return f"*{q}*", pieces

_EXPAND = [
    "safetensors", "config", "cardData", "tags", "gated", "downloads", "likes", "trendingScore",
    "lastModified", "createdAt", "pipeline_tag", "library_name",
]


class HubUnavailable(RuntimeError):
    """The Hub could not be reached (no internet, a proxy, an outage)."""


class HubNotFound(LookupError):
    """No such repository, or one this server's token may not see."""


@dataclass
class _Entry:
    expires: float
    value: Any


class _Cache:
    def __init__(self) -> None:
        self._data: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None or entry.expires < time.monotonic():
                return None
            return entry.value

    def put(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            if len(self._data) > 500:
                self._data.clear()
            self._data[key] = _Entry(time.monotonic() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = _Cache()


def _is_network_error(exc: BaseException) -> bool:
    """True when the Hub could not be reached -- not when it answered "no".

    The Hub's own refusals (not found, gated, bad revision) are ``OSError``
    subclasses too, via ``requests.HTTPError``; they carry the response.
    """
    if getattr(exc, "response", None) is not None:
        return False
    name = type(exc).__name__
    if name in {"RepositoryNotFoundError", "GatedRepoError", "EntryNotFoundError", "RevisionNotFoundError",
                "HfHubHTTPError", "FileNotFoundError"}:
        return False
    text = str(exc).lower()
    return (
        isinstance(exc, (ConnectionError, TimeoutError, OSError))
        or name in {"ConnectionError", "ConnectTimeout", "ReadTimeout", "ProxyError", "SSLError", "OfflineModeIsEnabled"}
        or "offline" in text or "failed to resolve" in text or "max retries" in text
    )


def _get(obj: Any, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, None) if not isinstance(obj, dict) else obj.get(name)
        if value is not None:
            return value
    return None


def info_to_dict(info: Any) -> dict[str, Any]:
    """A Hub ``ModelInfo`` as a plain dict, so normalization is testable without the Hub."""
    safetensors = _get(info, "safetensors")
    card = _get(info, "card_data", "cardData")
    if card is not None and not isinstance(card, dict):
        card = card.to_dict() if hasattr(card, "to_dict") else dict(vars(card))
    siblings = []
    for sibling in _get(info, "siblings") or []:
        siblings.append({
            "name": _get(sibling, "rfilename", "name"),
            "size": _get(sibling, "size"),
        })
    created = _get(info, "created_at", "createdAt")
    modified = _get(info, "last_modified", "lastModified")
    return {
        "id": _get(info, "id", "modelId"),
        "author": _get(info, "author"),
        "downloads": _get(info, "downloads") or 0,
        "likes": _get(info, "likes") or 0,
        "trending_score": _get(info, "trending_score", "trendingScore"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
        "last_modified": modified.isoformat() if hasattr(modified, "isoformat") else modified,
        "pipeline_tag": _get(info, "pipeline_tag"),
        "library_name": _get(info, "library_name"),
        "tags": list(_get(info, "tags") or []),
        "gated": _get(info, "gated") or False,
        "safetensors_parameters": dict(_get(safetensors, "parameters") or {}) if safetensors else {},
        "config": dict(_get(info, "config") or {}),
        "card": card or {},
        "siblings": siblings,
    }


def card_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """The marketplace card for one Hub model: what a person needs to choose it."""
    repo_id = str(data.get("id") or "")
    tags = [t for t in data.get("tags") or [] if isinstance(t, str)]
    config = data.get("config") or {}
    tokenizer_config = config.get("tokenizer_config") or {}
    chat_template = tokenizer_config.get("chat_template")
    if isinstance(chat_template, list):  # several named templates
        chat_template = " ".join(str(t.get("template", "")) for t in chat_template if isinstance(t, dict))
    params_by_dtype = data.get("safetensors_parameters") or {}
    siblings = [s.get("name") or "" for s in data.get("siblings") or []]
    fmt = facts.detect_format(tags, data.get("library_name"), bool(params_by_dtype), siblings)
    weights = facts.weights_bytes(params_by_dtype)
    if weights is None and data.get("siblings"):
        weight_files = [s for s in data["siblings"] if str(s.get("name", "")).endswith(".safetensors")]
        if weight_files and all(s.get("size") for s in weight_files):
            weights = sum(int(s["size"]) for s in weight_files)
    quant = facts.detect_quantization(repo_id, tags, config, params_by_dtype)
    params_count = sum(int(v) for v in params_by_dtype.values()) if params_by_dtype else None
    name_params = facts.params_from_name(repo_id)
    if quant in {"awq", "gptq", "int4", "nvfp4", "mxfp4", "bitsandbytes"} and name_params:
        params_b = name_params  # packed weights undercount the real parameter count
    elif params_count:
        params_b = round(params_count / 1e9, 2)
    else:
        params_b = name_params
    capabilities = facts.detect_capabilities(
        repo_id=repo_id,
        pipeline_tag=data.get("pipeline_tag"),
        tags=tags,
        architectures=list(config.get("architectures") or []),
        chat_template=chat_template if isinstance(chat_template, str) else None,
    )
    task = facts.task_of(capabilities, data.get("pipeline_tag"))
    runnable, why_not = facts.vllm_runnable(fmt, task)
    card = data.get("card") or {}
    license_ = card.get("license") or next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), None)
    base_model = card.get("base_model")
    if isinstance(base_model, list):
        base_model = base_model[0] if base_model else None
    return {
        "repo_id": repo_id,
        "name": repo_id.split("/")[-1],
        "author": data.get("author") or (repo_id.split("/")[0] if "/" in repo_id else None),
        "downloads": int(data.get("downloads") or 0),
        "likes": int(data.get("likes") or 0),
        "trending_score": data.get("trending_score"),
        "created_at": data.get("created_at"),
        "last_modified": data.get("last_modified"),
        "pipeline_tag": data.get("pipeline_tag"),
        "license": license_,
        "gated": bool(data.get("gated")),
        "params_b": params_b,
        "active_params_b": facts.active_params_from_name(repo_id),
        "weights_bytes": weights,
        "dtype": facts.compute_dtype(params_by_dtype),
        "format": fmt,
        "quantization": quant,
        "capabilities": capabilities,
        "task": task,
        "runnable": runnable,
        "not_runnable_reason": why_not,
        "architecture": (list(config.get("architectures") or []) or [None])[0],
        "model_type": config.get("model_type"),
        "base_model": base_model,
        "languages": card.get("language") if isinstance(card.get("language"), list) else None,
    }


_README_FRONT_MATTER = re.compile(r"^---\n.*?\n---\n", re.S)


def readme_summary(text: str, limit: int = 700) -> str | None:
    """The first real paragraph of a model card: what the authors say the model is."""
    body = _README_FRONT_MATTER.sub("", text or "")
    for block in re.split(r"\n\s*\n", body):
        line = block.strip()
        if not line or line.startswith(("#", "<", "!", "|", "```", "[![", "- ", "* ", ">")):
            continue
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)  # links -> text
        plain = re.sub(r"[*_`]", "", plain)
        plain = " ".join(plain.split())
        if len(plain) < 60:
            continue
        return plain if len(plain) <= limit else plain[: limit - 1].rsplit(" ", 1)[0] + "…"
    return None


class HubClient:
    """What the marketplace asks the Hub."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token or None

    def _api(self) -> Any:
        from huggingface_hub import HfApi  # noqa: PLC0415

        return HfApi(token=self.token)

    async def search(self, query: str = "", *, sort: str = "trending", task: str = "chat",
                     limit: int = 40, author: str | None = None) -> list[dict[str, Any]]:
        """Models matching *query* (all models when empty), as cards.

        *query* may hold ``*`` and ``?`` wildcards; *author* narrows to one
        organisation or user, which the Hub filters itself.
        """
        author = (author or "").strip() or None
        key = (f"search:{bool(self.token)}:{query.strip().lower()}:{sort}:{task}:{limit}"
               f":{(author or '').lower()}")
        cached = _cache.get(key)
        if cached is not None:
            return cached
        queries = TASK_QUERIES.get(task, TASK_QUERIES["chat"])
        hub_sort = SORTS.get(sort, SORTS["trending"])
        wild = wildcard_parts(query)
        terms = wild[1] if wild else [query.strip()]
        fetch = WILDCARD_FETCH if wild else limit

        def run() -> list[dict[str, Any]]:
            api = self._api()
            seen: dict[str, dict[str, Any]] = {}
            for extra in queries:
                for term in terms or [""]:
                    for info in api.list_models(
                        search=term or None, author=author, sort=hub_sort, limit=fetch, expand=_EXPAND,
                        **extra,
                    ):
                        data = info_to_dict(info)
                        if data["id"] and data["id"] not in seen:
                            seen[data["id"]] = card_from_dict(data)
            cards = list(seen.values())
            if wild:
                cards = [c for c in cards if fnmatch.fnmatchcase(c["repo_id"].lower(), wild[0])]
            if len(queries) > 1 or len(terms) > 1:  # lists merged: put them back in the order asked for
                order = {"downloads": "downloads", "likes": "likes"}.get(sort)
                if order:
                    cards.sort(key=lambda c: c.get(order) or 0, reverse=True)
                elif sort == "trending":
                    cards.sort(key=lambda c: c.get("trending_score") or 0, reverse=True)
                elif sort == "recent":
                    cards.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
            return cards[:limit]

        cards = await self._call(run)
        _cache.put(key, cards, SEARCH_TTL_SEC)
        return cards

    async def cards_for(self, repo_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Cards for named repositories (the curated list), fetched together."""
        missing = [r for r in repo_ids if _cache.get(f"card:{bool(self.token)}:{r}") is None]
        if missing:
            def run() -> dict[str, dict[str, Any]]:
                api = self._api()
                found: dict[str, dict[str, Any]] = {}
                for repo in missing:
                    try:
                        found[repo] = card_from_dict(info_to_dict(api.model_info(repo, expand=_EXPAND)))
                    except Exception as exc:  # noqa: BLE001 - one missing repo is not the list failing
                        if _is_network_error(exc):
                            raise
                        log.info("marketplace: %s not available on the Hub: %s", repo, exc)
                return found

            fetched = await self._call(run)
            for repo, card in fetched.items():
                _cache.put(f"card:{bool(self.token)}:{repo}", card, DETAIL_TTL_SEC)
        return {
            r: c for r in repo_ids if (c := _cache.get(f"card:{bool(self.token)}:{r}")) is not None
        }

    async def detail(self, repo_id: str) -> dict[str, Any]:
        """Everything the detail view shows: the card, the architecture, the files, the summary."""
        key = f"detail:{bool(self.token)}:{repo_id}"
        cached = _cache.get(key)
        if cached is not None:
            return cached

        def run() -> dict[str, Any]:
            from huggingface_hub import hf_hub_download  # noqa: PLC0415

            api = self._api()
            try:
                info = api.model_info(repo_id, files_metadata=True)
            except Exception as exc:
                if _is_network_error(exc):
                    raise
                raise HubNotFound(str(exc)) from exc
            data = info_to_dict(info)
            card = card_from_dict(data)
            names = {s["name"] for s in data["siblings"]}
            config: dict[str, Any] = {}
            if "config.json" in names:
                try:
                    path = hf_hub_download(repo_id, "config.json", token=self.token)
                    with open(path, encoding="utf-8") as fh:
                        config = json.load(fh)
                except Exception as exc:  # noqa: BLE001 - gated or broken config: the card still stands
                    log.info("marketplace: no config.json for %s: %s", repo_id, exc)
            summary = None
            if "README.md" in names:
                try:
                    path = hf_hub_download(repo_id, "README.md", token=self.token)
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        summary = readme_summary(fh.read(200_000))
                except Exception:  # noqa: BLE001
                    summary = None
            arch = facts.architecture_from_config(config)
            if config:
                # The full config knows the quantization the list could only guess at.
                card["quantization"] = facts.detect_quantization(
                    repo_id, data["tags"], config, data["safetensors_parameters"],
                ) or card["quantization"]
            files = sorted(
                ({"name": s["name"], "size": s["size"]} for s in data["siblings"] if s.get("name")),
                key=lambda f: f["name"],
            )
            return {
                **card,
                "summary": summary,
                "architecture_facts": arch.to_dict(),
                "max_context": arch.max_context,
                "kv_bytes_per_token": arch.kv_bytes_per_token(),
                "needs_remote_code": arch.needs_remote_code,
                "files": files,
                "total_bytes": sum(int(f["size"] or 0) for f in files) or None,
            }

        detail = await self._call(run)
        _cache.put(key, detail, DETAIL_TTL_SEC)
        return detail

    @staticmethod
    async def _call(fn: Any) -> Any:
        try:
            return await asyncio.to_thread(fn)
        except (HubNotFound, HubUnavailable):
            raise
        except Exception as exc:
            if _is_network_error(exc):
                raise HubUnavailable(str(exc)) from exc
            raise


def clear_cache() -> None:
    """Forget every cached answer (tests, and a token change)."""
    _cache.clear()
    _avatars.clear()


# ── Owner avatars ─────────────────────────────────────────────────────────
#
# A model has no picture of its own: the Hub shows its owner's, from
# /api/organizations/<name>/avatar (or /api/users/<name>/avatar for a person).
# The server fetches and keeps them, so a browser that cannot reach Hugging
# Face still sees them and the page loads nothing from another origin.

HUB_URL = "https://huggingface.co"
AVATAR_TTL_SEC = 24 * 3600
AVATAR_MISS_TTL_SEC = 3600
AVATAR_MAX_BYTES = 512 * 1024
AVATAR_CACHE_MAX = 500
_AVATAR_NAME = re.compile(r"^[A-Za-z0-9][\w.-]{0,95}$")
#: Pictures are served from our own origin: raster images only (an SVG can
#: carry script), and only from where the Hub keeps them.
AVATAR_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"})
AVATAR_HOSTS = ("huggingface.co", "hf.co", "gravatar.com")
_avatars: dict[str, tuple[float, tuple[str, bytes] | None]] = {}


def _avatar_host_ok(url: str) -> bool:
    """An https URL on one of AVATAR_HOSTS (or a subdomain of one)."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in AVATAR_HOSTS)


async def fetch_avatar(author: str, *, client: Any = None) -> tuple[str, bytes] | None:
    """``(content_type, bytes)`` of *author*'s avatar on the Hub, or None."""
    if not _AVATAR_NAME.match(author or ""):
        return None
    key = author.lower()
    hit = _avatars.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    import httpx  # noqa: PLC0415

    from llm_port_backend.services.tls import default_httpx_verify  # noqa: PLC0415

    own = client is None
    client = client or httpx.AsyncClient(timeout=5.0, follow_redirects=True, verify=default_httpx_verify())
    result: tuple[str, bytes] | None = None
    reached = False
    try:
        for kind in ("organizations", "users"):
            response = await client.get(f"{HUB_URL}/api/{kind}/{author}/avatar")
            reached = True
            if response.status_code != 200:
                continue
            url = (response.json() or {}).get("avatarUrl")
            if not isinstance(url, str) or not _avatar_host_ok(url):
                continue
            image = await client.get(url)
            kind_of = image.headers.get("content-type", "").split(";")[0].strip().lower()
            small = len(image.content) <= AVATAR_MAX_BYTES
            wanted = kind_of in AVATAR_TYPES and _avatar_host_ok(str(image.url))
            if image.status_code == 200 and wanted and small:
                result = (kind_of, image.content)
                break
    except Exception as exc:  # a missing picture is never an error
        log.info("marketplace: no avatar for %s: %s", author, type(exc).__name__)
    finally:
        if own:
            await client.aclose()
    if len(_avatars) >= AVATAR_CACHE_MAX:
        _avatars.clear()
    # Unreachable Hub: ask again soon. Known to have none: not for an hour.
    ttl = AVATAR_TTL_SEC if result else (AVATAR_MISS_TTL_SEC if reached else 600)
    _avatars[key] = (time.monotonic() + ttl, result)
    return result
