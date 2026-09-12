"""Exercise source-grounded entity references with a real local model.

Three synthetic cases; structural integrity does not establish semantic accuracy.
"""

import argparse
import asyncio
import json
import hashlib
import tempfile
import time
from pathlib import Path
from pydantic import ValidationError
from prme import MemoryEngine, PRMEConfig
from prme.types import NodeType, EdgeType


def failure_details(exc):
    """Keep schema feedback for synthetic probes without SDK messages or inputs."""
    pending, seen, kinds, validation = [exc], set(), [], []
    while pending and len(seen) < 24:
        error = pending.pop(0)
        if id(error) in seen:
            continue
        seen.add(id(error))
        kinds.append(type(error).__name__)
        if isinstance(error, ValidationError):
            validation.extend(error.errors(include_input=False, include_context=False, include_url=False))
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        pending.extend(attempt.exception for attempt in getattr(error, "failed_attempts", [])[:3])
    return {"error_chain": kinds, "validation_errors": validation}


def assess_claims(case, source, nodes, edges, *, expected_kinds=None, allowed_epistemic=None):
    """Assess every claim; an extra FACT cannot hide a misclassified preference."""
    claims = [n for n in nodes if n.node_type in {NodeType.FACT, NodeType.PREFERENCE, NodeType.DECISION}]
    linked = bool(claims) and all(
        n.metadata.get("subject_link_status") == "resolved"
        and any(e.edge_type == EdgeType.HAS_FACT and e.target_id == n.id for e in edges)
        for n in claims
    )
    # All three fixtures describe facts, including the hypothetical usage case.
    if expected_kinds is None:
        kinds_ok = bool(claims) and all(n.node_type == NodeType.FACT for n in claims)
        objects_present = bool(claims)
    else:
        expected = {obj.casefold(): kind for obj, kind in expected_kinds.items()}
        actual_objects = {n.metadata.get("object", "").casefold() for n in claims}
        objects_present = expected.keys() <= actual_objects
        kinds_ok = bool(claims) and all(
            n.node_type.value == expected.get(n.metadata.get("object", "").casefold())
            for n in claims
        )
    allowed = allowed_epistemic or ({"conditional", "hypothetical"} if case == "conditional" else None)
    temporal_ok = allowed is None or all(n.epistemic_type.value in allowed for n in claims)
    associations = all(e.edge_type in {EdgeType.HAS_FACT, EdgeType.MENTIONS} for e in edges)
    preserved = bool(claims) and all(n.content == source for n in claims)
    return {
        "passed": linked and objects_present and kinds_ok and temporal_ok and associations and preserved,
        "expected_objects_present": objects_present,
        "expected_claim_kinds": kinds_ok,
        "epistemic_qualifications_preserved": temporal_ok,
        "subject_links_complete": linked,
        "association_edges_only": associations,
        "full_source_preserved": preserved,
        "claim_node_types": [n.node_type.value for n in claims],
        "claim_epistemic_types": [n.epistemic_type.value for n in claims],
    }


async def run(args, *, cases=None):
    started = time.perf_counter()
    cases = cases or [
        ("service", "The Aster service uses PostgreSQL.", {}),
        ("namesake", "Jordan, the engineer, lives in Jordan, the country.", {}),
        (
            "conditional",
            "Alice might use PostgreSQL for the Atlas project if the evaluation succeeds.", {},
        ),
    ]
    reports = []
    with tempfile.TemporaryDirectory(prefix="prme-reference-quality-") as directory:
        root = Path(directory)
        (root / "lexical").mkdir()
        config = PRMEConfig(
            database_url=None,
            encryption_enabled=False,
            db_path=str(root / "memory.duckdb"),
            vector_path=str(root / "vectors.usearch"),
            lexical_path=str(root / "lexical"),
            organizer={"opportunistic_enabled": False},
            embedding={
                "provider": "fastembed",
                "model_name": "BAAI/bge-small-en-v1.5",
                "dimension": 384,
                "api_key": None,
            },
            extraction={
                "provider": "ollama",
                "model": args.model,
                "base_url": args.base_url,
                "api_key": None,
                "timeout": args.timeout,
                "max_retries": 3,
            },
        )
        async with MemoryEngine.open(config) as engine:
            engine._pipeline._retry_delays = ()
            for case, source, expectation in cases:
                try:
                    eid = await engine.ingest(
                        source, user_id=case, wait_for_extraction=True
                    )
                    nodes = await engine.get_event_nodes(eid, user_id=case)
                    facts = [node for node in nodes if node.node_type == NodeType.FACT]
                    saved = await engine.get_extraction(eid, user_id=case)
                    plan = await engine._event_store.get_derivation_plan(
                        eid, user_id=case
                    )
                    reports.append(
                        {
                            "case": case,
                            **assess_claims(case, source, nodes, plan.edges, **expectation),
                            "expectation": expectation,
                            "edge_types": [edge.edge_type.value for edge in plan.edges],
                            "facts": len(facts),
                            "has_fact_edges": sum(
                                edge.edge_type == EdgeType.HAS_FACT
                                for edge in plan.edges
                            ),
                            "extraction": saved.result,
                            "fact_epistemic_types": [
                                node.epistemic_type.value for node in facts
                            ],
                            "materialization_policy": plan.materialization_policy,
                        }
                    )
                except Exception as exc:
                    reports.append(
                        {
                            "case": case,
                            "passed": False,
                            "error_type": type(exc).__name__,
                            **failure_details(exc),
                        }
                    )
    out = {
        "passed": all(r["passed"] for r in reports),
        "cases": reports,
        "limits": "Three synthetic structural-link checks, not semantic accuracy or competitive evidence.",
    }
    out["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT, _CitedExtractionResult
    out["prompt_sha256"] = hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode()).hexdigest()
    out["response_schema_sha256"] = hashlib.sha256(json.dumps(_CitedExtractionResult.model_json_schema(), sort_keys=True).encode()).hexdigest()
    out["cases_sha256"] = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()
    out["case_count"] = len(cases)
    out["cases_passed"] = sum(r["passed"] for r in reports)
    out["provider"] = "ollama"
    out["model"] = args.model
    out["provider_max_retries"] = 3
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker:
            report = asyncio.run(run(args))
        else:
            from benchmarks.diagnostics._process import run_diagnostic

            report = run_diagnostic("benchmarks.diagnostics.entity_references", args)
    except Exception as exc:
        report = {
            "passed": False,
            "error_type": type(exc).__name__,
            "limits": "Incomplete workflow; no success claim.",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if not args.worker:
        print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
