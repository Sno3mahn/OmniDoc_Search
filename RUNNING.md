# Running OmniDoc Search

## TL;DR

```bash
./omnidoc doctor    # check prerequisites, start nothing
./omnidoc up        # start the whole stack (~11s)
./omnidoc status    # what's running, and what's indexed
./omnidoc down      # stop everything the script started
```

Then open **http://localhost:5173** and paste the API key that `up` printed.

---

## The script

Five processes have to be up for this to work end to end, and the failure mode
when they aren't is nasty: **the API accepts jobs whether or not a worker
exists to run them**, so a half-started stack looks healthy and silently does
nothing. `./omnidoc up` starts all five and waits for each to actually report
ready.

| command | what it does |
|---|---|
| `./omnidoc up` | Starts redis, both Celery workers, the API and the frontend. Idempotent — re-running it only starts what's missing. |
| `./omnidoc down` | Stops what the script started. A redis it found already running is left alone. |
| `./omnidoc restart` | `down` then `up`. |
| `./omnidoc status` | Per-service state and PID, plus the indexed corpora and their staleness. |
| `./omnidoc logs` | Tails all logs. `./omnidoc logs etl` for one. |
| `./omnidoc doctor` | Checks venv, packages, binaries, keys and ports without starting anything. Run this first if `up` misbehaves. |

Exit codes propagate, so `./omnidoc up && ./omnidoc status` behaves.

### Things it handles so you don't have to

- **`PYTHONPATH`** — the ETL task imports `agentic_etl` by module name, and
  Celery doesn't reliably put the invocation directory on `sys.path` for that
  deferred import. Forgetting it gives `ModuleNotFoundError: agentic_etl` only
  *after* you submit a job.
- **`--pool=solo` on macOS** — the default prefork pool aborts with
  `WorkerLostError`/SIGABRT because the ML and Playwright stacks aren't
  fork-safe here. On Linux the script uses prefork with `--concurrency`.
- **Frontend port pinned to 5173** — `CORS_ORIGINS` defaults to 5173, so a
  vite that quietly falls back to 5174 gets its requests blocked by the browser
  with nothing in the server logs to find. `--strictPort` fails loudly instead.
- **Crash detection** — if a service dies during startup the script says so
  immediately and prints the tail of its log, rather than waiting out a
  three-minute timeout.
- **Not killing your redis** — if redis was already running when `up` started,
  `down` leaves it running.

### Configuration

`OMNIDOC_VENV`, `OMNIDOC_API_PORT`, `OMNIDOC_WEB_PORT`, `OMNIDOC_REDIS_PORT`,
`OMNIDOC_CONCURRENCY` (Linux only) override the defaults.

Logs and PIDs live in `.run/` (gitignored). Each start rotates the previous log
to `.log.prev`.

---

## Using the system

### 1. Index a documentation site

In the UI: paste a docs URL, paste the API key, hit **Run pipeline**. The
pipeline screen streams each stage live.

Or from the terminal:

```bash
KEY=$(grep '^API_KEY=' .env | cut -d= -f2-)
curl -s -X POST localhost:8000/etl_workflow/ \
  -H "x-api-key: $KEY" -H 'content-type: application/json' \
  -d '{"homepage_url":"https://typer.tiangolo.com/"}'
```

Give it the **docs root**, not a single page — `https://docusaurus.io/docs`,
not `https://docusaurus.io/docs/installation`. Page discovery is scoped to the
subtree of the URL you give it.

What happens, in order:

1. **scan** — page discovery tries `sitemap.xml`, then the homepage nav, and
   falls back to the extraction agent only if both come back empty. The status
   line names which one won and how many pages each found.
2. **detect** — looks for raw markdown sources (`.md` suffix, `index.md`,
   `?plain=1`, "Edit on GitHub" → raw.githubusercontent). Deterministic, no LLM.
3. **extract** — fetches every page in batches, with retry and backoff.
4. **cleanup** — strips lines that appear on most pages (nav chrome, footers).
5. **index** — chunks and embeds into a per-site Chroma collection.

Typical run: **10–30s** for a 70–90 page site. Most sites cost **zero LLM
calls** for extraction.

### 2. Ask questions

Once the run reaches `ready`, click **Query this index**. Answers cite the
chunks they came from, with the page and a relevance bar.

```bash
curl -s -X POST localhost:8000/query \
  -H "x-api-key: $KEY" -H 'content-type: application/json' \
  -d '{"homepage_urls":["https://typer.tiangolo.com/"],"query":"how do I add a CLI argument?"}'
```

### 3. Query across several sites at once

The interesting case, and the thing no vendor's built-in docs search can do:
each one only owns its own corpus.

On the start screen, enter a site that's already indexed and click
**+ Add source** instead of running the pipeline. Repeat, then **Query these
sources**. Each source gets its own retrieval quota so the more verbose corpus
can't crowd the other out, and the prompt requires per-project attribution and
forbids inventing an integration the docs don't describe.

Via the API, pass several URLs in `homepage_urls`.

### 4. Re-indexing

`POST /etl_workflow/` returns `{"status":"cached"}` and queues nothing when the
index is still current, so re-submitting a site is cheap and safe. It re-indexes
only when something actually drifted:

- **source drift** — the site's sitemap reports a newer `<lastmod>` than when
  we indexed (checked at most hourly, and only when the site publishes one)
- **extraction drift** — `PIPELINE_VERSION` changed, so our output would differ
  for the same input
- **embedding drift** — the embed model or chunk size changed, so the vectors
  are invalid even though nothing upstream moved

Force a rebuild with `{"force": true}`. `GET /corpora` lists everything indexed
and why anything is stale — `./omnidoc status` renders it.

### Endpoints

| method | path | purpose |
|---|---|---|
| `GET` | `/health_check` | liveness |
| `POST` | `/etl_workflow/` | start (or skip) an indexing run |
| `WS` | `/ws/stream/{job_id}` | live pipeline events; replays from the start on reconnect |
| `GET` | `/corpora` | what's indexed, and staleness |
| `GET` | `/query/status` | whether a site is queryable |
| `POST` | `/query` | ask a question across one or more sites |

All of them take `x-api-key`. The WebSocket takes it as the second
`Sec-WebSocket-Protocol` entry (`omnidoc.v1, <key>`) because browsers can't set
headers on a WebSocket, and a query string would land in access logs.

---

## Environment

| var | default | meaning |
|---|---|---|
| `API_DEEPSEEK` | — | DeepSeek key (required) |
| `API_KEY` | — | the key clients send as `x-api-key` (required) |
| `RAG_TOP_K` | 8 | chunks retrieved per query |
| `RAG_HYBRID` | 1 | fuse BM25 with dense retrieval |
| `RAG_CHUNK_SIZE` | 256 | chunk size in tokens (measured optimum; see `rag_qe.py`) |
| `RAG_CHUNK_OVERLAP` | 64 | overlap between chunks |
| `CORPUS_TTL_SECONDS` | 1209600 | age at which an index is assumed drifted (14d) |
| `CORPUS_RECHECK_AFTER` | 3600 | min record age before spending a request on a `<lastmod>` check |
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
`"cached": true|false`, and the UI shows a `cached` chip. Re-indexing changes
the fingerprint, which makes stale answers unreachable without a purge.

Exact (normalized) question matching only. Semantic near-match caching was
measured on the typer eval set and rejected: at similarity ≥ 0.88 **every** pair
that crossed the threshold had a *different* gold page, and at ≥ 0.92 nothing
crossed at all. The table is in `query_cache.py`.

## Evaluating retrieval changes

```bash
python evals/build_evalset.py <corpus_dir> --pages 40 --per-page 2
python evals/run_eval.py doc-typer-tiangolo-com --hybrid
```

Retrieval only, no LLM, so it's free and fast enough to run on every change.
Absolute numbers are optimistic (questions are generated from the pages they
label) — use it to compare A against B, not to claim an accuracy.

Two eval sets are checked in: `evals/typer.jsonl` (52 questions) and
`evals/docusaurus.jsonl` (80). Test changes against both — a chunking win on
one corpus is not yet a win.

## Manual startup

If you'd rather not use the script:

```bash
source /Users/ricky/Documents/workspace/agents_learn/agent_env/bin/activate
export PYTHONPATH=$PWD
redis-server &
celery -A tasks worker -Q etl    --pool=solo -n etl@%h    --loglevel=info &
celery -A tasks worker -Q ingest --pool=solo -n ingest@%h --loglevel=info &
uvicorn api:app --port 8000 &
cd frontend && npm run dev -- --port 5173 --strictPort
```

Two queues rather than one because the halves have opposite shapes: extraction
is network-bound and slow and holds a browser; ingestion is CPU/GPU-bound. One
pool means a burst of extractions starves embedding. `tasks.py` routes by task
name.
