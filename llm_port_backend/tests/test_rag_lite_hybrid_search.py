"""RAG Lite search: keyword and vector, fused by rank, re-scored by a reranker.

Measured on RAGBench (galileo-ai/ragbench): re-scoring the candidates with
Qwen3-Reranker raised hit@1 on emanual from 0.52 to 0.76. Keyword search
ranked by Postgres's ts_rank_cd -- no weighting of rare words -- made fused
results worse (hotpotqa hit@1 0.92 -> 0.72); it is ranked by BM25 instead.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.rag_lite_dao import RagLiteChunkDAO
from llm_port_backend.db.models.rag_lite import RagLiteChunk, RagLiteCollection, RagLiteDocument
from llm_port_backend.services.rag_lite.rerank import NONE, QWEN3, RerankClient, fuse, template_for
from llm_port_backend.services.rag_lite.service import RagLiteService


def _hit(chunk_id: str, text: str = "") -> dict[str, Any]:
    return {"chunk_id": chunk_id, "chunk_text": text or chunk_id, "chunk_index": 0,
            "filename": "f.txt", "document_id": "d", "score": 0.0}


def test_fusion_goes_by_rank_and_rewards_agreement() -> None:
    dense = [_hit("a"), _hit("b"), _hit("c")]
    keyword = [_hit("c"), _hit("d")]
    order = [h["chunk_id"] for h in fuse(dense, keyword)]
    assert order[0] == "c", "found by both, it comes first"
    assert set(order) == {"a", "b", "c", "d"}
    assert order.index("a") < order.index("d"), "rank 1 of one list beats rank 2 of the other"


@pytest.mark.parametrize(
    ("model", "setting", "expected"),
    [
        ("qwen3-reranker", "auto", QWEN3),
        ("Qwen/Qwen3-Reranker-0.6B", None, QWEN3),
        ("tomaarsen/Qwen3-Reranker-0.6B-seq-cls", "auto", QWEN3),
        ("BAAI/bge-reranker-v2-m3", "auto", NONE),
        ("my-reranker", "qwen3", QWEN3),
        ("qwen3-reranker", "none", NONE),
    ],
)
def test_the_template_follows_the_model_unless_set(model: str, setting: str | None, expected: str) -> None:
    assert template_for(model, setting) == expected


async def test_qwen3_input_is_wrapped_and_scores_come_back_in_order() -> None:
    sent: dict[str, Any] = {}

    def answer(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        assert str(request.url) == "http://127.0.0.1:7998/v1/rerank"
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.0001}, {"index": 0, "relevance_score": 0.98},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        client = RerankClient("http://127.0.0.1:7998", "qwen3-reranker", template=QWEN3, http_client=http)
        scores = await client.rerank("When is the launch?", ["On 5 May.", "Bananas are yellow."])

    assert scores == [0.98, 0.0001]
    assert sent["query"].startswith("<|im_start|>system\nJudge whether the Document")
    assert "<Query>: When is the launch?" in sent["query"]
    assert sent["documents"][0].startswith("<Document>: On 5 May.")
    assert sent["documents"][0].endswith("<think>\n\n</think>\n\n")


class _Embed:
    dim = 3

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _Chunks:
    def __init__(self, dense: list[dict[str, Any]], keyword: list[dict[str, Any]]) -> None:
        self.dense, self.keyword = dense, keyword
        self.asked: list[tuple[str, int]] = []

    async def search_similar(self, *, query_vector: list[float], top_k: int, collection_ids: Any) -> list[dict[str, Any]]:
        self.asked.append(("dense", top_k))
        return self.dense[:top_k]

    async def search_lexical(self, query: str, *, top_k: int, collection_ids: Any) -> list[dict[str, Any]]:
        self.asked.append(("keyword", top_k))
        return self.keyword[:top_k]


class _Reranker:
    def __init__(self, scores: dict[str, float] | None = None, fails: bool = False) -> None:
        self.scores, self.fails, self.seen = scores or {}, fails, []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.seen = documents
        if self.fails:
            raise httpx.ConnectError("reranker down")
        return [self.scores.get(d, 0.0) for d in documents]


def _service() -> RagLiteService:
    return RagLiteService.__new__(RagLiteService)


async def test_the_reranker_orders_the_fused_candidates() -> None:
    chunks = _Chunks([_hit("a"), _hit("b")], [_hit("c")])
    reranker = _Reranker({"c": 0.9, "a": 0.2, "b": 0.5})
    out = await _service().search("q", chunk_dao=chunks, embedding_client=_Embed(), top_k=2,
                                  hybrid=True, reranker=reranker, candidates=30)  # type: ignore[arg-type]
    assert [h["chunk_id"] for h in out] == ["c", "b"]
    assert sorted(reranker.seen) == ["a", "b", "c"], "it saw every fused candidate"
    assert ("dense", 50) in chunks.asked and ("keyword", 50) in chunks.asked, "deep lists for fusion"


async def test_a_failed_reranker_leaves_the_search_order() -> None:
    chunks = _Chunks([_hit("a"), _hit("b")], [])
    out = await _service().search("q", chunk_dao=chunks, embedding_client=_Embed(), top_k=2,
                                  hybrid=False, reranker=_Reranker(fails=True))  # type: ignore[arg-type]
    assert [h["chunk_id"] for h in out] == ["a", "b"]


async def test_vector_alone_asks_for_no_more_than_it_returns() -> None:
    chunks = _Chunks([_hit(x) for x in "abcdefg"], [])
    out = await _service().search("q", chunk_dao=chunks, embedding_client=_Embed(), top_k=3)  # type: ignore[arg-type]
    assert [h["chunk_id"] for h in out] == ["a", "b", "c"]
    assert chunks.asked == [("dense", 3)]


async def test_bm25_ranks_a_rare_word_above_common_ones(dbsession: AsyncSession) -> None:
    """ts_rank_cd would rank the chunk with the most common words first."""
    collection = RagLiteCollection(name=f"bm25-{uuid.uuid4().hex[:6]}")
    dbsession.add(collection)
    await dbsession.flush()
    doc = RagLiteDocument(filename="kb.txt", doc_type="txt", collection_id=collection.id,
                          size_bytes=1, sha256=uuid.uuid4().hex, file_store_key="k")
    dbsession.add(doc)
    await dbsession.flush()
    texts = [
        "The server error appears when the server starts; restart the server after the error.",
        "Error CWSAA0100E is raised by the portal configuration.",
        *[f"The server logs an error at start number {i}." for i in range(8)],
    ]
    for i, t in enumerate(texts):
        dbsession.add(RagLiteChunk(document_id=doc.id, collection_id=collection.id, chunk_index=i, chunk_text=t))
    await dbsession.flush()

    hits = await RagLiteChunkDAO(dbsession).search_lexical(
        "server error CWSAA0100E", top_k=3, collection_ids=[collection.id],
    )
    assert hits[0]["chunk_text"].startswith("Error CWSAA0100E"), [h["chunk_text"][:40] for h in hits]


async def test_keyword_search_with_only_common_words_finds_nothing_rather_than_everything(
    dbsession: AsyncSession,
) -> None:
    collection = RagLiteCollection(name=f"bm25-{uuid.uuid4().hex[:6]}")
    dbsession.add(collection)
    await dbsession.flush()
    doc = RagLiteDocument(filename="kb.txt", doc_type="txt", collection_id=collection.id,
                          size_bytes=1, sha256=uuid.uuid4().hex, file_store_key="k")
    dbsession.add(doc)
    await dbsession.flush()
    for i in range(4):
        dbsession.add(RagLiteChunk(document_id=doc.id, collection_id=collection.id, chunk_index=i,
                                   chunk_text=f"server error {i}"))
    await dbsession.flush()
    assert await RagLiteChunkDAO(dbsession).search_lexical("server error", top_k=3, collection_ids=[collection.id]) == []


async def test_bm25_scores_are_exact(dbsession: AsyncSession) -> None:
    """The SQL scores are BM25's, computed here from the same ``tsvector``s."""
    collection = RagLiteCollection(name=f"bm25-{uuid.uuid4().hex[:6]}")
    dbsession.add(collection)
    await dbsession.flush()
    doc = RagLiteDocument(filename="kb.txt", doc_type="txt", collection_id=collection.id,
                          size_bytes=1, sha256=uuid.uuid4().hex, file_store_key="k")
    dbsession.add(doc)
    await dbsession.flush()
    # "manual" is in four of the six: common, it finds no candidates, but it
    # counts, a little, for the chunks the rarer words find.
    texts = [
        "Restart the queue manager after changing the channel definition, says the manual.",
        "The channel stops when the queue manager is restarted twice; restart the channel.",
        "Firmware updates reset the channel list on the television, the manual says.",
        "Queue depth alerts are raised by the monitoring agent (see the manual).",
        "Nothing here matches, manual or not.",
        "Another unrelated sentence about gardening.",
    ]
    for i, t in enumerate(texts):
        dbsession.add(RagLiteChunk(document_id=doc.id, collection_id=collection.id, chunk_index=i, chunk_text=t))
    await dbsession.flush()
    query = "how to restart the channel of a queue manager, from the manual"

    rows = (await dbsession.execute(
        text("SELECT chunk_text, content_tsv::text AS tsv, content_len FROM rag_lite_chunks WHERE collection_id = :c"),
        {"c": collection.id},
    )).all()
    query_terms = set((await dbsession.execute(
        text("SELECT tsvector_to_array(to_tsvector('english', :q))"), {"q": query},
    )).scalar_one())
    tf = {r.chunk_text: {m[0]: len(m[1].split(",")) for m in re.findall(r"'([^']+)':([\d,A-D]+)", r.tsv)} for r in rows}
    n, avgdl = len(rows), sum(r.content_len for r in rows) / len(rows)
    df = {t: sum(1 for words in tf.values() if t in words) for t in query_terms}
    telling = {t for t in query_terms if 0 < df[t] <= n * 0.5}
    expected = {}
    for r in rows:
        words = tf[r.chunk_text]
        if not telling & words.keys():
            continue
        expected[r.chunk_text] = sum(
            math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            * words[t] * 2.2 / (words[t] + 1.2 * (0.25 + 0.75 * r.content_len / avgdl))
            for t in query_terms if t in words
        )

    hits = await RagLiteChunkDAO(dbsession).search_lexical(query, top_k=10, collection_ids=[collection.id])
    assert {h["chunk_text"]: pytest.approx(h["score"]) for h in hits} == {
        k: pytest.approx(v) for k, v in expected.items()
    }
    assert "manual" in query_terms and df["manual"] > n * 0.5
    assert not any(h["chunk_text"].startswith("Nothing here") for h in hits), "only the common word: not a candidate"
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


async def test_the_keyword_search_runs_while_the_query_is_embedded() -> None:
    order: list[str] = []

    class _SlowEmbed(_Embed):
        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            order.append("embed started")
            await asyncio.sleep(0.05)
            order.append("embed done")
            return await super().embed_texts(texts)

    class _Logged(_Chunks):
        async def search_lexical(self, query: str, *, top_k: int, collection_ids: Any) -> list[dict[str, Any]]:
            order.append("keyword")
            return await super().search_lexical(query, top_k=top_k, collection_ids=collection_ids)

    chunks = _Logged([_hit("a")], [_hit("b")])
    await _service().search("q", chunk_dao=chunks, embedding_client=_SlowEmbed(), top_k=2, hybrid=True)  # type: ignore[arg-type]
    assert order.index("keyword") < order.index("embed done")


async def test_a_failed_keyword_search_does_not_leave_the_embedding_running() -> None:
    class _Broken(_Chunks):
        async def search_lexical(self, query: str, *, top_k: int, collection_ids: Any) -> list[dict[str, Any]]:
            raise RuntimeError("db down")

    finished = asyncio.Event()

    class _SlowEmbed(_Embed):
        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            await asyncio.sleep(0.05)
            finished.set()
            return await super().embed_texts(texts)

    with pytest.raises(RuntimeError, match="db down"):
        await _service().search("q", chunk_dao=_Broken([], []), embedding_client=_SlowEmbed(), top_k=2, hybrid=True)  # type: ignore[arg-type]
    await asyncio.sleep(0.1)
    assert not finished.is_set(), "the embedding was cancelled"


async def test_a_reranker_without_a_shared_client_does_not_build_an_ssl_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default httpx client loads the CA bundle: ~500 ms per search, on the event loop."""
    from llm_port_backend.services.rag_lite import rerank as rerank_module
    from llm_port_backend.services.tls import default_httpx_verify

    made: list[Any] = []
    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        made.append(kwargs.get("verify"))
        return real(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]}),
        ))

    monkeypatch.setattr(rerank_module.httpx, "AsyncClient", client)
    assert await RerankClient("http://r:1", "m").rerank("q", ["d"]) == [0.5]
    assert made == [default_httpx_verify()]



async def _collection_of(dbsession: AsyncSession, texts: list[str]) -> RagLiteCollection:
    collection = RagLiteCollection(name=f"bm25-{uuid.uuid4().hex[:6]}")
    dbsession.add(collection)
    await dbsession.flush()
    doc = RagLiteDocument(filename="kb.txt", doc_type="txt", collection_id=collection.id,
                          size_bytes=1, sha256=uuid.uuid4().hex, file_store_key="k")
    dbsession.add(doc)
    await dbsession.flush()
    for i, t in enumerate(texts):
        dbsession.add(RagLiteChunk(document_id=doc.id, collection_id=collection.id, chunk_index=i, chunk_text=t))
    await dbsession.flush()
    return collection


async def test_past_the_budget_only_the_rarest_words_find_candidates(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On 200,000 chunks, scoring every chunk with any word of the query took 1.9 s."""
    from llm_port_backend.db.dao import rag_lite_dao

    monkeypatch.setattr(rag_lite_dao, "_CANDIDATE_BUDGET", 2)
    collection = await _collection_of(dbsession, [
        "The zeppelin hangar is in Friedrichshafen.",
        "A hangar for aircraft.", "Another hangar.", "The hangar door.",
        "Unrelated text.", "More unrelated text.", "Still more.", "And more.",
    ])
    hits = await RagLiteChunkDAO(dbsession).search_lexical("zeppelin hangar", top_k=10, collection_ids=[collection.id])
    assert [h["chunk_text"] for h in hits] == ["The zeppelin hangar is in Friedrichshafen."]


async def test_a_word_no_chunk_had_is_found_once_a_document_brings_it(dbsession: AsyncSession) -> None:
    """Document counts are kept a minute; a count of nought is not, or the new document would hide."""
    collection = await _collection_of(dbsession, ["Bananas are yellow.", "Apples are red.", "Pears are green."])
    dao = RagLiteChunkDAO(dbsession)
    assert await dao.search_lexical("quokka", top_k=5, collection_ids=[collection.id]) == []

    doc = RagLiteDocument(filename="new.txt", doc_type="txt", collection_id=collection.id,
                          size_bytes=1, sha256=uuid.uuid4().hex, file_store_key="k2")
    dbsession.add(doc)
    await dbsession.flush()
    dbsession.add(RagLiteChunk(document_id=doc.id, collection_id=collection.id, chunk_index=0,
                               chunk_text="A quokka lives on Rottnest Island."))
    await dbsession.flush()
    hits = await dao.search_lexical("quokka", top_k=5, collection_ids=[collection.id])
    assert [h["chunk_text"] for h in hits] == ["A quokka lives on Rottnest Island."]
