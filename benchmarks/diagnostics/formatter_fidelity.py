"""Authored formatter counterexamples; not held-out or competitive accuracy."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sys
import urllib.request
from uuid import UUID

from benchmarks.diagnostics._process import checked_report
from benchmarks.integrations.memconflict import local_reader
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT
from prme.models import MemoryNode
from prme.retrieval.context_formatter import format_for_llm
from prme.retrieval.models import RetrievalCandidate

PREFIX = "For this project, the deployment policy covering production regions and release authorization is recorded here. "
CASES = [
    {"id": "unsupported_origin", "question": "Which country did I move away from?", "hint": "default",
     "expected": "Unknown; grandmother's residence does not establish the user's former country.",
     "records": ["Grandma lives in Norway.", "I moved away from my home country last year.",
                 "I visit grandma in winter.", "I prefer black coffee."]},
    {"id": "overlapping_children", "question": "How many distinct children are explicitly identified in these records?", "hint": "aggregation",
     "expected": "Two: Jonah and Ada; the youngest child is Jonah, already counted.",
     "records": ["My son is Jonah.", "My daughter is Ada.", "Jonah is my youngest child.", "We enjoy board games."]},
    {"id": "conditional_update", "question": "Which database is currently used in production?", "hint": "knowledge_update",
     "expected": "PostgreSQL; evaluating Oracle is conditional and in the future.",
     "records": ["The project currently uses PostgreSQL in production.", "The frontend uses React.",
                 "The team works remotely.", {"text": "If license terms change, we might evaluate Oracle next year.", "epistemic": "hypothetical"}]},
    {"id": "assistant_guess", "question": "Where do I currently live?", "hint": "knowledge_update",
     "expected": "Kyoto; the later system guess is not a supported update.",
     "records": ["I live in Kyoto.", "I work as an engineer.", "I enjoy cycling.",
                 {"text": "Perhaps you live in Osaka.", "source": "system_inferred", "epistemic": "hypothetical"}]},
    {"id": "late_exception", "question": "Which deployment policy applies to the protected Orion region?", "hint": "default",
     "expected": "Manual approval; Orion is an exception to the general canary policy.",
     "records": [PREFIX + "Use automatic canary releases in all regions.",
                 PREFIX + "Exception: in the protected Orion region, use manual approval instead."]},
]
PROTOCOL = {"token_budget": 2048, "reference_date": "2024-06-15T00:00:00+00:00", "include_profile": True}


def render_cases():
    cases = []
    for index, case in enumerate(CASES):
        nodes = []
        for position, record in enumerate(case["records"]):
            record = {"text": record} if isinstance(record, str) else record
            stamp = datetime(2024, 6, 1, tzinfo=timezone.utc) + timedelta(days=position)
            nodes.append(RetrievalCandidate(node=MemoryNode(
                id=UUID(int=index * 100 + position + 1), user_id="counterexample", node_type="note",
                content=record["text"], source_type=record.get("source", "user_stated"),
                epistemic_type=record.get("epistemic", "asserted"),
                event_time=stamp, created_at=stamp, updated_at=stamp, valid_from=stamp,
            ), composite_score=1 - position * .01))
        context = format_for_llm(
            nodes, case["question"], context_hint=case["hint"],
            question_date=datetime.fromisoformat(PROTOCOL["reference_date"]),
            token_budget=PROTOCOL["token_budget"], include_profile=PROTOCOL["include_profile"],
        )
        cases.append({"id": case["id"], "question": case["question"], "expected": case["expected"], "context": context})
    return {
        "kind": "authored_formatter_counterexamples", "protocol": PROTOCOL,
        "fixture_sha256": hashlib.sha256(json.dumps(CASES, sort_keys=True).encode()).hexdigest(),
        "formatter_sha256": hashlib.sha256(Path(inspect.getfile(format_for_llm)).read_bytes()).hexdigest(),
        "cases": cases,
    }


async def compare(before, after, reader):
    for field in ("fixture_sha256", "protocol"):
        if before[field] != after[field]:
            raise ValueError(f"Counterexample {field} differs")
    old = {case["id"]: case for case in before["cases"]}
    new = {case["id"]: case for case in after["cases"]}
    if not old or set(old) != set(new) or len(old) != len(before["cases"]) or len(new) != len(after["cases"]):
        raise ValueError("Cases must have unique matching identities")
    if any(old[key][field] != new[key][field] for key in old for field in ("question", "expected")):
        raise ValueError("Questions and evaluator expectations must match")
    results = []
    for key in old:
        variants = {}
        for name, case in (("before", old[key]), ("after", new[key])):
            variants[name] = {"context": case["context"], "answer": await reader(
                case["question"], before["protocol"]["reference_date"], case["context"],
            )}
        results.append({"id": key, "question": old[key]["question"], "expected": old[key]["expected"], "variants": variants})
    return {"passed": True, "kind": "authored_formatter_reader_comparison", "cases": results,
            "fixture_sha256": before["fixture_sha256"], "protocol": before["protocol"],
            "before_formatter_sha256": before["formatter_sha256"], "after_formatter_sha256": after["formatter_sha256"],
            "accuracy": None, "judge_status": "not_run",
            "limits": "Five authored counterexamples motivated by code inspection, created after the production edit. Unjudged reader outputs, not independent accuracy or competitive evidence."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if bool(args.before) != bool(args.after) or args.timeout <= 0:
        parser.error("Pass both before/after reports and a positive timeout")
    if not args.before:
        report = render_cases()
    elif not args.worker:
        report = checked_report([sys.executable, "-m", "benchmarks.diagnostics.formatter_fidelity",
                                 *sys.argv[1:], "--worker"], timeout=args.timeout * len(CASES) * 2 + 60)
    else:
        with urllib.request.urlopen(args.base_url.rstrip("/") + "/api/tags", timeout=args.timeout) as response:
            models = json.load(response)["models"]
        matches = [m for m in models if args.model in (m.get("model"), m.get("name"))]
        if len(matches) != 1 or not matches[0].get("digest"):
            raise ValueError("A unique local model digest is required")
        before, after = json.loads(args.before.read_text()), json.loads(args.after.read_text())
        report = asyncio.run(compare(before, after, local_reader(args.base_url, args.model, args.timeout)))
        report["reader"] = {"model": args.model, "digest": matches[0]["digest"],
                            "system_prompt": GENERATION_SYSTEM_PROMPT, "temperature": 0,
                            "seed": 42, "num_ctx": 16384, "num_predict": 512}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if report.get("passed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
