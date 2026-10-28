"""The records derived from a recorded extraction are pinned, so a change to them needs a DERIVATION_VERSION bump.

``reingest()`` treats a source as current while its marker names the engine's derivation settings, and those
settings carry ``DERIVATION_VERSION`` instead of the package version, so a release that leaves what is derived
as it was leaves every stored memory current. The price is that a change which does alter what is derived must
bump the number by hand, and a hand is forgotten. This test derives the recorded extraction of
``fixtures/extraction/trip_batch_haiku45.json`` (what Claude Haiku 4.5 returned for four turns, kept as the
model wrote it) with a stub provider and no model, dumps the nodes and edges in a form that holds no ID and no
clock, and compares the dump with ``fixtures/derivation/trip_derived.json``.

When it fails, the records changed. If that is intended, bump ``DERIVATION_VERSION`` in
``src/prme/ingestion/reingest.py``, then refresh the saved dump:

    PRME_REFRESH_DERIVATION_DUMP=1 uv run python -m pytest tests/test_derivation_version.py

The refresh refuses to write changed records under the version the saved dump already has, so the dump cannot
be refreshed without the bump.
"""

import difflib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.reingest import DERIVATION_VERSION, derivation_settings
from tests.test_durable_ingestion import MockEmbeddingProvider

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = json.loads((FIXTURES / "extraction" / "trip_batch_haiku45.json").read_text())
SAVED = FIXTURES / "derivation" / "trip_derived.json"
REFRESH = "PRME_REFRESH_DERIVATION_DUMP"
T0 = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
OWNER = "derivation-guard"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Fields that hold a clock or an ID: the dump compares what was derived, not when or under which ID.
CLOCKS = {"id", "created_at", "updated_at", "last_reinforced_at"}

BUMP = (
    "The records derived from the recorded extraction changed.\n"
    "If the change is intended, bump DERIVATION_VERSION in src/prme/ingestion/reingest.py (every stored memory then "
    "reads as stale once and reingest() offers to re-derive it), then refresh the saved dump:\n"
    "    {refresh}=1 uv run python -m pytest tests/test_derivation_version.py\n"
    "If it is not intended, the change altered what an unchanged source derives.\n\n{diff}"
)


def pinned_config(tmp_path, monkeypatch) -> PRMEConfig:
    """The derivation options set outright, so a default that flips later does not move the dump.

    A flipped default changes the settings fingerprint by itself; this test is about the code that derives.
    """
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    (tmp_path / "lexical").mkdir()
    return PRMEConfig(
        db_path=str(tmp_path / "memory.duckdb"), lexical_path=str(tmp_path / "lexical"),
        vector_path=str(tmp_path / "vectors.usearch"), organizer={"opportunistic_enabled": False},
        materialization_budget_ms=5000, extraction_batch_target_tokens=2048, enable_reingest=True,
        enable_claim_merge=True, enable_speaker_references=True, enable_claim_sentence_text=True,
        enable_speaker_grounding=False, enable_ingest_claim_fixes=True, enable_mention_links=True,
        enable_owner_name_carry=True, enable_owner_group_links=True, enable_extraction_corrections=False,
    )


def mask(text: str, labels: dict[str, str]) -> str:
    return UUID.sub(lambda found: labels.get(found.group(0), "<id>"), text)


def canonical(events, nodes, edges) -> dict:
    """The nodes and edges as sorted rows with every ID replaced by a label and no clock in them."""
    labels = {str(event.id): f"event-{number}"
              for number, event in enumerate(sorted(events, key=lambda event: event.event_time), 1)}
    rows = []
    for node in nodes:
        row = {key: value for key, value in node.model_dump(mode="json").items() if key not in CLOCKS}
        if "extraction_kind" not in (node.metadata or {}):
            # An entity and a stored note take the clock of their creation; a claim takes its source's time.
            row.pop("valid_from")
        rows.append((node, json.dumps(row, sort_keys=True)))
    rows.sort(key=lambda item: mask(item[1], labels))
    labels.update({str(node.id): f"node-{number}" for number, (node, _) in enumerate(rows, 1)})
    edge_rows = sorted(
        mask(json.dumps({key: value for key, value in edge.model_dump(mode="json").items()
                         if key not in CLOCKS | {"valid_from"}}, sort_keys=True), labels)
        for edge in edges
    )
    return {"nodes": [json.loads(mask(text, labels)) for _, text in rows],
            "edges": [json.loads(text) for text in edge_rows]}


async def derive(tmp_path, monkeypatch) -> dict:
    """Store the recorded turns plainly, re-derive them with a stub that answers the recorded response, and dump."""
    by_text = {turn["content"]: entry for turn, entry in zip(RECORDED["turns"], RECORDED["response"]["turns"])}

    async def extract_batch(turns, **_):
        return {"turns": [{**by_text[turn.content], "turn": turn.index} for turn in turns]}

    async with MemoryEngine.open(pinned_config(tmp_path, monkeypatch)) as engine:
        engine._pipeline._extraction_provider.extract_batch = extract_batch
        for number, turn in enumerate(RECORDED["turns"]):
            await engine.store(turn["content"], user_id=OWNER, role=turn["role"], session_id="trip-1",
                               event_time=T0 + timedelta(minutes=number))
        result = await engine.reingest(OWNER)
        assert (result.total, result.processed, result.failed) == (4, 4, 0)
        events = await engine.get_events(user_id=OWNER, limit=100)
        nodes = await engine.query_nodes(user_id=OWNER, limit=1000)
        edges = await engine._graph_store.get_edges(node_ids=[str(node.id) for node in nodes])
        return canonical(events, nodes, edges)


async def test_the_records_derived_from_the_recorded_extraction_are_the_saved_ones(tmp_path, monkeypatch):
    records = await derive(tmp_path, monkeypatch)
    # Something was derived: entities, claims, and the links among them and the stored notes.
    kinds = {row["node_type"] for row in records["nodes"]}
    assert {"entity", "fact", "note"} <= kinds and len(records["nodes"]) > 12 and len(records["edges"]) > 12

    if os.environ.get(REFRESH):
        saved = json.loads(SAVED.read_text()) if SAVED.exists() else None
        if saved is not None and saved["records"] != records and saved["derivation_version"] == DERIVATION_VERSION:
            pytest.fail("The records changed and DERIVATION_VERSION did not: bump it in "
                        "src/prme/ingestion/reingest.py first, then refresh the dump.")
        SAVED.parent.mkdir(parents=True, exist_ok=True)
        SAVED.write_text(json.dumps({"derivation_version": DERIVATION_VERSION, "records": records},
                                    indent=1, sort_keys=True) + "\n")
        return

    saved = json.loads(SAVED.read_text())
    if records != saved["records"]:
        diff = "\n".join(list(difflib.unified_diff(
            json.dumps(saved["records"], indent=1, sort_keys=True).splitlines(),
            json.dumps(records, indent=1, sort_keys=True).splitlines(), "saved", "derived", lineterm=""))[:60])
        pytest.fail(BUMP.format(refresh=REFRESH, diff=diff))
    assert saved["derivation_version"] == DERIVATION_VERSION, (
        f"The saved dump is for DERIVATION_VERSION {saved['derivation_version']} and the engine's is "
        f"{DERIVATION_VERSION}. The records are the same, so refresh the dump to record the new number: "
        f"{REFRESH}=1 uv run python -m pytest tests/test_derivation_version.py")


async def test_the_guard_pins_every_option_the_settings_fingerprint_covers(tmp_path, monkeypatch):
    """A new option in the fingerprint must be set outright here, or a default could move the dump unseen."""
    async with MemoryEngine.open(pinned_config(tmp_path, monkeypatch)) as engine:
        settings = derivation_settings(engine._pipeline)
    assert settings["derivation_version"] == DERIVATION_VERSION and "engine" not in settings
    flags = {name for name, value in settings.items() if isinstance(value, bool)}
    assert flags == {"merge_repeated_claims", "bind_speaker_references", "claim_sentence_text", "link_mentions",
                     "owner_name_carry", "link_owner_groups", "extraction_corrections", "claim_fixes",
                     "speaker_grounding"}
    assert {name: settings[name] for name in sorted(flags)} == {
        "claim_fixes": True, "link_mentions": True, "link_owner_groups": True, "merge_repeated_claims": True,
        "owner_name_carry": True, "bind_speaker_references": True, "claim_sentence_text": True,
        "speaker_grounding": False, "extraction_corrections": False}

