"""Knowledge the model looks up itself: search and open, as tools.

Retrieval used to run before the model on every request that asked for it:
the last user message searched RAG Lite and the results went in as a system
message, needed or not. Now the model decides. It gets two tools --
``knowledge_search`` to find passages, ``knowledge_open`` to read around one --
and the gateway runs them, as the user who asked: RAG Lite checks the user's
own permission to search.

They are offered only to a route whose model answers with tool calls
(``node_metadata["tools"]``); vLLM refuses a request that offers tools
otherwise.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from llm_port_api.services.gateway.rag_lite_client import RagLiteClient

SEARCH = "knowledge_search"
OPEN = "knowledge_open"
NAMES = frozenset({SEARCH, OPEN})

#: Passages a search returns at most, whatever the model asks for.
_MAX_RESULTS = 10

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": SEARCH,
            "description": (
                "Search the organisation's knowledge base -- the documents "
                "uploaded to it -- for passages relevant to a question. Use it "
                "when the answer may depend on internal documents, and write "
                "the query as a standalone question. Each result names its "
                "source: cite it when you use the passage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for, as a standalone question."},
                    "top_k": {
                        "type": "integer",
                        "description": f"How many passages to return (1-{_MAX_RESULTS}, default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": OPEN,
            "description": (
                "Read more of a document found by knowledge_search: the passage "
                "around one of its results, to follow it into its section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "The document_id of a search result."},
                    "chunk": {"type": "integer", "description": "The chunk of that result."},
                    "around": {
                        "type": "integer",
                        "description": "How many chunks to read either side (0-5, default 2).",
                    },
                },
                "required": ["document_id", "chunk"],
            },
        },
    },
]


@dataclass(slots=True)
class KnowledgeResult:
    """What a knowledge tool returned, for the model and the audit log."""

    content: str
    is_error: bool
    latency_ms: int


class KnowledgeTools:
    """Runs the knowledge tools for one request, as the user who made it."""

    def __init__(self, client: RagLiteClient, *, token: str | None) -> None:
        self._client = client
        self._token = token

    async def run(self, name: str, arguments: dict[str, Any]) -> KnowledgeResult:
        started = time.perf_counter()
        try:
            if name == SEARCH:
                content = await self._search(arguments)
            elif name == OPEN:
                content = await self._open(arguments)
            else:
                raise ValueError(f"No knowledge tool named {name}.")
            is_error = False
        except Exception as exc:  # the model is told, and can go on
            content, is_error = json.dumps({"error": str(exc)}), True
        return KnowledgeResult(content, is_error, int((time.perf_counter() - started) * 1000))

    async def _search(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("knowledge_search needs a query.")
        top_k = max(1, min(int(arguments.get("top_k") or 5), _MAX_RESULTS))
        results = await self._client.search(query=query, top_k=top_k, api_token=self._token)
        return json.dumps({
            "query": query,
            "results": [
                {
                    "source": r.get("filename", "unknown"),
                    "document_id": str(r.get("document_id", "")),
                    "chunk": r.get("chunk_index"),
                    "score": round(float(r.get("score") or 0), 3),
                    "text": r.get("chunk_text", ""),
                }
                for r in results
            ],
        })

    async def _open(self, arguments: dict[str, Any]) -> str:
        document_id = str(arguments.get("document_id") or "").strip()
        if not document_id:
            raise ValueError("knowledge_open needs the document_id of a search result.")
        passage = await self._client.passage(
            document_id=document_id,
            chunk=int(arguments.get("chunk") or 0),
            around=int(arguments.get("around") if arguments.get("around") is not None else 2),
            api_token=self._token,
        )
        return json.dumps({
            "source": passage.get("filename", "unknown"),
            "document_id": document_id,
            "chunks_in_document": passage.get("chunk_count"),
            "text": "\n\n".join(c.get("text", "") for c in passage.get("chunks", [])),
            "chunks": [c.get("chunk_index") for c in passage.get("chunks", [])],
        })


def calls_tools(candidate: Any) -> bool:
    """Whether the route's model answers with tool calls.

    Said by the route (``node_metadata["tools"]``). A remote API that says
    nothing is taken to (OpenAI-compatible APIs do); a local vLLM that says
    nothing is not, since vLLM refuses offered tools unless started for them.
    """
    said = (getattr(candidate, "node_metadata", None) or {}).get("tools")
    if said is not None:
        return bool(said)
    return str(getattr(getattr(candidate, "provider_type", None), "value", "")).startswith("remote_")
