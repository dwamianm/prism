"""The DeepSeek answer track over packs built through ingest() (``--contexts ingest``).

The saved run's packs were built with ``store()``, so an option that changes
extraction or the graph never reaches the contexts the answer track replays.
Its contexts can come from an ``extracted_packs`` build instead, on a track of
their own with its own contexts, answers, calibration, run logs and A/A and
verdict records, under the same rules.
"""

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from benchmarks.diagnostics import extracted_packs
from benchmarks.diagnostics import product_packing as gate
from benchmarks.integrations import gpt54_baselines as baselines
from benchmarks.retrieval_eval import provenance
from tests import test_gpt54_baselines
from tests.test_gpt54_baselines import OLLAMA_MODEL, answered, gate_case, make_gate_pack, ollama_provider

# The harness module's fixtures, its private data roots and the mock embedding model.
harness = test_gpt54_baselines.harness
private_data = test_gpt54_baselines.private_data
mock_embeddings = test_gpt54_baselines.mock_embeddings

INGEST = baselines.ollama_answers.AnswerModel(contexts="ingest")
QUESTIONS = ("conv-1-q0000", "conv-1-q0001")


def fake_build(root: Path, label: str, *, overrides: dict | None = None, commit: str = "a" * 40,
               dirty: bool = False, model: str = "deepseek-v4.1-flash:cloud") -> Path:
    """An ingest() build's record, as extracted_packs writes it; the replay's cases are faked separately."""
    packs = root / label
    packs.mkdir(parents=True)
    engine = provenance(gate.gate_config(Path("{pack}"), overrides))["engine_config"]
    (packs / "build.json").write_text(json.dumps({
        "kind": extracted_packs.KIND, "schema_version": extracted_packs.SCHEMA_VERSION, "label": label,
        "benchmark": "locomo", "dataset_sha256": "d" * 64, "overrides": overrides or {}, "roles": "the owner: user",
        "extraction_cache": True, "provenance": {"commit": commit, "dirty": dirty, "engine_config": engine},
        "model": {"provider": "ollama_cloud", "model": model, "manifest_digest_sha256": "e" * 64},
        "started_at": "2026-10-05T00:00:00+00:00", "server_versions": ["0.34.4"]}))
    return packs


async def built_cases(harness, monkeypatch) -> list[str]:
    """Two LoCoMo questions over one pack, which the gate replays for any build; returns the builds replayed."""
    pack = harness["root"] / "pack"
    identity = await make_gate_pack(pack)
    cases = [dataclasses.replace(gate_case(pack, identity), question_id=qid) for qid in QUESTIONS]
    replayed: list[str] = []

    def built(packs: Path, benchmark: str):
        replayed.append(packs.name)
        return cases

    monkeypatch.setattr(gate, "_built_cases", built)
    monkeypatch.setattr(gate, "_built_provenance", lambda packs, found: {"packs": {
        "path": str(packs), "build": packs.name, "model": "deepseek-v4.1-flash:cloud",
        "packs": {case.pack.parent.name: case.pack_sha256 for case in found}, "evidence_claim_coverage": {}}})
    return replayed


def test_ingest_contexts_are_answered_on_a_track_of_their_own(tmp_path):
    assert INGEST.track == "ollama-deepseek-v4.1-flash-cloud-ingest"
    assert OLLAMA_MODEL.track == "ollama-deepseek-v4.1-flash-cloud"
    assert baselines.data_root(INGEST) == baselines.OLLAMA_DATA / INGEST.track != baselines.data_root(OLLAMA_MODEL)
    # The reader and judge are the same; only where their contexts come from differs.
    assert INGEST.settings() == OLLAMA_MODEL.settings()
    with pytest.raises(ValueError, match="Contexts come from one of: store, ingest"):
        baselines.ollama_answers.AnswerModel(contexts="archive")
    assert baselines._aa_record_path(tmp_path, INGEST).name == f"{INGEST.track}-aa-checks.jsonl"
    assert baselines._verdict_record_path(tmp_path, INGEST).name == f"{INGEST.track}-pair-verdicts.jsonl"
    # A bare model name, as every earlier record names it, is the store() track's.
    assert baselines._aa_record_path(tmp_path, OLLAMA_MODEL.model) == baselines._aa_record_path(tmp_path, OLLAMA_MODEL)
    assert baselines._result_model({"model": OLLAMA_MODEL.model, "context_source": "ingest"}) == INGEST
    assert baselines._result_model({"model": OLLAMA_MODEL.model}) == OLLAMA_MODEL


@pytest.mark.usefixtures("mock_embeddings")
async def test_a_baseline_replays_a_defaults_build_and_a_variant_its_own_build(harness, monkeypatch):
    replayed = await built_cases(harness, monkeypatch)
    root, data = harness["root"] / "extracted", baselines.data_root(INGEST)
    defaults = fake_build(root, "defaults-locomo")
    sentences = fake_build(root, "sentences-locomo", overrides={"enable_claim_sentence_text": True})
    # A build with other settings than the defaults', or from uncommitted code, cannot hold the baseline.
    with pytest.raises(ValueError, match="not built with this commit's defaults"):
        baselines.prepare("prme", "locomo", data=data, packs=sentences)
    with pytest.raises(ValueError, match="uncommitted changes"):
        baselines.prepare("prme", "locomo", data=data, packs=fake_build(root, "dirty-locomo", dirty=True))
    with pytest.raises(ValueError, match="not both"):
        baselines.prepare("prme", "locomo", data=data, packs=defaults, archive=harness["archive"])
    with pytest.raises(ValueError, match="Only PRME's own arms"):
        baselines.prepare("plain-rrf", "locomo", data=data, packs=defaults)
    assert replayed == []
    prepared = await asyncio.to_thread(baselines.prepare, "prme", "locomo", data=data, packs=defaults)
    source = prepared["context_source"]
    assert (source["kind"], source["packs"], source["path"]) == ("ingest", "defaults-locomo", str(defaults))
    assert source["build"]["overrides"] == {} and source["build"]["commit"] == "a" * 40
    assert prepared["contexts_matching_saved_run"] is None
    assert "over the defaults-locomo ingest() packs" in prepared["context_rule"]
    assert "variant_settings" not in prepared and replayed == ["defaults-locomo"]
    # A variant's defaults replay reads the current baseline's packs, so it needs the baseline's own answer run (#139).
    with pytest.raises(ValueError, match="no defaults' packs to replay"):
        baselines.prepare("prme-sentences", "locomo", data=data, packs=sentences)
    answered(data, "locomo", prepared["provenance"]["commit"])
    # The two builds must differ only by the settings they were built with.
    for name, changes, differing in (("later", {"commit": "b" * 40}, "commit"),
                                     ("local", {"model": "qwen3:8b"}, "model")):
        other = fake_build(root, f"{name}-locomo", overrides={"enable_claim_sentence_text": True}, **changes)
        with pytest.raises(ValueError, match=f"differ from the baseline's defaults-locomo packs in {differing}"):
            baselines.prepare(f"prme-{name}", "locomo", data=data, packs=other)
    with pytest.raises(ValueError, match="conflicts with the value its ingest"):
        baselines.prepare("prme-sentences", "locomo", data=data, packs=sentences,
                          overrides=gate.parse_overrides(["enable_claim_sentence_text=false"]))
    replayed.clear()
    fold = gate.parse_overrides(["packing.fold_repeated_text=true"])
    variant = await asyncio.to_thread(baselines.prepare, "prme-sentences", "locomo", data=data, packs=sentences,
                                      overrides=fold)
    # The settings its packs were built with count as its own and apply to its replay, with those it sets (#130).
    assert variant["variant_settings"] == {"enable_claim_sentence_text": True, "packing.fold_repeated_text": True}
    assert variant["provenance"]["overrides"] == {"enable_claim_sentence_text": True,
                                                  "packing": {"fold_repeated_text": True}}
    assert variant["context_source"]["defaults_packs"] == "defaults-locomo"
    assert replayed == ["sentences-locomo", "defaults-locomo"]
    # Packs built with a setting change it on their own, so such a variant needs no --set.
    alone = await asyncio.to_thread(baselines.prepare, "prme-sentences-alone", "locomo", data=data, packs=sentences)
    assert alone["variant_settings"] == {"enable_claim_sentence_text": True}


@pytest.mark.usefixtures("mock_embeddings")
async def test_a_baseline_over_ingest_packs_is_answered_and_published_on_its_track(harness, monkeypatch):
    await built_cases(harness, monkeypatch)
    data = baselines.data_root(INGEST)
    defaults = fake_build(harness["root"] / "extracted", "defaults-locomo")
    await asyncio.to_thread(baselines.prepare, "prme", "locomo", data=data, packs=defaults)
    ollama_provider(monkeypatch)
    # Neither track answers contexts prepared for the other.
    with pytest.raises(ValueError, match="prepared over ingest packs, so the store track does not answer it"):
        await baselines.run("prme", "locomo", model=OLLAMA_MODEL, data=data, results=harness["results"])
    baselines.prepare("full-context", "locomo", data=data)
    await baselines.calibrate(INGEST, data=data)
    with pytest.raises(ValueError, match="prepared over store packs, so the ingest track does not answer it"):
        await baselines.run("full-context", "locomo", model=INGEST, data=data, results=harness["results"])
    result = await baselines.run("prme", "locomo", model=INGEST, data=data, results=harness["results"])
    assert result["complete"] and result["context_source"] == "ingest"
    assert result["prepared"]["context_source"]["packs"] == "defaults-locomo"
    [published] = harness["results"].glob(f"*/{INGEST.track}-prme-locomo-result.json")
    assert json.loads(published.read_text())["context_source"] == "ingest"
    # A result over ingest() packs is never compared with one over the saved store() packs.
    store = {key: value for key, value in result.items() if key != "context_source"}
    with pytest.raises(ValueError, match="differ in context_source"):
        baselines.compare(store, result)


@pytest.mark.parametrize("argv", [
    ["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--packs", "defaults-locomo"],
    ["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--contexts", "ingest"],
    ["prepare", "prme", "--benchmark", "locomo", "--contexts", "ingest", "--packs", "defaults-locomo"],
    ["run", "prme", "--benchmark", "locomo", "--provider", "ollama", "--contexts", "ingest", "--packs", "x"],
    ["calibrate", "--provider", "ollama", "--contexts", "ingest", "--packs", "defaults-locomo"],
    ["compare", "--before", "a.json", "--after", "b.json", "--provider", "ollama", "--contexts", "ingest"],
    ["record-aa-check", "--before", "a.json", "--after", "b.json", "--provider", "ollama", "--contexts", "ingest"],
    ["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--contexts", "ingest", "--packs", "../x"],
])
def test_cli_refuses_misplaced_context_sources(argv, monkeypatch):
    for name in ("run", "run_pair", "calibrate", "prepare", "compare", "record_aa_check"):
        monkeypatch.setattr(baselines, name, lambda *args, **kwargs: pytest.fail("started work"))
    monkeypatch.setattr(baselines, "_provenance", lambda: {"dirty": False})
    monkeypatch.setattr(baselines, "_on_main", lambda: True)
    with pytest.raises(SystemExit):
        baselines.main(argv)


def test_cli_calibrates_and_prepares_the_ingest_track(monkeypatch, tmp_path):
    seen = []

    async def fake_calibrate(model):
        seen.append(("calibrate", model))
        return {"complete": True}

    monkeypatch.setattr(baselines, "calibrate", fake_calibrate)
    monkeypatch.setattr(baselines, "_provenance", lambda: {"dirty": False, "commit": "1" * 40})
    monkeypatch.setattr(baselines, "_on_main", lambda: True)
    monkeypatch.setattr(baselines.gate, "_quiet_offline_cli", lambda: None)
    monkeypatch.setattr(extracted_packs, "default_root", lambda: tmp_path)
    monkeypatch.setattr(baselines, "prepare", lambda arm, benchmark, **kwargs: seen.append((arm, kwargs)) or {
        "arm": arm, "benchmark": benchmark, "questions": 0, "context_budget": 3996})
    baselines.main(["calibrate", "--provider", "ollama", "--contexts", "ingest"])
    baselines.main(["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--contexts", "ingest",
                    "--packs", "defaults-locomo"])
    baselines.main(["prepare", "prme", "--benchmark", "locomo", "--provider", "ollama", "--contexts", "ingest",
                    "--packs", "sentences-locomo", "--variant", "sentences"])
    assert seen[0] == ("calibrate", INGEST)
    assert [(arm, kwargs["data"], kwargs["packs"]) for arm, kwargs in seen[1:]] == [
        ("prme", baselines.data_root(INGEST), tmp_path / "defaults-locomo"),
        ("prme-sentences", baselines.data_root(INGEST), tmp_path / "sentences-locomo")]
