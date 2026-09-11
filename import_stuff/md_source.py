"""Deterministic discovery of raw-markdown sources for documentation pages.

Replaces the md_ify_agent ReAct loop, which was handed every URL in the site
and asked to browse each one - hundreds of LLM calls to answer what is really a
single question: "what rewrite turns a page URL into its markdown source?"

Strategies run cheapest-first and are proven on a small sample before being
applied to the whole site. Common case: zero LLM calls and a handful of HTTP
requests.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from .security import is_safe_url

_TIMEOUT = 10

_EDIT_LINK_RE = re.compile(
    r"edit\s*(this)?\s*page|edit\s+on\s+github|improve\s+this\s+doc"
    r"|suggest\s+edits?|view\s+source",
    re.I,
)
_HTML_START_RE = re.compile(r"^\s*(<!doctype|<html)", re.I)
_MD_MARKER_RE = re.compile(r"^\s{0,3}#{1,6}\s|```|^\s*[-*]\s+", re.M)
# Sites link to their source in several GitHub URL shapes: mkdocs-material and
# Docusaurus usually emit /edit/, others /blob/. Both map to the same raw URL.
_GITHUB_SRC_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/(?:blob|edit|raw)/(.+)")


def _fetch(url: str) -> Optional[requests.Response]:
    """Every probe goes through the same SSRF gate as the rest of the pipeline."""
    if not url or not is_safe_url(url):
        return None
    try:
        response = requests.get(url, timeout=_TIMEOUT)
    except requests.RequestException:
        return None
    return response if response.status_code == 200 else None


def looks_like_markdown(text: str, content_type: Optional[str]) -> bool:
    """A site that 404s to its SPA shell will happily return 200 HTML for any
    candidate URL, so a status check alone proves nothing."""
    if "html" in (content_type or "").lower():
        return False
    if _HTML_START_RE.search(text[:200]):
        return False
    return bool(_MD_MARKER_RE.search(text[:4000]))


# --- Pure URL transforms. Proving one on a sample generalises it to the site. ---

def _suffix_md(url: str) -> Optional[str]:
    parts = urlparse(url)
    path = parts.path.rstrip("/")
    if not path or path.endswith(".md"):
        return None
    return urlunparse(parts._replace(path=path + ".md"))


def _index_md(url: str) -> Optional[str]:
    parts = urlparse(url)
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return urlunparse(parts._replace(path=path + "index.md"))


def _github_plain(url: str) -> Optional[str]:
    if "github.com" not in urlparse(url).netloc or "/blob/" not in url:
        return None
    return url + ("&" if "?" in url else "?") + "plain=1"


TRANSFORMS: List[Tuple[str, Callable[[str], Optional[str]]]] = [
    ("suffix .md", _suffix_md),
    ("index.md", _index_md),
    ("github ?plain=1", _github_plain),
]


def _blob_to_raw(href: str) -> Optional[str]:
    match = _GITHUB_SRC_RE.match(href)
    if not match:
        return None
    org, repo, rest = match.groups()
    return f"https://raw.githubusercontent.com/{org}/{repo}/{rest}"


def edit_link_raw_url(page_url: str) -> Optional[str]:
    """Finds an 'Edit this page' style link and converts the GitHub blob URL it
    points at into a raw URL. This is exactly what the agent was prompted to do,
    done with an HTML parse instead - free, and more reliable."""
    response = _fetch(page_url)
    if response is None:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        label = " ".join(
            filter(
                None,
                [
                    anchor.get_text(" ", strip=True),
                    anchor.get("title", ""),
                    anchor.get("aria-label", ""),
                ],
            )
        )
        if not _EDIT_LINK_RE.search(label):
            continue
        raw = _blob_to_raw(urljoin(page_url, anchor["href"]))
        if raw:
            return raw
    return None


def _validated(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    response = _fetch(url)
    if response is None:
        return None
    return url if looks_like_markdown(response.text, response.headers.get("content-type")) else None


def resolve_markdown_sources(
    urls: List[str],
    sample_size: int = 3,
    max_workers: int = 8,
) -> Tuple[Dict[str, str], str]:
    """Maps each page URL to a raw markdown URL where one exists, else to itself.

    Returns (mapping, strategy_name). strategy_name is surfaced in the status
    stream so a run says *how* it found sources, not just that it did.
    """
    urls = list(urls)
    if not urls:
        return {}, "none"

    samples = urls[:sample_size]

    # 1. Pure transforms: if one holds across the samples, it holds site-wide,
    #    so the remaining URLs need no network calls at all.
    for name, transform in TRANSFORMS:
        candidates = [(u, transform(u)) for u in samples]
        checked = [(u, c) for u, c in candidates if c]
        if not checked:
            continue
        if all(_validated(c) for _, c in checked):
            return {u: (transform(u) or u) for u in urls}, name

    # 2. Per-page edit link. Validate on the samples before paying for N fetches.
    if any(_validated(edit_link_raw_url(u)) for u in samples):
        mapping: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(edit_link_raw_url, u): u for u in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    mapping[url] = future.result() or url
                except Exception:
                    mapping[url] = url
        return mapping, "edit-link -> raw.githubusercontent"

    return {u: u for u in urls}, "none"
