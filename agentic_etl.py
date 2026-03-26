from import_stuff import (
    PATTERN_MATCHING_PROMPT, MD_IFICATION_PROMPT, HOMEPAGE_EXTRACTION_PROMPT,
    get_html_body, extract_page_content,
)

import os
from dotenv import load_dotenv
from typing import List, Dict, Optional, Any, Tuple


load_dotenv()
OPENAI_KEY=os.getenv('API_OAI')


from llama_index.core.workflow import Event
from llama_index.core.tools import FunctionTool
from llama_index.core.agent.workflow import FunctionAgent, ReActAgent
# from llama_index.readers.web import SimpleWebPageReader
from llama_index.llms.openai import OpenAI
from llama_index.tools.code_interpreter import CodeInterpreterToolSpec
from llama_index.tools.playwright import PlaywrightToolSpec
# from llama_index.utils.workflow import draw_all_possible_flows
# from llama_index.core.agent.workflow import ToolCall, ToolCallResult, AgentStream


# Define LLMs

base_llm = OpenAI(model="gpt-5-nano", api_key=OPENAI_KEY)
smort_llm = OpenAI(model="gpt-5-mini", api_key=OPENAI_KEY)


async def _build_playwright_tools(use_playwright: bool) -> Tuple[List[Any], Any | None]:
    if not use_playwright:
        return [], None
    browser = await PlaywrightToolSpec.create_async_playwright_browser(headless=True)
    return PlaywrightToolSpec(async_browser=browser).to_tool_list(), browser


async def build_agents(use_playwright: bool = True) -> Tuple[Dict[str, Any], Any | None]:
    playwright_tools, browser = await _build_playwright_tools(use_playwright)
    md_tools = [FunctionTool.from_defaults(extract_page_content)]
    if playwright_tools:
        md_tools.extend(playwright_tools)

    return {
        "homepage_extraction_agent": ReActAgent(
            name="homepage_extraction_agent",
            description="Extracts links to list of contents and whether a md format of the page is available or not",
            llm=base_llm,
            system_prompt=HOMEPAGE_EXTRACTION_PROMPT,
            tools=[FunctionTool.from_defaults(extract_page_content), FunctionTool.from_defaults(get_html_body)],
        ),
        "md_ify_agent": FunctionAgent(
            name="md_ify_agent",
            description="Amends links to list of contents to md or txt or similar format of the page and notify if task was a success or not",
            llm=smort_llm,
            system_prompt=MD_IFICATION_PROMPT,
            tools=md_tools,
        ),
        "pattern_matching_agent": ReActAgent(
            name="pattern_matching_agent",
            description="Returns python code to clean up md files after identifying patterns in the homepage",
            llm=base_llm,
            system_prompt=PATTERN_MATCHING_PROMPT,
            tools=[FunctionTool.from_defaults(extract_page_content)] + CodeInterpreterToolSpec().to_tool_list(),
        ),
        # "extract_page_content_agent": FunctionAgent(name='extract_page_content_agent', description='extracts, cleans and stores the file content in md format in <framework_name>/ path',
        #                                   llm=base_llm, system_prompt=extract_page_content_prompt, tools=[FunctionTool.from_defaults(extract_page_content), FunctionTool.from_defaults(write_to_file)] + CodeInterpreterToolSpec().to_tool_list())
    }, browser


class AnalyseTextEvent(Event):
    # list_of_contents: List[str] | None = None
    pass
    # available_in_md: bool

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
    present_status: str
