"""Generates a retrieval eval set from an extracted corpus.

For each page, the LLM writes questions answerable *only* from that page, so
the gold retrieval target is that page. A few hundred labelled pairs for the
cost of one cheap call per page, with no human labelling.

Known bias: generated questions inherit the source's wording, which inflates
scores through lexical overlap. The prompt pushes for user-style phrasing to
blunt that, but treat absolute numbers as optimistic - the harness is for
comparing A against B, not for claiming an absolute accuracy.

    python evals/build_evalset.py tiangolo_dir --pages 30 --per-page 2
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from llama_index.llms.deepseek import DeepSeek

from import_stuff import parse_agent_json

load_dotenv()

PROMPT = """Below is one page from a documentation site.

Write {n} questions that a developer would type into a search box, which this
page answers. Rules:
- Phrase them the way a user actually asks ("how do I...", "why does...",
  "what's the difference between..."), NOT as a restatement of the headings.
- Each question must be answerable from THIS page alone.
- Do not mention the page title or file name in the question.
- No preamble. Return only JSON: {{"questions": ["...", "..."]}}

PAGE:
{page}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_dir")
    ap.add_argument("--pages", type=int, default=30)
    ap.add_argument("--per-page", type=int, default=2)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out_path = args.out or f"evals/{os.path.basename(args.corpus_dir.rstrip('/'))}.jsonl"
    llm = DeepSeek(model="deepseek-v4-flash", api_key=os.getenv("API_DEEPSEEK"))

    files = sorted(
        f for f in os.listdir(args.corpus_dir)
        if os.path.isfile(os.path.join(args.corpus_dir, f))
    )[: args.pages]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    written = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for i, name in enumerate(files, 1):
            with open(os.path.join(args.corpus_dir, name), encoding="utf-8") as fh:
                page = fh.read()[:6000]
            if len(page.strip()) < 200:
                continue
            try:
                raw = llm.complete(PROMPT.format(n=args.per_page, page=page))
                questions = parse_agent_json(str(raw), stage="evalset").get("questions", [])
            except Exception as exc:
                print(f"  [{i}/{len(files)}] {name}: skipped ({exc})")
                continue

            for question in questions:
                if isinstance(question, str) and question.strip():
                    out.write(json.dumps({"question": question.strip(), "gold": name}) + "\n")
                    written += 1
            print(f"  [{i}/{len(files)}] {name}: {len(questions)} questions")

    print(f"\nwrote {written} question/gold pairs -> {out_path}")


if __name__ == "__main__":
    main()
