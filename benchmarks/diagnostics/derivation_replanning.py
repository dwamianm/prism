"""Check real local-model ingestion followed by explicit stale-plan recovery.

Uses only synthetic text in a temporary pack. This is a fault-injection workflow,
not an extraction accuracy or performance benchmark.
"""

import argparse
import asyncio
import hashlib
import inspect
import importlib.metadata
import os
import json
from pathlib import Path
import tempfile
import time

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.errors import ExtractionError
from prme.ingestion.pipeline import IngestionPipeline
from prme.storage.embedding import FastEmbedProvider


async def run(args):
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="prme-replan-probe-") as directory:
        root = Path(directory)
        (root / "lexical").mkdir()
        config = PRMEConfig(
            database_url=None, encryption_enabled=False, db_path=str(root / "memory.duckdb"),
            vector_path=str(root / "vectors.usearch"), lexical_path=str(root / "lexical"),
            organizer={"opportunistic_enabled": False},
            embedding={"provider": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dimension": 384, "api_key": None},
            extraction={"provider": "ollama", "model": args.model, "base_url": args.base_url,
                        "api_key": None, "timeout": args.timeout, "max_retries": 1},
        )
        async with MemoryEngine.open(config) as engine:
            engine._pipeline._retry_delays = ()
            source = "Alice uses PostgreSQL for the Atlas project."
            first_event = await engine.ingest(source, user_id="alice", wait_for_extraction=True)
            assert (await engine.extraction_status(first_event, user_id="alice")).status == "complete"
            commit = engine._graph_store.commit_derivation
            async def change_dependency(plan, *, claim=None):
                assert plan.references, "Second ingestion must reuse a memory to exercise this fault"
                node = plan.references[0]
                await engine._graph_store.update_node(str(node.id), metadata={**(node.metadata or {}), "maintenance_note": "changed"})
                return await commit(plan, claim=claim)
            engine._graph_store.commit_derivation = change_dependency
            try:
                await engine.ingest(source, user_id="alice", wait_for_extraction=True)
            except ExtractionError as failure:
                event_id = failure.event_id
            else:
                raise AssertionError("Expected a stale dependency failure")
            status = await engine.extraction_status(event_id, user_id="alice")
            assert status.status == "failed" and status.last_error == "StaleDerivationPlanError"
            old = await engine._event_store.get_derivation_plan(event_id, user_id="alice")
            saved = await engine.get_extraction(event_id, user_id="alice")
            assert old and saved and saved.result["facts"]
            assert await engine.get_event_nodes(event_id, user_id="alice") == []
        async with MemoryEngine.open(config) as engine:
            async def forbidden(*args, **kwargs):
                raise AssertionError("Replanning must reuse grounded extraction")
            engine._pipeline._extraction_provider.extract = forbidden
            queued = await engine.retry_extraction(event_id, user_id="alice", replan=True)
            assert queued.plan_revision == 2 and queued.plan_id is None
            result = await engine.process_extractions(user_id="alice")
            assert (result.processed, result.pending, result.failed) == (1, 0, 0)
            current = await engine._event_store.get_derivation_plan(event_id, user_id="alice")
            assert current.revision == 2 and current.id != old.id
            assert await engine._event_store.get_derivation_plan(event_id, user_id="alice", revision=1) == old
            assert await engine.get_extraction(event_id, user_id="alice") == saved
            assert await engine.extraction_status(event_id, user_id="bob") is None
            receipt = await engine._event_store.get_derivation_receipt(event_id, user_id="alice")
            assert receipt.plan_checksum == current.checksum
            assert {node.id for node in await engine.get_event_nodes(event_id, user_id="alice")} == {node.id for node in current.nodes}
            response = await engine.retrieve("Which database does Alice use?", user_id="alice")
            assert any("PostgreSQL" in candidate.node.content for candidate in response.results)
        return {
            "passed": True,
            "embedding_provider_sha256": hashlib.sha256(Path(inspect.getfile(FastEmbedProvider)).read_bytes()).hexdigest(),
            "onnxruntime_version": importlib.metadata.version("onnxruntime"),
            "onnx_telemetry_disabled": os.environ.get("ORT_DISABLE_TELEMETRY"),
            "pipeline_sha256": hashlib.sha256(Path(inspect.getfile(IngestionPipeline)).read_bytes()).hexdigest(),
            "package_path": str(Path(inspect.getfile(IngestionPipeline)).resolve()),
            "provider": saved.provider, "model": saved.model,
            "old_revision": old.revision, "committed_revision": current.revision,
            "facts": len(saved.result["facts"]), "prepared_nodes": len(current.nodes),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "checks": ["real local extraction with reused entity", "changed dependency rejects publication",
                       "public replan after restart without repeated extraction", "immutable original plan and extraction",
                       "scoped revision status", "receipt and exact artifact identities", "retrieval finds database fact"],
            "limits": "One synthetic workflow. Replanning may recompute embeddings. No extraction accuracy or comparative claim.",
        }


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
            report = run_diagnostic("benchmarks.diagnostics.derivation_replanning", args)
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
