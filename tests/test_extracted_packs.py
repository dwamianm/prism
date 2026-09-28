"""LoCoMo packs built through ingest() for the offline evidence gate (#91, #102)."""
from datetime import datetime, timezone
import json

import pytest

from benchmarks.diagnostics.extracted_packs import (
    _same_build, build_config, build_conversation, check_local_model, ingest_turns, locomo_conversations,
)
from benchmarks.diagnostics.product_packing import (
    _built_locomo_cases, gate_markdown, gate_report, pack_identity, replay_gate,
)
from prme.ingestion.schema import ExtractionResult

STAMP = "1:56 pm on 8 May, 2023"
TURNS = [
    ("D1:1", "Caroline", "Caroline paints sunsets over the lake."),
    ("D1:2", "Melanie", "Melanie adopted a puppy named Oscar."),
    ("D1:3", "Caroline", "Thanks, that sounds fun."),
]


@pytest.fixture
def mock_embeddings(monkeypatch):
    from tests.test_durable_ingestion import MockEmbeddingProvider

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())


@pytest.fixture
def locomo(tmp_path, monkeypatch):
    from benchmarks.integrations import run_gpt54_comparison as gpt54
    from benchmarks.integrations.gpt54_budget import digest

    path = tmp_path / "locomo10.json"
    path.write_text(json.dumps([{"sample_id": "conv-1", "conversation": {
        "session_1_date_time": STAMP,
        "session_1": [{"speaker": speaker, "dia_id": dialog_id, "text": text} for dialog_id, speaker, text in TURNS]},
        "qa": [{"question": "What does Caroline paint?", "answer": "Sunsets", "evidence": ["D1:1"], "category": 1},
               {"question": "What did Melanie adopt?", "answer": "A puppy", "evidence": ["D1:2", "D9:9"],
                "category": 1}]}]))
    monkeypatch.setattr(gpt54, "LOCOMO", path)
    monkeypatch.setattr(gpt54, "LOCOMO_SHA", digest(path))
    return locomo_conversations()["conv-1"]


def _claim(quote, subject, predicate, obj):
    return {"subject": subject, "subject_entity_type": "person", "predicate": predicate, "object": obj,
            "object_entity_type": None, "polarity": "positive", "evidence_quote": quote}


class ScriptedExtraction:
    """One grounded claim for each turn that states something; nothing for the last turn."""

    def __init__(self):
        self.calls = []

    async def extract(self, content, *, role=None):
        self.calls.append((content, role))
        for _, speaker, text in TURNS[:2]:
            if content.endswith(text):
                subject, predicate, obj = {"Caroline": ("Caroline", "paints", "sunsets"),
                                           "Melanie": ("Melanie", "adopted", "a puppy named Oscar")}[speaker]
                return ExtractionResult.model_validate({"entities": [{"name": subject, "entity_type": "person"}],
                                                        "facts": [_claim(text, subject, predicate, obj)]})
        return ExtractionResult.model_validate({"entities": [], "facts": []})


def test_turns_keep_the_harness_text_and_make_the_first_speaker_the_owner(locomo):
    assert [turn["content"] for turn in locomo] == [f"({STAMP}) {speaker}: {text}" for _, speaker, text in TURNS]
    assert [(turn["role"], turn["speaker"]) for turn in locomo] == [
        ("user", "Caroline"), ("participant", "Melanie"), ("user", "Caroline")]
    assert [turn["metadata"]["source_dialog_id"] for turn in locomo] == ["D1:1", "D1:2", "D1:3"]
    assert {turn["event_time"] for turn in locomo} == {datetime(2023, 5, 8, 13, 56, tzinfo=timezone.utc)}
    with pytest.raises(ValueError, match="its own dialog ID"):
        ingest_turns([locomo[0], locomo[0]])


def test_builds_extract_only_on_a_local_ollama_model(tmp_path):
    for identity in ({"provider": "ollama_cloud", "model": "deepseek-v4.1-flash:cloud"},
                     {"provider": "ollama", "model": "m", "remote_host": "https://ollama.com"},
                     {"provider": "openai", "model": "gpt-4o-mini"}):
        with pytest.raises(ValueError, match="not a local Ollama model"):
            check_local_model(identity)
    check_local_model({"provider": "ollama", "model": "prme-qwen3.5:35b-a3b-8k"})
    with pytest.raises(ValueError, match="loopback"):
        build_config(tmp_path, endpoint="https://api.openai.com/v1")
    config = build_config(tmp_path, model="local-model")
    assert (config.extraction.provider, config.extraction.model, config.extraction.base_url) == (
        "ollama", "local-model", "http://127.0.0.1:11434/v1")
    assert config.extraction.api_key is None and config.organizer.opportunistic_enabled is False


def test_a_resumed_build_must_match_the_recorded_code_settings_and_model():
    record = {"kind": "k", "schema_version": 1, "benchmark": "locomo", "dataset_sha256": "d", "overrides": {},
              "roles": "r", "model": {"model_digest_sha256": "a"},
              "provenance": {"commit": "c", "dirty": False, "worktree_sha256": "w", "engine_config": {}}}
    assert _same_build(record, json.loads(json.dumps(record)))
    for change in ({"overrides": {"enable_claim_merge": False}}, {"model": {"model_digest_sha256": "b"}}):
        assert not _same_build(record, {**record, **change})
    assert not _same_build(record, {**record, "provenance": {**record["provenance"], "commit": "e"}})


async def test_build_resumes_ingests_each_turn_once_and_the_gate_credits_extracted_records(
        tmp_path, locomo, mock_embeddings):
    folder = tmp_path / "build" / "locomo" / "conv-1"
    scripted = ScriptedExtraction()

    def prepare(engine):
        engine._pipeline._extraction_provider.extract = scripted.extract

    def config_for(pack):
        return build_config(pack, model="local-model")

    # A trial stops after one new turn and leaves a partial pack.
    assert await build_conversation(folder, "conv-1", locomo, config_for, prepare=prepare, max_turns=1) is None
    assert not (folder / "manifest.json").exists()
    manifest = await build_conversation(folder, "conv-1", locomo, config_for, prepare=prepare)
    assert [content for content, _ in scripted.calls] == [turn["content"] for turn in locomo]
    assert [role for _, role in scripted.calls] == ["user", "participant", "user"]
    logged = [json.loads(line) for line in (folder / "turns.jsonl").read_text().splitlines()]
    assert [row["dialog_id"] for row in logged] == ["D1:1", "D1:2", "D1:3"]
    assert {row["status"] for row in logged} == {"complete"} and all(row["calls"] == 0 for row in logged)
    assert manifest["extraction_status"] == {"complete": 3} and manifest["unfinished_extractions"] == []
    assert (manifest["turns"], manifest["claims"], manifest["turns_with_claims"]) == (3, 2, 2)
    assert manifest["annotated_evidence_turns"] == 2 and manifest["evidence_claim_coverage"] == 1.0
    assert manifest["reference_time"] == "2023-05-08T13:56:00+00:00" and manifest["dollars"] == 0
    assert manifest["pack_sha256"] == pack_identity(folder / "pack")

    # A complete conversation is never ingested again.
    assert await build_conversation(folder, "conv-1", locomo, config_for, prepare=prepare) == manifest
    assert len(scripted.calls) == 3

    cases = _built_locomo_cases(tmp_path / "build")
    assert [(case.question_id, case.user_id, case.pack) for case in cases] == [
        ("conv-1-q0000", "conv-1", folder / "pack"), ("conv-1-q0001", "conv-1", folder / "pack")]
    assert cases[1].unresolved == ("D9:9",) and cases[0].saved_context_sha256 == ""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    rows = await replay_gate(cases, scratch=scratch)
    assert pack_identity(folder / "pack") == manifest["pack_sha256"]
    for row in rows:
        assert row["context_matches_saved"] is False
        assert row["evidence"]["packed_by_extracted"] >= 1
    report = gate_report(rows, provenance={"commit": "c", "overrides": {}, "packs": {
        "path": "build", "build": "trial", "conversations": {"conv-1": manifest["pack_sha256"]}}})
    summary = report["benchmarks"]["locomo"]["summary"]
    assert summary["evidence_turns_resolved"] == 2 and summary["evidence_turns_packed_by_extracted"] >= 2
    markdown = gate_markdown(report)
    assert "**Built packs, not the saved run.**" in markdown and "differ from the saved run" not in markdown


async def test_build_retries_a_failed_extraction_before_the_pack_is_complete(tmp_path, locomo, mock_embeddings):
    folder = tmp_path / "build" / "locomo" / "conv-1"
    scripted = ScriptedExtraction()
    failures = []

    async def flaky(content, *, role=None):
        if content == locomo[1]["content"] and not failures:
            failures.append(content)
            raise TimeoutError("model timed out")
        return await scripted.extract(content, role=role)

    def prepare(engine):
        engine._pipeline._extraction_provider.extract = flaky

    manifest = await build_conversation(folder, "conv-1", locomo, lambda pack: build_config(pack, model="m"),
                                        prepare=prepare)
    logged = [json.loads(line) for line in (folder / "turns.jsonl").read_text().splitlines()]
    assert logged[1]["error"] is not None and logged[1]["status"] != "complete"
    assert manifest["extraction_status"] == {"complete": 3} and manifest["claims"] == 2
