"""Build LoCoMo memory packs through ``ingest()`` with a local extraction model (#91, #102).

The offline evidence gate replays retrieval over the saved 2026-09-23 packs,
which the harness built with ``store()``. ``store()`` never runs extraction, so
the gate cannot see a change to extraction or the graph (#102). This module
builds fresh packs from the same LoCoMo source turns through the public
``ingest()`` path, and ``product_packing gate --packs`` replays retrieval over
them. The first such build is the ``ingest()`` baseline that #91 asks for
before its windowed extractor is measured.

Choices, and why:

- The source turns and their text are the registered harness's
  (``run_gpt54_comparison.source_turns``), so a raw turn reads as it does in
  the saved packs and its ``source_dialog_id`` names its annotation.
- The first speaker by sorted name is the memory owner, ``role="user"``. Every
  other speaker is stored as ``role="participant"`` with ``speaker`` set, the
  path #84 added for another person in a conversation. The ``store()`` harness
  maps that person to ``assistant``, which would make ``ingest()`` apply the
  assistant extraction policy to a human's turns.
- Extraction runs only on a local Ollama model at a loopback endpoint. Any
  other provider, a remote endpoint or a model the server does not run
  locally is refused, so a build cannot reach a paid API or send the dataset
  off this machine.
- The configuration is the gate's: current defaults, isolated from the
  environment and ``.env``, with the organizer off, so a pack holds exactly
  what ingestion wrote. ``--set`` changes build settings the same way.
- Turns are ingested one at a time in source order, and each waits for its
  extraction, as a live conversation would. A build resumes: a restarted
  conversation first finishes extraction left unfinished and then skips the
  turns its pack already holds. A resumed build must use the same commit,
  working tree, settings and model, or it is refused.

Layout under ``<root>/<label>/``: ``build.json`` (commit, configuration, model
identity, and every Ollama server version seen), and per conversation
``locomo/<id>/pack/`` (the memory pack), ``turns.jsonl`` (one line per turn
ingested: event, extraction status, seconds, model calls and tokens) and, once
complete, ``manifest.json`` (counts, the share of annotated evidence turns
that at least one extracted record cites, and the pack's tree identity).
Packs contain benchmark text, so the default root is the ignored ``data/``.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from benchmarks.diagnostics.product_packing import (
    _harness, _quiet_offline_cli, _source_key, gate_config, pack_identity, parse_overrides,
)
from benchmarks.integrations.ollama_answers import check_endpoint
from prme import MemoryEngine, NodeType, PRMEConfig
from prme.config import ExtractionConfig
from prme.types import LifecycleState

KIND = "prme-extracted-packs"
SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("data/extracted-packs-v1")
DEFAULT_MODEL = "prme-qwen3.5:35b-a3b-8k"
ENDPOINT = "http://127.0.0.1:11434/v1"
# Seconds per extraction call. The local model needs more than the 30 s default
# on long turns, and a timeout only fails the turn's extraction.
TIMEOUT_SECONDS = 180.0
# Passes over unfinished extraction after the ingest loop, for transient model failures.
RETRY_PASSES = 2
# Records that are not extracted claims: raw source notes and the entities claims attach to.
_NOT_CLAIMS = frozenset({NodeType.NOTE, NodeType.ENTITY})
# More than any one conversation holds; reads stay within one user.
_ALL = 1_000_000


def check_local_model(identity: dict) -> None:
    """Only a model the local Ollama server runs on this machine may extract benchmark text."""
    if identity.get("provider") != "ollama" or identity.get("remote_host") or identity.get("remote_model"):
        raise ValueError(f"{identity.get('model')} is not a local Ollama model. Builds extract only on this "
                         "machine, so no benchmark text leaves it and no paid API is called.")


def model_identity(model: str, endpoint: str = ENDPOINT) -> dict:
    """The local Ollama server's identity for the model, including the server version."""
    from benchmarks.integrations.run_longmemeval_v2 import _ollama_reader_identity

    check_endpoint(endpoint)
    parts = urlsplit(endpoint)
    identity = _ollama_reader_identity(urlunsplit((parts.scheme, parts.netloc, "", "", "")), model)
    check_local_model(identity)
    return identity


def build_config(pack: Path, overrides: dict | None = None, *, model: str = DEFAULT_MODEL,
                 endpoint: str = ENDPOINT) -> PRMEConfig:
    """The gate's isolated configuration over ``pack``, extracting with a local Ollama model."""
    check_endpoint(endpoint)
    config = gate_config(pack, overrides)
    with patch.dict(os.environ, {}, clear=True):
        extraction = ExtractionConfig(_env_file=None, provider="ollama", model=model,  # type: ignore[call-arg]
                                      base_url=endpoint, timeout=TIMEOUT_SECONDS)
    return config.model_copy(update={"extraction": extraction})


def ingest_turns(turns: list[dict]) -> list[dict]:
    """The harness's source turns as ``ingest()`` arguments: the first speaker owns the memory."""
    speakers = sorted({turn["metadata"]["source_speaker"] for turn in turns})
    dialog_ids = [turn["metadata"]["source_dialog_id"] for turn in turns]
    if None in dialog_ids or len(set(dialog_ids)) != len(dialog_ids):
        raise ValueError("Every source turn needs its own dialog ID so a build can resume")
    return [{"content": turn["content"], "session_id": turn["session_id"], "event_time": turn["event_time"],
             "metadata": turn["metadata"], "speaker": turn["metadata"]["source_speaker"],
             "role": "user" if turn["metadata"]["source_speaker"] == speakers[0] else "participant"}
            for turn in turns]


def locomo_conversations() -> dict[str, list[dict]]:
    """Each registered LoCoMo conversation's turns, as ``ingest()`` arguments."""
    gpt54 = _harness()
    if gpt54.digest(gpt54.LOCOMO) != gpt54.LOCOMO_SHA:
        raise ValueError("The LoCoMo dataset differs from the registered one")
    return {sample["sample_id"]: ingest_turns(gpt54.source_turns(sample["conversation"]))
            for sample in json.loads(gpt54.LOCOMO.read_text())}


def annotated_evidence(conversation_id: str) -> set[str]:
    """Dialog IDs that the gate's questions on this conversation cite as evidence."""
    return {key for question in _harness().question_rows("locomo")
            if question["conversation_id"] == conversation_id for key in question.get("evidence", [])}


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


async def _stored_events(engine, user_id: str) -> dict[str, str]:
    """Event ID by dialog ID for the turns this pack already holds, in the order they were saved."""
    events = sorted(await engine.get_events(user_id, limit=_ALL), key=lambda event: event.timestamp)
    return {key: str(event.id) for event in events
            if (key := _source_key("locomo", event.metadata or {})) is not None}


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


async def _pack_summary(engine, user_id: str, events: dict[str, str], annotated: set[str]) -> dict:
    """Counts over the finished pack, and the evidence turns its extracted records cite."""
    nodes = await engine.query_nodes(user_id=user_id, lifecycle_states=list(LifecycleState), limit=_ALL)
    dialog_by_event = {event_id: key for key, event_id in events.items()}
    active = [node for node in nodes if node.lifecycle_state in {LifecycleState.TENTATIVE, LifecycleState.STABLE}]
    claims = [node for node in active if node.node_type not in _NOT_CLAIMS]
    cited = {dialog_by_event[str(ref)] for node in claims for ref in node.evidence_refs
             if str(ref) in dialog_by_event}
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
            # Local inference is not billed per request or per token.
            "dollars": 0}


async def build_conversation(folder: Path, conversation_id: str, turns: list[dict], config_for,
                             *, progress=None, prepare=None, max_turns: int | None = None) -> dict | None:
    """Ingest one conversation into ``folder/pack`` and return its manifest; resumes a partial build.

    ``config_for(pack)`` gives the engine configuration. ``prepare(engine)``,
    for tests, runs before the first turn. With ``max_turns``, it stops after
    that many new turns and returns None, leaving a partial pack to resume.
    """
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    pack = folder / "pack"
    (pack / "lexical_index").mkdir(parents=True, exist_ok=True)
    log_path = folder / "turns.jsonl"
    async with MemoryEngine.open(config_for(pack)) as engine:
        if prepare is not None:
            prepare(engine)
        usage = _Usage(engine)
        stored = await _stored_events(engine, conversation_id)
        if set(stored) - {turn["metadata"]["source_dialog_id"] for turn in turns}:
            raise ValueError(f"The pack at {pack} holds turns that are not in {conversation_id}")
        # Finish the previous run's last turn before the next one, so extraction stays in source order.
        await _finish_extractions(engine, conversation_id, stored.values())
        added = 0
        with log_path.open("a") as log:
            for number, turn in enumerate(turns, start=1):
                dialog_id = turn["metadata"]["source_dialog_id"]
                if dialog_id in stored:
                    continue
                if max_turns is not None and added == max_turns:
                    return None
                added += 1
                before, started, error = usage.snapshot(), time.perf_counter(), None
                try:
                    event_id = await engine.ingest(turn["content"], user_id=conversation_id, role=turn["role"],
                                                   speaker=turn["speaker"], session_id=turn["session_id"],
                                                   metadata=turn["metadata"], event_time=turn["event_time"],
                                                   wait_for_extraction=True)
                except Exception as exc:
                    # The event is saved before extraction runs, so a failed extraction is retried later.
                    error = f"{type(exc).__name__}: {exc}"
                    event_id = (await _stored_events(engine, conversation_id)).get(dialog_id)
                    if event_id is None:
                        raise
                seconds = time.perf_counter() - started
                stored[dialog_id] = event_id
                status = await engine.extraction_status(event_id, user_id=conversation_id)
                calls, prompt_tokens, completion_tokens = (now - then for now, then in zip(usage.snapshot(), before))
                log.write(json.dumps({"dialog_id": dialog_id, "event_id": event_id, "role": turn["role"],
                                      "status": None if status is None else status.status, "error": error,
                                      "seconds": seconds, "calls": calls, "prompt_tokens": prompt_tokens,
                                      "completion_tokens": completion_tokens}) + "\n")
                log.flush()
                if progress is not None:
                    progress(conversation_id, number, len(turns))
        if list(await _stored_events(engine, conversation_id)) != [turn["metadata"]["source_dialog_id"]
                                                                  for turn in turns]:
            raise ValueError(f"The pack at {pack} does not hold every turn once, in source order")
        statuses = await _finish_extractions(engine, conversation_id, stored.values())
        await _drain_sources(engine, conversation_id)
        summary = await _pack_summary(engine, conversation_id, stored, annotated_evidence(conversation_id))
    unfinished = sorted(key for key, event_id in stored.items() if statuses[event_id] != "complete")
    manifest = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "benchmark": "locomo",
        "conversation_id": conversation_id, "user_id": conversation_id, "turns": len(turns),
        "reference_time": max(turn["event_time"] for turn in turns).isoformat(),
        "extraction_status": dict(sorted(Counter(statuses.values()).items())),
        "unfinished_extractions": unfinished, **_log_totals(log_path), **summary,
        "pack_sha256": pack_identity(pack),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _build_record(label: str, overrides: dict, config: PRMEConfig, identity: dict) -> dict:
    from benchmarks.retrieval_eval import provenance

    gpt54 = _harness()
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "label": label, "benchmark": "locomo",
            "dataset_sha256": gpt54.LOCOMO_SHA, "overrides": overrides,
            "roles": "first speaker by sorted name: user; every other speaker: participant with speaker set",
            "provenance": provenance(config),
            "model": {name: identity.get(name) for name in
                      ("provider", "model", "resolved_model", "model_digest_sha256", "api_base_url")}}


def _same_build(recorded: dict, current: dict) -> bool:
    keys = ("kind", "schema_version", "benchmark", "dataset_sha256", "overrides", "roles", "model")
    code = ("commit", "dirty", "worktree_sha256", "engine_config")
    return (all(recorded.get(key) == current.get(key) for key in keys)
            and all(recorded["provenance"].get(key) == current["provenance"].get(key) for key in code))


async def build(label: str, *, root: Path = DEFAULT_ROOT, conversations: list[str] | None = None,
                overrides: dict | None = None, model: str = DEFAULT_MODEL, jobs: int = 1,
                max_turns: int | None = None) -> dict:
    """Build or resume every selected conversation's pack; returns the complete manifests by conversation.

    ``max_turns`` stops each conversation after that many new turns, for a
    trial run that a later build resumes.
    """
    if not label or Path(label).name != label:
        raise ValueError("The label must be a plain folder name")
    overrides = overrides or {}
    identity = model_identity(model)
    available = locomo_conversations()
    selected = conversations or list(available)
    unknown = sorted(set(selected) - available.keys())
    if unknown:
        raise ValueError(f"Unknown LoCoMo conversations: {', '.join(unknown)}")
    folder = root / label
    config = build_config(Path("{pack}"), overrides, model=model)
    record = _build_record(label, overrides, config, identity)
    record_path = folder / "build.json"
    if record_path.exists():
        saved = json.loads(record_path.read_text())
        if not _same_build(saved, record):
            raise ValueError(f"{record_path} was built with other code, settings or model; use a new label")
        record = saved
    else:
        folder.mkdir(parents=True, exist_ok=True)
        record["started_at"] = datetime.now(timezone.utc).isoformat()
    # Every server version seen, as the answer track records them (an upgrade can change outputs).
    record.setdefault("server_versions", [])
    if identity.get("server_version") not in record["server_versions"]:
        record["server_versions"].append(identity.get("server_version"))
    record_path.write_text(json.dumps(record, indent=2) + "\n")

    def progress(conversation_id: str, number: int, total: int) -> None:
        if number % 25 == 0 or number == total:
            print(f"{conversation_id}: {number}/{total} turns", file=sys.stderr, flush=True)

    semaphore = asyncio.Semaphore(max(1, jobs))

    async def one(conversation_id: str) -> tuple[str, dict | None]:
        async with semaphore:
            manifest = await build_conversation(
                folder / "locomo" / conversation_id, conversation_id, available[conversation_id],
                lambda pack: build_config(pack, overrides, model=model), progress=progress,
                max_turns=max_turns)
            if manifest is None:
                print(f"{conversation_id}: stopped after {max_turns} new turns", file=sys.stderr, flush=True)
            else:
                print(f"{conversation_id}: complete, {manifest['claims']} claims, evidence claim coverage "
                      f"{manifest['evidence_claim_coverage']}", file=sys.stderr, flush=True)
            return conversation_id, manifest

    results = await asyncio.gather(*(one(conversation_id) for conversation_id in selected))
    manifests = {conversation_id: manifest for conversation_id, manifest in results if manifest is not None}
    identity = model_identity(model)
    if identity.get("server_version") not in record["server_versions"]:
        record["server_versions"].append(identity.get("server_version"))
    if len(manifests) == len(selected):
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    return manifests


def built_conversations(folder: Path) -> dict[str, dict]:
    """Manifests of the complete conversations under a build's ``locomo`` folder, by conversation."""
    manifests = {}
    for path in sorted(folder.glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        if manifest.get("kind") != KIND or manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{path} is not an extracted pack manifest")
        manifests[manifest["conversation_id"]] = manifest
    return manifests


def build_digest(folder: Path) -> str:
    return hashlib.sha256((folder / "build.json").read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.extracted_packs",
                                     description="Build LoCoMo memory packs through ingest() with a local "
                                                 "Ollama extraction model, for the offline evidence gate.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="Build or resume a labeled set of packs")
    command.add_argument("--label", required=True, help="Folder name for this build, e.g. ingest-baseline")
    command.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    command.add_argument("--conversation", action="append", help="Repeatable; every conversation by default")
    command.add_argument("--model", default=DEFAULT_MODEL, help="A local Ollama model tag")
    command.add_argument("--jobs", type=int, default=1, help="Conversations built at the same time")
    command.add_argument("--max-turns", type=int,
                         help="Stop each conversation after this many new turns; a later build resumes it")
    command.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                         help="Build setting: a dotted key and a JSON value, as for the gate")
    args = parser.parse_args(argv)
    try:
        overrides = parse_overrides(args.overrides)
        build_config(Path("{pack}"), overrides, model=args.model)
    except ValueError as exc:
        parser.error(str(exc))
    _quiet_offline_cli()
    manifests = asyncio.run(build(args.label, root=args.root, conversations=args.conversation,
                                  overrides=overrides, model=args.model, jobs=args.jobs, max_turns=args.max_turns))
    print(json.dumps({conversation_id: {key: manifest[key] for key in (
        "turns", "extraction_status", "claims", "evidence_claim_coverage", "seconds")}
        for conversation_id, manifest in manifests.items()}, indent=2))


if __name__ == "__main__":
    main()
