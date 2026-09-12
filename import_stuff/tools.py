import os
import subprocess
import sys
import tempfile
from typing import List

import html2text
import requests
from bs4 import BeautifulSoup
from llama_index.readers.web import SimpleWebPageReader

from .security import is_safe_url


def extract_page_content(sites: List[str]) -> List[str]:
    '''
    Extract and return webpages content
    Args:
        sites: list of webpage URLs
    '''
    unsafe = [s for s in sites if not is_safe_url(s)]
    if unsafe:
        raise ValueError(f"Refusing to fetch non-public URL(s): {unsafe}")
    docs = SimpleWebPageReader(html_to_text=True).load_data(sites)
    pages_content=[doc.get_content() for doc in docs]
    return pages_content



def html_to_text(html: str) -> str:
    """HTML -> markdown-ish text, from a string we already hold.

    SimpleWebPageReader(html_to_text=True).load_data([url]) does the same
    conversion but fetches the URL to get there - and measurably fetches it
    TWICE. Since the pipeline has already fetched and retried that exact page,
    routing through the reader made every HTML page cost three requests instead
    of one: ~250 requests for an 84-page site. Those extra fetches also bypass
    fetch_page's retry/backoff entirely, so they were both the likeliest cause
    of the rate-limiting that lost 9 of 29 pages on an early docusaurus run and
    the least able to recover from it.

    Same underlying converter the reader uses, so extracted text is unchanged.
    """
    return html2text.html2text(html)


def get_html_body(url: str):
    '''
    Extract and return webpages' html body
    Args:
        url: website URL string
    '''
    if not is_safe_url(url):
        raise ValueError(f"Refusing to fetch non-public URL: {url}")

    response = requests.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    body = soup.body

    if not body:
        return None

    return  str(body)


def _limit_child_resources():
    # POSIX-only; called in the child process before exec via preexec_fn.
    # RLIMIT_AS is skipped: macOS's kernel doesn't enforce it reliably (setrlimit
    # itself raises), so this stays portable across the dev machine (macOS) and
    # prod (Linux, where RLIMIT_AS does work) rather than hard-failing on macOS.
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    except (ValueError, OSError):
        pass


def sandboxed_code_interpreter(code: str) -> str:
    """
    A function to execute python code, and return the stdout and stderr.

    You should import any libraries that you wish to use. You have access to any libraries the user has installed.

    The code passed to this function is executed in isolation. It should be complete at the time it is passed to this function.

    You should interpret the output and errors returned from this function, and attempt to fix any problems.
    If you cannot fix the error, show the code to the user and ask for help

    It is not possible to return graphics or other complicated data from this function. If the user cannot see the output, save it to a file and tell the user.
    """
    # Replaces llama_index.tools.code_interpreter.CodeInterpreterToolSpec, whose
    # code_interpreter() is a bare `subprocess.run([sys.executable, "-c", code])`
    # with no timeout, no resource limits, and no filesystem/env restriction -
    # its own docstring calls it unsafe for production. This adds a wall-clock
    # timeout, a CPU/memory/fd cap (best-effort, POSIX only), a scratch cwd, and
    # a stripped environment. It is still the same OS user and still has network
    # access - this is a resource limit, not a real security boundary. True
    # isolation needs a container/VM.
    with tempfile.TemporaryDirectory(prefix="omnidoc_codeexec_") as scratch_dir:
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                cwd=scratch_dir,
                env={"PATH": os.environ.get("PATH", "")},
                timeout=10,
                preexec_fn=_limit_child_resources if sys.platform != "win32" else None,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return "StdOut:\nStdErr:\nExecution timed out after 10s"
        return f"StdOut:\n{result.stdout}\nStdErr:\n{result.stderr}"