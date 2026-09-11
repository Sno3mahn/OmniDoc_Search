"""Job state and event fan-out over Redis.

Replaces the module-level `active_wfs` dict in api.py, which held the workflow
handler, the event log and the set of attached WebSocket queues in the API
process's memory. That made three things true at once:

  - the ETL ran inside the web server's event loop, competing with request
    handling and holding a Playwright browser for the duration;
  - a second uvicorn worker was impossible, because a WebSocket could land on a
    replica that had never heard of the job_id;
  - a restart lost every in-flight run silently.

Here the worker publishes and the API subscribes, so neither has to be the
process that owns the run.

The replay problem: a client attaching mid-run needs everything already emitted
AND everything emitted from then on, with no gap and no duplicate. Every message
carries a monotonic `seq`; a reader subscribes FIRST, then snapshots the log,
then drops any live message whose seq it already replayed. Subscribing first
means the gap can only ever produce duplicates, and seq removes those - the
other order would silently drop events.
"""

import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import redis
import redis.asyncio as aioredis

REDIS_URL = "redis://localhost:6379/0"

# Long enough to survive a page refresh or a reconnect after a laptop sleeps,
# short enough that finished runs don't accumulate forever.
JOB_TTL_SECONDS = 6 * 3600

_sync_client: Optional[redis.Redis] = None
_async_client: Optional["aioredis.Redis"] = None


def sync_redis() -> redis.Redis:
    """For Celery workers, which are synchronous."""
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _sync_client


def async_redis() -> "aioredis.Redis":
    """For the FastAPI process."""
    global _async_client
    if _async_client is None:
        _async_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _async_client


def _meta_key(job_id: str) -> str:
    return f"job:{job_id}:meta"


def _log_key(job_id: str) -> str:
    return f"job:{job_id}:log"


def _channel(job_id: str) -> str:
    return f"job:{job_id}:events"


# --- writer side (Celery workers) ------------------------------------------

def create_job(job_id: str, owner: str, homepage_url: str, collection: str) -> None:
    r = sync_redis()
    pipe = r.pipeline()
    pipe.hset(
        _meta_key(job_id),
        mapping={
            "owner": owner,
            "homepage_url": homepage_url,
            "collection": collection,
            "status": "queued",
            "created_at": time.time(),
            "seq": 0,
        },
    )
    pipe.expire(_meta_key(job_id), JOB_TTL_SECONDS)
    pipe.execute()


def publish(job_id: str, message: Dict[str, Any]) -> None:
    """Appends to the replay log and fans out to live subscribers, atomically
    enough that the seq in the log always matches the seq on the wire."""
    r = sync_redis()
    seq = r.hincrby(_meta_key(job_id), "seq", 1)
    payload = dict(message, seq=seq)
    encoded = json.dumps(payload)
    pipe = r.pipeline()
    pipe.rpush(_log_key(job_id), encoded)
    pipe.expire(_log_key(job_id), JOB_TTL_SECONDS)
    pipe.expire(_meta_key(job_id), JOB_TTL_SECONDS)
    pipe.publish(_channel(job_id), encoded)
    pipe.execute()


def set_status(job_id: str, status: str) -> None:
    r = sync_redis()
    r.hset(_meta_key(job_id), "status", status)
    r.expire(_meta_key(job_id), JOB_TTL_SECONDS)


def finish_job(job_id: str, status: str) -> None:
    """Marks the run over and wakes every attached reader. The sentinel is a
    real message so a reader blocked on the channel doesn't sit until timeout."""
    set_status(job_id, status)
    publish(job_id, {"type": "__end__"})


# --- reader side (FastAPI) --------------------------------------------------

async def get_meta(job_id: str) -> Optional[Dict[str, str]]:
    meta = await async_redis().hgetall(_meta_key(job_id))
    return meta or None


async def replay_log(job_id: str) -> List[Dict[str, Any]]:
    raw = await async_redis().lrange(_log_key(job_id), 0, -1)
    return [json.loads(item) for item in raw]


async def stream(job_id: str) -> AsyncIterator[Dict[str, Any]]:
    """Yields every event for a job - the ones already emitted, then live ones -
    exactly once, in order. Ends when the job's sentinel arrives.

    Subscribing before snapshotting the log is deliberate: an event landing
    between the two calls arrives on both paths, and `seen` discards the copy.
    Snapshotting first would let that same event fall between them and vanish.
    """
    pubsub = async_redis().pubsub()
    await pubsub.subscribe(_channel(job_id))
    try:
        seen = 0
        for msg in await replay_log(job_id):
            seen = max(seen, msg.get("seq", 0))
            if msg.get("type") == "__end__":
                return
            yield msg

        meta = await get_meta(job_id)
        if meta and meta.get("status") in ("done", "failed"):
            return

        async for raw in pubsub.listen():
            if raw.get("type") != "message":
                continue
            msg = json.loads(raw["data"])
            if msg.get("seq", 0) <= seen:
                continue  # already replayed from the log
            seen = msg["seq"]
            if msg.get("type") == "__end__":
                return
            yield msg
    finally:
        await pubsub.unsubscribe(_channel(job_id))
        await pubsub.aclose()
