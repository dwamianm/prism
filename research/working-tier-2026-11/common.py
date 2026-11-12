"""Shared parts of the write rate measurements (PROTOCOL.md): the world, the engine and the stage counters.

No model call: the extraction provider is replaced by the contract harness's replay guard with no recording, so any
model call is a recorded miss, and the measurement fails on one.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import os
import random
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import duckdb

SEED = 20261111
USER = "robot-1"
BASE = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=10)

ADJECTIVES = ("red", "blue", "green", "black", "white", "yellow", "small", "large", "old", "new", "wooden", "metal",
              "glass", "plastic", "striped", "round", "square", "folded", "spare", "heavy", "light", "silver",
              "golden", "paper", "leather")
NOUNS = ("mug", "notebook", "charger", "umbrella", "leash", "screwdriver", "towel", "bottle", "bag", "scarf",
         "flashlight", "tape measure", "remote", "basket", "lamp", "bowl", "box", "jacket", "clipboard", "kettle")
ROOMS = ("kitchen", "garage", "hallway", "office", "bedroom", "bathroom", "laundry room", "pantry", "basement",
         "living room")
SURFACES = ("shelf", "counter", "table", "desk", "floor", "windowsill", "cabinet", "bench", "rack", "drawer",
            "stool", "dresser")

OBJECTS = tuple(f"{adjective} {noun}" for adjective in ADJECTIVES for noun in NOUNS)        # 500
PLACES = tuple(f"{room} {surface}" for room in ROOMS for surface in SURFACES)               # 120


def observations(total: int, seed: int = SEED) -> list[dict]:
    """``total`` stated-fact messages: each moves one object to a new place, 10 seconds apart."""
    rng = random.Random(seed)
    where: dict[str, str] = {}
    messages = []
    for index in range(total):
        obj = rng.choice(OBJECTS)
        place = rng.choice([p for p in PLACES if p != where.get(obj)])
        where[obj] = place
        messages.append({
            "content": f"The {obj} is on the {place}.",
            "role": "system",
            "event_time": BASE + STEP * index,
            "assertions": [{"subject": obj, "subject_type": "product", "relation": "located_in",
                            "object": place, "object_type": "location", "status": "current"}],
        })
    return messages


def truth_after(messages: list[dict]) -> dict[str, str]:
    """Each object's newest place after ``messages``."""
    where: dict[str, str] = {}
    for message in messages:
        assertion = message["assertions"][0]
        where[assertion["subject"]] = assertion["object"]
    return where


# Counters -------------------------------------------------------------------------------------------------------

STACK: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("stage", default=())
TIME: dict[str, float] = defaultdict(float)
CALLS: dict[str, int] = defaultdict(int)
SQL: dict[str, int] = defaultdict(int)
COUNTS: dict[str, int] = defaultdict(int)


def snapshot() -> dict:
    return {"time": dict(TIME), "calls": dict(CALLS), "sql": dict(SQL), "counts": dict(COUNTS)}


def delta(before: dict, after: dict) -> dict:
    out = {}
    for part in ("time", "calls", "sql", "counts"):
        keys = set(before[part]) | set(after[part])
        out[part] = {key: after[part].get(key, 0) - before[part].get(key, 0) for key in keys
                     if after[part].get(key, 0) != before[part].get(key, 0)}
    return out


def _label_of(owner, name: str) -> str:
    return f"{getattr(owner, '__name__', str(owner)).split('.')[-1]}.{name}"


def wrap(owner, name: str, label: str | None = None) -> None:
    """Time every call of ``owner.name`` (inclusive) and make it the stage its SQL statements count under."""
    fn = getattr(owner, name)
    label = label or _label_of(owner, name)
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def timed(*args, **kwargs):
            token = STACK.set(STACK.get() + (label,))
            started = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                TIME[label] += time.perf_counter() - started
                CALLS[label] += 1
                STACK.reset(token)
    else:
        @functools.wraps(fn)
        def timed(*args, **kwargs):
            token = STACK.set(STACK.get() + (label,))
            started = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                TIME[label] += time.perf_counter() - started
                CALLS[label] += 1
                STACK.reset(token)
    setattr(owner, name, timed)


def _count_sql() -> None:
    stack = STACK.get()
    SQL[stack[-1] if stack else "(no stage)"] += 1
    SQL["(total)"] += 1


class CountingConnection:
    """A DuckDB connection that counts each statement under the innermost timed stage."""

    def __init__(self, real) -> None:
        object.__setattr__(self, "_real", real)

    def execute(self, *args, **kwargs):
        _count_sql()
        return self._real.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        _count_sql()
        return self._real.executemany(*args, **kwargs)

    def sql(self, *args, **kwargs):
        _count_sql()
        return self._real.sql(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        return CountingConnection(self._real.cursor(*args, **kwargs))

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._real.__exit__(*exc_info)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


def count_duckdb_statements() -> None:
    real_connect = duckdb.connect

    @functools.wraps(real_connect)
    def connect(*args, **kwargs):
        return CountingConnection(real_connect(*args, **kwargs))

    duckdb.connect = connect


def instrument() -> None:
    """Wrap the stages of the stated-fact write path (PROTOCOL.md, M1)."""
    from prme.ingestion import assertions, claim_merge, group_links, mention_links, pipeline, planning, supersedence
    from prme.ingestion.entity_merge import EntityMerger
    from prme.storage import durable_queue, embedding, event_store, extraction_work, lexical_index, vector_index
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.engine import MemoryEngine

    count_duckdb_statements()
    wrap(MemoryEngine, "ingest_batch")
    wrap(MemoryEngine, "ingest")
    wrap(MemoryEngine, "_prepare_assertions")
    wrap(assertions, "read_held")
    wrap(assertions, "checked_assertions")
    wrap(assertions, "prepare_assertions")
    wrap(pipeline.IngestionPipeline, "_append_event")
    wrap(pipeline.IngestionPipeline, "_flush")
    wrap(pipeline.IngestionPipeline, "_run_extraction")
    wrap(pipeline.IngestionPipeline, "_extract_or_load")
    wrap(pipeline.IngestionPipeline, "_structured_record")
    wrap(pipeline.IngestionPipeline, "_materialize")
    wrap(pipeline.IngestionPipeline, "_prepare_plan")
    wrap(pipeline.IngestionPipeline, "_populate")
    wrap(pipeline.IngestionPipeline, "_publish_plan")
    wrap(pipeline.IngestionPipeline, "_arm_if_unfinished")
    wrap(pipeline, "evict_from_indexes")
    wrap(mention_links, "link_event_mentions")
    wrap(group_links, "link_group_claims")
    wrap(claim_merge, "find_repeated_claims")
    wrap(EntityMerger, "find_entity")
    wrap(EntityMerger, "find_or_create_entity")
    wrap(supersedence.SupersedenceDetector, "detect_and_supersede")
    wrap(supersedence.SupersedenceDetector, "detect_explicit_update")
    wrap(supersedence.SupersedenceDetector, "apply_named_correction")
    wrap(planning.PlanningIndexes, "prepare")
    wrap(event_store.EventStore, "append")
    wrap(event_store.EventStore, "get")
    wrap(event_store.EventStore, "get_extraction")
    wrap(event_store.EventStore, "record_extraction")
    wrap(event_store.EventStore, "get_derivation_plan")
    wrap(event_store.EventStore, "get_derivation_receipt")
    wrap(event_store.EventStore, "record_derivation_plan")
    wrap(event_store.EventStore, "get_direct_store")
    wrap(event_store.EventStore, "processing_counts")
    wrap(extraction_work.ExtractionWorkRepository, "claim")
    wrap(extraction_work.ExtractionWorkRepository, "status")
    wrap(extraction_work.ExtractionWorkRepository, "work_order")
    wrap(extraction_work.ExtractionWorkRepository, "pending_count")
    wrap(extraction_work.ExtractionWorkRepository, "renew")
    wrap(DuckPGQGraphStore, "commit_derivation")
    wrap(DuckPGQGraphStore, "get_event_nodes")
    wrap(DuckPGQGraphStore, "scan_nodes")
    wrap(DuckPGQGraphStore, "get_edges")
    wrap(DuckPGQGraphStore, "get_nodes")
    wrap(DuckPGQGraphStore, "get_node")
    wrap(DuckPGQGraphStore, "query_nodes")
    wrap(DuckPGQGraphStore, "create_edge")
    wrap(vector_index.VectorIndex, "stage")
    wrap(vector_index.VectorIndex, "_do_stage")
    wrap(vector_index.VectorIndex, "_persist_snapshot")
    wrap(vector_index.VectorIndex, "_save_snapshot")
    wrap(vector_index.VectorIndex, "index")
    wrap(lexical_index.LexicalIndex, "stage")
    wrap(lexical_index.LexicalIndex, "_do_stage")
    wrap(lexical_index.LexicalIndex, "index")
    wrap(lexical_index.LexicalIndex, "_commit_locked")
    wrap(lexical_index.LexicalIndex, "delete_by_node_id")
    from prme.privacy.admission import AdmissionCoordinator
    from prme.storage import erasure_indexes
    from prme.storage.privacy import PrivacyRepository

    wrap(AdmissionCoordinator, "require_legacy_inputs")
    wrap(PrivacyRepository, "records_for_objects")
    wrap(PrivacyRepository, "_object_identity")
    wrap(erasure_indexes, "refresh_lexical")
    wrap(durable_queue.DurableMaterializationQueue, "drain")
    wrap(durable_queue.DurableMaterializationQueue, "process_one")

    # Index work: texts embedded and lexical documents added.
    real_embed = embedding.FastEmbedProvider.embed

    @functools.wraps(real_embed)
    async def embed(self, texts, *args, **kwargs):
        COUNTS["embed.calls"] += 1
        COUNTS["embed.texts"] += len(texts)
        token = STACK.set(STACK.get() + ("FastEmbedProvider.embed",))
        started = time.perf_counter()
        try:
            return await real_embed(self, texts, *args, **kwargs)
        finally:
            TIME["FastEmbedProvider.embed"] += time.perf_counter() - started
            CALLS["FastEmbedProvider.embed"] += 1
            STACK.reset(token)

    embedding.FastEmbedProvider.embed = embed

    real_lexical_stage = lexical_index.LexicalIndex._do_stage

    @functools.wraps(real_lexical_stage)
    def lexical_stage(self, documents):
        COUNTS["lexical.stage_documents"] += len(documents)
        return real_lexical_stage(self, documents)

    lexical_index.LexicalIndex._do_stage = lexical_stage

    real_add = vector_index.VectorIndex._add_vector

    @functools.wraps(real_add)
    def add_vector(self, key, vector):
        COUNTS["vector.adds"] += 1
        return real_add(self, key, vector)

    vector_index.VectorIndex._add_vector = add_vector


# Engine -------------------------------------------------------------------------------------------------------

def blocked_environment() -> None:
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
                       "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                       "PRME_EXTRACTION_API_KEY": ""})


async def embedding_provider():
    from prme.storage.embedding import create_embedding_provider
    from tests.contract.harness import product_config

    directory = tempfile.mkdtemp(prefix="prme-write-rate-")
    provider = create_embedding_provider(product_config(directory).embedding)
    await provider.embed(["warm up"])
    return provider


async def new_engine(directory: str, provider, **settings):
    """The product defaults (classic mode), the pack in ``directory``, and a guard that fails any model call."""
    from prme.storage.engine import MemoryEngine
    from tests.contract.harness import ExtractionReplay, product_config

    config = product_config(directory, **settings)
    engine = await MemoryEngine.create(config, embedding_provider=provider)
    guard = ExtractionReplay({"calls": []})
    guard.install(engine._pipeline._extraction_provider)
    return engine, guard


async def write_current(engine, messages: list[dict], *, batch: int = 100) -> None:
    """The current path, as the contract harness writes stated facts."""
    for offset in range(0, len(messages), batch):
        await engine.ingest_batch(messages[offset:offset + batch], user_id=USER, wait_for_extraction=True)
        while (await engine.process_pending(user_id=USER, budget_ms=5000)).pending:
            pass
        if FLUSH_LEXICAL:
            # batch_lexical_commit(): one commit for the batch's staged documents.
            await engine._lexical_index.flush()


async def write_steps(engine_dir: str, provider, messages: list[dict], snapshots: list[int], snapshot_root: str,
                      *, settings: dict | None = None, engine=None, written: int = 0):
    """Write ``messages`` with the current path's calls and copy the closed pack at each count in ``snapshots``.

    A copy is taken with the engine closed, so DuckDB, the vector file and the lexical directory are all on disk; the
    engine is then opened again. Returns the open engine and its guard.
    """
    import shutil

    settings = settings or {}
    guard = None
    if engine is None:
        engine, guard = await new_engine(engine_dir, provider, **settings)
    for point in sorted(p for p in snapshots if p > written):
        await write_current(engine, messages[written:point])
        written = point
        assert not guard or not guard.misses, f"a model was called: {guard.misses[:3]}"
        await engine.close()
        target = os.path.join(snapshot_root, str(point))
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(os.path.join(engine_dir, "pack"), os.path.join(target, "pack"))
        print(f"snapshot {point} -> {target}", flush=True)
        engine, guard = await new_engine(engine_dir, provider, **settings)
    return engine, guard, written


PROTOTYPE = {"enable_filtered_entity_lookup": True}


def batch_lexical_commit() -> None:
    """Measurement only (prme-manager's add to P2): stage a plan's lexical documents with no commit and no reload.

    The documents wait in the writer and are committed once per batch by ``write_current`` (``flush_lexical``), so
    the timing shows an upper bound for one lexical commit per batch. The identity check against already committed
    documents is skipped too. This is not a candidate change: nothing checks what a retry would do.
    """
    import tantivy

    from prme.storage import lexical_index

    def stage_without_commit(self, documents):
        COUNTS["lexical.stage_documents"] += len(documents)
        if not documents:
            return
        writer = self._ensure_writer()
        for fields in documents:
            writer.add_document(tantivy.Document(**fields))
        self._uncommitted += len(documents)
        if self._oldest_uncommitted_at is None:
            self._oldest_uncommitted_at = time.monotonic()

    lexical_index.LexicalIndex._do_stage = stage_without_commit
    global FLUSH_LEXICAL
    FLUSH_LEXICAL = True


FLUSH_LEXICAL = False
