HOMEPAGE_EXTRACTION_PROMPT = '''
You are given the homepage of a documentation site (usually /docs, /learn, /guide, etc.).

Your goal:
1. Determine if there is a reliable way to access the **original Markdown source** of the pages (best quality, no conversion artifacts).
2. Extract the main table of contents / navigation structure as a flat list of full, absolute URLs to individual documentation pages.

Step-by-step instructions:

A. Markdown source availability (set "available_in_md": true ONLY if confident)
   - Look for patterns that indicate GitHub-sourced docs (highest priority):
     • "Edit this page" / "Edit on GitHub" / "Improve this doc" / pencil ✏️ icon / GitHub logo (usually top-right of content area or per-page)
     • "View source" / "Raw" / "Markdown" toggle on the page or in footer
   - If such a control exists on the homepage **or** is described in the page content / footer / sidebar, assume most (or all) content pages have the same pattern → set available_in_md = true
   - If no such indicator is found anywhere on the page → set false

B. Table of Contents extraction
   - Identify the main navigation / sidebar / left menu / "Contents" section (ignore header/global nav, footer, hero, ads)
   - Extract all documentation page links (usually under sections like "Getting Started", "Core Concepts", "API", "Tutorials", etc.)
   - Convert every relative link (/docs/intro, ./quickstart) → absolute full URL (https://docs.example.com/docs/intro)
   - Include only real content pages — skip category headers without links, external links, blog/changelog, etc.
   - Flatten the structure: return a simple list of full URLs (no nesting needed)

Rules:
- Base every decision ONLY on what you actually observe in the page content/summary.
- Do NOT guess URLs or assume patterns not visible.
- If markdown source discovery fails → still extract the list_of_contents normally.

Final output must be **only** this JSON — no other text:

{"list_of_contents": ["https://docs.example.com/docs/intro", "https://docs.example.com/api/reference", ...], "available_in_md": true/false}
'''


MD_IFICATION_PROMPT = '''
You're given tools to interact with a webpage (browse, click simulated via instructions, follow links, extract elements, etc.). Your goal is to find a clean Markdown (.md / .mdx / .txt / raw) version of the documentation page, stripping away headers, footers, navigation, sidebars, ads, footers, cookie banners, etc.

Preferred / primary method — highest quality, no conversion loss:
1. Look very carefully (top-right, bottom of content, footer area, floating buttons, pencil icon) for links/buttons with text like:
   - "Edit this page"
   - "Edit on GitHub"
   - "View source"
   - "Update on GitHub"
   - "GitHub" icon/link
   - "Improve this doc"
   - pencil/edit ✏️ icon
   These almost always point to the GitHub repo file (usually .md or .mdx).
2. If in Github, extract the html and finding the href assigned to the button raw will give you the expected md link
3. If found → follow that link → you will land on GitHub → find the "Raw" button (top-right of code area) or append ?raw=true or change URL to raw.githubusercontent.com/... and get the direct markdown source.
4. If already on GitHub file view, prioritize the raw/plain text URL.

Strong secondary / fallback methods (try these if no GitHub link found):
5. Search the page HTML/elements for any toggle/switch/button/link saying:
   - "View as Markdown"
   - "Markdown view"
   - "Source"
   - "Raw"
   - "Plain text"
   - "Disable rendering" / "Code view" (common on GitHub-hosted rendered files)
   - API/docs endpoint that serves ?format=md or /markdown or similar
6. If none exist, check if the URL itself can be modified to fetch markdown (some sites support /page.md, ?markdown=1, /raw/, etc. — experiment with one or two logical variations).

Rules:
- Only consider success if you reach a clean, mostly-plain Markdown-formatted text (starts with # headings, ```code blocks, - lists, etc.), without HTML tags or heavy website chrome.
- If the redirected page is still rendered HTML without a clear raw/markdown toggle, or if no method succeeds after reasonable attempts, consider the task failed for that URL.
- Never invent URLs or assume content — only use what you observe/follow from the actual page.

From the list of webpages provided, for each URL in list_of_contents, attempt the above process and replace it with the final markdown / raw / txt equivalent URL if successful (e.g. https://raw.githubusercontent.com/.../...md), otherwise leave the original URL but set source_found success flag accordingly.

Finally, return **only** a JSON object with no extra text, comments or explanations:
{"html_to_md": {"https://docs.some_framework.com/.../page":"https://raw.githubusercontent.com/.../page.md", ...}, "source_found": true/false}
'''


PATTERN_MATCHING_PROMPT = '''
In a typical documentation website, there is a lot of redundancies like repeated table of contents on each page, meaningless footers, etc.
Typically starts with # for Heading 1 in markdown and * * * to demarcate the end of the content, you can check for this first.
If that's not the case, you have to identify patterns that determine where the text content starts and finishes. For this choose any 3-4 weblinks provided to you and make your analysis
Once you've established the pattern return code in python within backticks as final answer. Providing additional text is prohibited
You can also execute and test your code using tools provided
'''


# extract_page_content_prompt = '''
# Your task is to extract text content from webpages using some tools provided to you. The text you've extracted should be stored in markdown file in the following format:
# dirname: <framework>_dir/ (dir_name will be provided)
# files: <topic>.md (the topic name can be inferred from the URL sub-path as well in the list_of_contents)
# However, between these steps, you have two choices- you can either store the content as is, or you may have to execute some clean-up code to rid the files of redundant pieces of data.
# You'll be notified of the outcome of the choice above by the user. And finally return the name of the directory which was created in json: {"save_dir": <framework>_dir}
# '''

# reviewer_agent = "Your task is to verify if the files are polluted or empty or not."

# file_store_prompt = "You're an ETL agent, your task is to extract web content from a list of hyperlinks given, and having extracted the content, clean up the content with some clean-up code provided to you.
# Finally once the operation is over, store all the documents in md files, on the path <framework_name>_dir/ . You're suppossed to generate and execute the code in python to complete your operation"