"""Deterministic table-of-contents extraction from a docs homepage.

The homepage_extraction_agent spends ~84s of a ~139s pipeline doing this with a
ReAct loop. But a docs sidebar is structured HTML: <nav>, role="navigation", or
a class containing "sidebar"/"toc"/"menu". Parsing it costs milliseconds.

The agent stays as a fallback for sites where this finds nothing (unusual
markup, or a nav that only exists after JS runs).
"""

import hashlib
import re
from typing import Dict, List, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

# Containers that hold a docs table of contents, most specific first.
_NAV_SELECTORS = [
    "nav",
    "[role=navigation]",
    "[class*=sidebar]",
    "[class*=Sidebar]",
    "[class*=toc]",
    "[class*=TOC]",
    "[class*=menu]",
    "aside",
]

# Paths that are part of a docs site's furniture, not its content.
_SKIP_PATH_RE = re.compile(
    r"/(blog|changelog|pricing|login|signin|sign-in|signup|register|careers"
    r"|privacy|terms|contact|sponsors?|team|about-us|search|tags?)(/|$)",
    re.I,
)
_SKIP_EXT_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|zip|pdf|xml|json)$", re.I)


def _is_doc_link(href: str, origin: str) -> bool:
    if not href or href.startswith(("mailto:", "tel:", "javascript:")):
        return False
    parsed = urlparse(href)
    if parsed.netloc and parsed.netloc != urlparse(origin).netloc:
        return False  # external
    if not parsed.path or parsed.path == "/":
        return False
    if _SKIP_PATH_RE.search(parsed.path) or _SKIP_EXT_RE.search(parsed.path):
        return False
    return True


def file_name_for(url: str) -> str:
    """Flat filename from a URL path, matching the convention the agent used
    (topic-subtopic-page.md) since everything lands in one directory."""
    path = urlparse(url).path.strip("/")
    if not path:
        return "index.md"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-").lower()
    slug = re.sub(r"-(html?|php|aspx)$", "", slug)
    return f"{slug or 'index'}.md"


def build_file_name_map(urls: List[str]) -> Dict[str, str]:
    """URL -> filename, guaranteed collision-free.

    file_name_for is per-URL and cannot see the rest of the site, so it maps
    /docs/a/b and /docs/a-b onto the same "docs-a-b.md" - every path separator
    and every punctuation run collapses to the same "-". Since the whole corpus
    lands in one flat directory, the second page silently overwrites the first,
    and because the completion check compares filename SETS the lost page isn't
    even reported missing.

    Disambiguating here rather than inside file_name_for keeps the readable
    name for the overwhelmingly common case and only suffixes actual clashes.
    The suffix is derived from the URL, not a counter, so a given page keeps
    the same filename across runs even if discovery order changes - otherwise
    the corpus fingerprint would move on every run and defeat the cache.
    """
    taken: Dict[str, str] = {}   # filename -> url that claimed it
    mapping: Dict[str, str] = {}
    for url in urls:
        name = file_name_for(url)
        if taken.get(name, url) != url:
            stem, dot, ext = name.rpartition(".")
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:6]
            name = f"{stem or name}-{digest}{dot}{ext}"
        taken[name] = url
        mapping[url] = name
    return mapping


def extract_toc(homepage_url: str, html: str, min_links: int = 3) -> Tuple[List[str], Dict[str, str]]:
    """Returns (doc_urls, file_name_map). Empty list means 'fall back to the agent'."""
    if not html:
        return [], {}

    soup = BeautifulSoup(html, "html.parser")
    seen: List[str] = []

    def collect(scope) -> List[str]:
        found = []
        for anchor in scope.find_all("a", href=True):
            absolute, _ = urldefrag(urljoin(homepage_url, anchor["href"]))
            if _is_doc_link(absolute, homepage_url) and absolute not in found:
                found.append(absolute)
        return found

    # Prefer links inside a real nav container; a whole-page scrape picks up
    # footers, banners and cross-links that aren't content pages.
    for selector in _NAV_SELECTORS:
        for scope in soup.select(selector):
            for url in collect(scope):
                if url not in seen:
                    seen.append(url)
        if len(seen) >= min_links:
            break

    if len(seen) < min_links:
        return [], {}

    return seen, build_file_name_map(seen)
