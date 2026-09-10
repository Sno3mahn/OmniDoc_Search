import time
import uuid
from typing import Optional

import redis.asyncio as aioredis
from fastapi import HTTPException

# Same broker/backend Redis instance tasks.py already hardcodes for Celery -
# reusing it rather than standing up a second Redis config for one counter.
REDIS_URL = "redis://localhost:6379/0"

_redis: Optional["aioredis.Redis"] = None


def _get_redis() -> "aioredis.Redis":
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def enforce_rate_limit(identity: str, bucket: str, max_requests: int, window_seconds: int) -> None:
    """Sliding-window rate limit backed by a Redis sorted set: each call's
    timestamp is recorded under a per-(identity, bucket) key, entries older
    than the window are trimmed, and the remaining count is compared to the
    limit. Chosen over a fixed-window counter to avoid the double-burst-at-
    the-boundary problem, and over an in-process counter because state in
    Redis stays correct across multiple API replicas - an in-memory counter
    would silently stop enforcing anything the moment a second replica exists.

    Raises HTTPException(429) if the caller is over the limit; returns None
    (and records the call) otherwise.
    """
    r = _get_redis()
    key = f"ratelimit:{bucket}:{identity}"
    now = time.time()
    window_start = now - window_seconds
    member = f"{now}-{uuid.uuid4().hex}"

    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, window_seconds)
    _, _, count, _ = await pipe.execute()

    if count > max_requests:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {max_requests} requests per {window_seconds}s for {bucket!r}",
        )
