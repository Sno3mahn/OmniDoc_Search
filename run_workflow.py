from agentic_etl import (
    MDifyEvent, ExtractWebpageEvent, StatusEmitterEvent, DirNameEvent, build_agents
)
from import_stuff import (
    run_agent_verbose, run_concurrent_workflows, write_to_file, parse_agent_json, html_to_text,
    resolve_markdown_sources, looks_like_markdown, strip_common_boilerplate,
    get_html_body, extract_toc, file_name_for, sitemap_urls, sitemap_lastmod, fetch_first,
)

import os
import re
import json
import time
import shutil
import argparse
import concurrent
import asyncio
from uuid import uuid4
from typing import Dict, Any

from llama_index.core.workflow import StartEvent, StopEvent, Context, Workflow, step

# Every run gets its own directory under here.
RUNS_ROOT = os.getenv("OMNIDOC_RUNS_ROOT", "runs")
# Extracted markdown is an intermediate: once it's embedded into Chroma, the
# only reason to keep it is debugging and re-embedding without re-fetching.
RUN_DIR_TTL_SECONDS = int(os.getenv("OMNIDOC_RUN_TTL_SECONDS", str(7 * 24 * 3600)))


def _site_slug(homepage_url: str) -> str:
    match = re.search(r'(?:https?://)?(?:[\w-]+\.)*([\w-]+)\.[a-z]{2,}', homepage_url)
    return match.group(1) if match else 'site'


def run_dir(homepage_url: str, run_id: str) -> str:
    """Output directory for one run, scoped by run_id.

    This used to be f'{domain}_dir', shared by every run of that site, and the
    workflow wiped it on start to clear stale files. Two concurrent runs of the
    same site therefore deleted each other's output mid-extraction - the second
    run's wipe landed while the first was still writing, and both ended up
    indexing a partial corpus. Scoping by run makes the collision impossible
    rather than merely unlikely, and removes the need to wipe anything.
    """
    return os.path.join(RUNS_ROOT, f"{_site_slug(homepage_url)}-{run_id}")


def prune_run_dirs(ttl_seconds: int = RUN_DIR_TTL_SECONDS) -> int:
    """Run directories are now unique per run, so without this they accumulate
    one corpus per run forever. Called at the start of a run: cheap, and it
    keeps cleanup in the same process that creates the garbage."""
    if not os.path.isdir(RUNS_ROOT):
        return 0
    cutoff = time.time() - ttl_seconds
    removed = 0
    for name in os.listdir(RUNS_ROOT):
        path = os.path.join(RUNS_ROOT, name)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


class ETLWorkflow(Workflow):
    def __init__(self, homepage_url: str, agents: Dict[str, Any] = None, timeout=45,
                 run_id: str = None, **kwargs):
        super().__init__(timeout=timeout, num_concurrent_runs=4)
        self.agents = agents
        self.homepage_url = homepage_url
        # Defaults so the CLI and any direct caller still get an isolated
        # directory without having to invent an id.
        self.run_id = run_id or uuid4().hex[:12]

    @step
    async def read_homepage_content(self, ctx: Context, ev: StartEvent) -> MDifyEvent:
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
            dir_name = run_dir(self.homepage_url, self.run_id)
            await ctx.store.set('dir_name', dir_name)
            os.makedirs(dir_name, exist_ok=True)
            prune_run_dirs()

            # Three discovery paths, cheapest and most authoritative first.
            #
            #   sitemap  the site declaring its own URL list. No inference, and
            #            it sees pages a JS-rendered sidebar hides (docusaurus:
            #            84 pages vs the nav's 29 and the agent's 50).
            #   nav      parsing the sidebar. Curated rather than complete, and
            #            it works on sites with no sitemap at all (docs.python.org).
            #   agent    a ReAct loop over the rendered page. ~84s and an LLM
            #            bill, so it only runs when both deterministic paths
            #            come back empty.
            #
            # Both cheap paths run and the larger result wins: they cost well
            # under a second each, and neither dominates across sites (sitemap
            # won on docusaurus, nav won on fastapi 162-150).
            loop = asyncio.get_running_loop()
            discovery = "agent"

            sitemap_pages, sitemap_names = [], {}
            nav_pages, nav_names = [], {}
            lastmod = None
            try:
                sitemap_pages, sitemap_names = await loop.run_in_executor(
                    None, sitemap_urls, self.homepage_url
                )
                # Recorded now so a later request can ask "has the site changed
                # since we indexed it?" for one HTTP request instead of a
                # full re-extraction.
                lastmod = await loop.run_in_executor(
                    None, sitemap_lastmod, self.homepage_url
                )
            except Exception as exc:
                ctx.write_event_to_stream(StatusEmitterEvent(status=f"Sitemap lookup failed ({exc})"))
            try:
                html = await loop.run_in_executor(None, get_html_body, self.homepage_url)
                nav_pages, nav_names = extract_toc(self.homepage_url, html or "")
            except Exception as exc:
                ctx.write_event_to_stream(StatusEmitterEvent(status=f"Homepage nav parse failed ({exc})"))

            if sitemap_pages or nav_pages:
                if len(sitemap_pages) >= len(nav_pages):
                    list_of_contents, file_name_map, discovery = sitemap_pages, sitemap_names, "sitemap.xml"
                else:
                    list_of_contents, file_name_map, discovery = nav_pages, nav_names, "homepage nav"
                ctx.write_event_to_stream(StatusEmitterEvent(
                    status=f"Found {len(list_of_contents)} doc pages via {discovery} "
                           f"(sitemap {len(sitemap_pages)}, nav {len(nav_pages)})"
                ))
            else:
                # Neither deterministic path found anything - unusual markup, or
                # a nav that only exists after JS runs and no sitemap to cover
                # for it. This is where the agent earns its cost.
                ctx.write_event_to_stream(StatusEmitterEvent(
                    status="No sitemap or parseable nav; using the extraction agent"
                ))
                res = await run_agent_verbose(self.agents["homepage_extraction_agent"], user_query)
                res = parse_agent_json(str(res), stage="homepage_extraction_agent")
                list_of_contents = res.get("list_of_contents", [])
                file_name_map = res.get("file_name_map", {}) or {
                    url: file_name_for(url) for url in list_of_contents
                }

            await ctx.store.set("list_of_contents", list_of_contents)
            await ctx.store.set("file_name_map", file_name_map)
            await ctx.store.set("discovery", discovery)
            await ctx.store.set("source_lastmod", lastmod)

            ctx.write_event_to_stream(StatusEmitterEvent(status="Analyzed homepage content"))
            # Always the md path now: resolve_markdown_sources decides
            # deterministically whether raw sources exist and falls back to
            # rendered pages when they don't, so there is nothing for the LLM
            # to predict here.
            return MDifyEvent()

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

        file_name_map = await ctx.store.get('file_name_map', {})

        def etl_per_site(site: str) -> str:
            """Returns '' on success, or a one-line reason on failure. The
            fetching itself now lives in import_stuff.fetch, which retries 429s
            and 5xx with jittered backoff and honours Retry-After - none of
            which this step used to do, which is most of why a docusaurus run
            lost 9 of 29 pages."""
            file_name = file_name_map.get(site, '')
            alt_site = md_to_html.get(site, '')
            if not file_name and alt_site:
                file_name = file_name_map.get(alt_site, '')
            if not file_name:
                file_name = file_name_for(alt_site or site)

            # The markdown source and the rendered page are two candidates for
            # the same page; either succeeding is a success.
            result, winning_url = fetch_first(site, alt_site)
            if not result.ok:
                return result.describe()

            # If the source is already markdown, keep it verbatim. Running it
            # through SimpleWebPageReader(html_to_text=True) converts markdown
            # as if it were HTML, which escapes frontmatter into "\--- title:"
            # and mangles fences - degrading the exact clean source the md
            # resolver worked to find.
            if looks_like_markdown(result.text, result.content_type):
                content = result.text
            else:
                # Convert the bytes already in hand rather than handing the URL
                # to SimpleWebPageReader, which re-fetches it (twice).
                content = html_to_text(result.text)
            write_to_file(content=content, dir_name=dir_name, file_name=file_name)
            return ''

        failed = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:

            future_to_url = {executor.submit(etl_per_site, url): url for url in list_of_contents}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    reason = future.result()
                except Exception as e:
                    reason = f"{url} {type(e).__name__}: {e}"
                if reason:
                    print(reason)
                    failed.append(reason)

        if failed:
            # Cap the detail: 80 failures shouldn't produce an 80-line status
            # event, but the count still has to be honest.
            shown = "; ".join(failed[:5])
            suffix = f" (+{len(failed) - 5} more)" if len(failed) > 5 else ""
            ctx.write_event_to_stream(
                StatusEmitterEvent(status=f"Failed to save {len(failed)} file(s): {shown}{suffix}")
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

        if not os.path.isdir(dir_name):
            return StopEvent(result='{"status":"failed","dir_name":"unavailable dir"}')

        # Compare the filenames this run expected against what's on disk, not
        # raw counts - a count comparison reported "-41 files missing" when the
        # directory held leftovers from an earlier extraction.
        file_name_map = await ctx.store.get('file_name_map', {})
        expected = set(file_name_map.values()) or {f'{i}' for i in range(len(list_of_contents))}
        present = set(os.listdir(dir_name))
        missing = len(expected - present)
        discovery = await ctx.store.get('discovery', 'unknown')
        source_lastmod = await ctx.store.get('source_lastmod', None)

        # json.dumps rather than string concatenation - dir_name and discovery
        # both derive from user input, and a quote in either produced malformed
        # JSON that the caller's json.loads then raised on.
        status = "success" if missing == 0 else f"partial success- {missing} of {len(expected)} files missing"
        return StopEvent(result=json.dumps({
            "status": status,
            "dir_name": dir_name,
            "discovery": discovery,
            "source_lastmod": source_lastmod,
            "pages_expected": len(expected),
            "pages_saved": len(expected & present),
        }))



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
