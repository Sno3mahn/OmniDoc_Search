# OmniDoc Search

Point it at a documentation site. It figures out what pages exist, pulls them
down, cleans them up, indexes them, and answers questions about them with
citations — typically in **15–40 seconds** and, for most sites, **zero LLM
calls** during extraction.

The interesting part is that it can answer across **several projects at once**,
which no vendor's built-in docs search can do: each one only owns its own
corpus.

```bash
./omnidoc up          # start the stack (~11s)
open http://localhost:5173
```

---

## Demo video

> **Coming soon.** A walkthrough video will be linked here once recorded.

<!--
Replace this block when the video is ready:

[![OmniDoc Search demo](docs/video-thumbnail.png)](https://youtu.be/VIDEO_ID)

Two cuts are planned:
  - technical deep dive  — architecture, the event graph, retrieval evaluation
  - MVP pitch            — problem, what it does, why agentic, close

Scripts live in docs/video-scripts.md (Phase 5, not yet written).
-->

*Placeholder — nothing has been recorded yet.*

---

## What it does

```
docs site URL
     │
     ├─ discovery ──── sitemap.xml  →  homepage nav  →  extraction agent (LLM)
     │                 cheapest and most authoritative first; the agent only
     │                 runs if both deterministic paths come back empty
     │
     ├─ source detect  raw markdown behind the rendered page? (.md, ?plain=1,
     │                 "Edit on GitHub" → raw.githubusercontent). No LLM.
     │
     ├─ extract ────── parallel fetch with retry, backoff and Retry-After
     │
     ├─ cleanup ────── strip lines that appear on most pages (nav, footers)
     │
     └─ index ──────── heading split → size split → embed → Chroma
                       one collection per site
```

Every stage streams over a WebSocket, so the UI shows the pipeline actually
running rather than a spinner.

### Measured

| | |
|---|---|
| docusaurus.io/docs | **84 pages**, 0 fetch failures, 36s (raw markdown via GitHub) |
| docs.pytest.org | 57 pages via nav (its sitemap lists only version roots), 0 failures |
| typer.tiangolo.com | 72 pages, identical to what the LLM agent found |
| query, cold / cached | 17.3s / **2.6ms** |
| retrieval (docusaurus, 80 q) | recall@1 **62.5%**, recall@10 **95.0%**, MRR .735 |

Discovery used to be an 84-second ReAct loop. Replacing it with sitemap parsing
and nav parsing cut roughly 80 seconds off every run and removed two of the
three agents, with equal or better page coverage. Runtime now depends mostly on
how pages are fetched: rendered HTML is fast, while sites whose raw markdown
lives on GitHub cost one extra request per page.

---

## Stack

**Backend** — FastAPI, Celery + Redis (separate `etl` and `ingest` queues),
LlamaIndex Workflow for the event graph, ChromaDB, DeepSeek for synthesis,
`bge-small-en-v1.5` for embeddings, Playwright as a fallback renderer.

**Frontend** — React 19 + Vite + TypeScript, hand-authored CSS. No component
library.

**Retrieval** — hybrid: dense embeddings fused with BM25 by reciprocal rank.
Dense alone is weak on the exact identifiers docs are full of
(`typer.Argument`, `--install-completion`); BM25 matches those literally.

---

## Quick start

Prerequisites: Python venv with the deps, Node, Redis, a DeepSeek key.

```bash
cp .env.example .env      # then fill in API_DEEPSEEK and API_KEY
./omnidoc doctor          # verify everything before starting anything
./omnidoc up
```

Open http://localhost:5173, paste the API key that `up` printed, and give it a
docs root (`https://docusaurus.io/docs`, not a single page).

`./omnidoc status` shows what's running and what's indexed. `./omnidoc down`
stops it.

**[RUNNING.md](RUNNING.md) is the full guide** — commands, endpoints, env vars,
how re-indexing decides what's stale, and how to evaluate retrieval changes.

---

## Design notes

A few decisions that aren't obvious, each measured rather than assumed:

**Agents are a fallback, not the hot path.** The original design used three
LLM agents. Two were replaced by deterministic code with equal or better
results; the survivor runs only when sitemap and nav parsing both find nothing.
An agent belongs where input is genuinely unstructured — not on the happy path.

**Chunk size is 256 tokens, not the model's 512.** `bge-small` has a 512-token
window and truncates silently past it; heading-only splitting produced chunks
up to 50,257 characters, so a fifth of the corpus was embedded from its first
~2000 characters. Swept across two corpora, 256 won on recall@1 and MRR.

**No semantic query caching.** Measured on the eval set: at similarity ≥ 0.88,
*every* question pair that crossed the threshold had a different gold page; at
≥ 0.92 nothing crossed at all. "How can I support the project?" and "How can I
contribute?" score 0.901 and are answered on different pages. Answer caching is
exact-match only.

**No reranker.** Measured: +12.1s model load, +197ms/query, and tuning chunk
size got the same recall@1 improvement for free.

Retrieval changes are gated by an eval harness (`evals/`) that scores recall@k
and MRR against labelled question/page pairs. It uses no LLM, so it's free to
run on every change.

---

## Project status

Built as a staged exercise: comprehension → hardening → scale planning →
frontend → demo. Phases 1–4 are done; the video scripts (Phase 5) are not
written yet, which is why the section above is a placeholder.

Known limitations are recorded honestly rather than hidden:

- `sitemap.xml` `<lastmod>` drift detection works on roughly one site in three —
  many generators omit it. The TTL is the fallback.
- ChromaDB's local `PersistentClient` is single-writer on local disk; that's
  the first thing to break under real concurrency.
- The ETL and ingest workers share a filesystem, so they must run on the same
  host until extraction output moves to object storage.
- `./omnidoc` hardcodes a local venv path as its default (`OMNIDOC_VENV`
  overrides it).
