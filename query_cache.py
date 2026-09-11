"""Answer cache for /query.

WHY ONLY EXACT MATCH - this was measured, not assumed.

Profiling put a query at ~3.7s end to end, of which retrieval is 9ms. The cost
is the LLM synthesis, so the only caching worth doing is caching the answer.
Caching retrieval or query embeddings would save single-digit milliseconds.

The tempting version is a semantic cache: embed the question, reuse the answer
of any cached question above a similarity threshold. On the 52-question typer
eval set, every pair of questions was scored by cosine similarity and checked
against whether the two questions actually have the same gold page:

    threshold   pairs over it   same page   DIFFERENT page   false-hit rate
      0.85           35             9             26              74%
      0.88            8             0              8             100%
      0.90            7             0              7             100%
      0.92+           0             -              -               -

There is no usable threshold. Below 0.92 nearly every hit is wrong; at 0.92 and
above the cache never fires at all. The failures are not exotic:

    "How can I support the Typer project?"   (help-typer.md)
    "How can I contribute to Typer?"         (resources.md)      sim 0.901

Two questions a person would call identical, answered on different pages. Docs
QA is full of this - near-identical phrasings that differ by one identifier or
one negation, which is exactly where embeddings are weakest and exactly why
retrieval here is hybrid rather than dense-only. A semantic cache would serve a
confident, well-cited, wrong answer, which is worse than a slow one.

(Caveat on the measurement: the eval set is generated one-question-per-page, so
genuine duplicate intents are under-represented and the "same page" column is
pessimistic. That doesn't rescue the idea - the pairs that DO cross the
threshold are the dangerous ones, and those are what a cache fires on.)

So: exact match on a normalized question. Normalization absorbs the harmless
variation (case, whitespace, trailing punctuation) and nothing else.

INVALIDATION. The key includes each collection's corpus fingerprint, so
re-indexing a site makes its cached answers unreachable rather than stale - no
explicit purge needed. It also includes retrieval parameters, because an answer
produced at top_k=8 isn't the answer the system would give at top_k=20.
"""

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

import corpus

REDIS_URL = "redis://localhost:6379/0"
CACHE_TTL_SECONDS = int(os.getenv("QUERY_CACHE_TTL", str(24 * 3600)))
CACHE_ENABLED = os.getenv("QUERY_CACHE", "1") != "0"

# Bump when the prompt or the answer's shape changes - old entries were
# produced by a different system and must not be served as this one's output.
ANSWER_VERSION = 1

_redis: Optional["aioredis.Redis"] = None


def _get_redis() -> "aioredis.Redis":
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def normalize_question(question: str) -> str:
    """Collapses only the variation that cannot change the answer: case,
    whitespace runs, and trailing punctuation. Deliberately does NOT stem,
    drop stopwords or strip punctuation mid-string - "don't" and "do not"
    are the same question, but "typer.Argument" and "typer Argument" are one
    identifier and two words, and conflating them is how a cache starts
    lying."""
    text = question.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.rstrip("?!. ")


def cache_key(question: str, collections: List[str], urls: List[str], top_k: int) -> str:
    """Identity of (this question, against this exact corpus state, with these
    retrieval settings). Any change to any part produces a different key."""
    fingerprints = []
    for url in sorted(urls):
        rec = corpus.get(url)
        # No record means no known corpus state to pin the answer to. Use a
        # marker rather than skipping, so a site that later gains a record
        # doesn't collide with entries written before it had one.
        fingerprints.append(f"{url}:{rec.fingerprint if rec else 'unregistered'}")

    material = json.dumps({
        "v": ANSWER_VERSION,
        "q": normalize_question(question),
        "c": sorted(collections),
        "f": fingerprints,
        "k": top_k,
    }, sort_keys=True)
    return f"qcache:{hashlib.sha256(material.encode()).hexdigest()}"


async def get(key: str) -> Optional[Dict[str, Any]]:
    if not CACHE_ENABLED:
        return None
    try:
        raw = await _get_redis().get(key)
    except Exception:
        # A cache that is down must not take the query path with it.
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def put(key: str, payload: Dict[str, Any]) -> None:
    if not CACHE_ENABLED:
        return
    try:
        await _get_redis().set(key, json.dumps(payload), ex=CACHE_TTL_SECONDS)
    except Exception:
        pass


async def invalidate_collection(collection: str) -> int:
    """Fingerprints already make stale entries unreachable, so this exists for
    the explicit case - an operator who wants the keys actually gone. SCAN
    rather than KEYS: KEYS blocks Redis for the whole scan."""
    removed = 0
    try:
        r = _get_redis()
        async for key in r.scan_iter(match="qcache:*", count=500):
            raw = await r.get(key)
            if raw and f'"{collection}"' in raw:
                await r.delete(key)
                removed += 1
    except Exception:
        pass
    return removed
