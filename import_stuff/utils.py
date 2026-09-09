import os
from math import ceil
from typing import Optional, List

from llama_index.core.workflow import Context, Event
from llama_index.core.agent.workflow import FunctionAgent, ReActAgent
from llama_index.readers.web import SimpleWebPageReader
from llama_index.core.agent.workflow import ToolCall, ToolCallResult, AgentStream


_NOT_PROVIDED = object()

async def run_concurrent_workflows(list_of_contents: List[str],
                                   ctx: Context,
                                   send_to_event: Event,
                                   stream_to_event: Event,
                                   source_found: Optional[bool] = _NOT_PROVIDED,
                                   clean_up_code: Optional[str] = _NOT_PROVIDED,
                                   batch_size: int = 4):

    html_to_md = await ctx.store.get('html_to_md')
    # list_of_contents_md = await ctx.store.get('list_of_contents_md', [])
    len_loc = len(list_of_contents)  # lets keep separation of values b/w list_of_contents and loc_md
    num_conc_running_events = ceil(len_loc/batch_size)
    # print('\nnum_conc_running_events =\t' + str(num_conc_running_events))
    await ctx.store.set('num_conc_running_events', num_conc_running_events)
    await ctx.store.set('list_of_contents', list_of_contents)
    
    for i in range(0, len_loc, batch_size):
        ctx.write_event_to_stream(stream_to_event(status=f"Saving batch {i//batch_size} of {num_conc_running_events}"))
        batch_of_contents = list_of_contents[i:i+batch_size]
        batch_of_md_dict = {src: html_to_md[src] for src in list(html_to_md.keys())[i:i+batch_size]}
        if clean_up_code is _NOT_PROVIDED:
            ctx.send_event(send_to_event(list_of_contents=batch_of_contents, html_to_md=batch_of_md_dict, source_found=source_found))
        elif source_found is _NOT_PROVIDED:
            ctx.send_event(send_to_event(list_of_contents=batch_of_contents, clean_up_code=clean_up_code))
        else:
            ctx.send_event(send_to_event(list_of_contents=batch_of_contents))


async def run_agent_verbose(agent: ReActAgent | FunctionAgent, query):
    handler = agent.run(query, max_iterations=50)
    print(f"User:  {query}")
    async for event in handler.stream_events():
        if isinstance(event, ToolCallResult):
            print(
                f"\n-----------\nCode execution result:\n{event.tool_output}"
            )
        elif isinstance(event, ToolCall):
            if 'code' in event.tool_kwargs:
                print(f"\n-----------\nParsed code:\n{event.tool_kwargs['code']}")
            else:
                print(f"\n-----------\nTool call: {event.tool_name}\nInput: {event.tool_kwargs}")
        elif isinstance(event, AgentStream):
            print(f"{event.delta}", end="", flush=True)

    return await handler


def write_to_file(content: str, file_name: str, dir_name: str) -> None:
    """
    Writes a string to a file inside a directory.
    If the directory does not exist, it will be created.
    Args:
        content: Text content to write
        file_name: Name of the file (e.g., "output.txt")
        dir_name: Directory where the file will be stored
    """
    # Ensure directory exists
    os.makedirs(dir_name, exist_ok=True)

    file_path = os.path.join(dir_name, file_name)

    # Write content to file
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


def extract_page_content(sites: List[str]) -> List[str]:
    '''
    Extract and return webpages content
    Args:
        sites: list of webpage URLs
    '''
    docs = SimpleWebPageReader(html_to_text=True).load_data(sites)
    pages_content=[doc.get_content() for doc in docs]
    return pages_content
