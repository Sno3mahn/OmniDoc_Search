"""Deterministic removal of per-page chrome from an extracted docs corpus.

Every page of a rendered docs site carries the same nav, sidebar and footer. In
the typer extraction that was 72 of 72 files opening with the identical header
block, which gets embedded 72 times: wasted index space, and near-duplicate
chunks that dilute retrieval and can win it outright for short queries.

Frequency is the signal. A line that appears on nearly every page of a site is
chrome; a line that appears on one or two is content. That needs no LLM and no
generated code to execute - unlike pattern_matching_agent, which cost an agent
run per site and was the arbitrary-code-execution surface.
"""

import os
from collections import Counter
from typing import List, Tuple

# Guards against eating real content that legitimately repeats.
_MIN_LINE_LEN = 10
_MIN_FILES = 5


def _is_candidate(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < _MIN_LINE_LEN:
        return False
    # Fences and headings repeat across pages as a matter of course and are
    # always content, never chrome.
    if stripped.startswith("```") or stripped.startswith("#"):
        return False
    return True


def find_boilerplate_lines(dir_name: str, threshold: float = 0.6) -> set:
    """Lines present in at least `threshold` of the corpus's files."""
    files = _corpus_files(dir_name)
    if len(files) < _MIN_FILES:
        return set()

    seen = Counter()
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.read().split("\n")
        except OSError:
            continue
        # set(): a nav block repeated twice within one page still counts once,
        # so the ratio stays "fraction of files", not "fraction of lines".
        for line in {ln.strip() for ln in lines if _is_candidate(ln)}:
            seen[line] += 1

    cutoff = threshold * len(files)
    return {line for line, count in seen.items() if count >= cutoff}


def strip_common_boilerplate(dir_name: str, threshold: float = 0.6) -> Tuple[int, int]:
    """Rewrites the corpus in place without its chrome.

    Returns (lines_removed, files_touched).
    """
    boilerplate = find_boilerplate_lines(dir_name, threshold=threshold)
    if not boilerplate:
        return 0, 0

    removed = 0
    touched = 0
    for path in _corpus_files(dir_name):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.read().split("\n")
        except OSError:
            continue

        kept = [ln for ln in lines if ln.strip() not in boilerplate]
        if len(kept) == len(lines):
            continue

        removed += len(lines) - len(kept)
        touched += 1
        # Collapse the blank runs left behind by the removals.
        text = "\n".join(kept)
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text.strip() + "\n")
        except OSError:
            continue

    return removed, touched


def _corpus_files(dir_name: str) -> List[str]:
    if not os.path.isdir(dir_name):
        return []
    return [
        os.path.join(dir_name, name)
        for name in sorted(os.listdir(dir_name))
        if os.path.isfile(os.path.join(dir_name, name))
    ]
