# Video scripts

Two cuts, both **live demo only** — screen recording of the product being used,
no slides, no diagrams, no code walkthrough. The technical deep dive lives in
the [README](../README.md); these scripts never cite an internal detail you
can't see happening on screen.

**Ground rules**

- Present tense, product voice. Describe what it *does*, never what it used to
  do or what changed.
- Don't call ingestion "AI-powered". Most sites index with zero LLM calls, and
  that's the better claim: it's why it's fast and cheap.
- Quote ranges, not best cases. A run is 15–40 seconds depending on the site.
- Everything said is visible on screen. If it isn't on screen, cut it.

---

# (a) Live demo — ~5 minutes

**Audience:** developers evaluating whether this is worth running.
**Promise:** see a docs site become a queryable index, end to end, unedited.

| # | beat | time | running |
|---|---|---|---|
| 1 | Hook | 0:25 | 0:25 |
| 2 | Start it | 0:30 | 0:55 |
| 3 | Index a site | 1:30 | 2:25 |
| 4 | Ask it something | 1:00 | 3:25 |
| 5 | Ask again — the cache | 0:20 | 3:45 |
| 6 | Two sources at once | 0:50 | 4:35 |
| 7 | Close | 0:25 | 5:00 |

---

### 1 — Hook (0:00–0:25)

> *Screen: six browser tabs, each a different docs site, each with its own
> search box. Click through two of them, get keyword matches.*

"Six tabs. Six search boxes. Every one of them searches only its own site, and
most of them match keywords rather than answering the question.

This is OmniDoc Search. You give it a documentation site, and it gives you
something you can actually ask."

---

### 2 — Start it (0:25–0:55)

> *Terminal. Type it live — it finishes in about eleven seconds.*

```
./omnidoc up
```

"One command brings up the whole stack — the API, two background workers, Redis
and the frontend. It waits for each one to actually report ready, so you're
never left with something half-started."

> *Let the five green `ok` lines land. Don't talk over them.*

> *Then:*

```
./omnidoc status
```

"And it'll tell you what's running and what's already indexed."

---

### 3 — Index a site (0:55–2:25)

> *Browser: `localhost:5173`. Pause a beat on the empty start screen.*

"Paste a documentation site and an API key."

> *Type a docs URL. Paste the key. Click **Run pipeline**.*

"One thing worth knowing: give it the docs root, not a single page. Everything
under that URL is what gets indexed."

> *Cut to the pipeline screen. Let it run. This is the centrepiece — do not
> speed it up, and leave gaps in the narration.*

"This is the live pipeline. Not a progress bar — these are real events streaming
over a WebSocket as the work happens."

> *Narrate lines as they land:*

- `Found 84 doc pages via sitemap.xml (sitemap 84, nav 29)`
  → "It found the page list three ways and tells you which one won. This site
  publishes a sitemap, so it just read the site's own list of pages — eighty-four
  of them. Parsing the sidebar would only have found twenty-nine, because the
  rest are rendered by JavaScript."

- `Markdown sources found via edit-link -> raw.githubusercontent`
  → "Now it's noticed these pages have raw markdown sitting behind them, on
  GitHub. So instead of scraping rendered HTML, it takes the clean original."

- `Saving batch 12 of 21`
  → "Fetching in parallel, with retries and backoff, so a slow or rate-limited
  site doesn't cost you pages."

- `Stripped 2075 boilerplate lines from 72 files`
  → "And stripping the navigation and footers that repeat on every page — found
  by frequency, so it works on any site without being told what to look for."

> *Land on the green result.*

"Eighty-four pages. No failures. Thirty-six seconds."

---

### 4 — Ask it something (2:25–3:25)

> *Click **Query this index**.*

"Now ask it something specific — the kind of thing a keyword search does badly."

> *Type a real question. Let the answer stream in.*

> *Scroll to the citations.*

"Every answer shows the chunks it came from — which page, and how strongly each
one matched. So you can check it, and you can go read the source.

And it's constrained to what's actually in the docs. If the documentation
doesn't cover something, it says so rather than inventing an API that sounds
right."

> *Optional, if you have a good example: ask something the docs don't cover and
> show it declining. This lands better than any claim about accuracy.*

---

### 5 — Ask again — the cache (3:25–3:45)

> *Ask the exact same question. It returns instantly.*

"Ask the same thing again and it's instant — that one came back in about two
milliseconds instead of seventeen seconds."

> *Point at the `cached` chip.*

"It marks cached answers, so you always know whether you're looking at a fresh
one. And re-indexing the site clears them automatically — you can't be served an
answer from documentation that's been replaced."

---

### 6 — Two sources at once (3:45–4:35)

> *Back to the start screen. Enter a second, already-indexed site. The
> **+ Add source** button appears.*

"Here's the part nothing else can do.

This site's already indexed, so instead of running the pipeline again, I add it
as a source."

> *Add it. The chips show both. Click **Query these sources**.*

"Now I've got two projects' documentation in one place — and I can ask a
question that crosses both."

> *Ask a genuine cross-project question. Let it answer.*

"No vendor can answer this, because no vendor owns both sets of docs.

And notice the citations are labelled by project."

> *Point at the per-source citation labels.*

"Each source gets its own share of the results, so the bigger, wordier
documentation can't crowd the other one out and quietly turn this back into a
single-source answer."

---

### 7 — Close (4:35–5:00)

> *Back to the query screen, cursor blinking in the prompt.*

"So: point it at a docs site, wait about half a minute, and ask.

Add a second site and ask across both.

Everything you just watched was live — the page discovery, the fetching, the
indexing, the answers and their sources. Nothing in that run was sped up or cut."

> *Hold on the prompt for a beat. End.*

---

# (b) Short pitch — ~90 seconds

**Audience:** non-technical or semi-technical. Same footage, tighter cut, no
terminal.

| # | beat | time | running |
|---|---|---|---|
| 1 | Hook | 0:12 | 0:12 |
| 2 | Problem | 0:18 | 0:30 |
| 3 | What it does | 0:27 | 0:57 |
| 4 | The part that's different | 0:20 | 1:17 |
| 5 | Close | 0:13 | 1:30 |

### 1 — Hook (0:00–0:12)

> *Six tabs, six docs sites.*

"If you build software, this is your afternoon. Six tabs, six search boxes, and
you're the one joining the answers together."

### 2 — Problem (0:12–0:30)

"Every documentation site has search, and every one of them searches only
itself. So the questions that actually block you — the ones that span two tools —
have nowhere to go.

You're not missing information. It's all published. It's just scattered, and
nothing reads across it for you."

### 3 — What it does (0:30–0:57)

> *Paste a URL. Hit run. Let the stages play — sped-up is acceptable here, this
> cut isn't making a claim about being live.*

"OmniDoc Search turns a documentation site into something you can ask questions.

Paste the link. It works out what pages exist, reads them, and indexes them —
about half a minute for a hundred-page site."

> *Type a question. Answer appears with citations.*

"Then just ask. And every answer shows its sources, so you can check it — it
won't tell you something the documentation doesn't say."

### 4 — The part that's different (0:57–1:17)

> *Add a second source. Ask a question spanning both.*

"And you're not limited to one project. Add a second — then ask a question that
crosses both.

No documentation site can answer that, because none of them own the other's
docs. That's the gap this fills."

### 5 — Close (1:17–1:30)

> *Query screen, cursor blinking.*

"Documentation isn't the problem. Reading six sites to answer one question is.

OmniDoc Search — point it at the docs, and ask."

---

## Production notes

**Record at 1280×720 or larger.** The pipeline screen is dense; the stage log
becomes unreadable below that.

**Don't speed up the pipeline footage in cut (a).** Its being real is the whole
point, and the close says so explicitly. If a run is too slow, pick a faster
site rather than time-lapsing it. Cut (b) makes no such claim, so speeding up is
fine there.

**Force a re-index, or you'll hit the cache on camera.** A site that's already
indexed returns instantly with "already indexed and current" and no pipeline
runs at all. Either use a site you've never indexed, or send `force: true`.
Check `./omnidoc status` before rolling.

**Seed the second corpus in advance** so beat 6 doesn't require sitting through
a second full run — and so the **+ Add source** button appears immediately.

**Pick the two sources so a real cross-project question exists.** Two web
frameworks is a weak pairing. A framework plus a tool people genuinely combine
with it gives you a question with a real answer, and the demo dies if the model
correctly says the integration isn't documented.

**Have the failure case ready but optional.** Asking something the docs don't
cover, and showing it decline, is the most persuasive twenty seconds available —
but only if it actually declines. Rehearse it; don't discover it live.

**Numbers spoken on camera** — re-check before recording, they drift:

| claim | where to verify |
|---|---|
| `./omnidoc up` ≈ 11s | `time ./omnidoc up` from a stopped stack |
| 84 pages / 29 nav | the `Found N doc pages` line in the run |
| 2075 boilerplate lines | the `Stripped N boilerplate lines` line |
| 36s for the run | the run clock on the pipeline screen |
| 17s cold / 2ms cached | time two identical queries |
