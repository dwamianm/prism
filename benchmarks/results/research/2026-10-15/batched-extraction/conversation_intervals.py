"""Conversation-level intervals for the #254 LoCoMo gate comparisons.

``product_packing gate-compare`` resamples questions only. LoCoMo's 1,540
questions come from 10 conversations, so this script pairs the same gate
reports question by question and resamples whole conversations with
``compare_public_captures.cluster_statistics`` (2,000 samples, seed 42), the
bootstrap the answer track uses for ``interval_95_conversations``. The metrics
are the gate's own (``product_packing._all_packed`` and the others), over the
same questions ``gate-compare`` uses.

Run from the repository root:

    PYTHONPATH=. uv run python benchmarks/results/research/2026-10-15/batched-extraction/conversation_intervals.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.diagnostics.compare_public_captures import cluster_statistics
from benchmarks.diagnostics.product_packing import _all_packed, _all_returned, _evidence_recall, _projected

HERE = Path(__file__).resolve().parent
# (after, before): each comparison that a gate-compare file in this folder also makes.
PAIRS = (
    ("locomo-ingest-baseline-repeat-gate.json", "locomo-ingest-baseline-gate.json"),
    ("locomo-batch-512-gate.json", "locomo-ingest-baseline-gate.json"),
    ("locomo-batch-2048-gate.json", "locomo-ingest-baseline-gate.json"),
    ("locomo-batch-8192-gate.json", "locomo-ingest-baseline-gate.json"),
    ("locomo-batch-8192-gate.json", "locomo-batch-2048-gate.json"),
    ("locomo-batch-1-ablation-gate.json", "locomo-ingest-baseline-gate.json"),
    ("locomo-batch-2048-gate.json", "locomo-batch-1-ablation-gate.json"),
    # The full builds of 2026-10-16, both at d810ee55: batched at 2,048 (the defaults) against one call per turn.
    ("locomo-full-defaults-gate.json", "locomo-full-per-turn-gate.json"),
)
METRICS = {
    "all_evidence_packed": (True, _all_packed),
    "evidence_recall": (True, _evidence_recall),
    "all_evidence_returned": (True, _all_returned),
    "projected_accuracy": (False, _projected),
}


def load(name: str) -> tuple[dict, str]:
    raw = (HERE / name).read_bytes()
    rows = {row["question_id"]: row for row in json.loads(raw)["rows"] if row["benchmark"] == "locomo"}
    return rows, hashlib.sha256(raw).hexdigest()


def compare(after_name: str, before_name: str) -> dict:
    (new, after_sha), (old, before_sha) = load(after_name), load(before_name)
    if old.keys() != new.keys():
        raise ValueError(f"{after_name} and {before_name} hold different questions")
    result = {"after": after_name, "before": before_name,
              "inputs": {"after_sha256": after_sha, "before_sha256": before_sha}}
    for metric, (annotated_only, value) in METRICS.items():
        keys = [key for key in old if old[key]["evidence"] is not None or not annotated_only]
        pairs = [(float(value(old[key])), float(value(new[key]))) for key in keys]
        conversations = [key.rsplit("-q", 1)[0] for key in keys]
        result[metric] = cluster_statistics(pairs, conversations)
    return result


def main() -> None:
    results = [compare(after, before) for after, before in PAIRS]
    out = HERE / "locomo-conversation-intervals.json"
    out.write_text(json.dumps({"kind": "gate-conversation-intervals", "samples": 2000, "seed": 42,
                               "comparisons": results}, indent=2) + "\n")
    for item in results:
        packed = item["all_evidence_packed"]
        low, high = packed["interval_95"]
        print(f"{item['after']} vs {item['before']}: all evidence packed {packed['delta'] * 100:+.2f} pp, "
              f"conversations 95% {low * 100:+.2f} to {high * 100:+.2f}")


if __name__ == "__main__":
    main()
