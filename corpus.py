"""The corpus registry: what has been indexed, from where, and whether it's stale.

Before this, "the corpus" was a directory on disk that the next run deleted.
Nothing recorded that typer.tiangolo.com had ever been indexed, so every request
re-ran the whole pipeline, and nothing could answer "is this index still good?".

DATA DRIFT - three separate drifts, which is why one fingerprint isn't enough:

  1. source drift      upstream docs changed. Detected by `fingerprint`, a hash
                       over the per-page content hashes of the extracted corpus.
                       Only knowable by re-fetching, so `indexed_at` + a TTL is
                       the cheap proxy for "worth re-checking".
  2. extraction drift  upstream is unchanged but OUR output changed, because the
                       pipeline changed (new boilerplate stripper, a different
                       markdown-source strategy, a new discovery path). Invisible
                       to any upstream check - hence PIPELINE_VERSION.
  3. embedding drift   corpus unchanged, vectors invalid, because the embed model
                       or the chunking changed. Nothing about the source moved at
                       all - hence embed_model + chunk_size on the record.

is_stale() checks all three. SQLite rather than Redis because this is the one
piece of state that must outlive a cache flush: losing it doesn't just cost a
lookup, it silently reverts the system to re-indexing everything.
"""

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse

# Bump when a pipeline change alters extracted output for the same input.
# Anything indexed under an older version is stale by definition.
PIPELINE_VERSION = 4

DEFAULT_TTL_SECONDS = int(os.getenv("CORPUS_TTL_SECONDS", str(14 * 24 * 3600)))
# How old a record must be before it's worth spending an HTTP request to ask
# the site whether it has changed. Walking a sitemap index for <lastmod> took
# 5.4s on docusaurus.io, and that cost would otherwise land on the cache-HIT
# path - the one that exists to be fast. Docs don't change by the minute.
RECHECK_AFTER_SECONDS = int(os.getenv("CORPUS_RECHECK_AFTER", "3600"))
CORPUS_DB_PATH = os.getenv("CORPUS_DB_PATH", "./omnidoc_corpus.sqlite3")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus (
    url             TEXT PRIMARY KEY,
    collection      TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    page_count      INTEGER NOT NULL,
    node_count      INTEGER NOT NULL DEFAULT 0,
    indexed_at      REAL NOT NULL,
    job_id          TEXT,
    status          TEXT NOT NULL,
    discovery       TEXT,
    pipeline_version INTEGER NOT NULL,
    source_lastmod  TEXT,
    embed_model     TEXT NOT NULL,
    chunk_size      INTEGER NOT NULL,
    detail          TEXT
);
CREATE INDEX IF NOT EXISTS corpus_collection_idx ON corpus(collection);
"""


@dataclass
class CorpusRecord:
    url: str
    collection: str
    fingerprint: str
    page_count: int
    node_count: int
    indexed_at: float
    job_id: Optional[str]
    status: str
    discovery: Optional[str]
    pipeline_version: int
    embed_model: str
    chunk_size: int
    source_lastmod: Optional[str] = None
    detail: Optional[str] = None

    @property
    def age_seconds(self) -> float:
        return time.time() - self.indexed_at


def normalize_url(homepage_url: str) -> str:
    """The dedup key. Two people typing the same docs site in slightly
    different ways must collapse to one record, or the cache never hits.
    Strips scheme differences, www, default ports, trailing slash, query and
    fragment - none of which change which docs site is meant."""
    raw = homepage_url.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    host = re.sub(r"^www\.", "", host)
    host = re.sub(r":(80|443)$", "", host)
    path = re.sub(r"/+$", "", parsed.path or "")
    return urlunparse(("https", host, path, "", "", ""))


def fingerprint_dir(input_dir: str) -> str:
    """Content hash of an extracted corpus: per-file SHA-256, sorted by name,
    hashed again. Order-independent and rename-sensitive, so a page appearing,
    vanishing or changing all move the fingerprint."""
    if not os.path.isdir(input_dir):
        return ""
    digest = hashlib.sha256()
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            digest.update(name.encode("utf-8"))
            digest.update(hashlib.sha256(fh.read()).digest())
    return digest.hexdigest()


def _connect(db_path: str = CORPUS_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    # Several processes touch this (API reads, ETL worker writes, ingest worker
    # writes); WAL lets readers proceed during a write instead of blocking.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


# Columns added after the table first shipped. CREATE TABLE IF NOT EXISTS is a
# no-op on an existing table, so a new column has to be ALTERed in or every
# read of an old database fails on the missing field.
_ADDED_COLUMNS = {
    "source_lastmod": "TEXT",
}


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(corpus)")}
    for column, decl in _ADDED_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE corpus ADD COLUMN {column} {decl}")


def record(
    url: str,
    collection: str,
    fingerprint: str,
    page_count: int,
    status: str,
    embed_model: str,
    chunk_size: int,
    node_count: int = 0,
    job_id: Optional[str] = None,
    discovery: Optional[str] = None,
    source_lastmod: Optional[str] = None,
    detail: Optional[Dict] = None,
    db_path: str = CORPUS_DB_PATH,
) -> CorpusRecord:
    """Upserts the record for a site. Keyed on the normalized URL, so a
    re-index replaces rather than accumulates."""
    key = normalize_url(url)
    row = CorpusRecord(
        url=key,
        collection=collection,
        fingerprint=fingerprint,
        page_count=page_count,
        node_count=node_count,
        indexed_at=time.time(),
        job_id=job_id,
        status=status,
        discovery=discovery,
        pipeline_version=PIPELINE_VERSION,
        embed_model=embed_model,
        chunk_size=chunk_size,
        source_lastmod=source_lastmod,
        detail=json.dumps(detail) if detail else None,
    )
    fields = asdict(row)
    columns = ", ".join(fields)
    placeholders = ", ".join(f":{k}" for k in fields)
    with _connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO corpus ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(url) DO UPDATE SET "
            + ", ".join(f"{k}=excluded.{k}" for k in fields if k != "url"),
            fields,
        )
    return row


def get(url: str, db_path: str = CORPUS_DB_PATH) -> Optional[CorpusRecord]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM corpus WHERE url = ?", (normalize_url(url),)
        ).fetchone()
    return CorpusRecord(**dict(row)) if row else None


def list_all(db_path: str = CORPUS_DB_PATH) -> List[CorpusRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM corpus ORDER BY indexed_at DESC").fetchall()
    return [CorpusRecord(**dict(r)) for r in rows]


def delete(url: str, db_path: str = CORPUS_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM corpus WHERE url = ?", (normalize_url(url),))


def staleness(
    rec: Optional[CorpusRecord],
    embed_model: str,
    chunk_size: int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    observed_lastmod: Optional[str] = None,
) -> Optional[str]:
    """Returns None if the index can be reused, else the reason it can't.

    A reason string rather than a bool, because the three drifts want different
    responses: an embedding mismatch needs a re-embed of the SAME files, while
    source drift needs a full re-extract. Returning *why* keeps that choice
    available to the caller instead of collapsing it here.
    """
    if rec is None:
        return "never indexed"
    if rec.status not in ("indexed", "partial"):
        return f"last run ended as {rec.status!r}"
    if rec.pipeline_version != PIPELINE_VERSION:
        return f"extraction drift: indexed by pipeline v{rec.pipeline_version}, now v{PIPELINE_VERSION}"
    if rec.embed_model != embed_model:
        return f"embedding drift: indexed with {rec.embed_model}, now {embed_model}"
    if rec.chunk_size != chunk_size:
        return f"embedding drift: chunk_size {rec.chunk_size} -> {chunk_size}"
    # Source drift, stated by the site rather than guessed from a clock. ISO
    # dates compare lexicographically. Only a *newer* value counts: a site that
    # stamps every build, or one that dropped lastmod entirely, must not make a
    # good index look stale.
    if observed_lastmod and rec.source_lastmod and observed_lastmod > rec.source_lastmod:
        return f"source drift: site reports lastmod {observed_lastmod} > indexed {rec.source_lastmod}"
    if rec.age_seconds > ttl_seconds:
        return f"possible source drift: indexed {rec.age_seconds / 86400:.1f} days ago"
    return None
