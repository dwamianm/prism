"""Compare packing orders on frozen candidates without changing retrieval.

The experimental comparator uses composite score within the multi-path tier,
replacing score/token there only. Instructions, pins, task priority, rendering,
fidelity and budget rules are unchanged. No production defaults are modified.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import patch

from benchmarks.integrations.memconflict import local_reader
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context


def packed_details(bundle):
    return {
        "context": bundle.render(), "tokens": bundle.tokens_used,
        "sources": [{"id": c.node.metadata["source_turn"], "representation": c.representation.value}
                    for group in bundle.sections.values() for c in group],
    }


async def compare(report, reader=None):
    if not report.get("complete") or report.get("process_exit_code") != 0:
        raise ValueError("A completed, normally exited replay is required")
    config = PackingConfig.model_validate(report["source"]["engine_config"]["packing"])
    config = config.model_copy(update={"token_budget": report["token_budget"]})
    output = {
        "complete": False, "kind": "fixed_candidates_packing_order_diagnostic",
        "variant": "composite score within the multi-path tier; other priorities unchanged",
        "packing_module_sha256": hashlib.sha256(Path(inspect.getfile(pack_context)).read_bytes()).hexdigest(),
        "packing_config": config.model_dump(mode="json"), "reader_executed": reader is not None,
        "judge_status": "not_run", "accuracy": None, "details": [],
        "limits": "Development counterfactual on previously retrieved candidates; not end-to-end retrieval or official accuracy. Baseline context must reproduce exactly before any reader calls.",
    }
    prepared = []
    for profile in report["results"]:
        for row in profile["details"]:
            saved = row["methods"]["prme_product"]
            if "candidate_snapshots" not in saved:
                raise ValueError("Replay must retain candidate snapshots")
            candidates = [RetrievalCandidate.model_validate(c) for c in saved["candidate_snapshots"]]
            original = [c.model_dump(mode="json") for c in candidates]
            control = pack_context(candidates, config)
            if control.render() != saved["context"] or control.tokens_used != saved["tokens"]:
                raise ValueError("Baseline context does not reproduce; use the original packing implementation")
            # Single-threaded counterfactual: keep each candidate's actual scores
            # and paths intact. Only the density comparator is substituted.
            with patch("prme.retrieval.packing.compute_str", lambda candidate: candidate.composite_score):
                candidate = pack_context(candidates, config)
            if original != [c.model_dump(mode="json") for c in candidates]:
                raise ValueError("Packing mutated frozen candidates")
            prepared.append((row, control, candidate))
    # Validate every input before sending any requests, even on a mixed report.
    for row, control, candidate in prepared:
        detail = {k: row[k] for k in ("question_id", "category", "reference_date")}
        detail["variants"] = {"density": packed_details(control), "score": packed_details(candidate)}
        for result in detail["variants"].values():
            if reader is not None:
                result["answer"] = await reader(row["question"], row["reference_date"], result["context"])
        output["details"].append(detail)
    if not prepared:
        raise ValueError("Replay must contain selected questions")
    output["complete"] = True
    output["baseline_reproduction_passed"] = True
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-reader", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("Timeout must be positive")
    raw = args.input.read_bytes()
    report = json.loads(raw)
    reader = None
    if args.with_reader:
        import urllib.request
        expected = {"system_prompt": GENERATION_SYSTEM_PROMPT, "temperature": 0,
                    "seed": 42, "num_ctx": 16384, "num_predict": 512}
        if any(report["reader"].get(name) != value for name, value in expected.items()):
            raise ValueError("Original reader prompt and sampling settings must match")
        with urllib.request.urlopen(args.base_url.rstrip("/") + "/api/tags", timeout=args.timeout) as response:
            models = json.load(response)["models"]
        if not any(m.get("digest") == report["reader"]["digest"]
                   and report["reader"]["model"] in (m.get("name"), m.get("model")) for m in models):
            raise ValueError("Original reader model digest is required")
        reader = local_reader(args.base_url, report["reader"]["model"], args.timeout)
    output = asyncio.run(compare(report, reader))
    output["input_sha256"] = hashlib.sha256(raw).hexdigest()
    output["original_source_commit"] = report["source"]["commit"]
    output["reader"] = report["reader"] if reader is not None else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"complete": output["complete"], "questions": len(output["details"]),
                      "baseline_reproduction_passed": output["baseline_reproduction_passed"]}))


if __name__ == "__main__":
    main()
