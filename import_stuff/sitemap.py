"""Page discovery via sitemap.xml - the cheapest and most reliable of the three
paths, and the one that should be tried first.

Nav parsing (toc.py) infers the page list from presentation, so it breaks on a
JS-rendered sidebar. The extraction agent reads the same rendered page and costs
an LLM loop. A sitemap is the site declaring its own URL list, no inference at
all, and every mainstream docs generator emits one because SEO requires it.

The tradeoff is that a sitemap is *complete*, not *curated*: it also lists blog
posts, archived versions (/docs/1.x/), locales (/fr/docs/) and marketing pages.
So the filtering below does most of the work - discovery itself is one HTTP GET.
"""

import gzip
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .security import is_safe_url
from .toc import _SKIP_EXT_RE, _SKIP_PATH_RE, file_name_for

_TIMEOUT = 10
# A sitemap index can fan out to dozens of children; docs sites rarely need
# more than a couple, and each one is a network round trip.
_MAX_SITEMAPS = 8
_MAX_URLS = 2000

# Archived doc versions duplicate the current ones and would triple the corpus
# with near-identical text, which is actively harmful for retrieval.
_VERSIONED_PATH_RE = re.compile(r"/(v?\d+(\.\d+)*(\.x)?|next|latest|legacy|archive)(/|$)", re.I)
# Locale segments: /fr/, /zh-cn/, /pt-BR/ ... but not /js/ or /ui/, so this only
# matches the shapes that are unambiguously language tags.
_LOCALE_PATH_RE = re.compile(r"/([a-z]{2}([-_][A-Za-z]{2})?)(/|$)")
_LOCALE_ALLOW = {"docs", "api", "dev", "js", "go", "ui", "db", "ml", "io", "cn"}


def _get(url: str) -> Optional[requests.Response]:
    if not url or not is_safe_url(url):
        return None
    try:
        response = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "OmniDocSearch/1.0"})
    except requests.RequestException:
        return None
    return response if response.status_code == 200 else None


def _body(response: requests.Response) -> str:
    """Sitemaps are commonly served gzipped as .xml.gz. requests only
    transparently decompresses Content-Encoding, not a gzipped *body*."""
    raw = response.content
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return raw.decode("utf-8", errors="replace")


def discover_sitemaps(homepage_url: str) -> List[str]:
    """robots.txt is the declared location; /sitemap.xml is the convention.
    Both are checked because plenty of sites have one and not the other."""
    parsed = urlparse(homepage_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    found: List[str] = []

    robots = _get(urljoin(root, "/robots.txt"))
    if robots is not None:
        for line in robots.text.splitlines():
            if line.lower().startswith("sitemap:"):
                url = line.split(":", 1)[1].strip()
                if url and url not in found:
                    found.append(url)

    for guess in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-0.xml"):
        url = urljoin(root, guess)
        if url not in found:
            found.append(url)
    return found


def _parse_sitemap(xml: str) -> Tuple[List[str], List[str]]:
    """Returns (page_urls, child_sitemap_urls). A <sitemapindex> points at other
    sitemaps rather than pages, and large sites always use one."""
    soup = BeautifulSoup(xml, "xml")
    children = [loc.get_text(strip=True) for loc in soup.select("sitemapindex > sitemap > loc")]
    pages = [loc.get_text(strip=True) for loc in soup.select("urlset > url > loc")]
    if not children and not pages:
        # Some sites serve a sitemap without the expected namespace, which
        # makes the structural selectors above miss; fall back to every <loc>.
        pages = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
    return pages, children


def _is_doc_url(url: str, homepage_url: str, base_path: str) -> bool:
    parsed = urlparse(url)
    home = urlparse(homepage_url)
    if parsed.netloc != home.netloc:
        return False
    path = parsed.path
    if not path or path == "/":
        return False
    # Confine to the docs subtree. Without this, a sitemap for a product site
    # drags in the whole marketing site alongside the documentation.
    if base_path and not path.startswith(base_path):
        return False
    if _SKIP_PATH_RE.search(path) or _SKIP_EXT_RE.search(path):
        return False
    if _VERSIONED_PATH_RE.search(path):
        return False
    for segment in path.strip("/").split("/"):
        match = _LOCALE_PATH_RE.fullmatch(f"/{segment}/")
        if match and segment.lower() not in _LOCALE_ALLOW:
            return False
    return True


def _base_path(homepage_url: str) -> str:
    """The docs root as a path prefix. https://x.io/docs/ -> /docs/ ;
    a bare https://typer.tiangolo.com/ -> '' (the whole site is the docs)."""
    path = urlparse(homepage_url).path
    if not path or path == "/":
        return ""
    if not path.endswith("/"):
        last = path.rsplit("/", 1)[-1]
        # "/docs" is a section root; "/docs/intro.html" is a page inside one.
        # Only an extension distinguishes them, and guessing wrong on the first
        # case collapses the prefix to "/" and lets the whole site through.
        path = (path.rsplit("/", 1)[0] if "." in last else path) + "/"
    return path


def sitemap_urls(
    homepage_url: str,
    max_urls: int = _MAX_URLS,
    min_links: int = 3,
) -> Tuple[List[str], Dict[str, str]]:
    """Returns (doc_urls, file_name_map), or ([], {}) to mean 'try the next
    discovery path'. Same contract as toc.extract_toc, so the workflow can try
    them in order without special-casing either."""
    base = _base_path(homepage_url)
    pages: List[str] = []
    seen_sitemaps = set()
    queue = discover_sitemaps(homepage_url)

    while queue and len(seen_sitemaps) < _MAX_SITEMAPS:
        url = queue.pop(0)
        if url in seen_sitemaps:
            continue
        seen_sitemaps.add(url)
        response = _get(url)
        if response is None:
            continue
        found, children = _parse_sitemap(_body(response))
        pages.extend(found)
        # Only follow children that could plausibly hold this docs subtree -
        # a blog sitemap is a guaranteed miss and a wasted round trip.
        queue.extend(c for c in children if not _SKIP_PATH_RE.search(urlparse(c).path))
        if len(pages) >= max_urls * 4:
            break

    ordered: List[str] = []
    for page in pages:
        absolute, _ = urldefrag(page.strip())
        if absolute and _is_doc_url(absolute, homepage_url, base) and absolute not in ordered:
            ordered.append(absolute)
        if len(ordered) >= max_urls:
            break

    if len(ordered) < min_links:
        return [], {}
    return ordered, {url: file_name_for(url) for url in ordered}
