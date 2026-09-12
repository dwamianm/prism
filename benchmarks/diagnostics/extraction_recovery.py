"""Exercise real local extraction, an indexing fault, and journal reuse.

Uses a temporary pack and synthetic source. Requires Ollama plus the selected
model. This is a workflow check, not an extraction accuracy benchmark.
"""

import argparse
import asyncio
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import time

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.errors import ExtractionError
from prme.ingestion.pipeline import IngestionPipeline


async def run(args):
    with tempfile.TemporaryDirectory(prefix="prme-live-journal-") as directory:
        root = Path(directory)
        (root / "lexical").mkdir()
        config = PRMEConfig(
            database_url=None, encryption_enabled=False,
            db_path=str(root / "memory.duckdb"), vector_path=str(root / "vectors.usearch"),
            lexical_path=str(root / "lexical"), organizer={"opportunistic_enabled": False},
            embedding={"provider": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dimension": 384, "api_key": None},
            extraction={"provider": "ollama", "model": args.model, "base_url": args.base_url,
                        "api_key": None, "timeout": args.timeout, "max_retries": 1},
        )
        started = time.perf_counter()
        calls = writes = 0
        calls_at_fault = None
        provider_errors = []
        async with MemoryEngine.open(config) as engine:
            pipeline = engine._pipeline
            pipeline._retry_delays = (0, 0, 0)
            extract = pipeline._extraction_provider.extract
            index = engine._vector_index.index

            async def counted(*a, **kw):
                nonlocal calls
                calls += 1
                try:
                    return await extract(*a, **kw)
                except Exception as exc:
                    provider_errors.append(type(exc).__name__)
                    raise

            async def failed_once(*a, **kw):
                nonlocal writes, calls_at_fault
                writes += 1
                if writes == 1:
                    calls_at_fault = calls
                    raise RuntimeError("injected downstream index failure")
                return await index(*a, **kw)

            pipeline._extraction_provider.extract = counted
            engine._vector_index.index = failed_once
            try:
                event_id = await engine.ingest(
                    "The Aster service uses PostgreSQL. Production credentials must never be used in staging.",
                    user_id="alice", wait_for_extraction=True,
                )
            except ExtractionError as failure:
                event_id = failure.event_id

            async def wait_retries():
                while pipeline._retry_tasks:
                    await asyncio.gather(*list(pipeline._retry_tasks.values()))
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_retries(), args.timeout + 30)
            saved = await engine.get_extraction(event_id, user_id="alice")
            assert saved is not None and saved.result["facts"]
            # Earlier provider/schema failures may legitimately use retries.
            # The storage recovery contract starts at the journal boundary.
            assert calls == calls_at_fault and writes > 1
            assert await engine.get_event_nodes(event_id, user_id="alice")
            first = saved.model_dump(mode="json")
        async with MemoryEngine.open(config) as engine:
            saved = await engine.get_extraction(event_id, user_id="alice")
            assert saved.model_dump(mode="json") == first
            source = await engine.get_event(event_id, user_id="alice")

            async def forbidden(*a, **kw):
                raise AssertionError("restart must reuse saved extraction")

            engine._pipeline._extraction_provider.extract = forbidden
            cached = await engine._pipeline._extract_or_load(source)
            assert cached.model_dump(mode="json") == saved.result
            assert await engine.get_extraction(event_id, user_id="bob") is None
        return {
            "passed": True,
            "pipeline_sha256": hashlib.sha256(Path(inspect.getfile(IngestionPipeline)).read_bytes()).hexdigest(),
            "provider": saved.provider, "model": saved.model, "provider_calls": calls,
            "provider_calls_at_index_fault": calls_at_fault,
            "provider_errors": provider_errors,
            "index_attempts": writes, "facts": len(saved.result["facts"]),
            "elapsed_seconds": time.perf_counter() - started,
            "checks": ["real local structured extraction", "journal before injected index failure",
                       "retry without repeated provider invocation", "identical extraction after restart", "scoped inspection"],
            "limits": "One synthetic integration workflow. Counts provider.extract invocations, not SDK HTTP retries. Not accuracy or full graph replay.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("Timeout must be positive")
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__,
                  "limits": "Incomplete workflow; no success or accuracy claim."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
