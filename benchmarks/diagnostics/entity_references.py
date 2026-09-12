"""Exercise source-grounded entity references with a real local model.

Three synthetic cases; structural integrity does not establish semantic accuracy.
"""

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path
from prme import MemoryEngine, PRMEConfig
from prme.types import NodeType, EdgeType


async def run(args):
    started = time.perf_counter()
    cases = [
        ("service", "The Aster service uses PostgreSQL."),
        ("namesake", "Jordan, the engineer, lives in Jordan, the country."),
        (
            "conditional",
            "Alice might use PostgreSQL for the Atlas project if the evaluation succeeds.",
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
            for case, source in cases:
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
                    facts_linked = all(
                        node.metadata.get("subject_link_status") == "resolved"
                        for node in facts
                    )
                    temporal_ok = case != "conditional" or all(
                        node.epistemic_type.value in {"conditional", "hypothetical"}
                        for node in facts
                    )
                    association_edges = all(
                        edge.edge_type in {EdgeType.HAS_FACT, EdgeType.MENTIONS}
                        for edge in plan.edges
                    )
                    sources_preserved = all(node.content == source for node in facts)
                    reports.append(
                        {
                            "case": case,
                            "passed": bool(facts) and facts_linked and temporal_ok and association_edges and sources_preserved,
                            "association_edges_only": association_edges,
                            "full_source_preserved": sources_preserved,
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
                        }
                    )
    out = {
        "passed": all(r["passed"] for r in reports),
        "cases": reports,
        "limits": "Three synthetic structural-link checks, not semantic accuracy or competitive evidence.",
    }
    out["elapsed_seconds"] = round(time.perf_counter() - started, 3)
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
