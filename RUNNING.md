# Running OmniDoc Search

The ETL no longer runs inside the API process, so starting the system now means
starting **two Celery workers** alongside the API. Starting only the API will
accept jobs and never run them.

```bash
source /Users/ricky/Documents/workspace/agents_learn/agent_env/bin/activate
redis-server                     # broker, job event bus, rate limiter
```

Then, one process each:

```bash
# 1. extraction: network-bound, slow, holds a Playwright browser
celery -A tasks worker -Q etl --pool=solo -n etl@%h --loglevel=info

# 2. ingestion: CPU/GPU-bound (embeddings)
celery -A tasks worker -Q ingest --pool=solo -n ingest@%h --loglevel=info

# 3. API
uvicorn api:app --reload --port 8000

# 4. frontend
cd frontend && npm run dev
```

`--pool=solo` is a macOS requirement: the default prefork pool crashes with
`WorkerLostError`/SIGABRT because of fork-safety in the ML/Playwright stack. On
Linux, drop it and use `--concurrency=N`.

Two queues rather than one, because the two halves have opposite shapes: a burst
of extractions would otherwise starve embedding, and they want different
concurrency and different scaling. `tasks.py` routes by task name.

## Environment

| var | default | meaning |
|---|---|---|
| `API_DEEPSEEK` | — | DeepSeek key (required) |
| `API_KEY` | — | the key clients send as `x-api-key` (required) |
| `RAG_TOP_K` | 8 | chunks retrieved per query |
| `RAG_HYBRID` | 1 | fuse BM25 with dense retrieval |
| `RAG_CHUNK_SIZE` | 256 | chunk size in tokens; see below |
| `RAG_CHUNK_OVERLAP` | 64 | overlap between chunks |
| `CORPUS_TTL_SECONDS` | 1209600 | age at which an index is assumed drifted (14d) |
| `CORPUS_RECHECK_AFTER` | 3600 | min record age before spending an HTTP request on a `<lastmod>` check |
| `CORPUS_DB_PATH` | ./omnidoc_corpus.sqlite3 | corpus registry |
| `QUERY_CACHE` | 1 | set to `0` to disable answer caching |
| `QUERY_CACHE_TTL` | 86400 | answer cache TTL, seconds |
| `OMNIDOC_RUNS_ROOT` | runs | per-run extraction output |
| `OMNIDOC_RUN_TTL_SECONDS` | 604800 | age at which a run directory is pruned (7d) |
| `AGENT_MAX_ITERATIONS` | 12 | cap on the fallback agent's ReAct loop |
| `ETL_SOFT_TIME_LIMIT` | 900 | seconds before an extraction task is killed |

## Answer caching

`/query` caches answers in Redis, keyed on the normalized question plus each
collection's corpus fingerprint and the retrieval settings. Responses carry
`"cached": true|false`. Re-indexing a site changes its fingerprint, which makes
its cached answers unreachable without an explicit purge.

Only exact (normalized) question matching. Semantic near-match caching was
measured on the typer eval set and rejected: at similarity >= 0.88 every pair
that crossed the threshold had a *different* gold page, and at >= 0.92 nothing
crossed at all. See the docstring in `query_cache.py` for the table.

## Re-indexing

`POST /etl_workflow/` returns `{"status": "cached"}` and queues nothing when the
corpus registry already holds a current index for that URL. Pass
`{"force": true}` to re-index anyway. `GET /corpora` lists what is indexed and
why anything is considered stale.

## Evaluating retrieval changes

```bash
python evals/build_evalset.py tiangolo_dir --pages 30 --per-page 2
python evals/run_eval.py doc-typer-tiangolo-com --hybrid
```

Retrieval only, no LLM, so it's free and fast enough to run on every change.
Absolute numbers are optimistic (questions are generated from the pages they
label); use it to compare A against B.
