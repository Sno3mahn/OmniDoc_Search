"""Measures retrieval quality against a labelled eval set.

Retrieval only - no generation, no LLM judge. If the right chunk isn't
retrieved, no amount of answer quality recovers it, so this number is the one
that gates everything downstream. It is also free and fast, which means it can
be run on every change.

Retrieves once at the largest k and slices, so a single pass reports recall at
every k. That turns "what should similarity_top_k be?" into a lookup rather
than a guess.

    python evals/run_eval.py doc-typer-tiangolo-com
    python evals/run_eval.py doc-typer-tiangolo-com --hybrid
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rag_qe  # noqa: F401  - applies the pysqlite3 swap before chromadb loads
from rag_qe import QueryEngine

KS = (1, 3, 5, 10)


def load_set(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def gold_rank(nodes, gold):
    """Index of the first retrieved node from the gold page, or None."""
    for i, node in enumerate(nodes):
        meta = node.node.metadata if hasattr(node, "node") else node.metadata
        if meta.get("file_name") == gold:
            return i
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("collection")
    ap.add_argument("--evalset", default=None)
    ap.add_argument("--db", default="./omnidoc_search.db")
    ap.add_argument("--hybrid", action="store_true", help="fuse BM25 with dense retrieval")
    ap.add_argument("--label", default=None)
    args = ap.parse_args()

    evalset_path = args.evalset or f"evals/{args.collection.replace('doc-', '').replace('-', '_')}.jsonl"
    if not os.path.exists(evalset_path):
        candidates = [f for f in os.listdir("evals") if f.endswith(".jsonl")]
        if len(candidates) == 1:
            evalset_path = os.path.join("evals", candidates[0])
        else:
            sys.exit(f"eval set not found: {evalset_path} (have: {candidates})")

    rows = load_set(evalset_path)
    engine = QueryEngine(db_path=args.db)
    retriever = engine.build_retriever(
        args.collection, similarity_top_k=max(KS), hybrid=args.hybrid
    )

    hits = {k: 0 for k in KS}
    reciprocal = 0.0
    missed = []

    for row in rows:
        nodes = retriever.retrieve(row["question"])
        rank = gold_rank(nodes, row["gold"])
        if rank is None:
            missed.append(row)
            continue
        reciprocal += 1.0 / (rank + 1)
        for k in KS:
            if rank < k:
                hits[k] += 1

    total = len(rows)
    label = args.label or ("hybrid" if args.hybrid else "dense")
    print(f"\n  collection : {args.collection}")
    print(f"  eval set   : {evalset_path}  ({total} questions)")
    print(f"  retrieval  : {label}\n")
    for k in KS:
        pct = 100.0 * hits[k] / total if total else 0
        bar = "#" * int(pct / 4)
        print(f"  recall@{k:<3} {pct:5.1f}%  {bar}")
    print(f"\n  MRR        {reciprocal / total:.3f}" if total else "")
    print(f"  missed     {len(missed)}/{total}")
    if missed[:3]:
        print("\n  examples the gold page never surfaced for:")
        for row in missed[:3]:
            print(f"    - {row['question'][:74]}  (gold: {row['gold']})")


if __name__ == "__main__":
    main()
