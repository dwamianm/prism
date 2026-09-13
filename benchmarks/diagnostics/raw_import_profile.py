"""Attribute raw import time to durable admission and indexing on one host.

This measures existing APIs on authored data, not competing products or semantic
equivalence of independently created node IDs/admission times.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import inspect
from pathlib import Path
import sys
import tempfile
import time

from prme import MemoryEngine
from benchmarks.diagnostics._process import checked_report
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from benchmarks.diagnostics.hybrid_lexical import raw_config, embedding_identity


async def run(sources):
    texts = [
        f"Observation {i}: The cobalt telescope retains calibration records for "
        "thirty days. The nightly procedure checks the optical filter and records "
        "the observer name."
        for i in range(sources)
    ]
    source_time = datetime(2025, 4, 3, tzinfo=timezone.utc)
    assets = await embedding_identity()
    results = []
    with tempfile.TemporaryDirectory(prefix="prme-import-profile-") as temporary:
        for mode in ("deferred", "store"):
            directory = Path(temporary) / mode
            config = raw_config().model_copy(update={
                "db_path": str(directory / "memory.duckdb"),
                "vector_path": str(directory / "vectors.usearch"),
                "lexical_path": str(directory / "lexical"),
                "duckdb_threads": 1,
            })
            async with MemoryEngine.open(config) as engine:
                await engine._vector_index._provider.embed(["Embedding warm-up."])
                append = engine._event_store.append
                admission_seconds = 0.0
                admission_calls = 0
                commits = 0

                async def measured_append(*args, **kwargs):
                    nonlocal admission_seconds, admission_calls
                    started = time.perf_counter()
                    try:
                        return await append(*args, **kwargs)
                    finally:
                        admission_seconds += time.perf_counter() - started
                        admission_calls += 1

                original_writer = engine._lexical_index._ensure_writer

                class CountedWriter:
                    def __init__(self, writer):
                        self.writer = writer

                    def __getattr__(self, name):
                        return getattr(self.writer, name)

                    def commit(self):
                        nonlocal commits
                        commits += 1
                        return self.writer.commit()

                engine._event_store.append = measured_append
                engine._lexical_index._ensure_writer = lambda: CountedWriter(original_writer())
                accept = engine.ingest_fast if mode == "deferred" else engine.store
                started = time.perf_counter()
                ids = [
                    await accept(text, user_id="authored-import", event_time=source_time,
                                 session_id=f"episode-{i // 4}",
                                 role="user" if i % 2 else "assistant",
                                 metadata={"source_turn": str(i)})
                    for i, text in enumerate(texts)
                ]
                accept_seconds = time.perf_counter() - started
                started = time.perf_counter()
                outcome = await engine.process_pending(user_id="authored-import", budget_ms=60000)
                processing_seconds = time.perf_counter() - started
                if outcome.pending or outcome.failed or admission_calls != sources:
                    raise ValueError("Import did not complete every source")
                for i, eid in enumerate(ids):
                    event = await engine.get_event(eid, user_id="authored-import")
                    nodes = await engine.get_event_nodes(eid, user_id="authored-import")
                    if event.content != texts[i] or event.event_time != source_time:
                        raise ValueError("Source text or clock changed")
                    if len(nodes) != 1 or nodes[0].content != texts[i] or nodes[0].event_time != source_time:
                        raise ValueError("Raw node text or clock changed")
                    if (await engine.processing_status(eid, user_id="authored-import")).status != "complete":
                        raise ValueError("Source has unacknowledged work")
                response = await engine.retrieve("How long are calibration records retained?",
                                                 user_id="authored-import", token_budget=4096)
                if not response.results or not response.bundle.render():
                    raise ValueError("Imported sources are not retrievable")
                results.append({
                    "mode": mode, "source_count": sources,
                    "accept_seconds": accept_seconds,
                    "processing_seconds": processing_seconds,
                    "total_seconds": accept_seconds + processing_seconds,
                    "event_append_seconds": admission_seconds,
                    "event_append_calls": admission_calls,
                    "lexical_commits": commits,
                    "source_and_node_fidelity_verified": sources,
                    "retrieval_nonempty": True,
                })
    if await embedding_identity() != assets:
        raise ValueError("Embedding assets changed during the diagnostic")
    return {
        "passed": True, "sources": sources, "trials": results,
        "source_texts_sha256": digest(canonical(texts)), "embedding_assets": assets,
        "package_path": str(Path(inspect.getfile(MemoryEngine)).resolve()),
        "engine_sha256": digest(Path(inspect.getfile(MemoryEngine)).read_bytes()),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "limits": "One authored history per existing API, one host, warmed model, "
                  "fixed deferred-then-store order under concurrent reader load. "
                  "Startup, validation reads, final shutdown and retrieval excluded "
                  "from timings. No competitive speed, statistical, or context parity claim.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=int, default=128)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.sources <= 500 or args.output.exists():
        raise ValueError("Use 1–500 sources and a fresh output path")
    if args.worker:
        report = asyncio.run(run(args.sources))
    else:
        report = checked_report([
            sys.executable, "-m", "benchmarks.diagnostics.raw_import_profile",
            "--worker", "--sources", str(args.sources),
        ], timeout=args.sources + 180)
    write(args.output, report)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
