"""List what ``retrieve()`` returns for a chat probe's questions, and what ranks above each answer.

The chat probe says where the answer ranks (``answer_rank``). This tool says why:
for each probe question of a finished build it prints the results in the order the
product returns them, with the score, the cosine and lexical score, the two ranks
that make a rank-fused score, the paths that produced the candidate and its text,
and it counts what ranks above the first record that holds the answer, by kind. A
kind is a record's type, and for a claim its grounding method: ``claim_sentences_v1``
holds source sentences, ``claim_statement_v1`` is a statement built from a subject,
predicate and object. No model is called.

    uv run python -m benchmarks.diagnostics.ranked_results --label on
    uv run python -m benchmarks.diagnostics.ranked_results --label on --probe cities --limit 14
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from benchmarks.diagnostics.chat_probe import DEFAULT_CONVERSATION, answer_rank, default_root, load_conversation
from benchmarks.diagnostics.product_packing import _quiet_offline_cli, gate_config
from prme import MemoryEngine


def result_kind(candidate) -> str:
    """A result's record type, and for a claim its grounding method."""
    node = candidate.node
    if node.node_type.value in {"entity", "note"}:
        return node.node_type.value
    return f"{node.node_type.value}/{(node.metadata or {}).get('grounding_method') or 'unknown'}"


def ranked_dicts(results) -> list[dict]:
    return [{"content": candidate.node.content, "type": candidate.node.node_type.value} for candidate in results]


def kinds_above_answer(probe: dict, results) -> Counter:
    """The kinds of the results that rank above the answer; empty when it ranks first or not at all."""
    rank = answer_rank(probe, ranked_dicts(results))
    return Counter(result_kind(candidate) for candidate in results[:rank - 1]) if rank else Counter()


def result_rows(probe: dict, results, limit: int) -> list[list]:
    """One row for each of the first ``limit`` results: rank, kind, score, channels, paths and text."""
    rank = answer_rank(probe, ranked_dicts(results))
    rows = []
    for place, candidate in enumerate(results[:limit], start=1):
        fusion = candidate.score_provenance.rank_fusion if candidate.score_provenance else None
        rows.append([place, "answer" if place == rank else "", result_kind(candidate), f"{candidate.composite_score:.4f}",
                     f"{candidate.semantic_score:.3f}", f"{candidate.lexical_score:.3f}",
                     f"{candidate.graph_proximity:.2f}",
                     f"{getattr(fusion, 'semantic_rank', None)}/{getattr(fusion, 'lexical_rank', None)}",
                     ",".join(candidate.paths), candidate.node.content[:90]])
    return rows


async def report(folder: Path, conversation: dict, probe_ids: set[str], limit: int) -> str:
    build_path = folder / "build.json"
    build = json.loads(build_path.read_text()) if build_path.exists() else {}
    reference_time = datetime.fromisoformat(conversation["reference_time"])
    lines, total = [], Counter()
    async with MemoryEngine.open(gate_config(folder / "pack", build.get("overrides"))) as engine:
        for probe in conversation["key"]["probes"]:
            if probe_ids and probe["id"] not in probe_ids:
                continue
            response = await engine.retrieve(probe["question"], user_id=conversation["owner"],
                                             reference_time=reference_time)
            results = list(response.results)
            above = kinds_above_answer(probe, results)
            total.update(above)
            rank = answer_rank(probe, ranked_dicts(results))
            lines += [f"## {probe['id']}: {probe['question']}", "",
                      f"Answer rank {rank or 'none'} of {len(results)} results; above it: {dict(above) or 'nothing'}.", "",
                      "| Place | | Kind | Score | Cosine | Lexical | Graph | Ranks (cosine/lexical) | Paths | Text |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
            lines += ["| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |"
                      for row in result_rows(probe, results, limit)]
            lines.append("")
    lines += ["## Above the answers, all probes", "", "| Kind | Results |", "|---|---:|"]
    lines += [f"| {kind} | {count} |" for kind, count in sorted(total.items())]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.ranked_results", description=__doc__.split("\n")[0])
    parser.add_argument("--conversation", type=Path, default=DEFAULT_CONVERSATION)
    parser.add_argument("--root", type=Path, default=None,
                        help="Where builds live; the main checkout's data/chat-probe-v1 by default")
    parser.add_argument("--label", required=True, help="The build to read")
    parser.add_argument("--probe", action="append", default=[], help="A probe ID; every probe by default")
    parser.add_argument("--limit", type=int, default=10, help="Results to list for each probe")
    args = parser.parse_args(argv)
    _quiet_offline_cli()
    conversation = load_conversation(args.conversation)
    root = default_root() if args.root is None else args.root
    print(asyncio.run(report(root / conversation["name"] / args.label, conversation, set(args.probe), args.limit)))


if __name__ == "__main__":
    main()
