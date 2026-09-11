from import_stuff import (
    HOMEPAGE_EXTRACTION_PROMPT,
    get_html_body, extract_page_content,
)

import os
from dotenv import load_dotenv
from typing import List, Dict, Optional, Any, Tuple


load_dotenv()
DEEPSEEK_KEY = os.getenv('API_DEEPSEEK')


from llama_index.core.workflow import Event
from llama_index.core.tools import FunctionTool
from llama_index.core.agent.workflow import ReActAgent
# from llama_index.readers.web import SimpleWebPageReader
from llama_index.llms.deepseek import DeepSeek
from llama_index.tools.playwright import PlaywrightToolSpec
# from llama_index.utils.workflow import draw_all_possible_flows
# from llama_index.core.agent.workflow import ToolCall, ToolCallResult, AgentStream


# Define LLM - deepseek-v4-flash is DeepSeek's cheap/fast tier. All three
# agents share one instance (the old base_llm/smort_llm split existed to
# mix OpenAI's nano/mini tiers; DeepSeek's flash tier covers all three
# agents' needs, so one shared client is simpler and there's nothing left
# to tier).

llm = DeepSeek(model="deepseek-v4-flash", api_key=DEEPSEEK_KEY)


async def _build_render_tool(use_playwright: bool) -> Tuple[Optional[FunctionTool], Any | None]:
    """A single narrow Playwright-backed tool, not the full PlaywrightToolSpec
    surface (click/fill/navigate_back/extract_hyperlinks/...). Neither agent's
    prompt ever asks for interactive automation - they only ever need "load
    this URL with JS execution and give me the rendered HTML" as a fallback
    for when the cheap plain-HTTP fetch (get_html_body) misses content that's
    added client-side (common on Docusaurus/Next.js/Mintlify-style doc sites).
    Exposing only this one tool keeps the agent's tool-choice surface small
    and keeps the (expensive, slow) browser path opt-in per URL rather than
    the default for every fetch.
    """
    if not use_playwright:
        return None, None
    browser = await PlaywrightToolSpec.create_async_playwright_browser(headless=True)

    async def render_page_html(url: str) -> str:
        """Loads a URL in a real browser (runs the page's JavaScript) and
        returns the rendered HTML. Slower and more expensive than
        get_html_body - only use this for a URL where get_html_body's result
        looks incomplete (e.g. missing content you'd expect, or a body that's
        mostly empty aside from a single root div, which usually means the
        real content is rendered client-side)."""
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=15000)
            return await page.content()
        finally:
            await page.close()

    return FunctionTool.from_defaults(render_page_html, name="render_page_html"), browser


async def build_agents(use_playwright: bool = True) -> Tuple[Dict[str, Any], Any | None]:
    render_tool, browser = await _build_render_tool(use_playwright)
    base_fetch_tools = [FunctionTool.from_defaults(extract_page_content), FunctionTool.from_defaults(get_html_body)]
    fetch_tools_with_fallback = base_fetch_tools + ([render_tool] if render_tool is not None else [])

    return {
        "homepage_extraction_agent": ReActAgent(
            name="homepage_extraction_agent",
            description="Extracts links to list of contents and whether a md format of the page is available or not",
            llm=llm,
            system_prompt=HOMEPAGE_EXTRACTION_PROMPT,
            tools=fetch_tools_with_fallback,
        ),
        # md_ify_agent removed: markdown-source detection is now
        # import_stuff/md_source.resolve_markdown_sources, which proves a URL
        # rewrite rule on a 3-page sample and applies it in plain Python. The
        # agent was being handed every URL on the site and asked to browse each
        # one - hundreds of LLM calls for a single rule - and MD_IFICATION_PROMPT
        # is kept in prompts.py only for reference.
    }, browser


class MDifyEvent(Event):
    pass

class ExtractWebpageEvent(Event):
    source_found: Optional[bool] = False
    clean_up_code: Optional[str] = None
    list_of_contents: Optional[List[str]] | None = []   
    html_to_md: Optional[Dict[str, str]] | None= {}

class DirNameEvent(Event):
    pass

class StatusEmitterEvent(Event):
    status: str
