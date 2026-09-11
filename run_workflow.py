from agentic_etl import (
    MDifyEvent, AnalyseTextEvent, ExtractWebpageEvent, StatusEmitterEvent, DirNameEvent, build_agents
)
from import_stuff import (
    extract_page_content, run_agent_verbose, run_concurrent_workflows, write_to_file, parse_agent_json,
    is_safe_url, resolve_markdown_sources, looks_like_markdown, strip_common_boilerplate,
)

import os
import re
import requests
from random import sample
import argparse
import concurrent
import asyncio
from typing import Dict, Any

from llama_index.core.workflow import StartEvent, StopEvent, Context, Workflow, step



class ETLWorkflow(Workflow):
    def __init__(self, homepage_url: str, agents: Dict[str, Any] = None, timeout=45, **kwargs):
        super().__init__(timeout=timeout, num_concurrent_runs=4)        
        self.agents=agents
        self.homepage_url = homepage_url

    @step
    async def read_homepage_content(self, ctx: Context, ev: StartEvent) -> MDifyEvent | AnalyseTextEvent:
            user_query = f'''
                Use your tools to fully analyze the homepage: {self.homepage_url}

                Tasks — return JSON only:

                1. Extract a clean list of all individual documentation content page URLs from the main sidebar/navigation/table-of-contents area.
                - Every URL must be absolute (include domain/protocol)
                - Only include real doc pages (guides, tutorials, API refs, concepts, examples…) pertaining to the framework
                - Ignore: home, blog, pricing, login, GitHub repo link itself, footer links, external sites

                2. Determine whether the documentation appears to be served from Markdown sources with direct/raw access available:
                - Evidence: "Edit on GitHub", pencil icon, "View source", "Raw", GitHub links in content area, footer note about Markdown, etc.
                - If strong evidence exists → "available_in_md": true
                - Otherwise → false
                
                3. Decide a name for the file from the site
                - Assume all files will be saved in the same root dir and will not contain any sub-directories
                ''' + '''\n
                - Eg: {"https://www.somesite.com/docs/topic/subtopic/page.html": "topic-subtopic-page.md"}

                Respond **only** with this JSON structure — nothing else (don't bluntly copy the same json example):
                {
                "list_of_contents": ["https://...", "https://...", ...],
                "available_in_md": true or false,
                "file_name_map": {"https://www.somesite.com/docs/topic/subtopic/page.html": "topic-subtopic-page.md", ...}
                }
            '''
            ctx.write_event_to_stream(StatusEmitterEvent(status="Reading content from homepage"))
            match = re.search(r'(?:https?://)?(?:[\w-]+\.)*([\w-]+)\.[a-z]{2,}', self.homepage_url)
            if match:
                dir_name = match.group(1)
                await ctx.store.set('dir_name', f'{dir_name}_dir')

            res = await run_agent_verbose(self.agents["homepage_extraction_agent"], user_query)

            res = parse_agent_json(str(res), stage="homepage_extraction_agent")
            available_in_md = res.get("available_in_md", False)
            list_of_contents = res.get("list_of_contents", [])
            file_name_map = res.get("file_name_map", {})
            
            await ctx.store.set("available_in_md", available_in_md)
            await ctx.store.set("list_of_contents", list_of_contents)
            await ctx.store.set("file_name_map", file_name_map)
            
            ctx.write_event_to_stream(StatusEmitterEvent(status="Analyzed homepage content"))
            if available_in_md:
                return MDifyEvent()
            return AnalyseTextEvent()

    @step
    async def get_markdown_links(self, ctx: Context, ev: MDifyEvent) -> ExtractWebpageEvent | None:

        list_of_contents = await ctx.store.get("list_of_contents", [])

        ctx.write_event_to_stream(StatusEmitterEvent(status="Scanning contents for md links"))
        # Deterministic, not agentic. This used to hand every URL to a ReAct
        # agent and ask it to browse each one - hundreds of LLM calls to derive
        # a single URL-rewrite rule. resolve_markdown_sources proves a rule on a
        # 3-page sample and applies it in plain Python: zero LLM calls, and the
        # result is reproducible enough to unit-test.
        loop = asyncio.get_running_loop()
        try:
            html_to_md, strategy = await loop.run_in_executor(
                None, resolve_markdown_sources, list_of_contents
            )
        except Exception as exc:
            ctx.write_event_to_stream(StatusEmitterEvent(
                status=f"Markdown source detection failed ({exc}); falling back to rendered pages"
            ))
            html_to_md, strategy = {}, "none"

        source_found = strategy != "none"
        ctx.write_event_to_stream(StatusEmitterEvent(
            status=f"Markdown sources found via {strategy}" if source_found
            else "No raw markdown sources; extracting rendered pages"
        ))

        if not html_to_md:
            html_to_md = {url: url for url in list_of_contents}
        await ctx.store.set("html_to_md", html_to_md)
        ctx.write_event_to_stream(StatusEmitterEvent(status="Fetched md links"))
        ctx.write_event_to_stream(StatusEmitterEvent(status="Extracting content and saving files"))

        await run_concurrent_workflows(list_of_contents=list_of_contents, ctx=ctx, send_to_event=ExtractWebpageEvent, stream_to_event=StatusEmitterEvent, source_found=source_found, batch_size=4)
        # return ExtractWebpageEvent(source_found=source_found)

    @step
    async def get_pattern(self, ctx: Context, ev: AnalyseTextEvent) -> ExtractWebpageEvent | None:

        ctx.write_event_to_stream(StatusEmitterEvent(status="Analyzing patterns to determine start and end of text content of a page"))
        list_of_contents = await ctx.store.get("list_of_contents", [])
        # Same reasoning as the md branch: generated cleanup code improves the
        # saved markdown but isn't required to save it at all.
        try:
            sample_size = min(3, len(list_of_contents))
            res = await run_agent_verbose(
                self.agents["pattern_matching_agent"],
                f'Go through the contents of the {sample(list_of_contents, sample_size)} and, find a pattern that can be used to clean the redundant sections of the docs, also verify the code you generate, using the tool provided to execute generated code. Final answer must only be a python code',
            )
            clean_up_code = str(res)
        except Exception as exc:
            ctx.write_event_to_stream(StatusEmitterEvent(
                status=f"Pattern detection failed ({exc}); saving pages without cleanup"
            ))
            clean_up_code = ''
        ctx.write_event_to_stream(StatusEmitterEvent(status="Extracting content and saving files"))
        await run_concurrent_workflows(list_of_contents=list_of_contents, ctx=ctx, send_to_event=ExtractWebpageEvent, stream_to_event=StatusEmitterEvent, clean_up_code=clean_up_code, batch_size=4)
        # return ExtractWebpageEvent(clean_up_code=str(res))

    @step(num_workers=6)
    async def save_files(self, ctx: Context, ev: ExtractWebpageEvent ) -> DirNameEvent: 

        # Use the batch-scoped values carried on this event, not the full
        # store-level lists - each of the N concurrent save_files calls must
        # only process its own batch, not every URL in the whole job.
        html_to_md = ev.html_to_md or {}
        md_to_html = {md: html for html, md in html_to_md.items()}

        if html_to_md:
            list_of_contents = list(html_to_md.values())
        else:
            list_of_contents = ev.list_of_contents or await ctx.store.get("list_of_contents", [])

        dir_name = await ctx.store.get('dir_name', 'save_dir')

        # TODO: implement extract raw page and clean-up using markdownify
        file_name_map = await ctx.store.get('file_name_map', {})
        def etl_per_site(site: str):
            if not is_safe_url(site):
                raise ValueError(f"Refusing to fetch non-public URL: {site}")
            file_name = file_name_map.get(site, '')
            alt_site = md_to_html.get(site, '')
            if not file_name and alt_site:
                file_name = file_name_map.get(alt_site, '')
            response = requests.get(site, timeout=15)
            status = response.status_code
            if status != 200:
                if alt_site:
                    if not is_safe_url(alt_site):
                        raise ValueError(f"Refusing to fetch non-public URL: {alt_site}")
                    response = requests.get(alt_site, timeout=15)
                    status = response.status_code
                    if status != 200:
                        raise RuntimeError(f"HTTP {status} fetching both {site} and {alt_site}")
                    site=alt_site
                else:
                    raise RuntimeError(f"HTTP {status} fetching {site}")
            # If the source is already markdown, keep it verbatim. Running it
            # through SimpleWebPageReader(html_to_text=True) converts markdown
            # as if it were HTML, which escapes frontmatter into "\--- title:"
            # and mangles fences - degrading the exact clean source the md
            # resolver worked to find.
            if looks_like_markdown(response.text, response.headers.get("content-type")):
                content = response.text
            else:
                content = extract_page_content([site])[0]
            write_to_file(content=content, dir_name=dir_name, file_name=file_name)

        failed = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:

            future_to_url = {executor.submit(etl_per_site, url): url for url in list_of_contents}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"{url} generated an exception: {e}")
                    failed.append(f"{url} ({e})")

        if failed:
            ctx.write_event_to_stream(
                StatusEmitterEvent(status=f"Failed to save {len(failed)} file(s): " + "; ".join(failed))
            )
        ctx.write_event_to_stream(StatusEmitterEvent(status="Completed saving batch of files"))
        return DirNameEvent()

    
            
    @step
    async def wait_until_over(self, ctx: Context, ev: DirNameEvent) -> StopEvent | None:
        num_running_events = await ctx.store.get('num_conc_running_events', 1)
        events = ctx.collect_events(ev, [DirNameEvent] * num_running_events)
        # collect_events returns None until ALL num_running_events DirNameEvents
        # have arrived - only proceed once the full set is in.
        if events is None:
            return None

        list_of_contents = await ctx.store.get('list_of_contents')
        dir_name = await ctx.store.get('dir_name', 'saved_dir')
        ctx.write_event_to_stream(StatusEmitterEvent(status="Saved all files"))

        # Runs here rather than per-page because it needs the whole corpus:
        # "appears on most pages" is only knowable once every page is on disk.
        loop = asyncio.get_running_loop()
        removed, touched = await loop.run_in_executor(
            None, strip_common_boilerplate, dir_name
        )
        if removed:
            ctx.write_event_to_stream(StatusEmitterEvent(
                status=f"Stripped {removed} boilerplate lines from {touched} file(s)"
            ))

        if os.path.isdir(dir_name):
            if len(os.listdir(dir_name)) == len(list_of_contents):
                return StopEvent(result='{"status":"success","dir_name":"'+dir_name+'"}')
            return StopEvent(result='{"status":"'+ f'partial success- {len(list_of_contents)-len(os.listdir(dir_name))} files missing' +'","dir_name":"'+dir_name+'"}')
        return StopEvent(result='{"status":"failed","dir_name":"unavailable dir"}')



async def main(homepage_url: str, use_playwright_tools: bool):
    agents, playwright_browser = await build_agents(use_playwright=use_playwright_tools)
    try:
        wf = ETLWorkflow(
            homepage_url=homepage_url,
            agents=agents,
            timeout=None,
        )
        result = await wf.run()
        print(result)
    finally:
        if playwright_browser is not None:
            await playwright_browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='OmniDoc Search Agent')
    parser.add_argument('-u', '--homepage_url', type=str, help='Documentation homepage to extract content from')
    parser.add_argument('-p', '--disable-playwright', action='store_true',
                        help='Skip the Playwright-derived navigation tools when extracting markdown links')
    args = parser.parse_args()
    if args.homepage_url:
        asyncio.run(main(homepage_url=args.homepage_url, use_playwright_tools=not args.disable_playwright))
        exit(0)
    print('--homepage_url arg not passed')
    exit(1)
