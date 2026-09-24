"""Files attached to a chat, too long to send whole: searched by the model.

An attachment went into the context whole, or -- when it did not fit the
session's token budget -- not at all, without a word: the model answered as
if nothing had been attached. Now a file that does not fit is named to the
model, and a model that calls tools gets ``attachment_search`` to look
through it; the gateway runs it, over the session's files, as the knowledge
tools run over the knowledge base.

The files are searched where they are: their text is stored encrypted with
the session, so it is not copied into an index. Each file is cut into
passages -- at paragraph and sentence ends -- when it is first searched, and
the passages are ranked by BM25, keyword search with rare words counting for
more. A session holds a few files; searching them in memory takes
milliseconds. (On RAGBench, BM25 alone ranks about as well as vector search:
mean MRR 0.80 against 0.81 over five subsets.)
"""

from __future__ import annotations

import heapq
import json
import math
import re
import threading
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Any

NAME = "attachment_search"

#: Passages a search returns at most, whatever the model asks for.
_MAX_RESULTS = 10

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": NAME,
            "description": (
                "Search the files attached to this conversation that were too "
                "long to include in full. Returns the passages that best match "
                "the query, each with its file name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for, in the words the file would use."},
                    "file": {"type": "string", "description": "Search only this file (its name); default: all of them."},
                    "top_k": {
                        "type": "integer",
                        "description": f"How many passages to return (1-{_MAX_RESULTS}, default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ── Passages ──────────────────────────────────────────────────────

#: About 400 tokens a passage, at 4 characters a token.
_PASSAGE_CHARS = 1600
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=\S)")


def passages(text: str, size: int = _PASSAGE_CHARS) -> list[str]:
    """*text* in passages of about *size* characters, ending where paragraphs
    and sentences do; a sentence longer than that is cut in pieces."""
    units: list[str] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        if len(paragraph) <= size:
            units.append(paragraph)
            continue
        for sentence in _SENTENCE_BREAK.split(paragraph):
            units.extend(sentence[i:i + size] for i in range(0, len(sentence), size))
    out: list[str] = []
    current = ""
    for unit in (u.strip() for u in units):
        if not unit:
            continue
        if current and len(current) + 1 + len(unit) > size:
            out.append(current)
            current = unit
        else:
            current = f"{current}\n{unit}" if current else unit
    if current:
        out.append(current)
    return out


# ── Words ─────────────────────────────────────────────────────────

_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset("""
a about above after again against all am an and any are as at be because been before being below between both but
by can could did do does doing down during each few for from further had has have having he her here hers him his
how i if in into is it its itself just me more most my no nor not of off on once only or other our ours out over own
same she should so some such than that the their theirs them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your yours
""".split())


def _stem(word: str) -> str:
    """A light English stem -- plurals, -ed, -ing, a final e (Porter's steps
    1a, 1b and 5a, roughly) -- so "closing" finds "closes" and "close"."""
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        stem = word[: -len(suffix)]
        if word.endswith(suffix) and len(stem) >= 3 and re.search(r"[aeiouy]", stem):
            if stem[-1] == stem[-2] and stem[-1] not in "lsz":
                stem = stem[:-1]  # running -> run
            word = stem
            break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def words(text: str) -> list[str]:
    """The words BM25 counts: lower case, stop words out, stemmed."""
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP]


# ── One file, cut and counted ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Indexed:
    passages: list[str]
    lengths: list[int]
    #: Each word's passages, with how often it occurs in each.
    postings: dict[str, list[tuple[int, int]]]


_CACHE: OrderedDict[tuple[str, int], _Indexed] = OrderedDict()
_CACHE_SIZE = 64
_CACHE_LOCK = threading.Lock()


def _indexed(key: str, text: str) -> _Indexed:
    """*text* cut into passages and counted -- once per file and process."""
    cache_key = (key, len(text))
    with _CACHE_LOCK:
        hit = _CACHE.get(cache_key)
        if hit is not None:
            _CACHE.move_to_end(cache_key)
            return hit
    parts = passages(text)
    lengths: list[int] = []
    postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for i, part in enumerate(parts):
        counts = Counter(words(part))
        lengths.append(sum(counts.values()))
        for word, tf in counts.items():
            postings[word].append((i, tf))
    indexed = _Indexed(parts, lengths, dict(postings))
    with _CACHE_LOCK:
        _CACHE[cache_key] = indexed
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return indexed


# ── The tool ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SearchableFile:
    """An attached file the model can search: its id, name and text."""

    id: str
    filename: str
    text: str


class AttachmentSearch:
    """Runs ``attachment_search`` over one request's attached files."""

    def __init__(self, files: list[SearchableFile]) -> None:
        self.files = files

    def run(self, arguments: dict[str, Any]) -> tuple[str, bool]:
        """The tool's answer for the model, and whether it is an error."""
        try:
            return json.dumps(self.search(arguments)), False
        except Exception as exc:  # the model is told, and can go on  # noqa: BLE001
            return json.dumps({"error": str(exc)}), True

    def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("attachment_search needs a query.")
        top_k = max(1, min(int(arguments.get("top_k") or 5), _MAX_RESULTS))
        files = self.files
        wanted = str(arguments.get("file") or "").strip().lower()
        if wanted:
            # A name that matches no file searches them all, rather than none.
            files = [f for f in files if f.filename.lower() == wanted] or files

        # BM25 over the passages of every file searched (k1 1.2, b 0.75),
        # walking only the passages that hold a word of the query.
        indexed = [(f, _indexed(f.id, f.text)) for f in files]
        n = sum(len(ix.passages) for _, ix in indexed)
        if not n:
            return {"query": query, "results": []}
        avgdl = max(sum(sum(ix.lengths) for _, ix in indexed) / n, 1.0)
        scores: dict[tuple[int, int], float] = defaultdict(float)
        for term in set(words(query)):
            df = sum(len(ix.postings.get(term, ())) for _, ix in indexed)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for f_at, (_, ix) in enumerate(indexed):
                for i, tf in ix.postings.get(term, ()):
                    scores[f_at, i] += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * ix.lengths[i] / avgdl))
        best = heapq.nlargest(top_k, scores.items(), key=lambda item: item[1])
        return {
            "query": query,
            "results": [
                {"file": indexed[f_at][0].filename, "passage": i + 1, "of": len(indexed[f_at][1].passages),
                 "score": round(score, 3), "text": indexed[f_at][1].passages[i]}
                for (f_at, i), score in best
            ],
        }
