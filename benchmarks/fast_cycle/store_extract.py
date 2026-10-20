"""Stage "store-extract": the store stage's memories stored with ``extract=True``, and the same questions.

``store(..., extract=True)`` (#260) saves the memory at once, as the store
stage's ``store()`` does, and queues the batched extraction ``ingest()`` runs, so
the stored memory also gets entities and claims. Whether that improves what
``retrieve()`` returns is a hypothesis, and this stage measures it on the store
stage's fixture: a fresh engine with ``enable_store_extraction`` on, the
extraction settings of the ingest stage (``deepseek-v4.1-flash:cloud`` through
Ollama, batched at the default token target), the same 75 memories with the
same role, speaker, node type, scope and metadata, and the same 17 questions.

Choices, and why:

- The background extraction is drained by hand (``process_extractions()``),
  and the engine's 30 second timer is set aside, so a run composes the same
  requests whatever the machine's speed. The hosted service lets the timer
  decide; a store with no ``session_id`` goes alone either way.
- A memory has no ``session_id`` in the store stage, so each one is its own
  extraction request. ``session_ids`` stores the fixture's five sessions as
  ``session_id`` instead, which lets the memories of a session share requests
  inside the engine's token target, as the hosted estimate (prism-monorepo
  #269) assumes. It also puts the session on each stored node, so the store
  stage with ``session_ids`` is the control for that variant.
- Requests go through the ingest stage's extraction cache, which replays a
  stored model response while the request is unchanged. A stored memory has the
  wall-clock time of its store, and a batched request shows each turn's time to
  the minute, so the cache key leaves the time out; a replay shows the model's
  recorded answer to the request as it was first sent.
- The cache keeps responses, not usage. ``usage.json`` keeps the tokens of each
  request the model answered live, under the stage's name, so a replay reports
  the cost of the run that filled the cache and a replayed request without a
  record leaves the total unknown instead of understating it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
import time

from benchmarks.diagnostics.chat_probe import _records
from benchmarks.diagnostics.extracted_packs import (
    _ALL, ExtractionCache, _drain_sources, _finish_extractions, _Usage, build_config, model_identity,
)
from benchmarks.fast_cycle import ingest, store
from prme import MemoryEngine
from prme.types import NodeType, Scope

STAGE = "store-extract"
SESSION_STAGE = "store-extract-session"
# Longer than any run: the engine's 30 second timer must not split a run's requests.
NO_TIMER_SECONDS = 86_400.0


class StoredTurnCache(ExtractionCache):
    """The extraction cache, keyed by a batched request without its turns' wall-clock times."""

    @staticmethod
    def _batch_key(provider, turns, memories=()) -> str:
        return ExtractionCache._batch_key(provider, [dataclasses.replace(turn, time=None) for turn in turns], memories)


class RequestMeter:
    """Requests that went through the cache and what they cost: live tokens, or those recorded for the request."""

    def __init__(self, provider, cache: StoredTurnCache, usage: _Usage, recorded: dict) -> None:
        self.requests, self.tokens, self.known = 0, 0, True
        self.live: dict[str, dict] = {}
        self._cache, self._usage, self._recorded, self._provider = cache, usage, recorded, provider
        self._cached = provider.extract_batch
        provider.extract_batch = self._extract_batch
        # With the window or fact text resolution on, the pipeline asks for one turn per request.
        self._cached_turn = provider.extract
        provider.extract = self._extract

    async def _extract_batch(self, turns, **extra):
        key = self._cache._batch_key(self._provider, turns, extra.get("memories", ()))
        misses, before = self._cache.misses, self._usage.snapshot()
        self.requests += 1
        try:
            return await self._cached(turns, **extra)
        finally:
            self._count(key, misses, before)

    def _count(self, key: str, misses: int, before: tuple[int, int, int]) -> None:
        if self._cache.misses > misses:
            calls, prompt, completion = (now - then for now, then in zip(self._usage.snapshot(), before))
            self.live[key] = {"calls": calls, "prompt_tokens": prompt, "completion_tokens": completion}
            self.tokens += prompt + completion
        else:
            entry = self._recorded.get(key)
            self.known = self.known and entry is not None
            self.tokens += entry["prompt_tokens"] + entry["completion_tokens"] if entry else 0

    async def _extract(self, content, *, role="user", context=(), resolve=None, speaker=None):
        window = tuple(line for line in context if line and line.strip())
        key = self._cache._key(self._provider, content, role, window, resolve)
        misses, before = self._cache.misses, self._usage.snapshot()
        self.requests += 1
        try:
            return await self._cached_turn(content, role=role, context=context, resolve=resolve, speaker=speaker)
        finally:
            self._count(key, misses, before)

    @property
    def total_tokens(self) -> int | None:
        return self.tokens if self.known else None


async def drain_extractions(engine, owner: str, event_ids: list[str]) -> dict[str, str]:
    """Run every queued extraction job, retry the failed ones, and return each event's final status."""
    for _ in range(2):
        result = await engine.process_extractions(user_id=owner, limit=2 * len(event_ids), budget_ms=_ALL)
        if not result.pending:
            break
    return await _finish_extractions(engine, owner, event_ids)


def extraction_measures(questions: list[dict], nodes: list[dict], meter: RequestMeter, statuses: dict[str, str]) -> dict:
    """What the extraction cost and made, by name; the store measures cover what retrieval then returned."""
    return {
        "extraction_requests": meter.requests,
        "extraction_tokens": meter.total_tokens,
        "extractions_incomplete": sum(status != "complete" for status in statuses.values()),
        "entities_made": sum(node["type"] == "entity" and node["state"] in store.ACTIVE for node in nodes),
        "graph_candidates_per_question": sum(question["graph_candidates"] for question in questions) / len(questions),
    }


async def run(work: Path, *, cache_dir: Path = ingest.CACHE_DIR, use_cache: bool = True, overrides: dict | None = None,
              model: str = ingest.DEFAULT_MODEL, session_ids: bool = False,
              fixture_path: Path = store.DEFAULT_FIXTURE, prepare=None) -> dict:
    """Store the fixture's memories with extraction, ask its questions; returns the stage's measures and details.

    ``use_cache=False`` starts from an empty cache, so every request goes to
    the model. ``prepare(engine)``, for tests, runs before the first store.
    """
    stage = SESSION_STAGE if session_ids else STAGE
    fixture = store.load_fixture(fixture_path)
    identity = model_identity(model, cloud=model.endswith(":cloud"))
    working = ingest.working_cache(work, cache_dir, use_committed=use_cache)
    recorded = ingest._read_usage(working).get(stage, {})
    settings = {**(overrides or {}), "enable_store_extraction": True}
    pack, owner = work / "pack", fixture["owner"]
    (pack / "lexical_index").mkdir(parents=True, exist_ok=True)
    async with MemoryEngine.open(build_config(pack, settings, model=model)) as engine:
        if prepare is not None:
            prepare(engine)
        usage = _Usage(engine)
        cache = StoredTurnCache(working)
        cache.wrap(engine._pipeline._extraction_provider)
        meter = RequestMeter(engine._pipeline._extraction_provider, cache, usage, recorded)
        engine._pipeline._batch_flush_seconds = NO_TIMER_SECONDS
        started = time.perf_counter()
        event_ids = []
        for memory in fixture["memories"]:
            receipt = await engine.store_with_receipt(
                memory["content"], user_id=owner, role=memory["role"], speaker=memory.get("speaker"),
                node_type=NodeType(memory["node_type"]), scope=Scope(memory["scope"]),
                session_id=memory["session"] if session_ids else None,
                metadata={"source": "synthetic-demo", "session": memory["session"]}, extract=True)
            event_ids.append(str(receipt.event_id))
        stored = time.perf_counter() - started
        extracted = time.perf_counter()
        statuses = await drain_extractions(engine, owner, event_ids)
        extracted = time.perf_counter() - extracted
        await _drain_sources(engine, owner)
        nodes, edges, _ = await _records(engine, owner)
        questions = []
        asked = time.perf_counter()
        for question in fixture["key"]["questions"]:
            questions.append(store.question_report(question, await engine.retrieve(question["question"], user_id=owner)))
        asked = time.perf_counter() - asked
    usage_now = ingest._read_usage(working)
    usage_now.setdefault(stage, {}).update(meter.live)
    ingest._write_usage(working, usage_now)
    measures = store.store_measures(questions, nodes, edges, len(fixture["memories"]), fixture["key"]["entities"])
    measures.update(extraction_measures(questions, nodes, meter, statuses))
    return {"backend": "duckdb", "fixture": fixture["name"], "fixture_sha256": store.fixture_digest(fixture_path),
            "settings": settings, "session_ids": session_ids, "calls": len(fixture["memories"]),
            "nodes": len(nodes), "edges": len(edges), "mode": "live" if cache.misses else "replay", "model": model,
            "server_version": identity.get("server_version"), "live_calls": cache.misses, "cache_hits": cache.hits,
            "unreproduced": cache.unreproduced, "store_seconds": stored, "extract_seconds": extracted,
            "retrieve_seconds": asked, "measures": measures, "working_cache": str(working),
            "questions": [{"id": question["id"], "question": question["question"], **{
                key: report[key] for key in ("answer_rank", "answer_in_context", "stale_in_context",
                                             "stale_above_answer", "tokens_used", "repeated_records",
                                             "lexical_candidates", "graph_candidates", "top_results")}}
                          for question, report in zip(fixture["key"]["questions"], questions)]}
