"""Record what the extraction model returns for a contract chat, once, so test_chat_ingest.py can replay it.

It runs the chat's four sessions through ``ingest_batch()``, one call per session, with the product defaults,
``memory_mode="address_first"`` and two write-time options set outright, typed extraction and corrections (so the
typed fields are asked for, and each batched request shows the earlier claims nearest its turns). The mode turned
both on when the fixtures were recorded on 2026-11-11 and no longer does, so they are named here to record the same
requests again. The run goes against the local Ollama server with ``deepseek-v4.1-flash:cloud``,
the only model the project's checks use; no paid provider is called. Every call the engine makes to the provider's
``extract_batch()`` and ``repair_pronouns()`` is saved with its request (the earlier claims it showed for
correction included) and the parsed answer, before any check. Afterwards it prints which claims ended retired,
against the chat's key. From the repository root:

    PYTHONPATH=. OPENAI_API_KEY= ANTHROPIC_API_KEY= uv run python \
        tests/contract/fixtures/record_chat_extraction.py contract
    PYTHONPATH=. OPENAI_API_KEY= ANTHROPIC_API_KEY= uv run python \
        tests/contract/fixtures/record_chat_extraction.py holdout

The first writes ``tests/contract/fixtures/chat_extraction.json``, the second
``tests/contract_holdout/fixtures/chat_extraction.json``. A live extraction is one sample: recording again gives
another one, so the expected failures of test_chat_ingest.py (``XFAIL_INGEST``) must then be decided again.
``--out`` writes elsewhere, to compare samples without replacing the fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from prme.storage.engine import MemoryEngine
from prme.types import NodeType
from tests.contract import scenes
from tests.contract.harness import (
    RECORDED_EXTRACTION,
    RECORDING_NAME,
    claim_record,
    memory_records,
    product_config,
    read_retirements,
    turn_record,
)

ROOT = Path(__file__).resolve().parents[3]
OLLAMA = "http://127.0.0.1:11434"


def ollama_identity() -> dict:
    """The Ollama server version and the model's local manifest digest (the hosted weights are not pinned)."""
    with urllib.request.urlopen(f"{OLLAMA}/api/version", timeout=10) as response:
        version = json.load(response)["version"]
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as response:
        digests = {model["name"]: model.get("digest") for model in json.load(response)["models"]}
    return {"ollama_version": version, "model_digest": digests.get(RECORDED_EXTRACTION["model"])}


def source_tree() -> dict:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "src"], cwd=ROOT).returncode != 0
    return {"commit": commit, "src_has_uncommitted_changes": dirty}


async def record(world: scenes.ChatWorld) -> dict:
    calls: list[dict] = []
    directory = tempfile.mkdtemp(prefix="prme-contract-record-")
    config = product_config(directory, mode="address_first", extraction=RECORDED_EXTRACTION,
                            enable_typed_extraction=True, enable_extraction_corrections=True)
    engine = await MemoryEngine.create(config)
    try:
        provider = engine._pipeline._extraction_provider
        extract_batch, repair_pronouns = provider.extract_batch, provider.repair_pronouns

        async def recording_extract_batch(turns, **options):
            response = await extract_batch(turns, **options)
            if hasattr(response, "model_dump"):
                response = response.model_dump(mode="json")
            calls.append({"method": "extract_batch", "options": {key: value for key, value in options.items()
                                                                  if key != "memories"},
                          "memories": memory_records(options.get("memories", ())),
                          "turns": [turn_record(turn) for turn in turns], "response": response})
            return response

        async def recording_repair_pronouns(window, claims, *, speaker=None):
            response = await repair_pronouns(window, claims, speaker=speaker)
            if hasattr(response, "model_dump"):
                response = response.model_dump(mode="json")
            calls.append({"method": "repair_pronouns", "window": list(window),
                          "claims": [claim_record(claim) for claim in claims], "speaker": speaker,
                          "response": response})
            return response

        async def refuse_single_turn(*_args, **_options):
            raise RuntimeError("the recording expects batched extraction only")

        provider.extract_batch = recording_extract_batch
        provider.repair_pronouns = recording_repair_pronouns
        provider.extract = refuse_single_turn
        for session_id, messages in scenes.chat_sessions(world):
            await engine.ingest_batch(messages, user_id=world.user, session_id=session_id, wait_for_extraction=True)
        while (status := await engine.process_pending(user_id=world.user, budget_ms=5000)).pending:
            pass
        if status.failed:
            raise SystemExit(f"{status.failed} sources failed to process; nothing saved")
        nodes = await engine.query_nodes(user_id=world.user, limit=1000)
        claims = sum(node.node_type == NodeType.FACT for node in nodes)
        typed = sum(node.node_type == NodeType.FACT and bool((node.metadata or {}).get("relation")) for node in nodes)
        retirements = await read_retirements(engine, world.user, world)
    finally:
        await engine.close()
    print(f"{len(calls)} calls, {claims} claims derived, {typed} with a relation", file=sys.stderr)
    for tag, found in retirements.matches.items():
        print(f"  key {tag}: " + ("; ".join(f"{line} [{'retired' if gone else 'active'}]" for line, gone in found)
                                  or "no claim"), file=sys.stderr)
    print(f"  retired outside the key: {retirements.wrong}", file=sys.stderr)
    return {
        "note": ("What the extraction model returned for this chat through ingest_batch() with the product defaults "
                 "and memory_mode='address_first', with typed extraction and corrections on. Replayed by test_chat_ingest.py; recorded by "
                 "tests/contract/fixtures/record_chat_extraction.py."),
        "recorded": {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "memory_mode": "address_first",
                     "enable_typed_extraction": True, "enable_extraction_corrections": True,
                     **RECORDED_EXTRACTION, **ollama_identity(), **source_tree(),
                     "claims_derived": claims, "claims_with_a_relation": typed,
                     "retired": retirements.retired, "retired_outside_the_key": retirements.wrong},
        "calls": calls,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("set", choices=("contract", "holdout"))
    parser.add_argument("--out", type=Path, default=None, help="write here instead of the set's fixture")
    args = parser.parse_args()
    if args.set == "contract":
        from tests.contract import world
        target = ROOT / "tests" / "contract" / "fixtures" / RECORDING_NAME
    else:
        from tests.contract_holdout import world
        target = ROOT / "tests" / "contract_holdout" / "fixtures" / RECORDING_NAME
    recording = asyncio.run(record(world.CHAT))
    target = args.out or target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(recording, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {target}", file=sys.stderr)


if __name__ == "__main__":
    main()
