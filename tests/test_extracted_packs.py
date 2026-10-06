"""Benchmark packs built through ingest() for the offline evidence gate (#91, #102)."""
from datetime import datetime, timezone
import json

import pytest

from benchmarks.diagnostics import extracted_packs
from benchmarks.diagnostics.extracted_packs import (
    ExtractionCache, _same_build, build_config, build_unit, built_units, check_model, ingest_turns, locomo_units,
    longmemeval_units,
)
from benchmarks.diagnostics.product_packing import _built_cases, gate_markdown, gate_report, pack_identity, replay_gate
from prme.ingestion.schema import ExtractionResult

STAMP = "1:56 pm on 8 May, 2023"
TURNS = [
    ("D1:1", "Caroline", "Caroline paints sunsets over the lake."),
    ("D1:2", "Melanie", "Melanie adopted a puppy named Oscar."),
    ("D1:3", "Caroline", "Thanks, that sounds fun."),
]
# Claims the scripted model finds, by sentence; any other text yields none.
CLAIMS = {
    "Caroline paints sunsets over the lake.": ("Caroline", "paints", "sunsets"),
    "Melanie adopted a puppy named Oscar.": ("Melanie", "adopted", "a puppy named Oscar"),
    "Jordan moved to Denver last spring.": ("Jordan", "moved_to", "Denver"),
}


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
    [unit] = locomo_units()
    return unit


@pytest.fixture
def longmemeval(tmp_path, monkeypatch):
    from benchmarks.integrations import run_gpt54_comparison as gpt54
    from benchmarks.integrations.gpt54_budget import digest

    shared = [{"role": "user", "content": "Jordan moved to Denver last spring.", "has_answer": True},
              {"role": "assistant", "content": "Denver is a lovely city."}]
    path = tmp_path / "longmemeval_s.json"
    path.write_text(json.dumps([
        {"question_id": "q1", "question_type": "single-session-user", "question": "Where did Jordan move?",
         "question_date": "2023/06/01 (Thu) 10:00", "haystack_session_ids": ["s-a", "s-b"],
         "haystack_dates": ["2023/05/01 (Mon) 09:00", "2023/05/02 (Tue) 09:00"],
         "haystack_sessions": [shared, [{"role": "user", "content": "Hello."}, {"role": "assistant", "content": " "}]]},
        {"question_id": "q2_abs", "question_type": "single-session-user", "question": "Where did Jordan study?",
         "question_date": "2023/06/02 (Fri) 10:00", "haystack_session_ids": ["s-a"],
         "haystack_dates": ["2023/05/01 (Mon) 09:00"], "haystack_sessions": [shared]},
    ]))
    monkeypatch.setattr(gpt54, "LONGMEM", path)
    monkeypatch.setattr(gpt54.lme, "DATASET_SHA256", digest(path))
    return {unit.unit_id: unit for unit in longmemeval_units()}


class ScriptedExtraction:
    """One grounded claim for each known sentence; nothing for other text."""

    def __init__(self):
        self.calls = []

    async def extract(self, content, *, role=None):
        self.calls.append((content, role))
        for text, (subject, predicate, obj) in CLAIMS.items():
            if content.endswith(text):
                return ExtractionResult.model_validate({
                    "entities": [{"name": subject, "entity_type": "person"}],
                    "facts": [{"subject": subject, "subject_entity_type": "person", "predicate": predicate,
                               "object": obj, "object_entity_type": None, "polarity": "positive",
                               "evidence_quote": text}]})
        return ExtractionResult.model_validate({"entities": [], "facts": []})


def _scripted(extract):
    def prepare(engine):
        engine._pipeline._extraction_provider.extract = extract
    return prepare


def _config(pack):
    return build_config(pack, model="local-model")


def test_turns_keep_the_harness_text_and_make_the_first_speaker_the_owner(locomo):
    turns = locomo.turns
    assert [turn["content"] for turn in turns] == [f"({STAMP}) {speaker}: {text}" for _, speaker, text in TURNS]
    assert [(turn["role"], turn["speaker"]) for turn in turns] == [
        ("user", "Caroline"), ("participant", "Melanie"), ("user", "Caroline")]
    assert [turn["key"] for turn in turns] == ["D1:1", "D1:2", "D1:3"]
    assert {turn["event_time"] for turn in turns} == {datetime(2023, 5, 8, 13, 56, tzinfo=timezone.utc)}
    assert locomo.annotated == {"D1:1", "D1:2", "D9:9"} and locomo.reference_time == turns[-1]["event_time"]
    with pytest.raises(ValueError, match="its own source key"):
        ingest_turns([turns[0], turns[0]])


def test_longmemeval_turns_keep_the_baseline_runners_roles_metadata_and_dates(longmemeval):
    question = longmemeval["q1"]
    assert [(turn["role"], turn["content"], turn["session_id"]) for turn in question.turns] == [
        ("user", "Jordan moved to Denver last spring.", "00000:s-a"),
        ("assistant", "Denver is a lovely city.", "00000:s-a"), ("user", "Hello.", "00001:s-b")]
    assert question.turns[0]["metadata"] == {"benchmark": "longmemeval-s", "source_session_id": "s-a",
                                             "source_session_position": 0, "source_turn_index": 0,
                                             "source_role": "user"}
    assert question.turns[2]["event_time"] == datetime(2023, 5, 2, 9, tzinfo=timezone.utc)
    assert question.user_id == "longmemeval-s-baseline"
    assert question.reference_time == datetime(2023, 6, 1, 10, tzinfo=timezone.utc)
    assert question.annotated == {"s-a#0#0"} and longmemeval["q2_abs"].annotated == frozenset()


def test_builds_extract_only_on_an_ollama_model_and_on_a_cloud_model_only_when_asked(tmp_path):
    for identity in ({"provider": "ollama_cloud", "model": "deepseek-v4.1-flash:cloud"},
                     {"provider": "ollama", "model": "m", "remote_host": "https://ollama.com"},
                     {"provider": "openai", "model": "gpt-4o-mini"}):
        with pytest.raises(ValueError, match="not a local Ollama model"):
            check_model(identity)
    check_model({"provider": "ollama", "model": "prme-qwen3.5:35b-a3b-8k"})
    check_model({"provider": "ollama_cloud", "model": "deepseek-v4.1-flash:cloud"}, cloud=True)
    for identity in ({"provider": "ollama", "model": "m"}, {"provider": "openai", "model": "gpt-4o-mini"}):
        with pytest.raises(ValueError, match="not an Ollama cloud model"):
            check_model(identity, cloud=True)
    with pytest.raises(ValueError, match="loopback"):
        build_config(tmp_path, endpoint="https://api.openai.com/v1")
    config = build_config(tmp_path, model="local-model")
    assert (config.extraction.provider, config.extraction.model, config.extraction.base_url) == (
        "ollama", "local-model", "http://127.0.0.1:11434/v1")
    assert config.extraction.api_key is None and config.organizer.opportunistic_enabled is False


def test_a_resumed_build_must_match_the_recorded_code_settings_and_model():
    record = {"kind": "k", "schema_version": 1, "benchmark": "locomo", "dataset_sha256": "d", "overrides": {},
              "roles": "r", "model": {"model_digest_sha256": "a"}, "extraction_cache": True,
              "provenance": {"commit": "c", "dirty": False, "worktree_sha256": "w", "engine_config": {}}}
    assert _same_build(record, json.loads(json.dumps(record)))
    for change in ({"overrides": {"enable_claim_merge": False}}, {"model": {"model_digest_sha256": "b"}},
                   {"extraction_cache": False}, {"benchmark": "longmemeval"}):
        assert not _same_build(record, {**record, **change})
    assert not _same_build(record, {**record, "provenance": {**record["provenance"], "commit": "e"}})


async def test_build_resumes_ingests_each_turn_once_and_the_gate_credits_extracted_records(
        tmp_path, locomo, mock_embeddings):
    folder = tmp_path / "build" / "locomo" / "conv-1"
    scripted = ScriptedExtraction()
    prepare = _scripted(scripted.extract)

    # A trial stops after one new turn and leaves a partial pack.
    assert await build_unit(folder, locomo, _config, prepare=prepare, max_turns=1) is None
    assert not (folder / "manifest.json").exists()
    manifest = await build_unit(folder, locomo, _config, prepare=prepare)
    assert [content for content, _ in scripted.calls] == [turn["content"] for turn in locomo.turns]
    assert [role for _, role in scripted.calls] == ["user", "participant", "user"]
    logged = [json.loads(line) for line in (folder / "turns.jsonl").read_text().splitlines()]
    assert [row["key"] for row in logged] == ["D1:1", "D1:2", "D1:3"]
    assert {row["status"] for row in logged} == {"complete"} and all(row["calls"] == 0 for row in logged)
    assert manifest["extraction_status"] == {"complete": 3} and manifest["unfinished_extractions"] == []
    assert (manifest["turns"], manifest["claims"], manifest["turns_with_claims"]) == (3, 2, 2)
    assert manifest["annotated_evidence_turns"] == 2 and manifest["evidence_claim_coverage"] == 1.0
    assert manifest["reference_time"] == "2023-05-08T13:56:00+00:00" and manifest["dollars"] == 0
    assert manifest["conversation_id"] == manifest["unit_id"] == "conv-1"
    assert manifest["pack_sha256"] == pack_identity(folder / "pack")

    # A complete pack is never ingested again.
    assert await build_unit(folder, locomo, _config, prepare=prepare) == manifest
    assert len(scripted.calls) == 3 and set(built_units(tmp_path / "build" / "locomo")) == {"conv-1"}

    cases = _built_cases(tmp_path / "build", "locomo")
    assert [(case.question_id, case.user_id, case.pack) for case in cases] == [
        ("conv-1-q0000", "conv-1", folder / "pack"), ("conv-1-q0001", "conv-1", folder / "pack")]
    assert cases[1].unresolved == ("D9:9",) and cases[0].saved_context_sha256 == ""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    # Folding would leave out the claims, whose text their turn already shows, so it is off here.
    rows = await replay_gate(cases, scratch=scratch, overrides={"packing": {"fold_repeated_text": False}})
    assert pack_identity(folder / "pack") == manifest["pack_sha256"]
    for row in rows:
        assert row["context_matches_saved"] is False
        assert row["evidence"]["packed_by_extracted"] >= 1
    report = gate_report(rows, provenance={"commit": "c", "overrides": {}, "packs": {
        "path": "build", "build": "trial", "model": "local-model", "packs": {"conv-1": manifest["pack_sha256"]}}})
    summary = report["benchmarks"]["locomo"]["summary"]
    assert summary["evidence_turns_resolved"] == 2 and summary["evidence_turns_packed_by_extracted"] >= 2
    markdown = gate_markdown(report)
    assert "**Built packs, not the saved run.**" in markdown and "differ from the saved run" not in markdown


async def test_a_failed_extraction_is_retried_before_the_next_turn_and_pauses_the_pack_if_it_persists(
        tmp_path, locomo, mock_embeddings, monkeypatch):
    monkeypatch.setattr(extracted_packs, "RETRY_DELAYS", (0.0, 0.0))
    folder = tmp_path / "build" / "locomo" / "conv-1"
    scripted = ScriptedExtraction()
    failures = []

    async def flaky(content, *, role=None):
        if content == locomo.turns[1]["content"] and len(failures) < limit:
            failures.append(content)
            raise TimeoutError("model timed out")
        return await scripted.extract(content, role=role)

    # Every attempt fails, as under a rate limit: the pack pauses at that turn without a manifest.
    limit = 10
    assert await build_unit(folder, locomo, _config, prepare=_scripted(flaky)) is None
    logged = [json.loads(line) for line in (folder / "turns.jsonl").read_text().splitlines()]
    assert [row["key"] for row in logged] == ["D1:1", "D1:2"] and logged[0]["status"] == "complete"
    assert logged[1]["status"] in {"pending", "failed"}
    assert logged[1]["error"] is not None and not (folder / "manifest.json").exists()
    assert [content for content, _ in scripted.calls] == [locomo.turns[0]["content"]]

    # The next build finishes that turn before the later one, then completes the pack.
    limit = 0
    manifest = await build_unit(folder, locomo, _config, prepare=_scripted(flaky))
    assert [content for content, _ in scripted.calls] == [turn["content"] for turn in locomo.turns]
    assert manifest["extraction_status"] == {"complete": 3} and manifest["claims"] == 2

    # A single failure is retried in place, so the pack completes in one build.
    failures.clear()
    limit = 1
    other = tmp_path / "build" / "locomo" / "conv-2"
    manifest = await build_unit(other, locomo, _config, prepare=_scripted(flaky))
    logged = [json.loads(line) for line in (other / "turns.jsonl").read_text().splitlines()]
    assert logged[1]["error"] is not None and logged[1]["status"] == "complete"
    assert manifest["extraction_status"] == {"complete": 3} and manifest["claims"] == 2


async def test_a_shared_cache_extracts_a_repeated_turn_once_and_the_gate_replays_longmemeval(
        tmp_path, longmemeval, mock_embeddings):
    build = tmp_path / "build"
    cache = ExtractionCache(build / "extraction-cache")
    scripted = ScriptedExtraction()
    first = await build_unit(build / "longmemeval" / "q1", longmemeval["q1"], _config,
                             prepare=_scripted(scripted.extract), cache=cache)
    second = await build_unit(build / "longmemeval" / "q2_abs", longmemeval["q2_abs"], _config,
                              prepare=_scripted(scripted.extract), cache=cache)
    # The shared session is extracted once; the cached response is replayed and grounds the same claim.
    assert [content for content, _ in scripted.calls] == [
        "Jordan moved to Denver last spring.", "Denver is a lovely city.", "Hello."]
    assert (first["cache_hits"], second["cache_hits"]) == (0, 2)
    assert first["claims"] == second["claims"] == 1
    assert first["evidence_claim_coverage"] == 1.0 and second["evidence_claim_coverage"] is None
    assert first["question_id"] == "q1" and first["user_id"] == "longmemeval-s-baseline"

    cases = _built_cases(build, "longmemeval")
    assert [(case.question_id, case.evidence) for case in cases] == [("q1", {"s-a#0#0"}), ("q2_abs", None)]
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    answered, _ = await replay_gate(cases, scratch=scratch)
    assert answered["evidence"]["all_packed"] is True and answered["evidence"]["packed_by_extracted"] == 1


class _Provider:
    """A model-backed provider as the cache sees it: its identity and extract()."""

    model_name, _model, _temperature, _reasoning_effort = "m", "m", 0.0, None

    def __init__(self):
        self.calls = []

    async def extract(self, content, *, role="user", **extra):
        self.calls.append(extra)
        return ExtractionResult.model_validate({"entities": [], "facts": []})


async def test_a_cached_response_replays_every_claim_the_live_validation_kept(tmp_path):
    from prme.ingestion.extraction import _CitedExtractionResult

    source = ("Really well. If Brightpath makes an offer, I'll probably take it. "
              "Their team uses Python for everything, which I love.")

    class Validating(_Provider):
        async def extract(self, content, *, role="user", **extra):
            self.calls.append(extra)
            return _CitedExtractionResult.model_validate({
                "entities": [{"name": "Python", "entity_type": "technology"}],
                "facts": [{"subject": "I", "predicate": "likes", "object": "Python", "fact_type": "preference",
                           "epistemic_type": "observed", "polarity": "positive",
                           "evidence_quote": "Their team uses Python for everything, which I love."}]},
                context={"source_text": content, "source_role": role})

    provider = Validating()
    cache = ExtractionCache(tmp_path)
    cache.wrap(provider)
    live = await provider.extract(source, role="user")
    # Validation widened the quote to its whole paragraph, which also holds another sentence's condition.
    assert [fact.evidence_quote for fact in live.facts] == [source]
    replayed = await provider.extract(source, role="user")
    assert (cache.misses, cache.hits) == (1, 1)
    assert replayed.model_dump() == live.model_dump()


async def test_a_cached_raw_output_is_checked_under_each_calls_grounding_rule(tmp_path):
    from prme.ingestion.extraction import _CitedExtractionResult

    source = "I live in Denver."
    raw = {"entities": [{"name": "Dana", "entity_type": "person"}, {"name": "Denver", "entity_type": "location"}],
           "facts": [{"subject": "Dana", "predicate": "lives_in", "object": "Denver", "polarity": "positive",
                      "epistemic_type": "observed", "evidence_quote": source}]}

    class Parsing(_Provider):
        async def extract(self, content, *, role="user", **extra):
            self.calls.append(extra)
            # As the instructor provider does: parse the model's output, then check it.
            return _CitedExtractionResult.model_validate_json(json.dumps(raw), context={
                "source_text": content, "source_role": role, "speaker": extra.get("speaker")})

    provider = Parsing()
    cache = ExtractionCache(tmp_path)
    cache.wrap(provider)
    live = await provider.extract(source, role="user", speaker="Dana")
    assert [(fact.subject, fact.object) for fact in live.facts] == [("Dana", "Denver")]
    # The same output checked without the speaker loses the claim, and with the speaker gives the live result.
    assert (await provider.extract(source, role="user")).facts == []
    assert (await provider.extract(source, role="user", speaker="Dana")).model_dump() == live.model_dump()
    assert provider.calls == [{"speaker": "Dana"}]
    assert (cache.misses, cache.hits, cache.unreproduced) == (1, 2, 0)
    assert [path.name.endswith(".raw.json") for path in tmp_path.rglob("*.json")] == [True]


async def test_an_entry_saved_after_its_checks_does_not_serve_a_call_with_a_speaker(tmp_path):
    provider = _Provider()
    cache = ExtractionCache(tmp_path)
    cache.wrap(provider)
    for speaker in (None, None, "Dana"):
        await provider.extract("Hello.", role="user", **({} if speaker is None else {"speaker": speaker}))
    # This provider has no raw output, so its checked result is kept. It was checked without a speaker.
    assert provider.calls == [{}, {"speaker": "Dana"}]
    assert (cache.misses, cache.hits) == (2, 1)


def test_a_turn_without_a_window_keeps_its_cache_key(tmp_path):
    import hashlib

    from prme.ingestion.extraction import _extraction_prompt_for_role

    provider = _Provider()
    before = hashlib.sha256(json.dumps(["m", "m", 0.0, None, _extraction_prompt_for_role("user"), "user",
                                        "Hello."]).encode()).hexdigest()
    assert ExtractionCache(tmp_path)._key(provider, "Hello.", "user") == before


async def test_the_window_and_resolution_reach_the_provider_and_the_cache_key(tmp_path):
    from prme.ingestion.extraction import FactTextResolution

    provider = _Provider()
    calls = provider.calls
    cache = ExtractionCache(tmp_path)
    cache.wrap(provider)
    resolve = FactTextResolution(speaker="Caroline", source_time=datetime(2023, 5, 8, tzinfo=timezone.utc))
    await provider.extract("Hello.", role="user")
    await provider.extract("Hello.", role="user", context=["Caroline: hi"])
    await provider.extract("Hello.", role="user", context=["Caroline: hi"], resolve=resolve)
    await provider.extract("Hello.", role="user", context=["Caroline: hi"], resolve=resolve)
    # Each different request is a miss and reaches the model as ingest() sends it; the repeat is a hit.
    assert calls == [{}, {"context": ["Caroline: hi"]}, {"context": ["Caroline: hi"], "resolve": resolve}]
    assert (cache.misses, cache.hits) == (3, 1)
