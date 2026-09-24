"""vLLM's own recipes: how the vLLM project says to serve a model.

`vLLM Recipes <https://recipes.vllm.ai>`_ (Apache-2.0, maintained in
``vllm-project/recipes``) publishes one JSON document per model: the base
arguments, the features that are on by default (tool calling, reasoning), the
ones to opt into, per-hardware overrides and the oldest vLLM that runs it.
``/models.json`` lists them.

A recipe is advice from the people who ship the engine, so its arguments go
into the suggested settings ahead of our own family rules. Not all of it
applies here:

- the copy's shape (tensor/pipeline parallel) is the fit check's to decide;
- a path into vLLM's source tree (``--chat-template examples/...``) does not
  exist in the runtime image;
- hardware overrides are per generation, and are applied only when the
  cluster's accelerator is clearly that generation;
- opt-in features are offered, not applied; one needing a JSON argument is
  listed with its recipe rather than turned into a setting.

The server fetches recipes itself and caches them; an offline server simply
has none, and the suggestions fall back to the family rules.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from llm_port_backend.services.tls import default_httpx_verify

log = logging.getLogger(__name__)

BASE_URL = "https://recipes.vllm.ai"
INDEX_TTL_SEC = 24 * 3600
RECIPE_TTL_SEC = 24 * 3600
FAILURE_TTL_SEC = 10 * 60
TIMEOUT_SEC = 6.0

#: Settings the host form decides from the fit check, never taken from a recipe.
SHAPE_FLAGS = frozenset({
    "tensor-parallel-size", "pipeline-parallel-size", "data-parallel-size",
    "served-model-name", "model", "host", "port",
})

_cache: dict[str, tuple[float, Any]] = {}


@dataclass
class OptIn:
    name: str
    description: str
    args: list[str]
    #: Whether the arguments are plain settings this server can apply.
    usable: bool
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class Recipe:
    hf_id: str
    url: str
    title: str | None
    min_vllm_version: str | None
    context_length: int | None
    #: Settings the recipe turns on by default, by vLLM's argument names.
    config: dict[str, Any]
    #: Arguments that could not be used here, as written in the recipe.
    dropped: list[str]
    opt_in: list[OptIn]
    #: The hardware override applied, if any ("hopper", "blackwell", "amd", ...).
    hardware: str | None = None

    def to_dict(self, *, vllm_version: str | None = None) -> dict[str, Any]:
        data = asdict(self)
        data["runtime_too_old"] = bool(
            vllm_version and self.min_vllm_version
            and _version(vllm_version) < _version(self.min_vllm_version)
        )
        return data


def clear_cache() -> None:
    _cache.clear()


def _cached(key: str) -> Any:
    entry = _cache.get(key)
    if entry and entry[0] > time.monotonic():
        return entry[1]
    return None


def _put(key: str, value: Any, ttl: float) -> None:
    _cache[key] = (time.monotonic() + ttl, value)


def _version(text: str) -> tuple[int, ...]:
    """``0.27.1+93523f72.dev`` -> (0, 27, 1)."""
    parts = re.findall(r"\d+", text.split("+", 1)[0])[:3]
    return tuple(int(p) for p in parts) or (0,)


def _coerce(raw: str) -> Any:
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d*", raw):
        return float(raw)
    return raw


def args_to_config(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    """``["--tool-call-parser", "hermes", "--enable-auto-tool-choice"]`` -> settings, and what was left out.

    ``--no-x`` sets ``x`` off. JSON values, file paths and dotted
    sub-options stay out: they are listed, not guessed at.
    """
    config: dict[str, Any] = {}
    dropped: list[str] = []
    i = 0
    while i < len(argv):
        token = str(argv[i])
        if not token.startswith("-"):
            dropped.append(token)
            i += 1
            continue
        if "=" in token:
            flag, value = token.split("=", 1)
            i += 1
        elif i + 1 < len(argv) and not str(argv[i + 1]).startswith("--"):
            flag, value = token, str(argv[i + 1])
            i += 2
        else:
            flag, value = token, None
            i += 1
        name = flag.lstrip("-")
        written = flag if value is None else f"{flag} {value}"
        if (not flag.startswith("--") or "." in name or name in SHAPE_FLAGS
                or (value is not None and (value.startswith(("{", "[")) or "/" in value or value.endswith(".jinja")))):
            if name not in SHAPE_FLAGS:
                dropped.append(written)
            continue
        if value is None:
            if name.startswith("no-"):
                config[name[3:].replace("-", "_")] = False
            else:
                config[name.replace("-", "_")] = True
        else:
            config[name.replace("-", "_")] = _coerce(value)
    return config, dropped


def hardware_generation(accelerator: str | None) -> str | None:
    """The recipe override key for an accelerator, only when it is clear.

    GB10 (DGX Spark) is a Blackwell chip but not the B200 class the
    ``blackwell`` overrides are written for, so it gets none.
    """
    name = (accelerator or "").upper()
    if not name:
        return None
    if re.search(r"\b(H100|H200|H800|H20|GH200)\b", name):
        return "hopper"
    if re.search(r"\b(B200|B300|GB200|GB300)\b", name):
        return "blackwell"
    if re.search(r"\bMI3\d\dX?\b", name) or "INSTINCT" in name:
        return "amd"
    return None


def parse_recipe(data: dict[str, Any], *, accelerator: str | None = None) -> Recipe:
    """A recipe document -> the settings it recommends here."""
    hf_id = str(data.get("hf_id") or "")
    model = data.get("model") or {}
    features = data.get("features") or {}
    opt_in_names = set(data.get("opt_in_features") or [])

    argv: list[str] = [str(a) for a in model.get("base_args") or []]
    for name, feature in features.items():
        if name not in opt_in_names:
            argv += [str(a) for a in (feature or {}).get("args") or []]
    hardware = hardware_generation(accelerator)
    override = (data.get("hardware_overrides") or {}).get(hardware) if hardware else None
    if override:
        argv += [str(a) for a in override.get("extra_args") or []]
    config, dropped = args_to_config(argv)

    opt_in: list[OptIn] = []
    for name in sorted(opt_in_names):
        feature = features.get(name) or {}
        args = [str(a) for a in feature.get("args") or []]
        cfg, left_out = args_to_config(args)
        opt_in.append(OptIn(
            name=name,
            description=str(feature.get("description") or ""),
            args=args,
            usable=bool(cfg) and not left_out,
            config=cfg if not left_out else {},
        ))
    return Recipe(
        hf_id=hf_id,
        url=f"{BASE_URL}/{hf_id}",
        title=(data.get("meta") or {}).get("title"),
        min_vllm_version=model.get("min_vllm_version"),
        context_length=model.get("context_length"),
        config=config,
        dropped=dropped,
        opt_in=opt_in,
        hardware=hardware if override else None,
    )


async def _get_json(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(f"{BASE_URL}{path}", timeout=TIMEOUT_SEC)
    response.raise_for_status()
    return response.json()


async def _index(client: httpx.AsyncClient) -> dict[str, str] | None:
    """``{hf_id.lower(): json path}``, or None when the site cannot be reached."""
    cached = _cached("index")
    if cached is not None:
        return cached or None
    try:
        rows = await _get_json(client, "/models.json")
        index = {
            str(r["hf_id"]).lower(): str(r.get("json") or f"/{r['hf_id']}.json")
            for r in rows if isinstance(r, dict) and r.get("hf_id")
        }
    except Exception as exc:  # noqa: BLE001 - recipes are advice; no recipe is fine
        log.info("vLLM recipes index unavailable: %s", exc)
        _put("index", {}, FAILURE_TTL_SEC)
        return None
    _put("index", index, INDEX_TTL_SEC)
    return index


async def recipe_for(repo_id: str, *, accelerator: str | None = None,
                     client: httpx.AsyncClient | None = None) -> Recipe | None:
    """vLLM's recipe for *repo_id*, or None when it has none (or cannot be reached)."""
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, verify=default_httpx_verify())
    try:
        index = await _index(client)
        path = (index or {}).get(repo_id.lower())
        if path is None:
            return None
        key = f"recipe:{path}"
        data = _cached(key)
        if data is None:
            try:
                data = await _get_json(client, path)
            except Exception as exc:  # noqa: BLE001
                log.info("vLLM recipe %s unavailable: %s", path, exc)
                _put(key, {}, FAILURE_TTL_SEC)
                return None
            _put(key, data, RECIPE_TTL_SEC)
        if not data:
            return None
        return parse_recipe(data, accelerator=accelerator)
    finally:
        if own:
            await client.aclose()
