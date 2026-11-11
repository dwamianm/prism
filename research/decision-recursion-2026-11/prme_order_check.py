"""Does PRME give the same order for the same question on two fresh builds of the same pack?

Builds the store() chat pack (tests/contract, product defaults, classic) twice, asks each question once per build,
and prints every question whose top 10 order differs, with node ids and scores at full precision for the first
such question. Not part of the decision experiments; it pins down a difference seen between their runs.

    uv run python research/decision-recursion-2026-11/prme_order_check.py --out research/decision-recursion-2026-11/results/prme_order_check.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from read_experiment import make_provider  # noqa: E402
from tests.contract import scenes, world  # noqa: E402
from tests.contract.harness import Scene, no_network  # noqa: E402


def rows(response, n=10):
    out = []
    for rank, c in enumerate(response.results[:n], start=1):
        out.append({"rank": rank, "node_id": str(c.node.id), "text": c.node.content,
                    "event_time": c.node.event_time.isoformat() if c.node.event_time else None,
                    "created_at": c.node.created_at.isoformat() if getattr(c.node, "created_at", None) else None,
                    "session_id": getattr(c.node, "session_id", None),
                    "composite_score": repr(c.composite_score), "semantic_score": repr(c.semantic_score),
                    "lexical_score": repr(c.lexical_score), "paths": c.paths,
                    "context_relevance": repr(c.context_relevance)})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    provider = make_provider()
    chat = world.CHAT
    questions = [q.question for q in (*chat.questions, chat.unanswerable)]
    builds = []
    for _ in range(2):
        scene = Scene(provider, user=chat.user, now=chat.now, stores=scenes.chat_messages(chat))
        with no_network():
            builds.append({q: rows(scene.ask(q)) for q in questions})
        scene.close()
    differing = [q for q in questions
                 if [r["text"] for r in builds[0][q]] != [r["text"] for r in builds[1][q]]]
    result = {"differing_questions": differing,
              "cases": {q: {"build1": builds[0][q], "build2": builds[1][q]} for q in differing}}
    for q in differing:
        print(f"== {q}")
        for a, b in zip(builds[0][q], builds[1][q]):
            mark = "  " if a["text"] == b["text"] else "≠ "
            print(f"{mark}{a['rank']:>2} {a['composite_score']:<22} {a['node_id'][:8]} {a['text'][:42]:42} | "
                  f"{b['composite_score']:<22} {b['node_id'][:8]} {b['text'][:42]}")
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
