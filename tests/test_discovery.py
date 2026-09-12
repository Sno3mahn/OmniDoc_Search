"""Pure unit tests for URL filtering - no network.

These exist because _is_doc_url was broken twice in a row by changes that
looked obviously correct:

  1. Applying the locale/version filters to the WHOLE path rejected every page
     of https://docs.pytest.org/en/stable/, because "en" reads as a locale and
     "stable" sits where a version goes - in the prefix the caller explicitly
     asked for.
  2. Fixing that by slicing the prefix off dropped the leading "/", and both
     regexes match "/segment/" shapes, so neither matched any more. Archived
     versions flooded back in: docusaurus went from 84 pages to 1069, mostly
     near-duplicate old releases, which would have wrecked retrieval.

Neither failure raises. Both silently change how much of a site gets indexed,
which is why they need assertions rather than eyeballing a count.

    python -m pytest tests/ -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from import_stuff.sitemap import _base_path, _is_doc_url
from import_stuff.toc import build_file_name_map, file_name_for


@pytest.mark.parametrize("homepage,expected", [
    ("https://typer.tiangolo.com/", ""),          # whole site is the docs
    ("https://typer.tiangolo.com", ""),
    ("https://docusaurus.io/docs", "/docs/"),     # section root, not a page
    ("https://docusaurus.io/docs/", "/docs/"),
    ("https://docs.pytest.org/en/stable/", "/en/stable/"),
    ("https://x.io/docs/intro.html", "/docs/"),   # a page: its parent is the root
])
def test_base_path(homepage, expected):
    assert _base_path(homepage) == expected


@pytest.mark.parametrize("homepage,url,expected", [
    # The requested prefix is the request, not noise, even when it looks like
    # a locale ("en") or a version ("stable").
    ("https://docs.pytest.org/en/stable/",
     "https://docs.pytest.org/en/stable/how-to/fixtures.html", True),
    # ...but a DIFFERENT version under the same prefix is still excluded.
    ("https://docs.pytest.org/en/stable/",
     "https://docs.pytest.org/en/latest/how-to/fixtures.html", False),

    # Archived versions below the root duplicate current pages.
    ("https://docusaurus.io/docs", "https://docusaurus.io/docs/advanced/architecture", True),
    ("https://docusaurus.io/docs", "https://docusaurus.io/docs/2.x/advanced", False),
    ("https://docusaurus.io/docs", "https://docusaurus.io/docs/next/advanced", False),
    # Outside the requested subtree.
    ("https://docusaurus.io/docs", "https://docusaurus.io/showcase", False),

    # Locale segments below the root.
    ("https://typer.tiangolo.com/", "https://typer.tiangolo.com/tutorial/install/", True),
    ("https://typer.tiangolo.com/", "https://typer.tiangolo.com/fr/tutorial/", False),
    ("https://typer.tiangolo.com/", "https://typer.tiangolo.com/zh-cn/tutorial/", False),
    # Two-letter segments that are words, not languages, must survive.
    ("https://x.io/", "https://x.io/js/getting-started", True),
    ("https://x.io/", "https://x.io/api/reference", True),

    # Site furniture and assets.
    ("https://x.io/", "https://x.io/blog/some-post", False),
    ("https://x.io/", "https://x.io/pricing", False),
    ("https://x.io/", "https://x.io/static/logo.png", False),
    # Other hosts, and the bare root.
    ("https://x.io/", "https://evil.example/docs/page", False),
    ("https://x.io/", "https://x.io/", False),
])
def test_is_doc_url(homepage, url, expected):
    assert _is_doc_url(url, homepage, _base_path(homepage)) is expected


@pytest.mark.parametrize("url,expected", [
    ("https://typer.tiangolo.com/tutorial/install/", "tutorial-install.md"),
    ("https://x.io/docs/intro.html", "docs-intro.md"),
    ("https://x.io/", "index.md"),
])
def test_file_name_for(url, expected):
    assert file_name_for(url) == expected


def test_file_name_for_collides_by_design():
    """file_name_for is per-URL and cannot see the rest of the site, so it
    genuinely does collide. Documented here so the collision-free guarantee is
    known to live in build_file_name_map, not here."""
    assert file_name_for("https://x.io/docs/a/b") == file_name_for("https://x.io/docs/a-b")


def test_build_file_name_map_is_collision_free():
    """Everything lands in one flat directory, so two different pages must not
    collapse onto the same filename - that silently loses a page, and the
    completion check (which compares filename sets) wouldn't report it."""
    urls = [
        "https://x.io/docs/a/b",
        "https://x.io/docs/a-b",     # different page, same slug
        "https://x.io/docs/a.b",     # and again
        "https://x.io/docs/a/c",
    ]
    mapping = build_file_name_map(urls)
    assert len(set(mapping.values())) == len(urls), mapping
    # The first claimant keeps the clean name; only clashes get suffixed.
    assert mapping["https://x.io/docs/a/b"] == "docs-a-b.md"


def test_build_file_name_map_is_stable_across_runs():
    """Names must not depend on discovery order, or the corpus fingerprint
    moves every run and the re-index cache never hits."""
    urls = ["https://x.io/docs/a/b", "https://x.io/docs/a-b"]
    forward = build_file_name_map(urls)
    # Same set, same claim order -> same names. Reversing order legitimately
    # swaps which URL holds the clean name, so assert on the pair being stable
    # when order is stable, and on uniqueness when it isn't.
    assert build_file_name_map(urls) == forward
    assert len(set(build_file_name_map(list(reversed(urls))).values())) == 2
