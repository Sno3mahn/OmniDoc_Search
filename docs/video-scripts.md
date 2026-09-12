# Video scripts

Two cuts, written against what the system actually does. Every number below was
measured in this repo and is cited to where it lives — if a figure changes,
re-measure before re-recording rather than rounding it in the edit.

**Ground rules for both scripts**

- Don't say "AI-powered" about the extraction path. Most sites now index with
  **zero LLM calls**, and that's the more interesting claim.
- Don't imply the agent is central. It's a fallback. Saying so is a strength.
- Quote the ranges, not the best case. A run is 15–40s depending on how pages
  are fetched, not "instant".

---

# (a) Technical deep dive — ~10 minutes

**Audience:** engineers who build RAG or ingestion pipelines.
**Promise:** a measured account of taking agents *out* of the hot path.

| # | beat | time | running |
|---|---|---|---|
| 1 | Hook | 0:40 | 0:40 |
| 2 | Problem | 1:00 | 1:40 |
| 3 | Architecture | 2:00 | 3:40 |
| 4 | Discovery: the 84-second loop | 1:30 | 5:10 |
| 5 | Retrieval: the invisible bug | 1:30 | 6:40 |
| 6 | Live demo | 2:20 | 9:00 |
| 7 | What I'd do differently | 0:50 | 9:50 |

### 1 — Hook (0:00–0:40)

> *On screen: a terminal, then the pipeline screen mid-run.*

"This started as an agentic pipeline with three LLM agents. It now has one, and
it almost never runs.
>
> Page discovery used to be an eighty-four second reasoning loop. It's now a
sitemap parse that takes under a second and finds *more* pages — eighty-four
against the agent's fifty on one site.
>
> This is the story of measuring an agentic system until most of the agents
turned out to be the expensive way to do something deterministic — and of the
two bugs that measurement exposed, both of which were silently corrupting
results."

### 2 — The problem (0:40–1:40)

> *On screen: a docs site with a search box that returns keyword matches.*

"Every documentation site has search. It searches *that* site, and it usually
matches keywords rather than answering questions.
>
> So the real question — 'I'm using this CLI framework with this test runner,
how do they fit together?' — has no home. Each vendor's search owns one corpus.
You end up with six tabs and you do the joining in your head.
>
> OmniDoc ingests any docs site into a queryable index, and can answer across
several at once. It's the cross-source case that's genuinely unavailable
elsewhere."

### 3 — Architecture (1:40–3:40)

> *On screen: the diagram from the README, built up one arrow at a time.*

"Four processes, and the interesting part is the seam between them.

- FastAPI is a **thin control plane**. It validates a URL, enqueues a job,
  returns an ID. It runs no pipeline work.
- A Celery worker on an `etl` queue runs the extraction workflow.
- A second worker on an `ingest` queue does embedding.
- Chroma holds one collection per site.

Two queues, not one, because the halves have opposite shapes: extraction is
network-bound and slow and holds a browser; embedding is CPU-bound. Share a
pool and a burst of extractions starves embedding.

The ETL used to run **inside** the API process, as an asyncio task, with job
state in a module-level dict. That made a second API replica impossible — a
WebSocket could land on a process that had never heard of the job. Now progress
goes over Redis pub/sub.

The replay problem is worth a beat: a client attaching mid-run needs everything
already emitted *and* everything after, with no gap and no duplicate. Every
message carries a sequence number; a reader **subscribes first**, then snapshots
the log, then drops anything it already replayed. Subscribing first means the
gap can only produce duplicates, and the sequence number removes those. The
other order silently drops events."

> *Pause on the code for `job_bus.stream()`.*

### 4 — Discovery: the eighty-four second loop (3:40–5:10)

> *On screen: split — old agent trace scrolling, new status line.*

"Original design: hand the homepage to a ReAct agent, ask it to find every doc
page. It worked. It took eighty-four seconds of a hundred-and-thirty-nine second
run, and it cost an LLM call per iteration.

But a docs sidebar is structured HTML. And better than that — most sites publish
`sitemap.xml`, because SEO requires it. That's the site *declaring its own page
list*. No inference at all.

So discovery is now three paths, cheapest first: **sitemap, then nav parse, then
the agent.**

| site | sitemap | nav | agent |
|---|---|---|---|
| docusaurus.io/docs | **84** | 29 | 50 |
| typer.tiangolo.com | 72 | 72 | 72 |
| docs.pytest.org | 0 | **57** | — |

Three different winners, which is why it's a chain and not a replacement. The
sitemap beats the agent on JS-rendered navs because it doesn't care how the page
renders. Pytest's sitemap lists only version roots, so nav parsing carries it.

The agent still exists. It runs when both deterministic paths find nothing. That
is where an agent belongs: unstructured input, not the happy path."

> *Beat.* "Removing the other two agents also removed the last
arbitrary-code-execution surface in the product — one of them generated Python
cleanup code and ran it."

### 5 — Retrieval: the invisible bug (5:10–6:40)

> *On screen: the chunk-size table from `rag_qe.py`.*

"Retrieval quality was the ceiling, and I assumed the fix was a reranker. I
measured it: twelve seconds of model load, two hundred milliseconds a query. I
didn't ship it.

Then I looked at the chunks. The markdown parser split on headings and nothing
else, so chunk size was whatever the page author happened to write. Median 584
characters — and one chunk of **fifty thousand**.

The embedding model has a five-hundred-and-twelve token window and **truncates
silently**. So a fifth of the corpus was embedded from its first two thousand
characters, while the retriever reported whole-chunk matches and the synthesiser
got the whole thing. Nothing errored. Nothing logged.

Bounding chunks to the model's actual window, then sweeping the size:

| chunk | recall@1 | MRR |
|---|---|---|
| heading-only | 53.8% | .671 |
| 512 | 61.2% | .719 |
| **256** | **62.5%** | **.735** |

That's on an eighty-question set built from a second corpus, specifically to
check the number wasn't tuned to the first one. It got the same recall@1 the
reranker did — at zero inference cost.

The lesson isn't 'use 256'. It's that the eval harness is free — no LLM, pure
retrieval — so it runs on every change, and it kept telling me my intuition was
wrong."

### 6 — Live demo (6:40–9:00)

> *Run `./omnidoc up` on camera. It takes about eleven seconds.*

"Five processes, one command."

> *Browser: `localhost:5173`. Paste a docs URL. Click **Run pipeline**.*

"The navbar is the route — source, pipeline, query — and it carries the live run
state, so the pipeline stays visible after you navigate away.

Watch the stages. This is the real WebSocket stream, not a progress animation."

> *Narrate the lines as they land — don't talk over the gaps, let it breathe:*

- `Found 84 doc pages via sitemap.xml (sitemap 84, nav 29)` — "it tells you
  which path won and what each found"
- `Markdown sources found via edit-link -> raw.githubusercontent` — "it noticed
  the rendered pages have raw markdown behind them, and took the clean source"
- `Saving batch 12 of 21`
- `Stripped 2075 boilerplate lines from 72 files` — "nav chrome that appears on
  every page, removed by frequency, no LLM"

> *Switch to query. Ask something specific.*

"Answers cite the chunks they came from — the page, and a relevance bar."

> *Ask the same question again.*

"Seventeen seconds cold. **Two and a half milliseconds** cached.

And that cache is exact-match only, deliberately. I tested semantic caching:
scored every question pair in the eval set by similarity and checked whether
they actually share an answer page. Above 0.88, *every single pair* that crossed
the threshold had a different gold page. Above 0.92, nothing crossed at all.

'How can I support this project?' and 'How can I contribute?' score 0.901. Two
different pages. A semantic cache would have served a confident, well-cited,
wrong answer — worse than a slow one."

> *Add a second source. Ask a cross-project question.*

"This is the case nothing else covers. Each source gets its own retrieval quota
so the more verbose corpus can't crowd the other out, and the prompt requires
per-project attribution and forbids inventing an integration the docs don't
describe."

### 7 — What I'd do differently (9:00–9:50)

"Three things I'd change.

**The corpus registry should have come first.** Once there's a record of what's
indexed and a content fingerprint, you get deduplication, cache invalidation and
drift detection from one table. I built it late and retrofitted everything to it.

**I'd have written the tests earlier.** I broke the same URL filter twice — once
rejecting every page of a site, once letting a thousand archived pages back in.
Neither raised an exception. Both silently changed how much of a site got
indexed. Twenty-eight assertions would have caught both in a second.

**And I'd have looked at the data sooner.** Two of the worst bugs were visible
by counting: a collection holding 4693 rows where the record said 1962, because
re-indexing appended instead of replacing. And every HTML page being fetched
three times, because the reader re-fetched a URL the pipeline already had —
roughly 250 requests for an 84-page site, which is almost certainly what got us
rate-limited into losing pages.

Neither was a hard bug. Both needed someone to check a number against the number
next to it."

---

# (b) MVP pitch — ~90 seconds

**Audience:** non-technical or semi-technical. No architecture, no metrics
tables.

| # | beat | time | running |
|---|---|---|---|
| 1 | Hook | 0:12 | 0:12 |
| 2 | Problem | 0:20 | 0:32 |
| 3 | What it does | 0:25 | 0:57 |
| 4 | Why it's different | 0:20 | 1:17 |
| 5 | Close | 0:15 | 1:32 |

### 1 — Hook (0:00–0:12)

> *Screen recording: six browser tabs, each a different docs site.*

"If you build software, this is your afternoon. Six tabs, six search boxes, and
you're the one joining the answers together."

### 2 — Problem (0:12–0:32)

"Every documentation site has search, and every one of them searches only
itself. So the questions that actually block you — the ones that span two tools —
have nowhere to go.

You're not missing information. It's all published. It's just scattered, and
nothing will read across it for you."

### 3 — What it does (0:32–0:57)

> *Paste a URL. Hit run. Let the stages play.*

"OmniDoc takes a documentation site and turns it into something you can ask
questions.

Paste the link. It works out what pages exist, reads them, and indexes them —
about half a minute for a hundred-page site.

Then you just ask."

> *Type a real question. Answer appears with citations.*

"And every answer shows its sources, so you can check it. It won't tell you
something the documentation doesn't say."

### 4 — Why it's different (0:57–1:17)

> *Add a second source. Ask a question that spans both.*

"Here's the part nothing else does. Add a second project — and ask a question
that crosses both.

No vendor can answer this, because no vendor owns both sets of docs. That's the
gap."

### 5 — Close (1:17–1:32)

> *Back to the query screen, cursor blinking.*

"Documentation isn't the problem. Reading six sites to answer one question is.

OmniDoc Search — point it at the docs, and ask."

---

## Production notes

**Record at 1280×720 or larger.** The pipeline screen is dense; the stage log
becomes unreadable below that.

**Don't speed up the pipeline footage.** The whole point is that it's visibly
real. If a run is too slow for the cut, pick a faster site — docs.pytest.org
indexes in well under a minute — rather than time-lapsing it.

**Use a site with a `lastmod`-free sitemap for the cold run** so you don't
accidentally hit the "already indexed and current" cached path on camera. Or
pass `force: true`. Check `./omnidoc status` before rolling.

**Seed one corpus in advance** so the multi-source demo doesn't require sitting
through a second full run.

**Numbers to re-verify before recording** (they will drift):

| claim | source of truth |
|---|---|
| 84 / 29 / 50 page counts | run the pipeline, read the `Found N doc pages` line |
| chunk-size table | comment block in `rag_qe.py` |
| semantic-cache thresholds | docstring in `query_cache.py` |
| 17.3s cold / 2.6ms cached | time two identical `/query` calls |
| `./omnidoc up` ≈ 11s | `time ./omnidoc up` from a stopped stack |
