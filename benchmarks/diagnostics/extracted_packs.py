"""Build benchmark memory packs through ``ingest()`` for the offline evidence gate (#91, #102).

The offline evidence gate replays retrieval over the saved 2026-09-23 packs,
which the harnesses built with ``store()``. ``store()`` never runs extraction,
so the gate cannot see a change to extraction or the graph (#102). This module
builds fresh LoCoMo and LongMemEval-S packs from the same source turns through
the public ``ingest()`` path, and ``product_packing gate --packs`` replays
retrieval over them. The first such builds are the ``ingest()`` baselines that
#91 asks for before its windowed extractor is measured.

Choices, and why:

- The source turns, their text and their source metadata are the registered
  harnesses' (``run_gpt54_comparison.source_turns`` for LoCoMo and the
  LongMemEval-S baseline runner's store loop), so a raw turn reads as it does
  in the saved packs and its source key names its annotation.
- In LoCoMo the first speaker by sorted name is the memory owner,
  ``role="user"``. Every other speaker is stored as ``role="participant"``
  with ``speaker`` set, the path #84 added for another person in a
  conversation. The ``store()`` harness maps that person to ``assistant``,
  which would make ``ingest()`` apply the assistant extraction policy to a
  human's turns. LongMemEval-S keeps its own user and assistant roles.
- Extraction runs on an Ollama model through the loopback server. By default
  only a model the server runs on this machine is accepted, so a build cannot
  reach a paid API or send benchmark text off this machine. ``--cloud``
  accepts an Ollama cloud model instead, which the owner chose for
  LongMemEval-S (246,738 turns would take weeks locally): Ollama's hosted
  service then receives the (public) turns, and nothing is billed per request.
  Other providers are always refused.
- The configuration is the gate's: current defaults, isolated from the
  environment and ``.env``, with the organizer off, so a pack holds exactly
  what ingestion wrote. ``--set`` changes build settings the same way.
- Turns are ingested one at a time in source order, and each waits for its
  extraction, as a live conversation would. Packs are built in parallel with
  ``--jobs`` and ``--shard``. A build resumes: a restarted pack first finishes
  extraction left unfinished and then skips the turns it already holds. A
  resumed build must use the same commit, working tree, settings and model,
  or it is refused.
- LongMemEval-S histories share sessions, so the same turn recurs across
  questions. A build keeps each model response in a cache keyed by the model,
  role, prompt and text, so an identical turn is extracted once and gets the
  same result in every pack. The cached response is validated again exactly
  as a live one. ``--no-cache`` extracts every turn afresh.

Layout under ``<root>/<label>/``: ``build.json`` (benchmark, commit,
configuration, model identity, and every Ollama server version seen),
``extraction-cache/``, and per conversation or question
``<benchmark>/<id>/pack/`` (the memory pack), ``turns.jsonl`` (one line per
turn ingested: event, extraction status, seconds, model calls and tokens) and,
once complete, ``manifest.json`` (counts, the share of annotated evidence turns
that at least one extracted record cites, and the pack's tree identity). Packs
contain benchmark text, so the default root is the ignored ``data/``.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from benchmarks.diagnostics.product_packing import (
    _harness, _quiet_offline_cli, _source_key, _turn_key, gate_config, pack_identity, parse_overrides,
)
from benchmarks.integrations.ollama_answers import check_endpoint
from prme import MemoryEngine, NodeType, PRMEConfig
from prme.config import ExtractionConfig
from prme.types import LifecycleState

KIND = "prme-extracted-packs"
SCHEMA_VERSION = 1
BENCHMARKS = ("locomo", "longmemeval")
DEFAULT_ROOT = Path("data/extracted-packs-v1")
DEFAULT_MODEL = "prme-qwen3.5:35b-a3b-8k"
ENDPOINT = "http://127.0.0.1:11434/v1"
# The LongMemEval-S baseline runner's owner for every question's pack.
LONGMEMEVAL_USER_ID = "longmemeval-s-baseline"
# Seconds per extraction call. The local model needs more than the 30 s default
# on long turns, and a timeout only fails the turn's extraction.
TIMEOUT_SECONDS = 180.0
# Passes over unfinished extraction after the ingest loop, for transient model failures.
RETRY_PASSES = 2
# Seconds to wait before each retry of a turn whose extraction failed. A turn that
# still fails pauses its pack, so later turns never extract ahead of it; a later
# build resumes the pack from that turn. Rate limits on a cloud model end this way.
RETRY_DELAYS = (15.0, 60.0, 240.0)
# Records that are not extracted claims: raw source notes and the entities claims attach to.
_NOT_CLAIMS = frozenset({NodeType.NOTE, NodeType.ENTITY})
# More than any one pack holds; reads stay within one user.
_ALL = 1_000_000


def default_root() -> Path:
    """Where builds live.

    A build runs from its own worktree so later work cannot change the code it
    imports, but its packs belong with the datasets in the main checkout's
    ignored ``data/``, as the saved run's archive does.
    """
    from benchmarks import checkout

    return checkout.main_checkout() / DEFAULT_ROOT


@dataclass(frozen=True)
class Unit:
    """One pack to build: a LoCoMo conversation or a LongMemEval-S question's history."""

    benchmark: str
    unit_id: str
    user_id: str
    turns: tuple[dict, ...]  # ingest() arguments, each with its source "key"
    reference_time: datetime
    annotated: frozenset[str]  # source keys the gate's questions cite as evidence


def check_model(identity: dict, *, cloud: bool = False) -> None:
    """Only a local Ollama model, or with ``cloud`` an Ollama cloud model, may extract benchmark text."""
    provider = identity.get("provider")
    if cloud:
        if provider != "ollama_cloud":
            raise ValueError(f"{identity.get('model')} is not an Ollama cloud model")
    elif provider != "ollama" or identity.get("remote_host") or identity.get("remote_model"):
        raise ValueError(f"{identity.get('model')} is not a local Ollama model. Builds extract only on this "
                         "machine unless --cloud is given, so no benchmark text leaves it and no paid API "
                         "is called.")


def model_identity(model: str, endpoint: str = ENDPOINT, *, cloud: bool = False) -> dict:
    """The Ollama server's identity for the model, including the server version."""
    from benchmarks.integrations.run_longmemeval_v2 import _ollama_reader_identity

    check_endpoint(endpoint)
    parts = urlsplit(endpoint)
    identity = _ollama_reader_identity(urlunsplit((parts.scheme, parts.netloc, "", "", "")), model)
    check_model(identity, cloud=cloud)
    return identity


def build_config(pack: Path, overrides: dict | None = None, *, model: str = DEFAULT_MODEL,
                 endpoint: str = ENDPOINT) -> PRMEConfig:
    """The gate's isolated configuration over ``pack``, extracting through the Ollama server."""
    check_endpoint(endpoint)
    config = gate_config(pack, overrides)
    with patch.dict(os.environ, {}, clear=True):
        extraction = ExtractionConfig(_env_file=None, provider="ollama", model=model,  # type: ignore[call-arg]
                                      base_url=endpoint, timeout=TIMEOUT_SECONDS)
    return config.model_copy(update={"extraction": extraction})


def ingest_turns(turns: list[dict]) -> list[dict]:
    """The LoCoMo harness's source turns as ``ingest()`` arguments: the first speaker owns the memory."""
    speakers = sorted({turn["metadata"]["source_speaker"] for turn in turns})
    return _keyed("locomo", [
        {"content": turn["content"], "session_id": turn["session_id"], "event_time": turn["event_time"],
         "metadata": turn["metadata"], "speaker": turn["metadata"]["source_speaker"],
         "role": "user" if turn["metadata"]["source_speaker"] == speakers[0] else "participant"}
        for turn in turns])


def _keyed(benchmark: str, turns: list[dict]) -> list[dict]:
    keyed = [{**turn, "key": _source_key(benchmark, turn["metadata"])} for turn in turns]
    keys = [turn["key"] for turn in keyed]
    if None in keys or len(set(keys)) != len(keys):
        raise ValueError("Every source turn needs its own source key so a build can resume")
    return keyed


def locomo_units() -> list[Unit]:
    """Each registered LoCoMo conversation, with the evidence its gate questions cite."""
    gpt54 = _harness()
    if gpt54.digest(gpt54.LOCOMO) != gpt54.LOCOMO_SHA:
        raise ValueError("The LoCoMo dataset differs from the registered one")
    questions = gpt54.question_rows("locomo")
    units = []
    for sample in json.loads(gpt54.LOCOMO.read_text()):
        turns = ingest_turns(gpt54.source_turns(sample["conversation"]))
        cited = {key for question in questions if question["conversation_id"] == sample["sample_id"]
                 for key in question.get("evidence", [])}
        units.append(Unit("locomo", sample["sample_id"], sample["sample_id"], tuple(turns),
                          max(turn["event_time"] for turn in turns), frozenset(cited)))
    return units


def longmemeval_turns(question: dict) -> list[dict]:
    """A question's history as ``ingest()`` arguments, with the baseline runner's text and metadata."""
    from benchmarks.integrations.run_longmemeval_s_baseline import _parse_date

    turns = []
    for position, (session_id, date_text, session) in enumerate(zip(
            question["haystack_session_ids"], question["haystack_dates"], question["haystack_sessions"],
            strict=True)):
        for index, turn in enumerate(session):
            if not turn["content"].strip():
                continue
            turns.append({"content": turn["content"], "role": turn["role"], "speaker": None,
                          "session_id": f"{position:05d}:{session_id}", "event_time": _parse_date(date_text),
                          "metadata": {"benchmark": "longmemeval-s", "source_session_id": session_id,
                                       "source_session_position": position, "source_turn_index": index,
                                       "source_role": turn["role"]}})
    return _keyed("longmemeval", turns)


def longmemeval_units() -> list[Unit]:
    """Each LongMemEval-S question's history; abstention questions cite no evidence."""
    from benchmarks.integrations.run_longmemeval_s_baseline import _parse_date

    gpt54 = _harness()
    if gpt54.digest(gpt54.LONGMEM) != gpt54.lme.DATASET_SHA256:
        raise ValueError("The LongMemEval-S dataset differs from the registered one")
    units = []
    for question in gpt54.question_rows("longmemeval"):
        cited = set() if question["question_id"].endswith("_abs") else {
            _turn_key(session_id, position, index)
            for position, (session_id, session) in enumerate(zip(
                question["haystack_session_ids"], question["haystack_sessions"], strict=True))
            for index, turn in enumerate(session) if turn.get("has_answer") is True}
        units.append(Unit("longmemeval", question["question_id"], LONGMEMEVAL_USER_ID,
                          tuple(longmemeval_turns(question)), _parse_date(question["question_date"]),
                          frozenset(cited)))
    return units


def benchmark_units(benchmark: str) -> list[Unit]:
    if benchmark not in BENCHMARKS:
        raise ValueError(f"Choose a benchmark from: {', '.join(BENCHMARKS)}")
    return locomo_units() if benchmark == "locomo" else longmemeval_units()


class _Usage:
    """Chat completion calls and tokens that one engine's extraction client reports."""

    def __init__(self, engine) -> None:
        self.calls = self.prompt_tokens = self.completion_tokens = 0
        provider = getattr(engine._pipeline, "_extraction_provider", None)
        if provider is None or not hasattr(provider, "_ensure_client"):
            raise ValueError("This engine has no model-backed extraction provider")
        provider._ensure_client().on("completion:response", self._record)

    def _record(self, response) -> None:
        usage = getattr(response, "usage", None)
        self.calls += 1
        self.prompt_tokens += getattr(usage, "prompt_tokens", None) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", None) or 0

    def snapshot(self) -> tuple[int, int, int]:
        return self.calls, self.prompt_tokens, self.completion_tokens


class ExtractionCache:
    """Model responses by model, role, prompt and text, shared by every pack of a build.

    A hit is validated again with the turn's text and role, exactly as the
    provider validates a live response, so grounding and discards repeat.
    Entries are written atomically, so parallel builds can share the folder.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.hits = self.misses = 0

    def _key(self, provider, content: str, role: str) -> str:
        from prme.ingestion.extraction import _extraction_prompt_for_role

        parts = [provider.model_name, provider._model, provider._temperature, provider._reasoning_effort,
                 _extraction_prompt_for_role(role), role, content]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    def wrap(self, provider) -> None:
        from prme.ingestion.extraction import _CitedExtractionResult

        extract = provider.extract

        async def cached(content: str, *, role: str = "user"):
            path = self.folder / (key := self._key(provider, content, role))[:2] / f"{key}.json"
            if path.exists():
                self.hits += 1
                return _CitedExtractionResult.model_validate_json(path.read_bytes(), context={
                    "source_text": content, "source_role": role.strip().casefold()})
            self.misses += 1
            result = await extract(content, role=role)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as out:
                out.write(result.model_dump_json())
            os.replace(out.name, path)
            return result

        provider.extract = cached


async def _stored_events(engine, benchmark: str, user_id: str) -> dict[str, str]:
    """Event ID by source key for the turns this pack already holds, in the order they were saved."""
    events = sorted(await engine.get_events(user_id, limit=_ALL), key=lambda event: event.timestamp)
    return {key: str(event.id) for event in events
            if (key := _source_key(benchmark, event.metadata or {})) is not None}


async def _finish_extractions(engine, user_id: str, event_ids, *, passes: int = RETRY_PASSES) -> dict[str, str]:
    """Retry extraction that did not complete; returns each event's final status."""
    event_ids = list(event_ids)
    statuses: dict[str, str] = {}
    for attempt in range(passes + 1):
        statuses = {}
        for event_id in event_ids:
            status = await engine.extraction_status(event_id, user_id=user_id)
            statuses[event_id] = "missing" if status is None else status.status
        unfinished = [event_id for event_id, status in statuses.items() if status in {"pending", "failed", "running"}]
        if not unfinished or attempt == passes:
            break
        for event_id in unfinished:
            status = await engine.extraction_status(event_id, user_id=user_id)
            if status.status == "running" and status.lease_expires_at is not None:
                # A worker that stopped mid-extraction holds a lease until it expires.
                await asyncio.sleep(max(0.0, (status.lease_expires_at - datetime.now(timezone.utc)).total_seconds()))
            await engine.retry_extraction(event_id, user_id=user_id)
        await engine.process_extractions(user_id=user_id, limit=len(unfinished), budget_ms=_ALL)
    return statuses


async def _retry_turn(engine, user_id: str, event_id: str) -> str:
    """Retry one turn's extraction after each delay in RETRY_DELAYS; returns its final status."""
    status = await engine.extraction_status(event_id, user_id=user_id)
    for delay in RETRY_DELAYS:
        if status is not None and status.status == "complete":
            break
        await asyncio.sleep(delay)
        await engine.retry_extraction(event_id, user_id=user_id)
        await engine.process_extractions(user_id=user_id, limit=1, budget_ms=_ALL)
        status = await engine.extraction_status(event_id, user_id=user_id)
    return "missing" if status is None else status.status


async def _drain_sources(engine, user_id: str) -> None:
    """Index every raw source note, which ingestion leaves to later processing."""
    while True:
        status = await engine.process_pending(user_id=user_id, budget_ms=60_000)
        if status.failed:
            raise ValueError(f"{status.failed} source records of {user_id} failed to index")
        if not status.pending:
            return
        if not status.processed:
            raise ValueError(f"{status.pending} source records of {user_id} are pending and none progressed")


async def _pack_summary(engine, user_id: str, events: dict[str, str], annotated: frozenset[str]) -> dict:
    """Counts over the finished pack, and the evidence turns its extracted records cite."""
    nodes = await engine.query_nodes(user_id=user_id, lifecycle_states=list(LifecycleState), limit=_ALL)
    key_by_event = {event_id: key for key, event_id in events.items()}
    active = [node for node in nodes if node.lifecycle_state in {LifecycleState.TENTATIVE, LifecycleState.STABLE}]
    claims = [node for node in active if node.node_type not in _NOT_CLAIMS]
    cited = {key_by_event[str(ref)] for node in claims for ref in node.evidence_refs if str(ref) in key_by_event}
    resolved = annotated & events.keys()
    return {
        "nodes": dict(sorted(Counter(f"{node.node_type.value}/{node.lifecycle_state.value}"
                                     for node in nodes).items())),
        "claims": len(claims), "claims_per_turn": len(claims) / len(events) if events else None,
        "entities": sum(node.node_type == NodeType.ENTITY for node in active),
        "turns_with_claims": len(cited),
        "annotated_evidence_turns": len(resolved),
        "annotated_evidence_turns_with_claims": len(resolved & cited),
        "evidence_claim_coverage": len(resolved & cited) / len(resolved) if resolved else None,
    }


def _log_totals(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return {"logged_turns": len(rows), "seconds": sum(row["seconds"] for row in rows),
            "calls": sum(row["calls"] for row in rows),
            "prompt_tokens": sum(row["prompt_tokens"] for row in rows),
            "completion_tokens": sum(row["completion_tokens"] for row in rows),
            "cache_hits": sum(row.get("cache_hits", 0) for row in rows),
            # Neither local inference nor an Ollama cloud model is billed per request or per token.
            "dollars": 0}


async def build_unit(folder: Path, unit: Unit, config_for, *, progress=None, prepare=None,
                     max_turns: int | None = None, cache: ExtractionCache | None = None) -> dict | None:
    """Ingest one unit into ``folder/pack`` and return its manifest; resumes a partial build.

    ``config_for(pack)`` gives the engine configuration. ``prepare(engine)``,
    for tests, runs before the first turn. With ``max_turns``, it stops after
    that many new turns and returns None, leaving a partial pack to resume. It
    also returns None, without a manifest, when a turn's extraction still
    fails after its retries.
    """
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    pack = folder / "pack"
    (pack / "lexical_index").mkdir(parents=True, exist_ok=True)
    log_path = folder / "turns.jsonl"
    keys = [turn["key"] for turn in unit.turns]
    async with MemoryEngine.open(config_for(pack)) as engine:
        if prepare is not None:
            prepare(engine)
        usage = _Usage(engine)
        hits = ExtractionCache(Path())  # counts this pack's hits when no cache is shared
        if cache is not None:
            hits = ExtractionCache(cache.folder)
            hits.wrap(engine._pipeline._extraction_provider)
        stored = await _stored_events(engine, unit.benchmark, unit.user_id)
        if set(stored) - set(keys):
            raise ValueError(f"The pack at {pack} holds turns that are not in {unit.unit_id}")
        # Finish the previous run's last turn before the next one, so extraction stays in source order.
        await _finish_extractions(engine, unit.user_id, stored.values())
        added = 0
        with log_path.open("a") as log:
            for number, turn in enumerate(unit.turns, start=1):
                if turn["key"] in stored:
                    continue
                if max_turns is not None and added == max_turns:
                    print(f"{unit.unit_id}: stopped after {max_turns} new turns", file=sys.stderr, flush=True)
                    return None
                added += 1
                before, hits_before = usage.snapshot(), hits.hits
                started, error = time.perf_counter(), None
                try:
                    event_id = await engine.ingest(turn["content"], user_id=unit.user_id, role=turn["role"],
                                                   speaker=turn["speaker"], session_id=turn["session_id"],
                                                   metadata=turn["metadata"], event_time=turn["event_time"],
                                                   wait_for_extraction=True)
                except Exception as exc:
                    # The event is saved before extraction runs, so a failed extraction is retried later.
                    error = f"{type(exc).__name__}: {exc}"
                    event_id = (await _stored_events(engine, unit.benchmark, unit.user_id)).get(turn["key"])
                    if event_id is None:
                        raise
                stored[turn["key"]] = event_id
                status = await _retry_turn(engine, unit.user_id, event_id) if error else "complete"
                seconds = time.perf_counter() - started
                calls, prompt_tokens, completion_tokens = (now - then for now, then in zip(usage.snapshot(), before))
                log.write(json.dumps({"key": turn["key"], "event_id": event_id, "role": turn["role"],
                                      "status": status, "error": error, "seconds": seconds, "calls": calls,
                                      "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                                      "cache_hits": hits.hits - hits_before}) + "\n")
                log.flush()
                if status != "complete":
                    print(f"{unit.unit_id}: paused at {turn['key']}, whose extraction failed: {error}",
                          file=sys.stderr, flush=True)
                    return None
                if progress is not None:
                    progress(unit.unit_id, number, len(unit.turns))
        if list(await _stored_events(engine, unit.benchmark, unit.user_id)) != keys:
            raise ValueError(f"The pack at {pack} does not hold every turn once, in source order")
        statuses = await _finish_extractions(engine, unit.user_id, stored.values())
        if set(statuses.values()) != {"complete"}:
            print(f"{unit.unit_id}: not complete, extraction status {dict(Counter(statuses.values()))}",
                  file=sys.stderr, flush=True)
            return None
        await _drain_sources(engine, unit.user_id)
        summary = await _pack_summary(engine, unit.user_id, stored, unit.annotated)
    manifest = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "benchmark": unit.benchmark, "unit_id": unit.unit_id,
        **({"conversation_id": unit.unit_id} if unit.benchmark == "locomo" else {"question_id": unit.unit_id}),
        "user_id": unit.user_id, "turns": len(unit.turns), "reference_time": unit.reference_time.isoformat(),
        "extraction_status": dict(sorted(Counter(statuses.values()).items())),
        "unfinished_extractions": sorted(key for key, event_id in stored.items() if statuses[event_id] != "complete"),
        **_log_totals(log_path), **summary, "pack_sha256": pack_identity(pack),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _build_record(label: str, benchmark: str, overrides: dict, config: PRMEConfig, identity: dict, *,
                  cache: bool) -> dict:
    from benchmarks.retrieval_eval import provenance

    gpt54 = _harness()
    datasets = {"locomo": gpt54.LOCOMO_SHA, "longmemeval": gpt54.lme.DATASET_SHA256}
    roles = {"locomo": "first speaker by sorted name: user; every other speaker: participant with speaker set",
             "longmemeval": "the dataset's user and assistant roles"}
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "label": label, "benchmark": benchmark,
            "dataset_sha256": datasets[benchmark], "overrides": overrides, "roles": roles[benchmark],
            "extraction_cache": cache, "provenance": provenance(config),
            "model": {name: identity.get(name) for name in
                      ("provider", "model", "resolved_model", "model_digest_sha256", "manifest_digest_sha256",
                       "remote_host", "remote_model", "api_base_url")}}


def _same_build(recorded: dict, current: dict) -> bool:
    keys = ("kind", "schema_version", "benchmark", "dataset_sha256", "overrides", "roles", "model")
    code = ("commit", "dirty", "worktree_sha256", "engine_config")
    return (all(recorded.get(key) == current.get(key) for key in keys)
            and recorded.get("extraction_cache", False) == current.get("extraction_cache", False)
            and all(recorded["provenance"].get(key) == current["provenance"].get(key) for key in code))


def _update_record(folder: Path, change) -> dict:
    """Read, change and atomically rewrite ``build.json`` under a lock, so parallel shards can share it."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "build.json"
    with (folder / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = change(json.loads(path.read_text()) if path.exists() else None)
        with tempfile.NamedTemporaryFile("w", dir=folder, delete=False, suffix=".tmp") as out:
            out.write(json.dumps(record, indent=2) + "\n")
        os.replace(out.name, path)
    return record


def _shard(value: str) -> tuple[int, int]:
    index, _, count = value.partition("/")
    shard = (int(index), int(count))
    if not 0 <= shard[0] < shard[1]:
        raise ValueError("A shard is K/N with 0 <= K < N")
    return shard


async def build(label: str, *, benchmark: str = "locomo", root: Path | None = None, units: list[str] | None = None,
                overrides: dict | None = None, model: str = DEFAULT_MODEL, jobs: int = 1,
                max_turns: int | None = None, cloud: bool = False, cache: bool = True,
                shard: tuple[int, int] | None = None) -> dict:
    """Build or resume every selected pack; returns the complete manifests by unit.

    ``units`` names conversations or questions (all by default). ``shard``
    (K, N) takes every Nth of them from the Kth, for parallel processes.
    ``max_turns`` stops each pack after that many new turns, for a trial run
    that a later build resumes.
    """
    if not label or Path(label).name != label:
        raise ValueError("The label must be a plain folder name")
    root = default_root() if root is None else root
    overrides = overrides or {}
    identity = model_identity(model, cloud=cloud)
    available = {unit.unit_id: unit for unit in benchmark_units(benchmark)}
    selected = units or list(available)
    unknown = sorted(set(selected) - available.keys())
    if unknown:
        raise ValueError(f"Unknown {benchmark} units: {', '.join(unknown)}")
    if shard is not None:
        selected = selected[shard[0]::shard[1]]
    folder = root / label
    current = _build_record(label, benchmark, overrides, build_config(Path("{pack}"), overrides, model=model),
                            identity, cache=cache)

    def start(saved: dict | None) -> dict:
        if saved is not None and not _same_build(saved, current):
            raise ValueError(f"{folder / 'build.json'} was built with other code, settings or model; "
                             "use a new label")
        record = saved or {**current, "started_at": datetime.now(timezone.utc).isoformat()}
        return _seen_version(record, identity)

    _update_record(folder, start)

    def progress(unit_id: str, number: int, total: int) -> None:
        if number % 25 == 0 or number == total:
            print(f"{unit_id}: {number}/{total} turns", file=sys.stderr, flush=True)

    semaphore = asyncio.Semaphore(max(1, jobs))
    shared = ExtractionCache(folder / "extraction-cache") if cache else None

    async def one(unit_id: str) -> tuple[str, dict | None]:
        async with semaphore:
            manifest = await build_unit(folder / benchmark / unit_id, available[unit_id],
                                        lambda pack: build_config(pack, overrides, model=model), progress=progress,
                                        max_turns=max_turns, cache=shared)
            if manifest is not None:
                print(f"{unit_id}: complete, {manifest['claims']} claims, evidence claim coverage "
                      f"{manifest['evidence_claim_coverage']}", file=sys.stderr, flush=True)
            return unit_id, manifest

    results = await asyncio.gather(*(one(unit_id) for unit_id in selected))
    manifests = {unit_id: manifest for unit_id, manifest in results if manifest is not None}
    identity = model_identity(model, cloud=cloud)

    def finish(record: dict) -> dict:
        record = _seen_version(record, identity)
        if len(built_units(folder / benchmark)) == len(available):
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
        return record

    _update_record(folder, finish)
    return manifests


def _seen_version(record: dict, identity: dict) -> dict:
    """Every Ollama server version seen, as the answer track records them (an upgrade can change outputs)."""
    versions = record.setdefault("server_versions", [])
    if identity.get("server_version") not in versions:
        versions.append(identity.get("server_version"))
    return record


def built_units(folder: Path) -> dict[str, dict]:
    """Manifests of the complete packs under a build's benchmark folder, by conversation or question."""
    manifests = {}
    for path in sorted(folder.glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        if manifest.get("kind") != KIND or manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{path} is not an extracted pack manifest")
        manifests[path.parent.name] = manifest
    return manifests


def build_digest(folder: Path) -> str:
    return hashlib.sha256((folder / "build.json").read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.extracted_packs",
                                     description="Build LoCoMo or LongMemEval-S memory packs through ingest() "
                                                 "with an Ollama extraction model, for the offline evidence gate.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="Build or resume a labeled set of packs")
    command.add_argument("--label", required=True, help="Folder name for this build, e.g. ingest-baseline")
    command.add_argument("--benchmark", choices=BENCHMARKS, default="locomo")
    command.add_argument("--root", type=Path, default=None,
                         help="Where the builds live; the main checkout's data/extracted-packs-v1 by default")
    command.add_argument("--unit", action="append",
                         help="A conversation or question ID; repeatable; every one by default")
    command.add_argument("--model", default=DEFAULT_MODEL, help="An Ollama model tag")
    command.add_argument("--cloud", action="store_true",
                         help="Accept an Ollama cloud model: Ollama's hosted service receives the turns")
    command.add_argument("--no-cache", action="store_true", help="Extract every turn afresh")
    command.add_argument("--jobs", type=int, default=1, help="Packs built at the same time")
    command.add_argument("--shard", type=_shard, metavar="K/N", help="Build every Nth pack from the Kth")
    command.add_argument("--max-turns", type=int,
                         help="Stop each pack after this many new turns; a later build resumes it")
    command.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                         help="Build setting: a dotted key and a JSON value, as for the gate")
    args = parser.parse_args(argv)
    try:
        overrides = parse_overrides(args.overrides)
        build_config(Path("{pack}"), overrides, model=args.model)
    except ValueError as exc:
        parser.error(str(exc))
    _quiet_offline_cli()
    manifests = asyncio.run(build(args.label, benchmark=args.benchmark, root=args.root, units=args.unit,
                                  overrides=overrides, model=args.model, jobs=args.jobs, max_turns=args.max_turns,
                                  cloud=args.cloud, cache=not args.no_cache, shard=args.shard))
    print(json.dumps({unit_id: {key: manifest[key] for key in (
        "turns", "extraction_status", "claims", "evidence_claim_coverage", "seconds")}
        for unit_id, manifest in manifests.items()}, indent=2))


if __name__ == "__main__":
    main()
