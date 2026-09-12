# OmniDoc Search

Point it at a documentation site. It works out which pages exist, fetches them,
cleans them, indexes them, and answers questions about them with citations —
typically in **15–40 seconds**, and for most sites with **zero LLM calls** during
ingestion.

It can also answer across **several projects at once**, which no vendor's
built-in docs search can do: each one only owns its own corpus.

```bash
./omnidoc up          # start the stack (~11s)
open http://localhost:5173
```

---

## Demo video

> **Coming soon.** A walkthrough will be linked here once recorded.

<!--
Replace this block when the video is ready:

[![OmniDoc Search demo](docs/video-thumbnail.png)](https://youtu.be/VIDEO_ID)

Scripts: docs/video-scripts.md (live demo + short pitch).
-->

*Placeholder — nothing recorded yet. The demo is scripted beat-by-beat in
[docs/video-scripts.md](docs/video-scripts.md).*

---

## Contents

- [What it does](#what-it-does)
- [Running it](#running-it)
- [Using it](#using-it)
- [Architecture](#architecture)
- [Page discovery](#page-discovery)
- [Extraction](#extraction)
- [Retrieval](#retrieval)
- [Answer caching](#answer-caching)
- [The corpus registry](#the-corpus-registry)
- [Security](#security)
- [Performance](#performance)
- [Evaluating retrieval](#evaluating-retrieval)
- [Project layout](#project-layout)
- [Limitations](#limitations)

---

## What it does

```
docs site URL
     │
     ├─ discovery ──── sitemap.xml → homepage nav → extraction agent
     │                 Cheapest and most authoritative first. The agent runs
     │                 only when both deterministic paths find nothing.
     │
     ├─ source detect  Is there raw markdown behind the rendered page?
     │                 (.md suffix, index.md, ?plain=1, "Edit on GitHub" →
     │                 raw.githubusercontent). Proven on a 3-page sample,
     │                 then applied site-wide. No LLM.
     │
     ├─ extract ────── Parallel fetch, batched, with retry, jittered backoff
     │                 and Retry-After support.
     │
     ├─ cleanup ────── Remove lines appearing on most pages (nav chrome,
     │                 footers), detected by frequency.
     │
     └─ index ──────── Heading split → size split → embed → Chroma.
                       One collection per site.
```

Every stage emits status events over a WebSocket, so the UI shows the pipeline
actually running rather than a spinner.

---

## Running it

### Prerequisites

- Python environment with the dependencies (LlamaIndex, FastAPI, Celery,
  ChromaDB, Playwright, `llama-index-llms-deepseek`)
- Node 18+
- Redis
- A DeepSeek API key

### Setup

```bash
cp .env.example .env        # fill in API_DEEPSEEK and API_KEY
cd frontend && npm install && cd ..
./omnidoc doctor            # verify everything before starting anything
```

`doctor` checks the venv, every required package, the binaries, both keys and
all three ports, and reports what's wrong without starting a thing.

### Start and stop

```bash
./omnidoc up        # start all five processes (~11s)
./omnidoc status    # per-service state + what's indexed
./omnidoc logs      # tail all logs; ./omnidoc logs etl for one
./omnidoc down      # stop what it started
./omnidoc restart
```

Five processes have to be up for the system to work end to end: Redis, an `etl`
worker, an `ingest` worker, the API, and the frontend. The failure mode when
they aren't is quiet — **the API accepts jobs whether or not a worker exists to
run them** — so a half-started stack looks healthy and silently does nothing.
`up` starts all five and waits for each to report ready, aborting immediately
with a log tail if one dies.

It's idempotent, exits non-zero on failure, and leaves a Redis it didn't start
alone. Logs and PIDs live in `.run/`.

`OMNIDOC_VENV`, `OMNIDOC_API_PORT`, `OMNIDOC_WEB_PORT`, `OMNIDOC_REDIS_PORT`
and `OMNIDOC_CONCURRENCY` override the defaults.

**[RUNNING.md](RUNNING.md)** has the full environment-variable table, manual
startup, and operational detail.

---

## Using it

### Index a site

In the UI: paste a docs URL and the API key, click **Run pipeline**. Or:

```bash
KEY=$(grep '^API_KEY=' .env | cut -d= -f2-)
curl -s -X POST localhost:8000/etl_workflow/ \
  -H "x-api-key: $KEY" -H 'content-type: application/json' \
  -d '{"homepage_url":"https://typer.tiangolo.com/"}'
```

Give it the **docs root**, not a single page — `https://docusaurus.io/docs`, not
`https://docusaurus.io/docs/installation`. Discovery is scoped to the subtree of
the URL you provide.

### Ask questions

```bash
curl -s -X POST localhost:8000/query \
  -H "x-api-key: $KEY" -H 'content-type: application/json' \
  -d '{"homepage_urls":["https://typer.tiangolo.com/"],
       "query":"how do I add a CLI argument?"}'
```

Answers cite the chunks they came from, with the source page and a relevance
score.

### Query across several sites

The case nothing else covers. In the UI, enter an already-indexed site and click
**+ Add source** instead of running the pipeline; repeat, then **Query these
sources**. Via the API, pass several URLs in `homepage_urls`.

Each source gets its **own retriever with its own quota**, so a single global
top-k can't return every chunk from the more verbose corpus and silently turn a
cross-source question back into a single-source one. The synthesis prompt
requires per-project attribution and explicitly forbids inventing an API, import
path or integration that isn't in the retrieved context — when two projects
genuinely don't document how they fit together, an unconstrained model invents a
plausible bridge, which is worse than saying so because it reads authoritative
and cites real chunks.

### Endpoints

| method | path | purpose |
|---|---|---|
| `GET` | `/health_check` | liveness |
| `POST` | `/etl_workflow/` | start (or skip) an indexing run |
| `WS` | `/ws/stream/{job_id}` | live pipeline events; replays from the start on reconnect |
| `GET` | `/corpora` | what's indexed, and staleness |
| `GET` | `/query/status` | whether a site is queryable |
| `POST` | `/query` | ask across one or more sites |

All take `x-api-key`. The WebSocket takes it as the second
`Sec-WebSocket-Protocol` entry (`omnidoc.v1, <key>`), because browsers can't set
headers on a WebSocket and a query string would land in access logs.

---

## Architecture

```
browser ──HTTP──> FastAPI ──enqueue──> Redis ──> celery [etl queue]
   │                 │                              │
   │                 │                              ├─> ETLWorkflow
   │                 │                              │   (LlamaIndex Workflow)
   │                 │                              │
   │                 │                              └─> runs/{site}-{job_id}/*.md
   │                 │                                          │
   │              subscribe                                  enqueue
   │                 │                                          ▼
   └──WebSocket──────┴────────── Redis pub/sub <──── celery [ingest queue]
                                                          │
                                        ChromaDB <────────┘
                                     (one collection per site)

                    SQLite corpus registry — what's indexed, and whether it's stale
```

**FastAPI is a thin control plane.** It validates a URL, checks the corpus
registry, enqueues a job, returns an ID. It runs no pipeline work and holds no
job state, so it scales horizontally: a WebSocket can attach to any replica.

**Two Celery queues, not one.** Extraction is network-bound, slow, and holds a
Playwright browser; embedding is CPU/GPU-bound. Sharing a pool means a burst of
extractions starves embedding, and the two want very different concurrency.
`tasks.py` routes by task name so they scale independently.

**The workflow is an event graph.** `ETLWorkflow` is a LlamaIndex `Workflow`:
steps consume and emit typed events, fan-out is `ctx.send_event` and fan-in is
`ctx.collect_events`. Concurrency is layered and declarative — extraction splits
into batches, `@step(num_workers=6)` runs several batches at once, and each
batch uses a 10-thread pool.

**Progress streaming doubles as control flow.** `ctx.write_event_to_stream` is
the same mechanism that drives the workflow, so observability isn't bolted on —
it's the same wire. That's why the pipeline screen can be honest instead of a
spinner.

### Event replay

A client attaching mid-run needs everything already emitted *and* everything
after, with no gap and no duplicate. Every message carries a monotonic sequence
number. A reader **subscribes first**, then snapshots the log, then discards any
live message it already replayed.

Subscribing first means the window between the two can only produce duplicates,
and the sequence number removes those. The other order lets an event fall
between them and vanish. Job state has a 6-hour TTL, so a page refresh or a
laptop waking up replays the whole run rather than joining blind.

### Storage

Extraction output goes to `runs/{site}-{job_id}/`, scoped per run so two
concurrent runs of the same site can't overwrite each other. Directories are
pruned after 7 days.

---

## Page discovery

Three paths, tried cheapest and most authoritative first. Both deterministic
paths run (each costs well under a second) and the larger result wins.

| path | how it works | cost |
|---|---|---|
| **sitemap.xml** | The site declaring its own URL list. Found via `robots.txt` and conventional locations; follows sitemap indexes; handles gzip. | 1 request |
| **homepage nav** | Parses `<nav>`, `[role=navigation]`, `[class*=sidebar\|toc\|menu]`, `<aside>`. | 1 request |
| **extraction agent** | A ReAct agent with a Playwright renderer, capped at 12 iterations. | LLM loop |

Measured on real sites:

| site | sitemap | nav | agent |
|---|---|---|---|
| docusaurus.io/docs | **84** | 29 | 50 |
| typer.tiangolo.com | 72 | 72 | 72 |
| fastapi.tiangolo.com | 150 | **162** | — |
| docs.pytest.org | 0 | **57** | — |

Three different winners, which is why it's a chain rather than a replacement.
A sitemap doesn't care how a page renders, so it sees pages a JS-rendered
sidebar hides. But it's *complete*, not *curated* — it also lists blog posts,
archived versions and locales, so filtering does most of the work: same host,
inside the requested subtree, minus site furniture, archived versions and
locale segments. Sites like pytest publish a sitemap listing only version roots,
where nav parsing carries the run.

**The agent runs only when both deterministic paths find nothing** — unusual
markup, or a JS-only nav on a site with no sitemap. That's where an agent
belongs: genuinely unstructured input, not the happy path.

---

## Extraction

**Markdown source detection.** Many docs sites render from markdown that's still
reachable: a `.md` suffix, an `index.md`, `?plain=1` on GitHub, or an "Edit this
page" link pointing at a GitHub blob that maps to `raw.githubusercontent`. Each
rewrite rule is proven against a 3-page sample before being applied site-wide,
so a whole site costs a handful of probe requests. Where raw markdown exists
it's kept verbatim — running it through an HTML-to-text converter would escape
frontmatter and mangle code fences, degrading the clean source.

**Fetching** is a dedicated layer with retry, full-jitter exponential backoff,
and `Retry-After` support. It retries 429/408/425 and 5xx; other 4xx are
statements about the request, so retrying them is noise. Failures return a
structured result rather than raising, so a run can report *why* each page
failed — status code, attempts, error class — instead of collapsing every cause
into "failed". Each page is fetched exactly once; HTML is converted to text
in-process.

**Cleanup** removes lines appearing on more than 60% of pages — navigation
chrome, footers, banners. Frequency-based, no LLM, with guards so short lines,
headings and code fences are never stripped. On a 72-page corpus this removes
~2,000 lines of repeated furniture that would otherwise appear in the embedding
of nearly every chunk.

---

## Retrieval

**Hybrid by default.** Dense embeddings are fused with BM25 by reciprocal rank.
Dense retrieval is weak on rare exact tokens, and docs are full of them
(`typer.Argument`, `--install-completion`); BM25 matches those literally. Fusion
needs no LLM.

| config (typer, 52 questions) | recall@1 | recall@10 | MRR | missed |
|---|---|---|---|---|
| dense only | 46.2% | 78.8% | .568 | 11 |
| **dense + BM25** | 46.2% | **82.7%** | .572 | **9** |

**Chunking is bounded to the embedding model's window.** `bge-small-en-v1.5`
accepts 512 tokens and truncates silently past that. Splitting markdown on
headings alone leaves chunk size to whatever the page author wrote — on one
corpus that meant a median of 584 characters but 143 chunks over 2,000 and a
maximum of 50,257, so a fifth of the corpus would be embedded from its opening
fragment while the retriever reported whole-chunk matches. Headings split first
(so every chunk inherits a heading path), then a size split, then embedding.

Chunk size swept on two independent corpora:

| chunk | typer r@1 | typer MRR | docusaurus r@1 | docusaurus r@10 | docusaurus MRR |
|---|---|---|---|---|---|
| heading-only | 46.2% | .572 | 53.8% | 92.5% | .671 |
| 512 | 48.1% | .585 | 61.2% | 95.0% | .719 |
| 384 | 50.0% | .604 | — | — | — |
| **256** | **53.8%** | **.627** | **62.5%** | **95.0%** | **.735** |
| 192 | 44.2% | .587 | — | — | — |
| 128 | 42.3% | .548 | — | — | — |

The curve has a peak rather than a direction: too large truncates, too small
strips the context that makes a chunk matchable. 256 wins on both corpora, with
64 tokens of overlap so an answer straddling a boundary stays reachable.

**No reranker.** Measured at +12.1s model load and +197ms per query for a
recall@1 that chunk sizing matched at zero inference cost.

**Metadata is curated.** Each chunk embeds its page identity and heading path,
and excludes the local file path — which would otherwise put an identical
60-character prefix into every embedding in the corpus, blunting discrimination
and leaking the directory layout into the vector store.

---

## Answer caching

A query is ~3.7s of which **retrieval is 9ms** — the cost is LLM synthesis, so
the answer is the only thing worth caching.

Cache keys combine the normalized question, each collection's corpus
fingerprint, and the retrieval parameters. Re-indexing a site changes its
fingerprint, which makes its cached answers unreachable without an explicit
purge. Responses carry `"cached": true|false` and the UI shows it.

| | |
|---|---|
| cold query | 17.3s |
| cached | **2.6ms** |

**Matching is exact (after normalization), not semantic** — a measured decision,
not a conservative default. Scoring every question pair in the eval set by
cosine similarity and checking whether the pair actually shares an answer page:

| threshold | pairs over it | same page | different page | false-hit rate |
|---|---|---|---|---|
| 0.85 | 35 | 9 | 26 | 74% |
| 0.88 | 8 | 0 | **8** | **100%** |
| 0.90 | 7 | 0 | **7** | **100%** |
| 0.92+ | 0 | — | — | — |

There is no usable threshold: below 0.92 nearly every hit is wrong, and at 0.92
and above the cache never fires. The failures aren't exotic — *"How can I support
the project?"* and *"How can I contribute?"* score 0.901 and are answered on
different pages. Docs QA is full of near-identical phrasings separated by one
identifier or one negation, exactly where embeddings are weakest. A semantic
cache would serve a confident, well-cited, wrong answer.

Normalization therefore collapses only what cannot change an answer: case,
whitespace runs, trailing punctuation. `typer.Argument` and `typer Argument`
stay distinct.

---

## The corpus registry

A SQLite table recording what's indexed, from where, and whether it's still
good. `POST /etl_workflow/` returns `{"status":"cached"}` and queues nothing
when the index is current, so re-submitting a site is cheap and safe.

It distinguishes **three separate drifts**, because conflating them is how stale
indexes happen:

| drift | cause | detected by |
|---|---|---|
| **source** | upstream docs changed | content fingerprint; sitemap `<lastmod>` as a cheap proxy |
| **extraction** | our pipeline changed, so output differs for the same input | `PIPELINE_VERSION` |
| **embedding** | embed model or chunk size changed; corpus untouched, vectors invalid | `embed_model` + `chunk_size` on the record |

`staleness()` returns *why* an index can't be reused rather than a boolean, so
the response can differ by cause — an embedding mismatch needs a re-embed of the
same files, source drift needs a full re-extract.

The `<lastmod>` check costs one request and is gated behind a 1-hour recheck
interval so it never lands on the fast cache-hit path. Only a *newer* value
counts, so a site that stamps every page at build time can't make a good index
look stale. It's advisory: roughly one site in three publishes usable
`<lastmod>`, and the TTL is the fallback.

`GET /corpora` lists everything with its staleness reason; `./omnidoc status`
renders it.

---

## Security

- **SSRF guard** on every fetch. User-supplied URLs are resolved and rejected if
  they land on private, loopback, link-local, reserved or multicast addresses,
  or use a non-HTTP scheme.
- **Constant-time API key comparison** (`hmac.compare_digest`) — a plain `!=`
  short-circuits on the first differing byte, which is a timing side-channel on
  a secret.
- **Key fingerprints downstream.** Everything past the auth boundary — rate-limit
  buckets, job ownership — uses a SHA-256 fingerprint, so the raw key never
  lands in a dict, log or response.
- **WebSocket auth via subprotocol**, not a query string, so keys stay out of
  access logs.
- **Rate limiting** on a Redis sorted set (sliding window, correct across
  replicas): 5 indexing requests/min, 30 queries/min per key.
- **Job ownership** is checked on WebSocket attach, and a missing job and
  someone else's job return the same error so job IDs can't be enumerated.
- **No arbitrary code execution.** Cleanup is frequency analysis rather than
  generated-and-executed Python.

---

## Performance

Measured on an M3 Pro (36 GB), embeddings on MPS.

| | |
|---|---|
| stack cold start (`./omnidoc up`) | ~11s |
| docusaurus.io/docs — 84 pages, raw markdown via GitHub | 36s |
| docs.pytest.org — 57 pages, rendered HTML | <30s |
| retrieval | 9ms |
| query, cold / cached | 17.3s / 2.6ms |
| embedding throughput | ~60 chunks/s |

Runtime is dominated by fetching, so it depends on the source path: rendered
HTML is one request per page, while sites whose raw markdown lives on GitHub
cost an extra request each. Embedding throughput is flat from batch size 8 to 64
(60.7 / 60.5 / 55.2 / 55.5 chunks/s) — `bge-small` is small enough on MPS that
per-batch overhead dominates, so there's nothing to amortize.

---

## Evaluating retrieval

```bash
python evals/build_evalset.py <corpus_dir> --pages 40 --per-page 2
python evals/run_eval.py doc-typer-tiangolo-com --hybrid
```

`build_evalset.py` asks an LLM to write questions answerable only from a given
page, making that page the gold retrieval target — a few hundred labelled pairs
for one cheap call per page.

`run_eval.py` reports recall@1/3/5/10 and MRR. It retrieves once at the largest
k and slices, so one pass reports every k — which turns "what should top-k be?"
into a lookup rather than a guess. **No LLM and no generation**, so it's free and
fast enough to run on every change.

Two eval sets are checked in: `evals/typer.jsonl` (52 questions) and
`evals/docusaurus.jsonl` (80). Test against both — a chunking win on one corpus
is not yet a win.

Known bias: generated questions inherit the source page's wording, which
inflates scores through lexical overlap. Absolute numbers are optimistic. Use
the harness to compare A against B, not to claim an accuracy.

```bash
python -m pytest tests/ -q      # 28 unit tests, no network
```

---

## Project layout

```
api.py                  FastAPI control plane
tasks.py                Celery tasks — ETL and ingestion, on separate queues
run_workflow.py         ETLWorkflow: the event graph, plus a CLI entrypoint
agentic_etl.py          The fallback extraction agent and workflow event types
rag_qe.py               QueryEngine — ingestion pipeline, retrievers, prompts
job_bus.py              Job state and event fan-out over Redis
corpus.py               Corpus registry and drift detection
query_cache.py          Answer cache
rate_limit.py           Sliding-window rate limiter

import_stuff/
  sitemap.py            sitemap.xml discovery and <lastmod>
  toc.py                Homepage nav parsing, filename assignment
  md_source.py          Raw-markdown source detection
  fetch.py              HTTP with retry, backoff, structured failure
  boilerplate.py        Frequency-based cleanup
  security.py           SSRF guard
  tools.py              Agent tools and HTML→text

frontend/src/
  screens/              Start, Pipeline, Query
  components/Navbar     Route stepper + live run state
  lib/stages.ts         Maps status strings to pipeline stages
  api/                  Typed client, live and mock

evals/                  Retrieval eval harness and labelled sets
tests/                  Unit tests
omnidoc                 Start/stop/status script
```

---

## Limitations

- **ChromaDB's local `PersistentClient` is single-writer on local disk.** It's
  the first thing to break under real concurrency; a hosted vector store is the
  replacement.
- **The ETL and ingest workers share a filesystem**, so they must run on the
  same host until extraction output moves to object storage.
- **`<lastmod>` drift detection covers roughly one site in three.** Many
  generators omit it, and some stamp every page at build time. The TTL is the
  fallback.
- **Eval sets are small** (52 and 80 questions) and generated from the pages
  they label, so differences of a few points are within noise.
- **`./omnidoc` hardcodes a local venv path** as its default (`OMNIDOC_VENV`
  overrides it).
- **The extraction agent has no test coverage**, since exercising it requires a
  site where both deterministic paths fail.
