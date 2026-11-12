"""Profile the hosted service's request shapes on PostgreSQL at 10,000 nodes for one owner.

The hosted service's limit is the engine's CPU per request (dwamianm/prism#454,
dwamianm/prism-monorepo#511). This harness mirrors the three request shapes the
service serves, in process, against a store of raw conversation turns:

- ``retrieve``: ``engine.retrieve()`` under the engine settings the service pins
  in ``apps/api/src/prism_api/app.py``;
- ``store``: ``engine.store_with_receipt(extract=False)``, the service's store route;
- ``ingest_fast``: ``engine.ingest_fast_many()`` followed by the materialization
  route's ``engine.process_pending(budget_ms=1000)``.

Each shape reports wall time and process CPU time per request, and the retrieve
shape reports both per engine stage. Stages are measured by wrapping the
pipeline's own stage functions (and the candidate backends) for the duration of
the run; the engine's code is not changed and the wrappers are removed on exit.
The four candidate backends run concurrently under ``asyncio.gather``, so their
wall times overlap, and a CPU delta taken inside one coroutine also covers the
CPU its siblings used in the same interval. The per-request total is process CPU
time, so a background thread's work during the span is included, and the
per-stage split is an attribution, not an accounting identity. Each measured
query's cached vector is dropped before the request, so the query embedding is
paid on every sample as a new question pays it.

The store is built with ``conversation_pad()`` from the graph oracle's scale
fixture: ordinary LoCoMo and LongMemEval turns, deterministic, no model call.
The benchmark data under ``data/benchmarks`` must be downloaded first
(``scripts/download_benchmarks.py``); it is not in the repository. Embeddings
are the local fastembed model the service pins, with one ONNX thread as the
service runs it; the first run downloads the model if it is not already cached.
No paid model is called. The run writes rows to the PostgreSQL database it is
given, under a fresh owner id, so point it at a scratch database.

    uv run --extra postgres python -m benchmarks.diagnostics.hosted_profile run \
      --database-url postgresql://user:pass@127.0.0.1:5432/db \
      --nodes 10000 --requests 50 --batch-size 10 --out /tmp/hosted-profile
"""
from __future__ import annotations

import argparse
import asyncio
import cProfile
import functools
import hashlib
import inspect
import json
import os
import pstats
import platform
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from pydantic import SecretStr

import prme
from prme.config import EmbeddingConfig, MCPConfig, OrganizerConfig, PRMEConfig
from prme.models.processing import FastIngestItem
from prme.retrieval.config import default_packing_config, default_scoring_weights
from prme.storage.engine import MemoryEngine

KIND = "prme-hosted-request-profile"
SCHEMA_VERSION = 1
DEFAULT_NODES = 10_000
DEFAULT_REQUESTS = 50
DEFAULT_BATCH_SIZE = 10
MATERIALIZATION_BUDGET_MS = 1000
# What the service writes into rrf_graph_weight while rrf_graph_seed_count is on (app.py, 0.16.0a6).
GRAPH_SEED_COUNT_WEIGHT = 0.5
# The service pins one ONNX Runtime thread for the embedding model (settings.embedding_threads).
HOSTED_EMBEDDING_THREADS = 1

# Fixed questions, asked in order and repeated when a run asks for more than there are. They cover the
# shapes the hosted traffic carries: a first-person question (owner profile and relation routing), list and
# count questions (aggregation), a temporal question, and plain lookups.
QUERIES: tuple[str, ...] = (
    "What did I say about my family?",
    "Which cities did I visit on my trip?",
    "How many books did I read last year?",
    "Who are my dependents?",
    "What did I say about my job?",
    "When did I move to a new apartment?",
    "What kind of music does my sister like?",
    "What did I plan for the weekend?",
    "Which countries has my friend travelled to?",
    "How many pets do I have?",
    "What did I say about the meeting on Monday?",
    "What is my favorite food?",
    "What did I say about my childhood?",
    "Which projects am I working on?",
    "What did I say about my health?",
    "Who did I meet at the conference?",
    "What did I say about the car?",
    "How many times did I go running?",
    "What did I say about my parents?",
    "Which restaurants did I recommend?",
)


# --- The service's engine settings ---------------------------------------------------------------


def hosted_config(database_url: str) -> PRMEConfig:
    """The engine settings the hosted service pins (``prism_api/app.py``), isolated from the environment.

    Every nested setting is built without ``.env``, as the evidence gate builds them, so a checkout's file cannot
    change a measured run.
    """
    from unittest.mock import patch

    with patch.dict(os.environ, {}, clear=True):
        return _hosted_config(database_url)


def _hosted_config(database_url: str) -> PRMEConfig:
    from pydantic_settings import BaseSettings

    from prme.storage.embedding import fastembed_model_dimension

    model_name = EmbeddingConfig.model_fields["model_name"].default
    embedding = EmbeddingConfig(
        _env_file=None,
        provider="fastembed",
        model_name=model_name,
        dimension=fastembed_model_dimension(model_name),
        threads=HOSTED_EMBEDDING_THREADS,
        query_prefix=None,
        document_prefix=None,
        additional_models=[],
    )
    sections = {
        name: field.annotation(_env_file=None)
        for name, field in PRMEConfig.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseSettings)
    }
    sections["embedding"] = embedding
    # The service leaves the organizer as shipped: opportunistic maintenance on, inside requests.
    sections["organizer"] = OrganizerConfig(_env_file=None)
    sections["mcp"] = MCPConfig.model_construct(user_id=None, user_keys={})
    return PRMEConfig(
        _env_file=None,
        database_url=SecretStr(database_url),
        **sections,
        enable_query_reformulation=False,
        enable_store_extraction=False,
        enable_reingest=False,
        enable_pronoun_repair=False,
        enable_extraction_corrections=False,
        enable_multi_model_embeddings=False,
        enable_superseded_source_suppression=False,
        enable_speaker_references=True,
        enable_speaker_grounding=True,
        # The service runs the classic mode: it sets relation routing itself and takes none of the mode's other
        # read-time settings, and this profile measures the service as it is deployed.
        memory_mode="classic",
        enable_relation_routing=True,
        enable_claim_sentence_text=True,
        enable_ingest_claim_fixes=True,
        enable_owner_name_carry=True,
        enable_owner_group_links=True,
        enable_mention_links=True,
        lexical_term_weights=True,
        enable_windowed_extraction=False,
        enable_owner_window_extraction=False,
        enable_fact_text_resolution=False,
        privacy_policy=None,
        encryption_enabled=False,
        scoring=default_scoring_weights().model_copy(
            update={"rrf_graph_seed_count": True, "rrf_graph_weight": GRAPH_SEED_COUNT_WEIGHT}
        ),
        packing=default_packing_config().model_copy(
            update={
                "graph_seed_words": True,
                "folded_records_keep_rank": True,
                "session_context_skip_entities": True,
            }
        ),
    )


def settings_digest(config: PRMEConfig) -> dict[str, Any]:
    """The settings a run reports, so two runs can be compared without re-reading the service's code."""
    return {
        "backend": config.backend,
        "embedding": {
            "provider": config.embedding.provider,
            "model_name": config.embedding.model_name,
            "dimension": config.embedding.dimension,
            "threads": config.embedding.threads,
        },
        "organizer": {
            "opportunistic_enabled": config.organizer.opportunistic_enabled,
            "opportunistic_cooldown": config.organizer.opportunistic_cooldown,
            "opportunistic_budget_ms": config.organizer.opportunistic_budget_ms,
        },
        "switches": {
            name: getattr(config, name)
            for name in (
                "enable_query_reformulation",
                "enable_store_extraction",
                "enable_reingest",
                "enable_pronoun_repair",
                "enable_extraction_corrections",
                "enable_multi_model_embeddings",
                "enable_superseded_source_suppression",
                "enable_speaker_references",
                "enable_speaker_grounding",
                "enable_relation_routing",
                "enable_mention_links",
                "enable_owner_name_carry",
                "enable_owner_group_links",
                "enable_claim_sentence_text",
                "enable_ingest_claim_fixes",
                "lexical_term_weights",
                "memory_mode",
                "enable_windowed_extraction",
                "enable_owner_window_extraction",
                "enable_fact_text_resolution",
                "enable_reranker",
                "encryption_enabled",
            )
        },
        "scoring": {"fusion": config.scoring.fusion, "rrf_graph_seed_count": config.scoring.rrf_graph_seed_count,
                    "rrf_graph_weight": config.scoring.rrf_graph_weight},
        "packing": {
            "context_format": config.packing.context_format,
            "token_budget": config.packing.token_budget,
            "graph_seed_words": config.packing.graph_seed_words,
            "folded_records_keep_rank": config.packing.folded_records_keep_rank,
            "session_context_skip_entities": config.packing.session_context_skip_entities,
        },
    }


# --- Timing ---------------------------------------------------------------------------------------


def percentiles(values: list[float]) -> dict:
    """Median, 95th percentile, mean and maximum of ``values``, as the graph oracle reports them."""
    from benchmarks.graph_oracle.run import percentiles as oracle_percentiles

    return oracle_percentiles(values)


class StageRecorder:
    """Per-request wall and CPU milliseconds for each instrumented stage."""

    def __init__(self, stages: tuple[str, ...]) -> None:
        self.stages = stages
        self._request = -1
        self._samples: dict[str, dict[int, list[float]]] = {}

    def next_request(self) -> None:
        self._request += 1

    def record(self, stage: str, wall_ms: float, cpu_ms: float) -> None:
        sample = self._samples.setdefault(stage, {}).setdefault(self._request, [0.0, 0.0, 0.0])
        sample[0] += wall_ms
        sample[1] += cpu_ms
        sample[2] += 1.0

    def report(self) -> dict[str, dict]:
        """Each stage's wall and CPU percentiles over the completed requests, and its mean calls per request."""
        completed = self._request + 1
        result: dict[str, dict] = {}
        for stage in self.stages:
            per_request = self._samples.get(stage, {})
            walls = [per_request.get(index, [0.0, 0.0, 0.0])[0] for index in range(completed)]
            cpus = [per_request.get(index, [0.0, 0.0, 0.0])[1] for index in range(completed)]
            calls = [per_request.get(index, [0.0, 0.0, 0.0])[2] for index in range(completed)]
            if not any(calls):
                continue
            result[stage] = {
                "wall_ms": percentiles(walls),
                "cpu_ms": percentiles(cpus),
                "calls_per_request": round(sum(calls) / completed, 2),
            }
        return result


def _wrap_async(original, recorder: StageRecorder, stage: str):
    @functools.wraps(original)
    async def wrapped(*args, **kwargs):
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            return await original(*args, **kwargs)
        finally:
            recorder.record(stage, (time.perf_counter() - wall) * 1000, (time.process_time() - cpu) * 1000)

    return wrapped


def _wrap_sync(original, recorder: StageRecorder, stage: str):
    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            return original(*args, **kwargs)
        finally:
            recorder.record(stage, (time.perf_counter() - wall) * 1000, (time.process_time() - cpu) * 1000)

    return wrapped


@contextmanager
def instrumented(target: Any, bindings: tuple[tuple[str, str], ...], recorder: StageRecorder):
    """Temporarily wrap ``target``'s attributes, recording the stage each call belongs to.

    ``bindings`` pairs a stage name with an attribute name. An attribute the target does not have is a
    configuration error, not a silently missing stage.
    """
    saved: list[tuple[str, Any, bool]] = []
    try:
        for stage, name in bindings:
            original = getattr(target, name)
            present = name in getattr(target, "__dict__", {})
            wrapper = _wrap_async(original, recorder, stage) if inspect.iscoroutinefunction(original) else \
                _wrap_sync(original, recorder, stage)
            setattr(target, name, wrapper)
            saved.append((name, original, present))
        yield
    finally:
        for name, original, present in reversed(saved):
            if present:
                setattr(target, name, original)
            else:
                delattr(target, name)


# --- The instrumented stages ----------------------------------------------------------------------


def retrieve_bindings(engine: MemoryEngine) -> tuple[tuple[Any, tuple[tuple[str, str], ...]], ...]:
    """The pipeline's stage functions and the candidate backends, plus query embedding and token counting."""
    from prme.models import relevance as relevance_module
    from prme.retrieval import candidates as candidates_module
    from prme.retrieval import packing as packing_module
    from prme.retrieval import pipeline as pipeline_module

    return (
        (pipeline_module, (
            ("query_analysis", "analyze_query"),
            ("filtering", "filter_epistemic"),
            ("scoring", "score_and_rank"),
            ("packing", "pack_context"),
        )),
        (candidates_module, (
            ("graph_expansion", "_generate_graph_candidates"),
            ("vector_search", "_generate_vector_candidates"),
            ("lexical_search", "_generate_lexical_candidates"),
            ("pinned_candidates", "_generate_pinned_candidates"),
            ("candidate_merge", "merge_candidates"),
        )),
        (packing_module, (("token_counting", "count_tokens"),)),
        (relevance_module, (("receipts", "make_receipt"),)),
        (engine._vector_index._provider, (("embedding", "embed"),)),
    )


def forget_embeddings(engine: MemoryEngine, texts: list[str]) -> None:
    """Drop ``texts``' cached vectors, so a measured request pays their embedding as new text does.

    The service wraps its provider in the same LRU cache, so repeated text is free there. A profile that warmed
    every query first, or indexed the same text in an earlier shape, would report the cached path and hide the
    embedding stage.
    """
    cache = getattr(engine._vector_index._provider, "_cache", None)
    if not isinstance(cache, dict):
        return
    for text in texts:
        digest = hashlib.sha256(text.encode()).hexdigest()
        cache.pop(digest, None)
        cache.pop("query:" + digest, None)


def write_bindings(engine: MemoryEngine) -> tuple[tuple[Any, tuple[tuple[str, str], ...]], ...]:
    """The write path's durable append, graph and index writes, and the materialization pass around them."""
    return (
        (engine, (("materialization", "_materialize_event"),)),
        (engine._event_store, (("event_append", "append"), ("event_append", "append_many"))),
        (engine._graph_store, (("graph_write", "create_node"),)),
        (engine._vector_index, (("vector_index", "index"),)),
        (engine._lexical_index, (("lexical_index", "index"),)),
        (engine._vector_index._provider, (("embedding", "embed"),)),
    )


RETRIEVE_STAGES = (
    "query_analysis", "graph_expansion", "vector_search", "lexical_search", "pinned_candidates",
    "candidate_merge", "filtering", "embedding", "scoring", "packing", "token_counting", "receipts",
)
WRITE_STAGES = ("event_append", "embedding", "graph_write", "vector_index", "lexical_index", "materialization")


@contextmanager
def instrument(bindings: tuple[tuple[Any, tuple[tuple[str, str], ...]], ...], recorder: StageRecorder):
    with ExitStack() as stack:
        for target, attributes in bindings:
            stack.enter_context(instrumented(target, attributes, recorder))
        yield


async def settle_maintenance(engine: MemoryEngine) -> None:
    """Wait for a scheduled opportunistic maintenance pass, so its CPU does not land in a measured request."""
    runner = getattr(engine, "_maintenance_runner", None)
    drain = getattr(runner, "drain", None)
    if drain is not None:
        await drain()


# --- The store ------------------------------------------------------------------------------------


def corpus(count: int) -> list[str]:
    """``count`` ordinary conversation turns, deterministic and local (no model call)."""
    from benchmarks.graph_oracle.scale import conversation_pad

    return conversation_pad(count)


def required_texts(nodes: int, requests: int, batch_size: int) -> int:
    """The turns a run needs: the store's, then one warm-up and ``requests`` batches the build did not store."""
    return nodes + requests * batch_size + 1


async def build_store(engine: MemoryEngine, owner: str, texts: list[str]) -> dict:
    """Store the raw turns with ``extract=False``, then let PostgreSQL see a table of this size."""
    began = time.perf_counter()
    for text in texts:
        await engine.store(text, user_id=owner, extract=False)
    store_seconds = time.perf_counter() - began
    await settle_maintenance(engine)
    # A source whose index write failed nonfatally would otherwise be drained inside the first retrieve sample.
    await drain_materialization(engine, owner)
    analyzed = await analyze_tables(engine)
    return {"nodes": len(texts), "seconds": round(store_seconds, 1), "analyzed": analyzed}


async def analyze_tables(engine: MemoryEngine) -> bool:
    """Run ``ANALYZE`` on the tables a retrieve reads after a bulk load, as a deployment's autovacuum would have."""
    pool = getattr(engine._graph_store, "_pool", None)
    if pool is None:
        return False
    async with pool.acquire() as connection:
        for table in ("nodes", "edges", "lexical_documents"):
            await connection.execute(f"ANALYZE {table}")
    return True


async def storage_identity(engine: MemoryEngine) -> dict:
    """The PostgreSQL and pgvector versions a run measured against."""
    pool = getattr(engine._graph_store, "_pool", None)
    if pool is None:
        return {}
    async with pool.acquire() as connection:
        server = await connection.fetchval("SHOW server_version")
        vector = await connection.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    return {"postgres": server, "pgvector": vector}


def cpu_brand() -> str:
    """The CPU's name where the platform reports one, else its architecture."""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=2, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return platform.processor()
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return platform.processor()


def library_versions() -> dict:
    """The versions of the components whose CPU this profile measures."""
    from importlib.metadata import PackageNotFoundError, version

    found = {}
    for name in ("fastembed", "onnxruntime", "usearch", "tantivy", "asyncpg", "pgvector"):
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            continue
    return found


# --- Measurement ----------------------------------------------------------------------------------


async def measure_retrieve(engine: MemoryEngine, owner: str, requests: int) -> dict:
    recorder = StageRecorder(RETRIEVE_STAGES)
    with instrument(retrieve_bindings(engine), recorder):
        # Warm the model, every query shape's lazy parsers and the service's once-per-cooldown maintenance pass.
        for query in QUERIES:
            await engine.retrieve(query, user_id=owner)
        await settle_maintenance(engine)
        walls: list[float] = []
        cpus: list[float] = []
        for index in range(requests):
            query = QUERIES[index % len(QUERIES)]
            forget_embeddings(engine, [query])
            recorder.next_request()
            wall, cpu = time.perf_counter(), time.process_time()
            await engine.retrieve(query, user_id=owner)
            walls.append((time.perf_counter() - wall) * 1000)
            cpus.append((time.process_time() - cpu) * 1000)
        stages = recorder.report()
    return {"requests": requests, "wall_ms": percentiles(walls), "cpu_ms": percentiles(cpus), "stages": stages}


async def drain_materialization(engine: MemoryEngine, owner: str) -> int:
    """Run one materialization pass and refuse a truncated or failed one, so no work moves to the next sample."""
    result = await engine.process_pending(user_id=owner, budget_ms=MATERIALIZATION_BUDGET_MS)
    if result.pending or result.failed:
        raise ValueError(
            f"materialization left {result.pending} pending and {result.failed} failed for this owner; "
            f"lower --batch-size so one request fits in {MATERIALIZATION_BUDGET_MS} ms"
        )
    return result.processed


async def checked_store(engine: MemoryEngine, owner: str, text: str) -> None:
    """Store one source and refuse a receipt that leaves index work pending."""
    receipt = await engine.store_with_receipt(text, user_id=owner, extract=False)
    if receipt.processing_status.status != "complete":
        raise ValueError(
            f"store left work {receipt.processing_status.status} for event {receipt.event_id}; "
            "the request's CPU would omit its materialization"
        )


async def measure_requests(engine: MemoryEngine, owner: str, requests: int, *, store: bool,
                           texts: list[str], batch_size: int = 1) -> dict:
    """Time ``requests`` store calls, or ``ingest_fast_many`` batches each followed by a materialization pass."""
    recorder = StageRecorder(WRITE_STAGES)
    per_request = 1 if store else batch_size
    if 1 + requests * per_request > len(texts):
        raise ValueError("the corpus is smaller than the requests need")
    with instrument(write_bindings(engine), recorder):
        # Warm the model and the materialization queue outside the samples.
        if store:
            await checked_store(engine, owner, texts[0])
        else:
            await engine.ingest_fast_many([FastIngestItem(content=texts[0])], user_id=owner)
            await drain_materialization(engine, owner)
        await settle_maintenance(engine)
        walls: list[float] = []
        cpus: list[float] = []
        admissions: list[float] = []
        materializations: list[float] = []
        for index in range(requests):
            start = 1 + index * per_request
            batch = texts[start:start + per_request]
            forget_embeddings(engine, batch)
            recorder.next_request()
            wall, cpu = time.perf_counter(), time.process_time()
            if store:
                await checked_store(engine, owner, batch[0])
            else:
                admit_started = time.perf_counter()
                event_ids = await engine.ingest_fast_many(
                    [FastIngestItem(content=text) for text in batch], user_id=owner,
                )
                admissions.append((time.perf_counter() - admit_started) * 1000)
                material_started = time.perf_counter()
                await drain_materialization(engine, owner)
                materializations.append((time.perf_counter() - material_started) * 1000)
                if len(event_ids) != len(batch):
                    raise ValueError("ingest_fast_many admitted fewer events than it was given")
            walls.append((time.perf_counter() - wall) * 1000)
            cpus.append((time.process_time() - cpu) * 1000)
        stages = recorder.report()
    report = {"requests": requests, "wall_ms": percentiles(walls), "cpu_ms": percentiles(cpus), "stages": stages}
    if not store:
        report["items_per_request"] = batch_size
        report["admission_wall_ms"] = percentiles(admissions)
        report["materialization_wall_ms"] = percentiles(materializations)
    return report


async def profile_top(engine: MemoryEngine, owner: str, query: str, limit: int = 20) -> list[dict]:
    """One request under ``cProfile``: the engine functions on the calling thread, by elapsed time.

    The figures are elapsed time, include the profiler's own overhead and any waiting, and are not CPU
    accounting or comparable to the stage table. They are a pointer to hot spots. Functions the pipeline
    offloads to a worker thread (packing, token counting, embedding) have no row of their own.
    """
    forget_embeddings(engine, [query])
    package_root = Path(prme.__file__).resolve().parent
    profile = cProfile.Profile()
    profile.enable()
    try:
        await engine.retrieve(query, user_id=owner)
    finally:
        profile.disable()
    rows: list[dict] = []
    for (filename, lineno, name), (_cc, calls, tottime, cumtime, _callers) in pstats.Stats(profile).stats.items():
        resolved = Path(filename).resolve()
        if package_root not in resolved.parents:
            continue
        rows.append({
            "function": f"{resolved.name}:{lineno}({name})",
            "calls": calls,
            "tottime_ms": round(tottime * 1000, 3),
            "cumtime_ms": round(cumtime * 1000, 3),
        })
    rows.sort(key=lambda row: row["cumtime_ms"], reverse=True)
    return rows[:limit]


# --- Entry point ----------------------------------------------------------------------------------


async def run(*, database_url: str, nodes: int, requests: int, batch_size: int, out: Path) -> dict:
    texts = corpus(required_texts(nodes, requests, batch_size))
    owner = f"hosted-profile-{uuid.uuid4().hex[:8]}"
    config = hosted_config(database_url)
    report: dict[str, Any] = {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "engine_version": prme.__version__,
        "backend": config.backend,
        "nodes": nodes,
        "requests_per_shape": requests,
        "batch_size": batch_size,
        "hosted_settings": settings_digest(config),
        "corpus": {
            "source": "benchmarks.graph_oracle.scale.conversation_pad",
            "texts": len(texts),
            "sha256": hashlib.sha256("\n".join(texts).encode()).hexdigest(),
        },
        "machine": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": cpu_brand(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "libraries": library_versions(),
    }
    async with MemoryEngine.open(config) as engine:
        provider = engine._vector_index._provider
        report["embedding_identity"] = {
            "model_name": provider.model_name,
            "model_version": provider.model_version,
            "dimension": provider.dimension,
        }
        report["storage_identity"] = await storage_identity(engine)
        await provider.embed(["Warm-up for the profile."])
        report["build"] = await build_store(engine, owner, texts[:nodes])
        # The write shapes use turns the build did not store, so their embedding and index writes are cold.
        unwritten = texts[nodes:]
        report["shapes"] = {
            "retrieve": await measure_retrieve(engine, owner, requests),
            "store": await measure_requests(engine, owner, requests, store=True, texts=unwritten),
            "ingest_fast": await measure_requests(engine, owner, requests, store=False, texts=unwritten,
                                                  batch_size=batch_size),
        }
        report["profile_top"] = await profile_top(engine, owner, QUERIES[0])
    report["owner"] = owner
    report["limits"] = (
        "One process, one owner, raw conversation turns stored with extract=False. Wall time and process CPU time "
        "are per request on this machine; candidate backends run concurrently, so their stage wall times overlap "
        "and a stage's CPU delta can cover its siblings' work. Each measured query's and text's cached vector is "
        "dropped first. Build time, model load and the first maintenance pass are outside the samples. No answer "
        "quality or cross-machine speed claim."
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "hosted-profile.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def markdown(report: dict) -> str:
    """The report's tables, in the shape BENCHMARKS.md uses."""
    lines = [
        f"Engine {report['engine_version']}, {report['backend']}, {report['nodes']} nodes, "
        f"{report['requests_per_shape']} requests per shape, {report['machine']['platform']}, "
        f"{report['machine']['cpu_count']} logical CPUs.",
        "",
        "| Shape | Wall p50 / p95 ms | CPU p50 / p95 ms |",
        "|---|---:|---:|",
    ]
    for name, shape in report["shapes"].items():
        lines.append(f"| {name} | {shape['wall_ms']['p50']} / {shape['wall_ms']['p95']} | "
                     f"{shape['cpu_ms']['p50']} / {shape['cpu_ms']['p95']} |")
    lines += ["", "Retrieve stages, milliseconds per request:", "",
              "| Stage | Wall p50 / p95 | CPU p50 / p95 | Calls per request |", "|---|---:|---:|---:|"]
    for name, stage in report["shapes"]["retrieve"]["stages"].items():
        lines.append(f"| {name} | {stage['wall_ms']['p50']} / {stage['wall_ms']['p95']} | "
                     f"{stage['cpu_ms']['p50']} / {stage['cpu_ms']['p95']} | {stage['calls_per_request']} |")
    for shape_name in ("store", "ingest_fast"):
        stages = report["shapes"][shape_name]["stages"]
        if not stages:
            continue
        lines += ["", f"{shape_name} stages, milliseconds per request:", "",
                  "| Stage | Wall p50 / p95 | CPU p50 / p95 | Calls per request |", "|---|---:|---:|---:|"]
        for name, stage in stages.items():
            lines.append(f"| {name} | {stage['wall_ms']['p50']} / {stage['wall_ms']['p95']} | "
                         f"{stage['cpu_ms']['p50']} / {stage['cpu_ms']['p95']} | {stage['calls_per_request']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="build the store and profile the three request shapes")
    runner.add_argument("--database-url", required=True, help="scratch PostgreSQL database; the run adds rows to it")
    runner.add_argument("--nodes", type=int, default=DEFAULT_NODES)
    runner.add_argument("--requests", type=int, default=DEFAULT_REQUESTS)
    runner.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    runner.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.nodes < 1 or args.requests < 1 or args.batch_size < 1:
        raise ValueError("--nodes, --requests and --batch-size must be at least 1")
    report = asyncio.run(run(database_url=args.database_url, nodes=args.nodes, requests=args.requests,
                             batch_size=args.batch_size, out=args.out))
    sys.stdout.write(markdown(report))


if __name__ == "__main__":
    main()
