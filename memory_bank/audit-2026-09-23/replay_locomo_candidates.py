"""Replay LoCoMo retrieval on copies of the saved packs and capture every candidate.

For each of the 1,540 LoCoMo questions in the 2026-09-23 GPT-5.4 run, this replays the
public `retrieve()` call on a scratch copy of the conversation's memory pack and records,
for every scored candidate before packing: its true `path_count`, its paths, its
composite score, and its own semantic and lexical scores. It also checks that the
replayed context is byte-identical (same SHA-256) to the saved one. The saved archive
is never modified.

Written during the 2026-09-23 ticket verification. The saved receipts copy a trigger's
score trace onto session-expansion neighbors, so rankings rebuilt from receipts are
approximate for about 10% of candidates; this replay records each candidate's own
scores. About 5 minutes, local query embedding only, no paid API calls.

Run from the repository root:
  PYTHONPATH=. .venv/bin/python memory_bank/audit-2026-09-23/replay_locomo_candidates.py
  PYTHONPATH=. .venv/bin/python memory_bank/audit-2026-09-23/exact_ranking_projection.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline_evidence_audit as A  # noqa: E402
import prme.retrieval.pipeline as P  # noqa: E402
from benchmarks.integrations import run_gpt54_comparison as gpt54  # noqa: E402
from prme.config import PRMEConfig  # noqa: E402
from prme.storage.engine import MemoryEngine  # noqa: E402

SCRATCH = Path("data/audit-2026-09-23-replay")
OUT = SCRATCH / "captured.jsonl"
captured: dict[str, list[dict]] = {}
_original_pack_context = P.pack_context


def _capture_then_pack(scored, *args, **kwargs):
    """Record the scored candidates, then pack exactly as the product does."""
    captured["scored"] = [
        {
            "id": str(c.node.id),
            "pc": c.path_count,
            "paths": list(c.paths),
            "score": c.composite_score,
            "sem": c.semantic_score,
            "lex": c.lexical_score,
        }
        for c in scored
    ]
    return _original_pack_context(scored, *args, **kwargs)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def main() -> None:
    P.pack_context = _capture_then_pack
    prepared = json.loads((A.RUN / "locomo/prepared.json").read_text())
    contexts = {path.stem: path for path in sorted((A.RUN / "locomo/contexts").glob("*.json"))}
    questions = {q["question_id"]: q for q in gpt54.question_rows("locomo")}
    SCRATCH.mkdir(parents=True, exist_ok=True)
    matched = total = 0
    with OUT.open("w") as out:
        for record in prepared["packs"]:
            conversation = record["conversation_id"]
            source = Path(record["config"]["db_path"]).parent
            copy = SCRATCH / conversation
            if copy.exists():
                shutil.rmtree(copy)
            shutil.copytree(source, copy)
            settings = dict(record["config"])
            settings.update(
                db_path=str(copy / "memory.duckdb"),
                vector_path=str(copy / "vectors.usearch"),
                lexical_path=str(copy / "lexical_index"),
            )
            with patch.dict(os.environ, {}, clear=True):
                config = PRMEConfig(_env_file=None, **settings)
            started = time.time()
            async with MemoryEngine.open(config) as engine:
                for qid, path in contexts.items():
                    if questions[qid]["conversation_id"] != conversation:
                        continue
                    saved = json.loads(path.read_text())
                    reference_time = datetime.fromisoformat(
                        saved["receipt"]["reference_time"].replace("Z", "+00:00")
                    )
                    response = await engine.retrieve(
                        questions[qid]["question"], user_id=conversation, reference_time=reference_time
                    )
                    same = sha256(response.bundle.render()) == sha256(saved["context"])
                    matched += same
                    total += 1
                    out.write(json.dumps({"qid": qid, "same_context": same, "cands": captured["scored"]}) + "\n")
            print(conversation, "done", round(time.time() - started, 1), "s", matched, "/", total, flush=True)
            shutil.rmtree(copy)
    print("contexts identical", matched, "/", total)


if __name__ == "__main__":
    asyncio.run(main())
