# How RAG Lite searches, and how well

RAG Lite is the knowledge base built into the backend. The model searches it
itself, with the `knowledge_search` tool (see
[What the gateway does with a chat request](gateway-pipeline.md)). This page
says how a search is made, what each part adds, and what it costs, measured
on a public benchmark.

## A search, step by step

1. **Keyword search**, ranked by BM25. Each chunk has a generated full-text
   column (English: stemmed, stop words out) with a GIN index. BM25 is
   computed in SQL: a word rare in the collection counts for more than a
   common one, and a long chunk does not win for being long. The candidates
   are the chunks with the query's rarest words, up to a budget of matches;
   a chunk that shares only common words with the query cannot reach the
   top and is not scored (as Lucene's MaxScore does).
2. **Vector search**, by cosine similarity over the chunk embeddings
   (pgvector, HNSW index). It runs once the query is embedded; the keyword
   search runs while the embedding is on its way.
3. **Fusion.** The two rankings become one by reciprocal rank fusion
   (k = 60): ranks, not scores, since BM25 scores and cosine similarities do
   not compare.
4. **Reranking**, when a reranker is set. A cross-encoder reads the query
   and each of the best candidates together and scores them again. If it
   fails, the fused order stands.

Keyword and vector search together is the default (`rag_lite.hybrid_search`).
Reranking is off until a reranker's provider is chosen
(`rag_lite.rerank_provider_id`). Both are system settings and take effect
at once.

## Measured on RAGBench

[RAGBench](https://huggingface.co/datasets/galileo-ai/ragbench) has questions
with the documents that answer them, and marks which sentences do. Five of
its test sets were loaded, each as a collection: every document of every
question, so each question is searched among all the others' documents too.

| Set | What it is | Questions | Chunks |
|---|---|---|---|
| emanual | TV user manual | 132 | 114 |
| covidqa | COVID-19 research papers | 242 | 902 |
| techqa | IBM technotes | 251 | 1,952 |
| hotpotqa | Wikipedia paragraphs | 390 | 1,555 |
| msmarco | Web passages | 421 | 3,481 |

A result counts when its chunk holds one of the question's marked
sentences. **hit@1** is how often the first result does, **hit@5** how often
one of the first five does (the tool returns five by default), and **MRR**
the mean of 1 / the rank of the first one that does.

Embeddings: Qwen3-Embedding-0.6B (vLLM, DGX Spark). Reranker:
Qwen3-Reranker-0.6B (vLLM 0.9.2, TITAN RTX), 10 candidates unless said.

| Search | emanual | covidqa | techqa | hotpotqa | msmarco | Mean MRR |
|---|---|---|---|---|---|---|
| Keyword (BM25) | 0.795 / 0.857 | 0.624 / 0.710 | 0.570 / 0.674 | 0.831 / 0.901 | 0.751 / 0.851 | 0.799 |
| Vector | 0.523 / 0.659 | 0.748 / 0.828 | 0.637 / 0.720 | 0.921 / 0.957 | 0.838 / 0.905 | 0.814 |
| **Hybrid** (default) | 0.773 / 0.836 | 0.727 / 0.809 | 0.633 / 0.724 | 0.905 / 0.949 | 0.838 / 0.905 | 0.845 |
| Vector + reranker (30 candidates) | 0.758 / 0.843 | 0.769 / 0.855 | 0.681 / 0.766 | 0.967 / 0.983 | 0.910 / 0.950 | 0.879 |
| **Hybrid + reranker** | 0.735 / 0.835 | 0.769 / 0.850 | 0.681 / 0.773 | 0.972 / 0.984 | 0.912 / 0.950 | 0.878 |

Each cell is hit@1 / MRR. What it says:

- **Hybrid is the better default.** On the manual, whose questions name
  features and buttons, vector search alone put the right chunk first half
  the time (0.52) and hybrid three times in four (0.77). Elsewhere the two
  are within 0.02.
- **A reranker adds the most**, on every set: mean MRR 0.845 to 0.878, and
  the answer in the first five 95-100% of the time on four of the five.
  With it, hybrid or vector first matters little.
- **Keyword search ranked by Postgres's own `ts_rank_cd` made things worse**
  (hotpotqa hit@1 0.92 to 0.72 fused): it gives rare and common words the
  same weight. That is why BM25 is computed in SQL.

## What it costs

Median time of one search through the backend's API, the collection of that
set only:

| Search | emanual | covidqa | techqa | hotpotqa | msmarco |
|---|---|---|---|---|---|
| Vector | 43 ms | 52 ms | 62 ms | 59 ms | 73 ms |
| Hybrid | 49 ms | 53 ms | 78 ms | 61 ms | 76 ms |
| Hybrid + reranker, 10 candidates | 178 ms | 172 ms | 409 ms | 182 ms | 194 ms |
| Hybrid + reranker, 30 candidates | 429 ms | | 1,170 ms | | |

About 30 ms of every search is embedding the query (the DGX, over the
network); the keyword search runs meanwhile, so hybrid costs little more
than vector alone. The reranker's time grows with how much text it reads:
techqa's chunks are long, and a TITAN RTX (Turing, no FlashAttention) reads
about 10,000 tokens a second. 10 candidates ranked as well as 30 (mean MRR
0.878 both) in 40% of the time, so 10 is the default
(`rag_lite.rerank_candidates`).

## At 200,000 chunks

Every RAGBench chunk copied 25 times into one collection: 200,100 chunks,
208,105 in the table. The copies' vectors were shuffled so vector search
still finds the originals; their text was kept, the worst case for keyword
search (every match 25 times). 40 questions, median / p95:

| Search | Before | After |
|---|---|---|
| Vector (HNSW) | 10 / 12 ms | 10 / 12 ms |
| Keyword (BM25), first time | 380 ms / 2.9 s | 77 / 211 ms |
| Keyword (BM25), words seen before | | 53 / 98 ms |

Keyword search had scored every chunk with any word of the query -- 85,625
of them for one 13-word question -- and counted each word's documents in
full, every time. Now candidates come from the rarest words, up to 4,000
matches; a collection's size and each word's document count are kept a
minute per process; and a word's documents are counted up to 2,000, the
rest estimated from Postgres's own column statistics. Quality on the five
sets is unchanged.

The copying took 20 minutes: 166 chunks a second, one process, the HNSW
index taking most of it.

## Through the gateway

RAGBench questions through the gateway to `qwen2.5-0.5b-instruct` (DGX),
which calls `knowledge_search` itself, with PII on (tokenize) and the
reranker on. One round with a search:

| Step | Time |
|---|---|
| The model's first round (asks for the search) | 100-900 ms |
| The search (all collections, reranked) | ~470 ms, of which the reranker ~350 ms |
| PII scan of the results, passages seen before | ~20 ms |
| PII scan of the results, new passages | 370-620 ms |
| The model's second round (answers) | 300-900 ms |
| The gateway's own time besides | ~55 ms |

Two searches asked for in one message run side by side. A 0.5-billion-
parameter model asks for a search in only a few questions of twenty, even
when told to; a larger model is needed to judge that.

## Found and fixed on the way

| Found | Fixed |
|---|---|
| The rerank client built a new HTTP client for every search, which loads the CA bundle -- about 500 ms, on the event loop, freezing the backend meanwhile. Half of a reranked search's time. | Searches use the app's pooled client; a client of its own shares the process's SSL context. |
| BM25 first shortlisted the 500 best chunks by `ts_rank_cd`: 115 ms on techqa, and it dropped chunks BM25 ranks in the top ten. | Every candidate is scored, on the query's words alone (`setweight` + `ts_filter`), with a stored length column: 49 ms, and exact. |
| Ingestion ran one document at a time per worker (RabbitMQ prefetch 1): 7.8 documents a second. | Prefetch 16 (`taskiq_prefetch`): 37 a second; the upload is now the limit. |
| An ingest job was queued before it was committed; the worker could not see it, and 252 of 3,481 documents failed. | Committed first. A redelivered message replaces the document's chunks rather than adding to them. |
| The worker never saw a settings change made in the UI (it runs in another process), so it kept embedding with the old provider. | It reads RAG Lite's settings when a job runs. |
| Chunks were fixed-size windows, cut mid-sentence and mid-word. | Chunks end at paragraph and sentence ends and overlap by whole sentences. |
| Qwen3-Reranker, sent the query and documents raw, ranked nonsense first. | Its instruction format is applied (`rag_lite.rerank_template`, `auto` by model name). |
| The migration rebuilds the HNSW index; built in parallel it failed in Docker's 64 MB `/dev/shm`. | Built by one process in the migration; the compose file gives Postgres 1 GB of shared memory. |
| Keyword search on 200,000 chunks: 380 ms median, 2.9 s p95. | Rarest-word candidates, cached counts, capped counting: 53-77 ms median. |
| The faster BM25 query scored chunks on the rare words only; words in over half the collection stopped counting. | All the query's words count again (emanual BM25 MRR 0.839 to 0.857). |
| Search results were PII-scanned as one JSON string per tool answer, analysed afresh each time: 400-900 ms a round. | Each passage is its own text, so the PII service's cache knows it the next time: ~20 ms. |

## Not done

- **Context for each chunk.** A chunk from the middle of a long document
  does not say what document it is from. A line of context written by a
  model for each chunk ("contextual retrieval") helps there; the column for
  it exists (`context`) but nothing writes it. It costs a model call per
  chunk, and RAGBench could not show its gain: its documents are mostly one
  chunk each (techqa's about 2.5), and the technotes' first lines are
  boilerplate, not titles.
- **Vectors are stored at 2,000 dimensions**, zero-padded, whatever the
  model's size (Qwen3-Embedding: 1,024). It lets one table hold any model.
  But 2,000 is pgvector's HNSW limit: at that size an index entry does not
  fit an 8 KB page with its neighbours and takes two. At 200,000 chunks the
  table and its indexes took 5.2 GB (about 25 KB a chunk) and the HNSW
  index 3.1 GB; at 1,024 dimensions, or 2,000 stored as `halfvec`, it
  would be about a quarter or half of that, and faster to build. Search
  time is not the problem (10 ms); memory and ingest speed are.
- **New passages still cost a PII scan** the first time a search returns
  them (hundreds of ms for five). Scanning chunks once, when they are
  ingested, would take it off the search entirely.
