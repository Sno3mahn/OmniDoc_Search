"""HTTP fetching with retry, backoff and structured failure.

Pulled out of ETLWorkflow.save_files, where fetching was inlined inside a
thread pool inside a workflow step. Fetching is the part that actually fails
(9 of 29 pages on one docusaurus run) and it was the one part with no retry and
no reportable outcome - a failure became a printed line and a +1 on a counter.

A FetchResult is returned rather than raised, so a caller can report *why* each
page failed (status code, attempts, error class) instead of collapsing every
cause into "failed".
"""

import random
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .security import is_safe_url

DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 2
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0
# 429 is explicitly retryable and the main thing a docs site throws at a
# 10-thread pool. 5xx is transient by definition. Everything else in 4xx is a
# statement about the request, and retrying it is just noise.
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

_UA = "OmniDocSearch/1.0 (+https://github.com/Sno3mahn/OmniDoc_Search)"

_session = requests.Session()
_session.headers.update({"User-Agent": _UA})


@dataclass
class FetchResult:
    url: str
    ok: bool
    text: Optional[str] = None
    status: Optional[int] = None
    content_type: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0

    def describe(self) -> str:
        """One line fit for the status stream - the caller shouldn't have to
        reassemble this at every call site."""
        if self.ok:
            return f"{self.url} ok"
        if self.status is not None:
            return f"{self.url} HTTP {self.status} after {self.attempts} attempt(s)"
        return f"{self.url} {self.error} after {self.attempts} attempt(s)"


def _sleep_for(attempt: int, response: Optional[requests.Response]) -> float:
    """Honours Retry-After when the server sends one - guessing a backoff when
    the server has stated its own is how you get rate-limited twice."""
    if response is not None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), _BACKOFF_CAP)
            except ValueError:
                pass
    # Full jitter: a batch of 10 threads that all 429 together would otherwise
    # retry in lockstep and 429 together again.
    return random.uniform(0, min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP))


def fetch_page(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> FetchResult:
    """Fetches one URL. Never raises for an HTTP or network outcome."""
    if not is_safe_url(url):
        return FetchResult(url=url, ok=False, error="blocked: non-public address", attempts=0)

    last: Optional[FetchResult] = None
    for attempt in range(retries + 1):
        response = None
        try:
            response = _session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last = FetchResult(
                url=url, ok=False, error=type(exc).__name__, attempts=attempt + 1
            )
        else:
            if response.status_code == 200:
                return FetchResult(
                    url=url,
                    ok=True,
                    text=response.text,
                    status=200,
                    content_type=response.headers.get("content-type"),
                    attempts=attempt + 1,
                )
            last = FetchResult(
                url=url, ok=False, status=response.status_code, attempts=attempt + 1
            )
            if response.status_code not in _RETRY_STATUS:
                return last

        if attempt < retries:
            time.sleep(_sleep_for(attempt, response))

    return last or FetchResult(url=url, ok=False, error="unknown", attempts=retries + 1)


def fetch_first(*urls: str, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES):
    """Tries each URL in order, returning the first success. Used for the
    "raw markdown source, else the rendered page" pair: both are real
    candidates for the same page, and either one succeeding is a success.
    Returns (FetchResult, url_that_worked_or_None)."""
    attempted = []
    for url in urls:
        if not url:
            continue
        result = fetch_page(url, timeout=timeout, retries=retries)
        if result.ok:
            return result, url
        attempted.append(result)
    if not attempted:
        return FetchResult(url="", ok=False, error="no candidate URLs", attempts=0), None
    # Report the first candidate's failure - it's the one the caller asked for.
    return attempted[0], None
