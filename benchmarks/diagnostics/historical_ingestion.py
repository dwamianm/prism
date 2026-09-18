"""Real-model historical ingestion and saved-plan recovery on one authored source.

A workflow diagnostic, not an accuracy benchmark or comparative measurement.
"""

import argparse
import asyncio
from datetime import datetime, timedelta
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import time

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.errors import ExtractionError
from prme.ingestion.pipeline import IngestionPipeline
from prme.types import NodeType


async def run(args):
    started = time.perf_counter()
    source_time = datetime.fromisoformat("2024-03-10T01:30:00-06:00")
    source = "Alice started using Rust yesterday."
    with tempfile.TemporaryDirectory(prefix="prme-historical-") as directory:
        root = Path(directory)
        config = PRMEConfig(
            database_url=None, encryption_enabled=False, db_path=str(root / "memory.duckdb"),
            vector_path=str(root / "vectors.usearch"), lexical_path=str(root / "lexical"),
            organizer={"opportunistic_enabled": False},
            embedding={"provider": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dimension": 384, "api_key": None},
            extraction={"provider": "ollama", "model": args.model, "base_url": args.base_url,
                        "api_key": None, "timeout": args.timeout, "max_retries": 1},
        )
        async with MemoryEngine.open(config) as engine:
            stage = engine._vector_index.stage

            async def fail_after_stage(*args, **kwargs):
                await stage(*args, **kwargs)
                raise OSError("injected failure after vector staging")

            engine._vector_index.stage = fail_after_stage
            engine._pipeline._retry_delays = ()
            try:
                await engine.ingest(source, user_id="alice", event_time=source_time, wait_for_extraction=True)
            except ExtractionError as failure:
                eid = failure.event_id
            else:
                raise AssertionError("Expected publication failure")
            event = await engine.get_event(eid, user_id="alice")
            plan = await engine._event_store.get_derivation_plan(eid, user_id="alice")
            saved = await engine.get_extraction(eid, user_id="alice")
            assert plan and saved and event.event_time == source_time
            facts = [n for n in plan.nodes if n.node_type == NodeType.FACT]
            assert facts and any(n.metadata.get("temporal_ref") == "yesterday" and
                                 n.event_time == source_time - timedelta(days=1) for n in facts)
            assert await engine.get_event_nodes(eid, user_id="alice") == []
        async with MemoryEngine.open(config) as engine:
            async def forbidden(*args, **kwargs):
                raise AssertionError("Saved-plan recovery cannot infer")
            engine._pipeline._extraction_provider.extract = forbidden
            engine._vector_index._provider.embed = forbidden
            await engine.retry_extraction(eid, user_id="alice")
            result = await engine.process_extractions(user_id="alice", budget_ms=5000)
            assert (result.processed, result.pending, result.failed) == (1, 0, 0)
            nodes = await engine.get_event_nodes(eid, user_id="alice")
            assert {n.id: n.event_time for n in nodes} == {n.id: n.event_time for n in plan.nodes}
            assert await engine.get_event(eid, user_id="alice") == event
            assert await engine.get_event(eid, user_id="bob") is None
        return {
            "passed": True, "provider": saved.provider, "model": saved.model,
            "source_time": source_time.isoformat(), "expected_fact_time": (source_time - timedelta(days=1)).isoformat(),
            "fact_times": [n.event_time.isoformat() for n in facts],
            "prepared_nodes": len(plan.nodes), "elapsed_seconds": round(time.perf_counter() - started, 3),
            "pipeline_sha256": hashlib.sha256(Path(inspect.getfile(IngestionPipeline)).read_bytes()).hexdigest(),
            "package_path": str(Path(inspect.getfile(IngestionPipeline)).resolve()),
            "checks": ["real local extraction preserves relative reference", "historical source anchors new plan",
                       "no partial graph after staged failure", "restart preserves exact saved dates without providers",
                       "source admission timestamp and owner boundary survive recovery"],
            "limits": "One authored local-model workflow; no extraction accuracy, calendar ambiguity or competitive claim.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("Timeout must be positive")
    try:
        if args.worker:
            report = asyncio.run(run(args))
        else:
            from benchmarks.diagnostics._process import run_diagnostic
            report = run_diagnostic("benchmarks.diagnostics.historical_ingestion", args)
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__, "limits": "Incomplete workflow; no success claim."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if not args.worker:
        print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
