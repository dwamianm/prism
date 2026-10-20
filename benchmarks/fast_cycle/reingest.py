"""Stage "reingest": the store stage's memories stored plainly, re-derived with ``reingest()``, and the same questions twice.

``store()`` keeps a memory's text and nothing derived from it, so a memory stored
before an engine change that improves extraction and links stays as it was
(prism-monorepo #278). ``MemoryEngine.reingest()`` is the supported way to
re-derive it, and this stage measures what it does on the store stage's
fixture, in minutes, so a later engine change can be checked the same way:

1. A fresh engine with ``enable_reingest`` on and the extraction settings of the
   ingest stage (``deepseek-v4.1-flash:cloud`` through Ollama, batched at the
   default token target) stores the 75 memories with plain ``store()``, as the
   store stage does, and answers the 17 questions. Those are the ranks before.
2. A dry run says how many requests the re-derivation needs, and
   ``reingest()`` runs it. Its requests go through the extraction cache that the
   store-extract stage uses, so a request the cache holds is replayed, and the
   stage's own entries join the committed cache with ``--update-cache``. A
   memory with no ``session_id`` is its own request, as in store-extract.
3. A second ``reingest()`` must do nothing: that is the stage's check of
   idempotency, a measure that is 0 when it holds.
4. The same 17 questions are asked again. Those are the ranks after, and the
   stage's measures are the store stage's, over the pack as it is then.

The memories' own events and raw notes are never written by a re-derivation, so
the plain store's records are the same before and after; the stage counts them
to show it. What it cannot show: the fixture is one made-up user's, a rank is
the place of the first record that holds the answer, and a run that asked the
model differs from the next, like any live stage.
"""
from __future__ import annotations

from pathlib import Path
import time

from benchmarks.diagnostics.chat_probe import _records
from benchmarks.diagnostics.extracted_packs import _drain_sources, _Usage, build_config, model_identity
from benchmarks.fast_cycle import ingest, store, store_extract
from prme import MemoryEngine
from prme.types import NodeType, Scope

STAGE = "reingest"


def _answers(questions: list[dict], reports: list[dict]) -> list[dict]:
    return [{"id": question["id"], "question": question["question"], **{
        key: report[key] for key in ("answer_rank", "answer_in_context", "stale_in_context", "stale_above_answer",
                                     "tokens_used", "repeated_records", "lexical_candidates", "graph_candidates",
                                     "top_results")}} for question, report in zip(questions, reports)]


async def run(work: Path, *, cache_dir: Path = ingest.CACHE_DIR, use_cache: bool = True, overrides: dict | None = None,
              model: str = ingest.DEFAULT_MODEL, fixture_path: Path = store.DEFAULT_FIXTURE, prepare=None) -> dict:
    """Store the fixture's memories plainly, re-derive them, ask its questions before and after.

    ``use_cache=False`` starts from an empty cache, so every request goes to the
    model. ``prepare(engine)``, for tests, runs before the first store.
    """
    fixture = store.load_fixture(fixture_path)
    identity = model_identity(model, cloud=model.endswith(":cloud"))
    working = ingest.working_cache(work, cache_dir, use_committed=use_cache)
    usage_by_stage = ingest._read_usage(working)
    # The requests are the store-extract stage's, so its recorded usage prices a replay of one.
    recorded = {**usage_by_stage.get(store_extract.STAGE, {}), **usage_by_stage.get(STAGE, {})}
    settings = {**(overrides or {}), "enable_reingest": True}
    pack, owner = work / "pack", fixture["owner"]
    (pack / "lexical_index").mkdir(parents=True, exist_ok=True)
    questions = fixture["key"]["questions"]
    async with MemoryEngine.open(build_config(pack, settings, model=model)) as engine:
        if prepare is not None:
            prepare(engine)
        usage = _Usage(engine)
        cache = store_extract.StoredTurnCache(working)
        cache.wrap(engine._pipeline._extraction_provider)
        meter = store_extract.RequestMeter(engine._pipeline._extraction_provider, cache, usage, recorded)
        started = time.perf_counter()
        for memory in fixture["memories"]:
            await engine.store_with_receipt(
                memory["content"], user_id=owner, role=memory["role"], speaker=memory.get("speaker"),
                node_type=NodeType(memory["node_type"]), scope=Scope(memory["scope"]),
                metadata={"source": "synthetic-demo", "session": memory["session"]})
        stored = time.perf_counter() - started
        await _drain_sources(engine, owner)
        plain_nodes, plain_edges, _ = await _records(engine, owner)
        events = await engine.get_events(user_id=owner, limit=len(fixture["memories"]) + 1)
        raw = {node.id: node.model_dump_json() for node in await engine.query_nodes(user_id=owner, limit=1000)}
        before = [store.question_report(question, await engine.retrieve(question["question"], user_id=owner))
                  for question in questions]
        dry = await engine.reingest(owner, dry_run=True)
        reingested = time.perf_counter()
        first = await engine.reingest(owner)
        reingested = time.perf_counter() - reingested
        second = await engine.reingest(owner)
        await _drain_sources(engine, owner)
        nodes, edges, _ = await _records(engine, owner)
        # What the sources and the nodes stored with them were, and are: a re-derivation never writes them. Taken before
        # the questions, which retrieval may leave marks of its own on.
        raw_after = {node.id: node.model_dump_json() for node in await engine.query_nodes(user_id=owner, limit=1000)
                     if node.id in raw}
        events_after = await engine.get_events(user_id=owner, limit=len(fixture["memories"]) + 1)
        asked = time.perf_counter()
        after = [store.question_report(question, await engine.retrieve(question["question"], user_id=owner))
                 for question in questions]
        asked = time.perf_counter() - asked
    usage_now = ingest._read_usage(working)
    usage_now.setdefault(STAGE, {}).update(meter.live)
    ingest._write_usage(working, usage_now)
    measures = store.store_measures(after, nodes, edges, len(fixture["memories"]), fixture["key"]["entities"])
    measures.update(store_extract.extraction_measures(
        after, nodes, meter, {"reingest": "complete" if not first.remaining else "incomplete"}))
    measures["extractions_incomplete"] = first.remaining
    measures["reingest_second_run_processed"] = second.processed
    return {"backend": "duckdb", "fixture": fixture["name"], "fixture_sha256": store.fixture_digest(fixture_path),
            "settings": settings, "session_ids": False, "calls": len(fixture["memories"]),
            "nodes": len(nodes), "edges": len(edges), "plain_nodes": len(plain_nodes), "plain_edges": len(plain_edges),
            "mode": "live" if cache.misses else "replay", "model": model,
            "server_version": identity.get("server_version"), "live_calls": cache.misses, "cache_hits": cache.hits,
            "unreproduced": cache.unreproduced, "store_seconds": stored, "reingest_seconds": reingested,
            "retrieve_seconds": asked, "measures": measures, "working_cache": str(working),
            "reingest": {
                "dry_run": dry.model_dump(mode="json"), "first": first.model_dump(mode="json"),
                "second": second.model_dump(mode="json"),
                "sources_unchanged": sorted(event.model_dump_json() for event in events)
                == sorted(event.model_dump_json() for event in events_after)
                and raw_after == raw and len(raw) == len(fixture["memories"]),
            },
            "before": _answers(questions, before),
            "questions": _answers(questions, after)}
