"""Product evidence credit requires source text, not merely an included UUID."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import warnings

import pytest

from benchmarks.diagnostics import product_packing
from benchmarks.diagnostics.product_packing import (
    GATE_CONDITIONAL_ACCURACY, GATE_MEASURED_ACCURACY, GATE_UNSCORED_ACCURACY, GateCase, _locomo_cases,
    _longmemeval_cases, _source_key, compare, compare_gates, comparison_markdown, gate_config, gate_main,
    gate_markdown, gate_report, main, measure, pack_identity, pack_records, parse_overrides, plain_ranking,
    plain_record, projected_correct, replay_gate, run_gate, summarize_gate,
)
from benchmarks.evidence import reciprocal_rank_fusion
from prme import MemoryEngine, NodeType
from prme.config import OrganizerConfig
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import compute_str, pack_context, requires_memory_text
from prme.retrieval.tokenization import count_tokens


def make_candidate(sid, text, score):
    return RetrievalCandidate(
        node=MemoryNode(user_id="u", node_type="note", content=text, metadata={"source_turn": sid}),
        composite_score=score, path_count=2, paths=["VECTOR", "LEXICAL"],
    )


def fixture_report(tmp_path):
    relevant = make_candidate("s0:t0", "The project uses PostgreSQL. " * 100, .95)
    short = make_candidate("s1:t0", "Thanks.", .5)
    cfg = PackingConfig(
        token_budget=3000,
        overhead_tokens=0,
        min_fidelity="full",
        multipath_ordering="density",
    )
    cfg.token_budget = pack_context([relevant], cfg).tokens_used
    candidates = [relevant, short]
    control = pack_context(candidates, cfg)
    snapshot = {"question_id": "q", "packing_config": cfg.model_dump(mode="json"),
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "control": {"context": control.render(), "tokens": control.tokens_used}}
    filename = hashlib.sha256(b"q").hexdigest() + ".json"
    raw = json.dumps(snapshot).encode()
    (tmp_path / filename).write_bytes(raw)
    return {"complete": True, "errors": 0, "process_exit_code": 0,
            "dataset": {"split": "dev", "selected_question_ids": ["q"]},
            "provenance": {"engine_config": {"packing": cfg.model_dump(mode="json")}},
            "budgets": [cfg.token_budget], "details": [
                {"question_id": "q", "category": "single-session-user", "evidence_source_ids": ["s0:t0"],
                 "candidate_snapshot": {"filename": filename, "sha256": hashlib.sha256(raw).hexdigest()}}]}


def test_frozen_comparison_changes_only_order_and_labels_do_not_control_packing(tmp_path):
    report = fixture_report(tmp_path)
    result = compare(report, tmp_path, samples=20)
    budget = str(report["budgets"][0])
    stats = result["summary"][budget]["evidence_recall"]
    assert stats["before"] == 0 and stats["after"] == 1
    changed = deepcopy(report)
    changed["details"][0]["evidence_source_ids"] = ["s1:t0"]
    other = compare(changed, tmp_path, samples=20)
    for name in ("density", "score"):
        old = result["details"][0]["variants"][name][budget]
        new = other["details"][0]["variants"][name][budget]
        assert old["context_sha256"] == new["context_sha256"]
        assert old["evidence_recall"] != new["evidence_recall"]
    from prme.retrieval import packing
    assert packing.compute_str is compute_str


def test_pointer_only_evidence_is_not_recall_and_unlabeled_is_null():
    source = make_candidate("s0:t0", "Critical evidence with exception. " * 1000, .9)
    cfg = PackingConfig(token_budget=300, overhead_tokens=0, min_fidelity="reference")
    bundle = pack_context([source], cfg)
    measured = measure(bundle, {"s0:t0"}, cfg)
    assert measured["pointer_source_ids"] == ["s0:t0"]
    assert measured["content_source_ids"] == []
    assert measured["evidence_recall"] == 0 and not measured["all_evidence_retained"]
    unlabeled = measure(bundle, set(), cfg)
    assert unlabeled["evidence_recall"] is None and unlabeled["all_evidence_retained"] is None


@pytest.mark.parametrize("mutation", ["exit", "split", "missing", "duplicate", "hash", "context", "tokens", "config"])
def test_invalid_or_unreproducible_captures_are_rejected(tmp_path, mutation):
    report = fixture_report(tmp_path)
    if mutation == "exit":
        report["process_exit_code"] = -6
    elif mutation == "split":
        report["dataset"]["split"] = "test"
    elif mutation == "missing":
        report["dataset"]["selected_question_ids"].append("missing")
    elif mutation == "duplicate":
        report["details"].append(deepcopy(report["details"][0]))
    elif mutation == "hash":
        report["details"][0]["candidate_snapshot"]["sha256"] = "changed"
    else:
        ref = report["details"][0]["candidate_snapshot"]
        path = tmp_path / ref["filename"]
        snapshot = json.loads(path.read_bytes())
        if mutation == "context":
            snapshot["control"]["context"] = "changed"
        elif mutation == "tokens":
            snapshot["control"]["tokens"] += 1
        else:
            snapshot["packing_config"]["overhead_tokens"] += 1
        raw = json.dumps(snapshot).encode()
        path.write_bytes(raw)
        ref["sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        compare(report, tmp_path, samples=20)


async def test_public_capture_reproduces_after_engine_close_and_excludes_labels(tmp_path, monkeypatch):
    from benchmarks.retrieval_eval import evaluate_question
    from prme import PRMEConfig
    from tests.test_evidence_evaluation import question
    from tests.test_durable_ingestion import MockEmbeddingProvider

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    # The diagnostic measures JSON contexts, with the settings it was registered under.
    config = PRMEConfig(enable_qa_pairing=False, organizer={"opportunistic_enabled": False},
                        scoring=ScoringWeights(), packing=PackingConfig())
    row = await evaluate_question(question(), config, budgets=[1000], count_tokens=len, k=10,
                                  capture_candidates=tmp_path)
    raw = (tmp_path / row["candidate_snapshot"]["filename"]).read_bytes()
    assert b"secret-answer-label" not in raw and b"answer-labelled-session" not in raw
    assert b"has_answer" not in raw and b"answer_session_ids" not in raw
    report = {"complete": True, "errors": 0, "process_exit_code": 0,
              "dataset": {"split": "dev", "selected_question_ids": [row["question_id"]]},
              "provenance": {"engine_config": config.model_dump(mode="json")},
              "budgets": [1000], "details": [row]}
    result = compare(report, tmp_path, samples=20)
    assert result["baseline_reproduction_passed"]
    assert result["summary"]["1000"]["evidence_recall"]["before"] == 1


@pytest.mark.parametrize("content", ["", " \n"])
def test_blank_sources_are_accounted_without_positive_evidence_credit(content):
    source = make_candidate("blank", content, .9)
    cfg = PackingConfig(token_budget=1000, overhead_tokens=0)
    result = measure(pack_context([source], cfg), {"blank"}, cfg)
    assert result["blank_source_ids"] == ["blank"]
    assert result["content_source_ids"] == [] and result["pointer_source_ids"] == []
    assert result["evidence_recall"] == 0


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_separators_inside_source_are_not_json_record_boundaries(separator):
    source = make_candidate("unicode", "The source before" + separator + "and after.", .9)
    cfg = PackingConfig(token_budget=1000, overhead_tokens=0, min_fidelity="full")
    result = measure(pack_context([source], cfg), {"unicode"}, cfg)
    assert result["content_source_ids"] == ["unicode"]
    assert result["evidence_recall"] == 1


def confirmation_fixture(tmp_path):
    import benchmarks.diagnostics.product_packing as diagnostic
    from pathlib import Path
    report = fixture_report(tmp_path)
    report["dataset"].update(split="test", sha256="fixture", variant="custom", split_seed="fixed")
    report["provenance"].update(commit="frozen", dirty=False)
    report.update(budgets=[2048, 4096, 8192], query_clock="question", profile="raw-turns-static",
                  started_at="2026-09-12T00:01:00+00:00")
    plan = {"schema_version": 1, "algorithm": "multipath_score_vs_density_v1", "dataset": deepcopy(report["dataset"]),
            "runtime_commit": "frozen", "packing_config": report["provenance"]["engine_config"]["packing"],
            "budgets": report["budgets"], "bootstrap_samples": 20, "registered_at": "2026-09-12T00:00:00+00:00",
            "packing_module_sha256": hashlib.sha256(Path(inspect.getfile(pack_context)).read_bytes()).hexdigest(),
            "diagnostic_sha256": hashlib.sha256(Path(diagnostic.__file__).read_bytes()).hexdigest()}
    return report, plan


def test_test_split_requires_matching_prospective_confirmation_plan(tmp_path):
    report, plan = confirmation_fixture(tmp_path)
    with pytest.raises(ValueError, match="development split"):
        compare(report, tmp_path, samples=20)
    result = compare(report, tmp_path, samples=20, confirmation=plan)
    assert result["complete"] and result["kind"] == "test-product-packing-confirmation"
    assert result["quality_gate_passed"] is False  # No gain at these ample fixture budgets.


@pytest.mark.parametrize("field,value", [("runtime_commit", "changed"), ("diagnostic_sha256", "changed"),
    ("packing_module_sha256", "changed"), ("bootstrap_samples", 21), ("registered_at", "2026-09-12T00:02:00+00:00"),
    ("algorithm", "a different method")])
def test_confirmation_rejects_changed_method_or_retroactive_plan(tmp_path, field, value):
    report, plan = confirmation_fixture(tmp_path)
    plan[field] = value
    with pytest.raises(ValueError):
        compare(report, tmp_path, samples=20, confirmation=plan)


# Offline evidence gate -----------------------------------------------------

GATE_TURNS = [
    ("D1:1", "(1:56 pm on 8 May, 2023) Caroline: I painted a sunset over the lake last spring."),
    ("D1:2", "(1:56 pm on 8 May, 2023) Melanie: That sounds lovely. Which colors did you use?"),
    ("D1:3", "(1:56 pm on 8 May, 2023) Caroline: Mostly orange and purple. I also painted a horse."),
    ("D1:4", "(1:56 pm on 8 May, 2023) Melanie: I adopted a puppy named Oscar this week."),
]
DIAGNOSTICS = Path(__file__).parents[1] / "benchmarks/results/research/2026-09-23/gpt54-posthoc-evidence-diagnostics.json"


@pytest.fixture
def mock_embeddings(monkeypatch):
    from tests.test_durable_ingestion import MockEmbeddingProvider

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())


async def make_gate_pack(path):
    async with MemoryEngine.open(gate_config(path)) as engine:
        for minute, (dialog_id, text) in enumerate(GATE_TURNS):
            await engine.store(text, user_id="conv-1", session_id="s001", node_type=NodeType.FACT,
                               metadata={"source_dialog_id": dialog_id},
                               event_time=datetime(2023, 5, 8, 13, minute, tzinfo=timezone.utc))
        status = await engine.process_pending(user_id="conv-1", budget_ms=0)
        assert not status.pending and not status.failed
    return pack_identity(path)


def gate_case(pack, pack_sha256, *, evidence=("D1:1", "D1:3"), unresolved=(), saved="0" * 64):
    return GateCase(benchmark="locomo", question_id="conv-1-q0000", category="multi-hop",
                    question="What has Caroline painted?", user_id="conv-1",
                    reference_time=datetime(2023, 6, 1, tzinfo=timezone.utc), pack=pack, pack_sha256=pack_sha256,
                    saved_context_sha256=saved, evidence=frozenset(evidence) or None, unresolved=unresolved)


async def test_gate_replays_public_retrieval_on_a_verified_copy_and_measures_reader_context(
        tmp_path, mock_embeddings):
    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    [row] = await replay_gate([gate_case(pack, identity, evidence=("D1:1", "D1:3", "D9:9"), unresolved=("D9:9",))],
                              scratch=scratch, capture_dir=tmp_path / "captures")
    assert pack_identity(pack) == identity and not any(scratch.iterdir())
    assert row["context_matches_saved"] is False
    assert row["records"] == len(GATE_TURNS) and row["records_without_text"] == 0
    assert row["memory_text_tokens"] == sum(count_tokens(text) for _, text in GATE_TURNS)
    assert row["memory_text_tokens"] < row["context_tokens"]
    evidence = row["evidence"]
    assert (evidence["annotated"], evidence["packed"], evidence["unresolved"]) == (3, 2, ["D9:9"])
    assert evidence["all_packed"] is False and set(evidence["ranks"]) == {"D1:1", "D1:3"}
    assert all(isinstance(rank, int) for rank in evidence["ranks"].values())
    assert row["projected_correct"] == pytest.approx(1 / 4)  # Unscored multi-hop rate.
    capture = (tmp_path / "captures" / "locomo" / "conv-1-q0000.json").read_bytes()
    assert hashlib.sha256(capture).hexdigest() == row["capture_sha256"]
    saved = json.loads(capture)
    assert hashlib.sha256(saved["context"].encode()).hexdigest() == row["context_sha256"]
    assert saved["receipt"]["context_sha256"] == row["context_sha256"]

    # A fresh copy reproduces the context byte for byte, and resolved evidence is fully packed.
    [again] = await replay_gate([gate_case(pack, identity, saved=row["context_sha256"])], scratch=scratch)
    assert again["context_matches_saved"] is True and again["evidence"]["all_packed"] is True
    assert again["projected_correct"] == pytest.approx(37 / 45)

    # Overrides change the replayed configuration, never the saved pack.
    [small] = await replay_gate([gate_case(pack, identity)], scratch=scratch,
                                overrides=parse_overrides(["packing.token_budget=160"]))
    assert small["context_tokens"] <= 60 and small["records"] < row["records"]
    assert pack_identity(pack) == identity


async def test_gate_records_temporal_affinity_and_the_current_state_path(tmp_path, mock_embeddings):
    # Issue #85: which questions get temporal scoring, and which take the current-state path.
    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    dated = replace(gate_case(pack, identity), question="What did Caroline paint in May 2023?")
    current = replace(gate_case(pack, identity), question="What does Caroline paint today?")
    rows = {}
    for order in ("entity_first", "temporal_first"):
        overrides = parse_overrides([f'query_intent_order="{order}"'])
        rows[order] = [row for case in (dated, current)
                       for row in await replay_gate([case], scratch=scratch, overrides=overrides)]
    observed = {order: [row["query_scoring"] for row in replayed] for order, replayed in rows.items()}
    assert observed["entity_first"][0] == {"temporal_affinity_varies": False, "current_state_path": False}
    assert observed["temporal_first"][0] == {"temporal_affinity_varies": True, "current_state_path": False}
    # Explicit current wording keeps the current-state path under either order.
    assert observed["entity_first"][1]["current_state_path"] is True
    assert observed["temporal_first"][1]["current_state_path"] is True
    # Weighted scoring and rank fusion without the recency boost do not show the current-state path.
    for override in ('scoring.fusion="weighted"', "scoring.rrf_recency_boost=null"):
        [unseen] = await replay_gate([current], scratch=scratch, overrides=parse_overrides([override]))
        assert unseen["query_scoring"]["current_state_path"] is None
    assert pack_identity(pack) == identity


async def test_gate_records_each_channels_own_evidence_ranks_and_the_candidate_limits(tmp_path, mock_embeddings):
    # Issue #87: channel recall comes from each index's own ranking, whatever the limits.
    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    case = gate_case(pack, identity, evidence=("D1:1", "D1:3", "D9:9"), unresolved=("D9:9",))
    limited = parse_overrides(["packing.vector_k=1", "packing.lexical_k=1"])
    probe = tmp_path / "probe"
    shutil.copytree(pack, probe)
    async with MemoryEngine.open(gate_config(probe, limited)) as engine:
        vector = await plain_ranking(engine, "vector", case.question, case.user_id)
        # Only the two turns that say "painted" share a term with the question.
        lexical = [node.metadata["source_dialog_id"]
                   for node in await plain_ranking(engine, "bm25", case.question, case.user_id)]
        # At vector_k=1, retrieval's vector candidate is the first turn of the vector ranking.
        response = await engine.retrieve(case.question, user_id=case.user_id, reference_time=case.reference_time)
        assert [result.node.id for result in response.results if "VECTOR" in result.paths] == [vector[0].id]
    assert sorted(lexical) == ["D1:1", "D1:3"]
    expected_vector = {node.metadata["source_dialog_id"]: rank for rank, node in enumerate(vector, start=1)}

    [row] = await replay_gate([case], scratch=scratch)
    assert row["stored_turns"] == len(GATE_TURNS) and row["aggregation"] is None
    assert row["channel_limits"] == {"vector": 500, "lexical": 500}
    ranks = row["evidence"]["channel_ranks"]
    assert ranks == {"vector": {key: expected_vector[key] for key in ("D1:1", "D1:3")},
                     "lexical": {key: lexical.index(key) + 1 for key in ("D1:1", "D1:3")}}

    [small] = await replay_gate([case], scratch=scratch, overrides=limited)
    assert small["evidence"]["channel_ranks"] == ranks
    assert small["channel_limits"] == {"vector": 1, "lexical": 1}
    # A count question widens both limits (1 x 3.0 here), and the report names the channels that filled them.
    count = replace(case, question="How many things has Caroline painted?")
    [widened] = await replay_gate([count], scratch=scratch, overrides=limited)
    assert widened["channel_limits"] == {"vector": 3, "lexical": 3}
    # BM25 matches only the two turns that share a term, under its widened limit of 3.
    assert widened["aggregation"] == {"candidate_limit_paths": ["VECTOR"]}
    [plain] = await replay_gate([case], scratch=scratch, plain="rrf")
    assert plain["evidence"]["channel_ranks"] == ranks and plain["stored_turns"] == len(GATE_TURNS)
    assert "aggregation" not in plain and "channel_limits" not in plain
    assert pack_identity(pack) == identity


async def test_gate_skips_channel_ranks_for_packs_that_hold_more_than_turns(tmp_path, mock_embeddings):
    pack = tmp_path / "pack"
    await make_gate_pack(pack)
    async with MemoryEngine.open(gate_config(pack)) as engine:
        await engine.store("A note.", user_id="conv-1", node_type=NodeType.NOTE)
        await engine.process_pending(user_id="conv-1", budget_ms=0)
    identity = pack_identity(pack)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    # Retrieval is measured as before; the channel ranks cannot be, so they are left out.
    [row] = await replay_gate([gate_case(pack, identity)], scratch=scratch)
    assert row["stored_turns"] is None and row["evidence"]["channel_ranks"] is None
    assert row["evidence"]["all_packed"] is True and row["channel_limits"] == {"vector": 500, "lexical": 500}
    summary = summarize_gate([row])
    assert summary["channel_recall"] is None and summary["channel_recall_at_run_limits"] is None
    # A plain baseline ranks turns only, so it still refuses the pack.
    with pytest.raises(ValueError, match="of which 4 are turns"):
        await replay_gate([gate_case(pack, identity)], scratch=scratch, plain="bm25")


async def test_gate_warms_each_engine_before_its_first_timed_question(tmp_path, mock_embeddings, monkeypatch):
    import benchmarks.diagnostics.product_packing as diagnostic

    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    warm_up, retrieve = diagnostic._warm_up, MemoryEngine.retrieve

    async def no_warm_up(engine, user_id):
        return None

    monkeypatch.setattr(diagnostic, "_warm_up", no_warm_up)
    [cold] = await replay_gate([gate_case(pack, identity)], scratch=scratch)
    events = []

    async def record_warm_up(engine, user_id):
        events.append(("warm-up", user_id))
        await warm_up(engine, user_id)

    async def record_retrieve(self, query, **kwargs):
        events.append(("retrieve", kwargs["user_id"]))
        return await retrieve(self, query, **kwargs)

    monkeypatch.setattr(diagnostic, "_warm_up", record_warm_up)
    monkeypatch.setattr(MemoryEngine, "retrieve", record_retrieve)
    rows = await replay_gate([gate_case(pack, identity), gate_case(pack, identity)], scratch=scratch)
    assert events == [("warm-up", "conv-1"), ("retrieve", "conv-1"), ("retrieve", "conv-1")]
    # The warm-up changes nothing the reader sees, and nothing the channel ranks record.
    assert rows[0]["context_sha256"] == cold["context_sha256"]
    assert rows[0]["evidence"]["channel_ranks"] == cold["evidence"]["channel_ranks"]


async def test_warm_up_loads_the_reranker_only_when_it_is_enabled(tmp_path, mock_embeddings):
    import benchmarks.diagnostics.product_packing as diagnostic

    pack = tmp_path / "pack"
    await make_gate_pack(pack)
    scored = []

    class Reranker:
        def _predict_sync(self, pairs):
            scored.append(pairs)
            return [0.5] * len(pairs)

    async with MemoryEngine.open(gate_config(pack)) as engine:
        await diagnostic._warm_up(engine, "conv-1")
        assert scored == []
        engine._retrieval_pipeline._reranker = Reranker()
        await diagnostic._warm_up(engine, "conv-1")
    assert scored == [[(diagnostic.GATE_WARM_UP_QUERY, diagnostic.GATE_WARM_UP_QUERY)]]
    async with MemoryEngine.open(gate_config(pack)) as engine:
        with pytest.raises(ValueError, match="Unknown candidate channel"):
            await diagnostic._index_rankings(engine, "q", "nobody", {}, ["graph"])


async def test_gate_rejects_changed_packs_unordered_questions_and_missing_receipts(
        tmp_path, mock_embeddings, monkeypatch):
    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    other = tmp_path / "other"
    shutil.copytree(pack, other)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(ValueError, match="differs from the identity"):
        await replay_gate([gate_case(pack, "0" * 64)], scratch=scratch)
    with pytest.raises(ValueError, match="contiguous"):
        await replay_gate([gate_case(pack, identity), gate_case(other, identity), gate_case(pack, identity)],
                          scratch=scratch)

    async def no_receipt(self, request_id, *, user_id):
        return None

    monkeypatch.setattr(MemoryEngine, "get_retrieval_receipt", no_receipt)
    with pytest.raises(ValueError, match="receipt was not persisted"):
        await replay_gate([gate_case(pack, identity)], scratch=scratch)
    assert not any(scratch.iterdir()) and pack_identity(pack) == identity


def test_gate_config_isolates_environment_and_dotenv_and_refuses_unsafe_overrides(tmp_path, monkeypatch):
    from prme.config import EmbeddingConfig, ExtractionConfig

    monkeypatch.setenv("PRME_ENABLE_RERANKER", "true")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("PRME_EMBEDDING_MODEL_NAME=BAAI/bge-base-en-v1.5\n"
                                   "PRME_ORGANIZER_OPPORTUNISTIC_COOLDOWN=5\n"
                                   "PRME_EXTRACTION_BASE_URL=http://user:secret@example.com\n")
    config = gate_config(tmp_path, parse_overrides(["packing.token_budget=8192", "scoring.relevance_floor=0.3"]))
    assert config.enable_reranker is False
    assert config.embedding.model_name == EmbeddingConfig.model_fields["model_name"].default
    assert config.extraction.base_url == ExtractionConfig.model_fields["base_url"].default
    assert config.organizer.opportunistic_enabled is False
    assert config.organizer.opportunistic_cooldown == OrganizerConfig.model_fields["opportunistic_cooldown"].default
    assert config.packing.token_budget == 8192 and config.scoring.relevance_floor == .3
    assert config.db_path == str(tmp_path / "memory.duckdb")
    assert parse_overrides(["packing.context_format=compact"]) == {"packing": {"context_format": "compact"}}
    for unsafe in ("embedding.model_name=other", "db_path=/tmp/other.duckdb", "enable_query_reformulation=true",
                   "temporal_relation.enabled=true", "extraction.provider=openai",
                   "organizer.opportunistic_enabled=true", "api.api_key=secret"):
        with pytest.raises(ValueError, match="cannot override"):
            gate_config(tmp_path, parse_overrides([unsafe]))
    for unknown in ("packing.tokn_budget=8192", "enable_rerankr=true"):
        with pytest.raises(ValueError, match="Unknown configuration key"):
            gate_config(tmp_path, parse_overrides([unknown]))
    for malformed in (["no-separator"], ["=1"], ["packing=1", "packing.token_budget=2"],
                      ["packing.token_budget=1", "packing.token_budget=2"]):
        with pytest.raises(ValueError):
            parse_overrides(malformed)


def test_gate_config_selects_rank_fusion_and_refuses_a_rank_constant_without_it(tmp_path):
    fused = gate_config(tmp_path, parse_overrides(['scoring.fusion="rrf"', "scoring.rrf_k=30"]))
    assert (fused.scoring.fusion, fused.scoring.rrf_k) == ("rrf", 30)
    assert gate_config(tmp_path, parse_overrides(["scoring.fusion=rrf"])).scoring.rrf_k == 60
    # Rank fusion is the default, so a constant alone applies to it, and other scoring settings keep it.
    assert gate_config(tmp_path).scoring == ScoringWeights(fusion="rrf", rrf_recency_boost=.25,
                                                           rrf_tie_break="event_time")
    assert gate_config(tmp_path, parse_overrides(["scoring.rrf_k=30"])).scoring.rrf_k == 30
    assert gate_config(tmp_path, parse_overrides(["scoring.relevance_floor=0.3"])).scoring.fusion == "rrf"
    # A weighted fusion takes none of the rank fusion defaults, so it warns about nothing.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        weighted = gate_config(tmp_path, parse_overrides(['scoring.fusion="weighted"'])).scoring
    assert weighted == ScoringWeights()
    with pytest.warns(UserWarning), pytest.raises(ValueError, match="applies only with"):
        gate_config(tmp_path, parse_overrides(['scoring.fusion="weighted"', "scoring.rrf_k=30"]))


@pytest.mark.parametrize("setting", ["scoring.rrf_recency_boost=0.5", 'scoring.rrf_tie_break="event_time"'])
def test_gate_config_refuses_rank_fusion_recency_and_tie_break_without_rank_fusion(tmp_path, setting):
    name, value = setting.removeprefix("scoring.").split("=")
    for fused in (gate_config(tmp_path, parse_overrides(['scoring.fusion="rrf"', setting])),
                  # Rank fusion is the default, so the setting alone applies to it.
                  gate_config(tmp_path, parse_overrides([setting]))):
        assert getattr(fused.scoring, name) == json.loads(value)
    with pytest.warns(UserWarning), pytest.raises(ValueError, match=f"scoring.{name} applies only with"):
        gate_config(tmp_path, parse_overrides(['scoring.fusion="weighted"', setting]))


def test_gate_config_refuses_event_time_recency_without_weighted_scoring(tmp_path):
    setting = 'scoring.recency_time="event_time"'
    weighted = gate_config(tmp_path, parse_overrides(['scoring.fusion="weighted"', setting]))
    assert (weighted.scoring.fusion, weighted.scoring.recency_time) == ("weighted", "event_time")
    # Rank fusion is the default and ignores the setting, so a run would change nothing.
    for overrides in ([setting], ['scoring.fusion="rrf"', setting]):
        with pytest.warns(UserWarning), pytest.raises(
                ValueError, match='scoring.recency_time applies only with scoring.fusion="weighted"'):
            gate_config(tmp_path, parse_overrides(overrides))


def test_gate_config_refuses_a_rank_fusion_session_decay_without_rank_fusion(tmp_path):
    decay = "packing.session_context_rank_fusion_score_decay=0.5"
    fused = gate_config(tmp_path, parse_overrides(['scoring.fusion="rrf"', decay]))
    assert fused.packing.session_context_rank_fusion_score_decay == .5
    # The default is 0.6, and other packing settings keep it, as they keep the reader format and balanced order.
    assert gate_config(tmp_path).packing.session_context_rank_fusion_score_decay == .6
    budget = gate_config(tmp_path, parse_overrides(["packing.token_budget=8192"])).packing
    assert (budget.session_context_rank_fusion_score_decay, budget.context_format,
            budget.multipath_ordering) == (.6, "reader", "balanced")
    # Score order, the default before balanced order passed the default-change test, keeps the others too.
    score = gate_config(tmp_path, parse_overrides(['packing.multipath_ordering="score"'])).packing
    assert (score.session_context_rank_fusion_score_decay, score.context_format,
            score.multipath_ordering) == (.6, "reader", "score")
    # Weighted scoring never applies the default decay, so only a decay set with it is refused.
    assert gate_config(tmp_path, parse_overrides(['scoring.fusion="weighted"'])).scoring.fusion == "weighted"
    with pytest.raises(ValueError, match="applies only with"):
        gate_config(tmp_path, parse_overrides(['scoring.fusion="weighted"', decay]))


@pytest.mark.parametrize("value", ['"full"', "full", '"structured"'])
def test_gate_config_selects_a_text_bearing_floor(tmp_path, value):
    floored = gate_config(tmp_path, parse_overrides([f"packing.min_fidelity={value}"]))
    assert floored.packing.min_fidelity.value == value.strip('"')
    assert requires_memory_text(floored.packing)
    # The default reader format always keeps text-free records out; the auditable format needs the floor.
    auditable = gate_config(tmp_path, parse_overrides(['packing.context_format="auditable"']))
    assert not requires_memory_text(auditable.packing)
    assert requires_memory_text(gate_config(tmp_path, parse_overrides(
        ['packing.context_format="auditable"', f"packing.min_fidelity={value}"])).packing)


def gate_row(question_id, category, *, all_packed=None, unresolved=(), ranks=None, records=25,
             text=300, tokens=1000, without_text=0, benchmark="locomo", matches=True, candidates=500,
             seconds=.1, **observed):
    """A report row; ``observed`` adds fields that earlier reports lack (channel_ranks goes in evidence)."""
    evidence = None
    channel_ranks = observed.pop("channel_ranks", None)
    if all_packed is not None:
        ranks = {"a": 1, "b": 2 if all_packed else None} if ranks is None else ranks
        evidence = {"annotated": 2, "unresolved": list(unresolved), "packed": 2 if all_packed else 1,
                    "packed_with_text": 2 if all_packed else 1, "all_packed": all_packed,
                    "all_packed_with_text": all_packed, "ranks": ranks,
                    **({"channel_ranks": channel_ranks} if channel_ranks is not None else {})}
    return {"benchmark": benchmark, "question_id": question_id, "category": category,
            "context_sha256": "0" * 64, "context_matches_saved": matches, "context_tokens": tokens,
            "memory_text_tokens": text, "records": records, "records_without_text": without_text,
            "representations": {"full": records - without_text, "reference": without_text},
            "candidates": candidates, "retrieval_seconds": seconds, "evidence": evidence,
            "projected_correct": projected_correct(benchmark, category, evidence), **observed}


def gate_fixture(rows, *, datasets=None, archive="prepared", overrides=None, timing=None):
    return gate_report(rows, provenance={
        "commit": "abc123", "dirty": False, "overrides": overrides or {},
        **({"retrieval_timing": timing} if timing else {}),
        "engine_config": {"packing": {"tokenizer": "cl100k_base"}},
        "archive": {"path": "/archive", "prepared_sha256": {"locomo": archive}},
        "datasets": datasets or {"locomo": "dataset"}})


def test_projection_reproduces_the_saved_run_for_each_benchmark_and_locomo_category():
    rows = [gate_row(f"{category}-{state}-{n}", category, all_packed=state == "all_packed")
            for category, rates in GATE_CONDITIONAL_ACCURACY["locomo"].items()
            for state in ("all_packed", "missing") for n in range(rates[state][1])]
    rows += [gate_row(f"{category}-unscored-{n}", category)
             for category, (_, total) in GATE_UNSCORED_ACCURACY["locomo"].items() for n in range(total)]
    summary = summarize_gate(rows)
    assert summary["questions"] == 1540 and summary["projected_correct"] == pytest.approx(985)
    for category, rates in GATE_CONDITIONAL_ACCURACY["locomo"].items():
        measured = rates["all_packed"][0] + rates["missing"][0] + GATE_UNSCORED_ACCURACY["locomo"][category][0]
        assert summarize_gate([row for row in rows if row["category"] == category])["projected_correct"] == (
            pytest.approx(measured))
    rows = ([gate_row(f"a{n}", "multi-session", all_packed=True, benchmark="longmemeval") for n in range(403)]
            + [gate_row(f"m{n}", "temporal-reasoning", all_packed=False, benchmark="longmemeval") for n in range(67)]
            + [gate_row(f"{category}-{n}_abs", category, benchmark="longmemeval")
               for category, (_, total) in GATE_UNSCORED_ACCURACY["longmemeval"].items() for n in range(total)])
    assert summarize_gate(rows)["projected_correct"] == pytest.approx(430)
    assert projected_correct("locomo", "single-hop", {"unresolved": ["D9:9"], "all_packed": False}) == 0
    with pytest.raises(ValueError, match="no measured rate"):
        projected_correct("longmemeval", "single-session-assistant", None)


def test_projection_tables_match_the_saved_run_diagnostics():
    saved = json.loads(DIAGNOSTICS.read_text())["benchmarks"]

    def rate(counts, *states):
        correct = sum(counts.get(f"{state}_correct", 0) for state in states)
        return correct, correct + sum(counts.get(f"{state}_incorrect", 0) for state in states)

    locomo, longmemeval = saved["locomo"]["categories"], saved["longmemeval"]["categories"]
    assert GATE_CONDITIONAL_ACCURACY["locomo"] == {
        category: {"all_packed": rate(counts, "all_annotated_turns_packed"),
                   "missing": rate(counts, "annotated_turns_missing")} for category, counts in locomo.items()}
    assert GATE_UNSCORED_ACCURACY["locomo"] == {
        category: rate(counts, "no_annotations", "unresolved_annotations") for category, counts in locomo.items()}
    pooled = {state: tuple(map(sum, zip(*(rate(counts, state) for counts in longmemeval.values()))))
              for state in ("all_annotated_turns_packed", "annotated_turns_missing")}
    assert GATE_CONDITIONAL_ACCURACY["longmemeval"] == {"*": {
        "all_packed": pooled["all_annotated_turns_packed"], "missing": pooled["annotated_turns_missing"]}}
    assert GATE_UNSCORED_ACCURACY["longmemeval"] == {
        category: rate(counts, "abstention") for category, counts in longmemeval.items()
        if rate(counts, "abstention")[1]}
    assert GATE_MEASURED_ACCURACY == {
        name: (sum(row["correct"] for row in value["rows"]), len(value["rows"])) for name, value in saved.items()}


def test_summary_reports_text_share_evidence_and_rank_distribution():
    summary = summarize_gate([
        gate_row("q1", "multi-hop", all_packed=True, ranks={"a": 1, "b": 30}, records=20, text=250, without_text=1),
        gate_row("q2", "multi-hop", all_packed=False, ranks={"a": 3, "b": None}, records=30, text=250),
        gate_row("q3", "multi-hop", all_packed=False, unresolved=["z"], ranks={"a": 200}),
        gate_row("q4", "open-domain"),
    ])
    assert summary["records_per_context"] == 25 and summary["memory_text_share"] == pytest.approx(1100 / 4000)
    assert summary["records_without_text"] == 1 and summary["representations"]["reference"] == 1
    assert (summary["annotated_questions"], summary["all_evidence_packed"]) == (3, 1)
    assert summary["all_evidence_packed_share"] == pytest.approx(1 / 3) and summary["candidates_mean"] == 500
    ranks = summary["evidence_ranks"]
    assert (ranks["annotated_turns"], ranks["not_returned"], ranks["median"]) == (5, 1, 16.5)
    assert (ranks["top_25_share"], ranks["beyond_150_share"]) == (.5, .25)
    assert ranks["all_within_top_25_share"] == 0 and ranks["all_within_top_75_share"] == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        summarize_gate([])
    with pytest.raises(ValueError):
        gate_fixture([])


def test_summary_reports_channel_recall_candidate_recall_latency_and_aggregation():
    # Issue #87.
    rows = [
        gate_row("q1", "multi-hop", all_packed=True, ranks={"a": 1, "b": 30}, candidates=100, seconds=.1,
                 stored_turns=400, channel_ranks={"vector": {"a": 10, "b": 120}, "lexical": {"a": 3, "b": None}},
                 aggregation=None, channel_limits={"vector": 100, "lexical": 100}),
        gate_row("q2", "multi-hop", all_packed=False, ranks={"a": 3, "b": None}, candidates=300, seconds=.3,
                 stored_turns=600, channel_ranks={"vector": {"a": 60, "b": 600}, "lexical": {"a": 400, "b": 90}},
                 aggregation={"candidate_limit_paths": ["VECTOR", "LEXICAL"]},
                 channel_limits={"vector": 300, "lexical": 300}),
        gate_row("q3", "multi-hop", all_packed=False, unresolved=["z"], ranks={"a": 200}, seconds=.2,
                 stored_turns=500, channel_ranks={"vector": {"a": 1}, "lexical": {"a": 1}},
                 aggregation={"candidate_limit_paths": ["LEXICAL_AGG"]}, channel_limits={"vector": 300, "lexical": 300}),
        # A user with no stored turns has no candidate share.
        gate_row("q4", "open-domain", seconds=.5, candidates=0, stored_turns=0, aggregation=None,
                 channel_limits={"vector": 100, "lexical": 100}),
    ]
    summary = summarize_gate(rows)
    assert summary["candidate_share_mean"] == pytest.approx((.25 + .5 + 1) / 3)
    assert summary["retrieval_seconds"]["p50"] == pytest.approx(.25)
    assert summary["retrieval_seconds"]["p95"] == pytest.approx(.47)
    # Only q1 has all its evidence among the candidates: q2 lost "b" and q3 has an unresolved turn.
    assert (summary["all_evidence_returned"], summary["all_evidence_returned_share"]) == (1, pytest.approx(1 / 3))
    recall = summary["channel_recall"]
    assert list(recall) == ["vector", "lexical", "either"]
    assert list(recall["vector"]) == ["25", "50", "100", "150", "500", "all"]
    assert recall["vector"]["25"] == {"evidence_turns": pytest.approx(2 / 5), "all_evidence": 0}
    assert recall["vector"]["150"] == {"evidence_turns": pytest.approx(4 / 5), "all_evidence": pytest.approx(1 / 3)}
    assert recall["vector"]["all"]["evidence_turns"] == 1
    # Lexical never ranks q1's "b"; either takes each turn's better rank.
    assert recall["lexical"]["all"] == {"evidence_turns": pytest.approx(4 / 5), "all_evidence": pytest.approx(1 / 3)}
    assert recall["either"]["100"] == {"evidence_turns": pytest.approx(4 / 5), "all_evidence": pytest.approx(1 / 3)}
    assert recall["either"]["150"] == {"evidence_turns": 1, "all_evidence": pytest.approx(2 / 3)}
    # q3 has an unresolved annotation, so its evidence is never all inside any depth.
    assert recall["either"]["all"]["all_evidence"] == pytest.approx(2 / 3)
    # At each question's own limits: q1 is cut at 100, the widened q2 and q3 at 300.
    assert summary["channel_recall_at_run_limits"] == {
        "vector": {"evidence_turns": pytest.approx(3 / 5), "all_evidence": 0},
        "lexical": {"evidence_turns": pytest.approx(3 / 5), "all_evidence": 0},
        "either": {"evidence_turns": pytest.approx(4 / 5), "all_evidence": pytest.approx(1 / 3)}}
    # The keyword scan's fixed limit is not a widened limit.
    assert summary["aggregation"] == {"questions": 2, "filled_widened_limit": 1,
                                      "paths_at_limit": {"LEXICAL": 1, "LEXICAL_AGG": 1, "VECTOR": 1}}
    # Reports written before issue #87 summarize without these observations.
    earlier = summarize_gate([gate_row("q1", "multi-hop", all_packed=True)])
    assert earlier["channel_recall"] is None and earlier["aggregation"] is None
    assert earlier["channel_recall_at_run_limits"] is None
    assert earlier["candidate_share_mean"] is None and earlier["all_evidence_returned"] == 1


def test_gate_markdown_labels_the_projection_and_flags_mismatches_and_ineffective_overrides():
    report = gate_fixture([gate_row("q1", "multi-hop", all_packed=True),
                           gate_row("q2", "single-hop", all_packed=False, matches=False)])
    assert report["benchmarks"]["locomo"]["context_mismatches"] == ["q2"]
    markdown = gate_markdown(report)
    assert "planning estimate, not an answer score" in markdown
    assert "Jaccard 0.83" in markdown and "63.3% against 64.0%" in markdown and "985/1,540" in markdown
    assert "distractor" in markdown and "held-out" in markdown
    assert "Contexts that differ from the saved run: 1 (q2)" in markdown
    assert "all evidence packed for 45/282 multi-hop questions. This run: 25.0, 30.0%, 1/1." in markdown
    assert "changed nothing" not in markdown
    unchanged = gate_markdown(gate_fixture([gate_row("q1", "multi-hop", all_packed=True)],
                                           overrides={"enable_qa_pairing": True}))
    assert "overrides changed nothing the reader sees" in unchanged and '"enable_qa_pairing": true' in unchanged
    longmemeval = gate_markdown(gate_fixture([gate_row("q1", "multi-session", all_packed=True,
                                                       benchmark="longmemeval")]))
    assert "all evidence packed for 403/470 annotated questions. This run: 25.0, 30.0%, 1/1." in longmemeval


def test_comparison_pairs_questions_and_rejects_mismatched_inputs(tmp_path, capsys):
    before_rows = [gate_row("q1", "multi-hop", all_packed=False), gate_row("q2", "multi-hop", all_packed=True),
                   gate_row("q3", "single-hop", all_packed=True), gate_row("q4", "open-domain")]
    after_rows = [gate_row("q1", "multi-hop", all_packed=True), gate_row("q2", "multi-hop", all_packed=False),
                  gate_row("q3", "single-hop", all_packed=True), gate_row("q4", "open-domain", matches=False)]
    before, after = gate_fixture(before_rows), gate_fixture(after_rows)
    result = compare_gates(before, after, samples=50)
    bench = result["benchmarks"]["locomo"]
    assert bench["gained_all_evidence"] == ["q1"] and bench["lost_all_evidence"] == ["q2"]
    stats = bench["all_evidence_packed"]
    assert (stats["queries"], stats["wins"], stats["losses"], stats["ties"]) == (3, 1, 1, 1)
    assert bench["all_evidence_packed_with_text"]["queries"] == 3
    by_category = bench["all_evidence_packed_by_category"]
    assert by_category["multi-hop"]["queries"] == 2 and by_category["open-domain"]["queries"] == 0
    assert bench["projected_accuracy"]["queries"] == 4
    assert bench["context"]["after"]["contexts_matching_saved"] == 3
    markdown = comparison_markdown(result)
    assert "All evidence packed, multi-hop" in markdown and "All evidence packed with memory text" in markdown
    assert "4/4 to 3/4" in markdown and "10 conversations" in markdown

    mismatched = {
        "Question sets differ": gate_fixture(after_rows[:3]),
        "different datasets": gate_fixture(after_rows, datasets={"locomo": "other"}),
        "different archives": gate_fixture(after_rows, archive="other"),
        "different projection constants": {**after, "projection": {**after["projection"], "method": "other"}},
        "repeats": gate_fixture(after_rows + after_rows[:1]),
        "annotation differs": gate_fixture([gate_row("q1", "temporal", all_packed=True), *after_rows[1:]]),
        "not a complete evidence gate": {**after, "complete": False},
    }
    for message, report in mismatched.items():
        with pytest.raises(ValueError, match=message):
            compare_gates(before, report)
    tokenizer = deepcopy(after)
    tokenizer["provenance"]["engine_config"]["packing"]["tokenizer"] = "o200k_base"
    with pytest.raises(ValueError, match="different tokenizers"):
        compare_gates(before, tokenizer)

    paths = [tmp_path / "before.json", tmp_path / "after.json"]
    for path, report in zip(paths, (before, after)):
        path.write_text(json.dumps(report))
    main(["gate-compare", *map(str, paths), "--output", str(tmp_path / "out" / "comparison.json"), "--samples", "20"])
    written = json.loads((tmp_path / "out" / "comparison.json").read_text())
    assert written["kind"] == "offline-evidence-gate-comparison"
    assert written["inputs"]["before_sha256"] == hashlib.sha256(paths[0].read_bytes()).hexdigest()
    assert (tmp_path / "out" / "comparison.md").read_text() in capsys.readouterr().out
    for output in (str(paths[1]), str(tmp_path / "comparison.md")):
        with pytest.raises(SystemExit):
            main(["gate-compare", *map(str, paths), "--output", output])


def test_comparison_reports_temporal_affinity_and_current_state_changes():
    def observed(row, varies, path):
        return {**row, "query_scoring": {"temporal_affinity_varies": varies, "current_state_path": path}}

    rows = [gate_row("q1", "temporal", all_packed=True), gate_row("q2", "temporal", all_packed=True),
            gate_row("q3", "single-hop"), gate_row("q4", "single-hop")]
    before = gate_fixture([observed(row, varies, path) for row, varies, path in
                           zip(rows, (False, False, True, False), (True, False, None, True))])
    after = gate_fixture([observed(row, varies, path) for row, varies, path in
                          zip(rows, (True, True, True, False), (False, True, False, True))])
    result = compare_gates(before, after, samples=20)
    assert result["benchmarks"]["locomo"]["query_scoring"] == {
        "temporal_affinity_varies_by_category": {"questions": {"single-hop": 2, "temporal": 2},
                                                 "before": {"single-hop": 1, "temporal": 0},
                                                 "after": {"single-hop": 1, "temporal": 2}},
        # The receipts cannot show q3's current-state path before, so it is in neither list.
        "entered_current_state_path": ["q2"], "left_current_state_path": ["q1"],
        "current_state_path_unknown": 1,
    }
    markdown = comparison_markdown(result)
    assert "| locomo | temporal | 2 | 0 | 2 |" in markdown
    assert "locomo questions that entered the current-state path: 1 (q2)." in markdown
    assert "locomo questions that left the current-state path: 1 (q1)." in markdown
    assert "locomo questions whose current-state path these receipts cannot show" in markdown
    assert "not shown" not in markdown
    # Reports without the observations, such as plain baselines and earlier reports, compare as before.
    plain = gate_fixture(rows)
    for pair in ((plain, after), (before, plain)):
        assert compare_gates(*pair, samples=20)["benchmarks"]["locomo"]["query_scoring"] is None
    unobserved = comparison_markdown(compare_gates(plain, after, samples=20))
    assert "current-state path are not shown for locomo" in unobserved
    assert "questions that left" not in unobserved


def test_gate_and_comparison_report_candidate_recall_limits_and_latency():
    # Issue #87: candidate recall separately from context retention, and latency before and after.
    def observed(question_id, *, returned, seconds, limited, limit):
        return gate_row(question_id, "multi-hop", all_packed=False, ranks={"a": 1, "b": 9 if returned else None},
                        seconds=seconds, candidates=500 if returned else 150, stored_turns=500,
                        channel_ranks={"vector": {"a": 1, "b": 9}, "lexical": {"a": 2, "b": None}},
                        aggregation={"candidate_limit_paths": ["VECTOR", "LEXICAL_AGG"] if limited else []},
                        channel_limits={"vector": limit, "lexical": limit})

    timing = "after-warm-up-v1"
    before = gate_fixture([observed("q1", returned=True, seconds=.2, limited=False, limit=500),
                           observed("q2", returned=True, seconds=.4, limited=False, limit=500)], timing=timing)
    after = gate_fixture([observed("q1", returned=False, seconds=.1, limited=True, limit=5),
                          observed("q2", returned=True, seconds=.1, limited=False, limit=50)], timing=timing)
    before["load_average"] = {"start": [1.25, 1, 1], "end": [2.0, 1, 1]}
    markdown = gate_markdown(before)
    assert "| locomo | 2 | 2/2 | 500.0 (100.0% of stored turns) | 0.300 / 0.390 s |" in markdown
    assert "| Channel | Top 25 | Top 50 | Top 100 | Top 150 | Top 500 | All | This run's limits |" in markdown
    assert "| vector | 100.0% (100.0%) |" in markdown and "| lexical | 50.0% (0.0%) |" in markdown
    assert "Aggregation: 2 questions read as counts or lists" in markdown
    assert "0 still filled a widened limit" in markdown and "limits: none." in markdown
    assert "| multi-hop | 2 | 2/2 (100.0%) | 0/2" in markdown
    # q1 is cut at 5 after, so its "b" (vector rank 9) falls outside this run's limits.
    at_limits = summarize_gate(after["rows"])["channel_recall_at_run_limits"]
    assert at_limits["vector"] == {"evidence_turns": .75, "all_evidence": .5}
    assert "| vector | 100.0% (100.0%) | 100.0% (100.0%) | 100.0% (100.0%) | 100.0% (100.0%) | 100.0% (100.0%) " \
           "| 100.0% (100.0%) | 75.0% (50.0%) |" in gate_markdown(after)

    result = compare_gates(before, after, samples=50)
    bench = result["benchmarks"]["locomo"]
    returned = bench["all_evidence_returned"]
    assert (returned["before"], returned["after"], returned["losses"]) == (1, .5, 1)
    assert bench["lost_all_evidence_among_candidates"] == ["q1"]
    assert bench["gained_all_evidence_among_candidates"] == []
    assert bench["retrieval_seconds"] == {"before": {"p50": pytest.approx(.3), "p95": pytest.approx(.39)},
                                          "after": {"p50": pytest.approx(.1), "p95": pytest.approx(.1)}}
    assert bench["context"]["after"]["candidates_mean"] == 325
    assert bench["aggregation"]["after"]["paths_at_limit"] == {"LEXICAL_AGG": 1, "VECTOR": 1}
    assert result["before"]["load_average"]["start"][0] == 1.25 and result["after"]["load_average"] is None
    markdown = comparison_markdown(result)
    assert "| locomo | All evidence among the candidates | 100.0% | 50.0% | -50.0 pp |" in markdown
    assert "| 500.0 to 325.0 | 0.300 / 0.390 s to 0.100 / 0.100 s |" in markdown
    assert "compare it only between reports run on one machine" in markdown
    assert "load average from start to end: before 1.2 to 2.0, after not recorded." in markdown
    assert "- locomo questions that lost all their evidence among the candidates: 1 (q1)." in markdown
    assert "- locomo after: 2 questions read as counts or lists" in markdown
    assert "1 still filled a widened limit" in markdown and "LEXICAL_AGG 1, VECTOR 1." in markdown
    # Reports timed before the warm-up included the model load, and a plain baseline times other work.
    earlier = gate_fixture(before["rows"])
    plain = gate_fixture(before["rows"], timing=timing)
    plain["provenance"].update(plain="rrf", plain_rrf_k=60)
    for pair in ((earlier, after), (after, earlier), (plain, after), (after, plain)):
        unmatched = compare_gates(*pair, samples=20)
        assert unmatched["benchmarks"]["locomo"]["retrieval_seconds"] is None
        assert "n/a to n/a" in comparison_markdown(unmatched)
        assert "a plain baseline times its own ranking and packing" in comparison_markdown(unmatched)
    # Count and list questions compare only when both reports record them.
    unrecorded = gate_fixture([{key: value for key, value in row.items() if key != "aggregation"}
                               for row in before["rows"]])
    for pair in ((unrecorded, after), (after, unrecorded)):
        compared = compare_gates(*pair, samples=20)
        assert compared["benchmarks"]["locomo"]["aggregation"] is None
        assert "locomo count and list questions: not recorded in both reports" in comparison_markdown(compared)


async def test_gate_cli_writes_json_and_markdown_for_a_replayed_run(tmp_path, mock_embeddings, monkeypatch, capsys):
    import benchmarks.diagnostics.product_packing as diagnostic

    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    archive = tmp_path / "archive"
    (archive / "locomo").mkdir(parents=True)
    (archive / "locomo" / "prepared.json").write_text("{}")
    monkeypatch.setattr(diagnostic, "_locomo_cases", lambda _: [gate_case(pack, identity)])
    monkeypatch.setattr(diagnostic, "_quiet_offline_cli", lambda: None)
    output = tmp_path / "out" / "gate.json"
    await asyncio.to_thread(main, ["gate", "--benchmark", "locomo", "--archive", str(archive),
                                   "--set", "packing.token_budget=3000", "--output", str(output)])
    report = json.loads(output.read_text())
    assert report["complete"] and [row["question_id"] for row in report["rows"]] == ["conv-1-q0000"]
    provenance = report["provenance"]
    assert provenance["overrides"] == {"packing": {"token_budget": 3000}} and provenance["commit"]
    assert provenance["engine_config"]["packing"]["token_budget"] == 3000
    assert provenance["engine_config"]["organizer"]["opportunistic_enabled"] is False
    assert provenance["retrieval_timing"] == "after-warm-up-v1"
    assert set(report["load_average"]) == {"start", "end"} and len(report["load_average"]["end"]) == 3
    assert set(provenance["datasets"]) == {"locomo"} and set(provenance["archive"]["prepared_sha256"]) == {"locomo"}
    assert output.with_suffix(".md").read_text() in capsys.readouterr().out
    for arguments in (["--output", str(tmp_path / "gate.md")],
                      ["--output", str(output), "--set", "packing.tokn_budget=1"],
                      ["--output", str(output), "--set", "embedding.model_name=other"]):
        with pytest.raises(SystemExit):
            main(["gate", *arguments])
    for benchmarks in ([], ["LoCoMo"], "locmo"):
        with pytest.raises(ValueError, match="Choose benchmarks"):
            await run_gate(benchmarks, archive=archive)


def test_loaders_verify_the_archive_and_resolve_annotations(tmp_path, monkeypatch):
    from benchmarks.integrations import run_gpt54_comparison as gpt54
    from benchmarks.integrations.gpt54_budget import digest

    def write_run(folder, contexts, extra):
        (folder / "contexts").mkdir(parents=True)
        manifest = []
        for question_id, saved in contexts.items():
            path = folder / "contexts" / f"{question_id}.json"
            path.write_text(json.dumps(saved))
            manifest.append({"question_id": question_id, "sha256": digest(path)})
        (folder / "prepared.json").write_text(json.dumps({"complete": True, "contexts": manifest, **extra}))

    locomo = tmp_path / "locomo10.json"
    locomo.write_text(json.dumps([{"sample_id": "conv-1", "conversation": {
        "session_1_date_time": "1:56 pm on 8 May, 2023",
        "session_1": [{"speaker": "Caroline", "dia_id": "D1:1", "text": "I paint."},
                      {"speaker": "Melanie", "dia_id": "D1:2", "text": "Nice."}]},
        "qa": [{"question": "What does Caroline do?", "answer": "Paints", "evidence": ["D1:1", "D7:7"], "category": 1},
               {"question": "Who is kind?", "answer": "Melanie", "evidence": [], "category": 3}]}]))
    monkeypatch.setattr(gpt54, "LOCOMO", locomo)
    monkeypatch.setattr(gpt54, "LOCOMO_SHA", digest(locomo))
    archive = tmp_path / "archive"
    with pytest.raises(ValueError, match="No saved run"):
        _locomo_cases(archive)
    pack = tmp_path / "packs" / "conv-1"
    write_run(archive / "locomo", {
        f"conv-1-q000{n}": {"context": f"context {n}",
                            "receipt": {"user_id": "conv-1", "reference_time": "2023-05-08T13:56:00Z"}}
        for n in range(2)}, {"packs": [{"conversation_id": "conv-1", "config": {"db_path": str(pack / "memory.duckdb")},
                                        "final_artifact": {"tree_sha256": "p" * 64}}]})
    with pytest.raises(ValueError, match="pack not found"):
        _locomo_cases(archive)
    pack.mkdir(parents=True)
    (pack / "memory.duckdb").write_bytes(b"")
    first, second = _locomo_cases(archive)
    assert (first.category, first.user_id, first.pack, first.pack_sha256) == ("multi-hop", "conv-1", pack, "p" * 64)
    assert first.evidence == {"D1:1", "D7:7"} and first.unresolved == ("D7:7",)
    assert first.reference_time == datetime(2023, 5, 8, 13, 56, tzinfo=timezone.utc)
    assert first.saved_context_sha256 == hashlib.sha256(b"context 0").hexdigest()
    assert second.evidence is None and second.category == "open-domain"
    (archive / "locomo" / "contexts" / "conv-1-q0001.json").write_text("{}")
    with pytest.raises(ValueError, match="differs from the archive manifest"):
        _locomo_cases(archive)
    monkeypatch.setattr(gpt54, "LOCOMO_SHA", "0" * 64)
    with pytest.raises(ValueError, match="dataset differs from the saved run"):
        _locomo_cases(archive)

    longmemeval = tmp_path / "longmemeval_s.json"
    longmemeval.write_text(json.dumps([
        {"question_id": "q1", "question_type": "multi-session", "question": "How long did I wait?",
         "haystack_session_ids": ["s-a", "s-b"],
         "haystack_sessions": [[{"role": "user", "content": "Hello."}],
                               [{"role": "user", "content": "It took a year.", "has_answer": True},
                                {"role": "assistant", "content": " ", "has_answer": True}]]},
        {"question_id": "q2_abs", "question_type": "temporal-reasoning", "question": "When?",
         "haystack_session_ids": ["s-c"], "haystack_sessions": [[{"role": "user", "content": "x", "has_answer": True}]]},
    ]))
    monkeypatch.setattr(gpt54, "LONGMEM", longmemeval)
    monkeypatch.setattr(gpt54.lme, "DATASET_SHA256", digest(longmemeval))
    saved = {}
    for question_id in ("q1", "q2_abs"):
        capture = tmp_path / "control" / question_id / "capture.json"
        (capture.parent / "pack").mkdir(parents=True)
        (capture.parent / "pack" / "memory.duckdb").write_bytes(b"")
        capture.write_text(json.dumps({"retrievals": [{"receipt": {
            "user_id": "longmemeval-s-baseline", "reference_time": "2023-05-30T10:18:00Z"}}]}))
        saved[question_id] = {"context_sha256": "a" * 64, "source_capture": str(capture),
                              "source_capture_sha256": digest(capture), "artifact_checksum": "b" * 64}
    write_run(archive / "longmemeval", saved, {})
    answered, abstention = _longmemeval_cases(archive)
    assert answered.evidence == {"s-b#1#0", "s-b#1#1"} and answered.unresolved == ("s-b#1#1",)
    assert answered.pack == tmp_path / "control" / "q1" / "pack" and answered.pack_sha256 == "b" * 64
    assert answered.user_id == "longmemeval-s-baseline"
    assert answered.reference_time == datetime(2023, 5, 30, 10, 18, tzinfo=timezone.utc)
    assert abstention.evidence is None and abstention.unresolved == ()
    (tmp_path / "control" / "q1" / "capture.json").write_text("{}")
    with pytest.raises(ValueError, match="control capture for q1 differs"):
        _longmemeval_cases(archive)
    assert _source_key("longmemeval", {"source_session_id": "s-b", "source_session_position": 1,
                                       "source_turn_index": 0}) == "s-b#1#0"
    assert _source_key("longmemeval", {"source_session_id": "s-b"}) is None
    assert _source_key("locomo", {"source_dialog_id": "D1:1"}) == "D1:1"


# Plain RAG reference ----------------------------------------------------------

def test_pack_records_fills_the_budget_in_rank_order_and_skips_records_that_do_not_fit():
    records = ["alpha beta gamma", "one two three four five six seven eight nine ten eleven twelve", "delta",
               "epsilon zeta"]
    limit = count_tokens("alpha beta gamma\ndelta\nepsilon zeta")
    context, packed, tokens = pack_records(records, limit, "cl100k_base")
    assert packed == [0, 2, 3] and context == "alpha beta gamma\ndelta\nepsilon zeta"
    assert tokens == count_tokens(context) <= limit
    assert pack_records(records, 0, "cl100k_base") == ("", [], 0)
    assert pack_records([], 100, "cl100k_base") == ("", [], 0)


def test_pack_records_drops_a_record_when_the_exact_count_overshoots(monkeypatch):
    # Joined text costs more than its parts here, so the estimate admits a record the exact count rejects.
    monkeypatch.setattr(product_packing, "count_tokens", lambda text, _: len(text.split()) + 3 * text.count("\n"))
    context, packed, tokens = pack_records(["a b", "c d", "e"], 6, "any")
    assert packed == [0, 2] and context == "a b\ne" and tokens == 6


def test_plain_record_uses_the_dataset_date_speaker_and_stored_text():
    node = MemoryNode(user_id="u", node_type=NodeType.FACT, content="Line one.\nLine two.",
                      metadata={"source_role": "assistant"},
                      event_time=datetime(2023, 5, 20, 2, 21, tzinfo=timezone.utc))
    assert plain_record("longmemeval", node) == "(2023/05/20 (Sat) 02:21) assistant: Line one.\nLine two."
    chicago = node.event_time.astimezone(timezone(timedelta(hours=-5)))
    assert plain_record("longmemeval", node.model_copy(update={"event_time": chicago})) == plain_record(
        "longmemeval", node)
    locomo = node.model_copy(update={"content": "(1:56 pm on 8 May, 2023) Caroline: Hi."})
    assert plain_record("locomo", locomo) == "(1:56 pm on 8 May, 2023) Caroline: Hi."
    for broken in (node.model_copy(update={"event_time": None}), node.model_copy(update={"metadata": {}})):
        with pytest.raises(ValueError, match="no session date or role"):
            plain_record("longmemeval", broken)


async def test_plain_ranking_ranks_every_stored_turn_of_the_user_and_fuses_with_rrf(tmp_path, mock_embeddings):
    pack = tmp_path / "pack"
    await make_gate_pack(pack)
    async with MemoryEngine.open(gate_config(pack)) as engine:
        await engine.store("(1:56 pm on 8 May, 2023) Other: I painted a horse too.", user_id="conv-2",
                           node_type=NodeType.FACT, metadata={"source_dialog_id": "D1:1"})
        await engine.process_pending(user_id="conv-2", budget_ms=0)
        question = "What has Caroline painted?"
        vector = await plain_ranking(engine, "vector", question, "conv-1")
        bm25 = await plain_ranking(engine, "bm25", question, "conv-1")
        rrf = await plain_ranking(engine, "rrf", question, "conv-1")
        assert await plain_ranking(engine, "vector", question, "nobody") == []
        with pytest.raises(ValueError, match="plain method"):
            await plain_ranking(engine, "graph", question, "conv-1")
        # A record that is not a turn, or a turn the vector index lacks, fails loudly.
        await engine.store("A note.", user_id="conv-2", node_type=NodeType.NOTE)
        await engine.process_pending(user_id="conv-2", budget_ms=0)
        with pytest.raises(ValueError, match="of which 1 are turns"):
            await plain_ranking(engine, "bm25", question, "conv-2")
        missing = await engine._vector_index.search("anything", "conv-1", k=1)
        await engine._vector_index.delete_by_node_id(missing[0]["node_id"])
        with pytest.raises(ValueError, match="vector index ranks 3 records for 4 stored turns"):
            await plain_ranking(engine, "vector", question, "conv-1")
    texts = {text for _, text in GATE_TURNS}
    assert {node.content for node in vector} == texts and len(vector) == len(texts)
    assert {node.user_id for node in vector + bm25 + rrf} == {"conv-1"}
    assert 0 < len(bm25) < len(texts) and "painted" in bm25[0].content
    assert [str(node.id) for node in rrf] == reciprocal_rank_fusion(
        [[str(node.id) for node in bm25], [str(node.id) for node in vector]], constant=60)


async def test_gate_measures_a_plain_baseline_on_a_verified_copy(tmp_path, mock_embeddings):
    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    [row] = await replay_gate([gate_case(pack, identity)], scratch=scratch, plain="rrf",
                              capture_dir=tmp_path / "captures")
    assert pack_identity(pack) == identity and not any(scratch.iterdir())
    assert row["context_matches_saved"] is False
    assert row["records"] == row["representations"]["plain"] == len(GATE_TURNS)
    assert row["records_without_text"] == 0 and row["candidates"] == len(GATE_TURNS)
    assert row["memory_text_tokens"] == sum(count_tokens(text) for _, text in GATE_TURNS)
    assert row["context_tokens"] <= 3996
    assert row["evidence"]["all_packed"] is True and set(row["evidence"]["ranks"]) == {"D1:1", "D1:3"}
    capture = (tmp_path / "captures" / "locomo" / "conv-1-q0000.json").read_bytes()
    assert hashlib.sha256(capture).hexdigest() == row["capture_sha256"]
    saved = json.loads(capture)
    assert hashlib.sha256(saved["context"].encode()).hexdigest() == row["context_sha256"]
    assert count_tokens(saved["context"]) == row["context_tokens"] == saved["plain"]["context_tokens"]
    assert saved["plain"]["method"] == "rrf" and len(saved["plain"]["packed_node_ids"]) == len(GATE_TURNS)
    assert set(saved["context"].split("\n")) == {text for _, text in GATE_TURNS}

    [small] = await replay_gate([gate_case(pack, identity)], scratch=scratch, plain="vector",
                                overrides=parse_overrides(["packing.token_budget=130"]))
    assert small["context_tokens"] <= 30 and 0 < small["records"] < len(GATE_TURNS)
    for override in ("packing.context_format=\"reader\"", "scoring.relevance_floor=0.3", "packing.tokenizer=x"):
        with pytest.raises(ValueError, match="accepts only packing.token_budget"):
            await replay_gate([gate_case(pack, identity)], scratch=scratch, plain="bm25",
                              overrides=parse_overrides([override]))


def test_gate_reports_label_plain_baselines_and_compare_them_with_prme():
    prme = gate_fixture([gate_row("q1", "multi-hop", all_packed=False), gate_row("q2", "single-hop", all_packed=True)])
    assert "plain" not in prme["provenance"]
    plain = gate_fixture([gate_row("q1", "multi-hop", all_packed=True, matches=False),
                          gate_row("q2", "single-hop", all_packed=True, matches=False)])
    plain["provenance"].update(plain="rrf", plain_rrf_k=60)
    markdown = gate_markdown(plain)
    assert "plain rrf baseline (no PRME retrieval)" in markdown
    assert "reciprocal rank fusion (k=60) of both" in markdown
    assert "Contexts that differ from the saved run" not in markdown
    comparison = compare_gates(prme, plain, samples=200)
    assert comparison["benchmarks"]["locomo"]["gained_all_evidence"] == ["q1"]
    assert "plain rrf baseline" in comparison_markdown(comparison)


def test_gate_cli_refuses_settings_a_plain_baseline_would_ignore(tmp_path):
    for argv in (["--plain", "rrf", "--set", "scoring.relevance_floor=0.3"], ["--plain", "graph"]):
        with pytest.raises(SystemExit):
            gate_main(["--output", str(tmp_path / "gate.json"), *argv])


# --- Session expansion counts (issue #86) -------------------------------------


def _session_row(question_id, category, reached, added, promoted, **kwargs):
    row = gate_row(question_id, category, all_packed=True, **kwargs)
    row["session_context"] = {"reached": reached, "added": added, "promoted": promoted}
    return row


def test_session_expansion_counts_come_from_the_packed_records_paths_and_provenance():
    from types import SimpleNamespace

    def packed(number, paths, *, decay=False):
        kinds = ("current_update", "session_decay") if decay else ("current_update",)
        provenance = SimpleNamespace(adjustments=[SimpleNamespace(kind=kind) for kind in kinds])
        return SimpleNamespace(paths=list(paths), score_provenance=provenance if number != 1 else None)

    counts = product_packing._session_context_observations([
        packed(1, ["VECTOR", "LEXICAL"]),
        packed(2, ["VECTOR", "SESSION_CONTEXT"]),
        packed(3, ["VECTOR", "SESSION_CONTEXT"], decay=True),
        packed(4, ["SESSION_CONTEXT"], decay=True),
    ])
    assert counts == {"reached": 3, "added": 1, "promoted": 2}


async def test_gate_rows_record_session_expansion_counts(tmp_path, mock_embeddings):
    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    [row] = await replay_gate([gate_case(pack, identity)], scratch=scratch)
    counts = row["session_context"]
    assert set(counts) == set(product_packing.SESSION_CONTEXT_COUNTS)
    # Every stored turn shares one session, so expansion reaches the packed turns.
    assert 0 < counts["reached"] <= row["records"] and counts["added"] <= counts["reached"]
    # The setting reaches the replayed retrieval through --set.
    for setting in ("trigger_tier", "adjacent"):
        [opted] = await replay_gate([gate_case(pack, identity)], scratch=scratch,
                                    overrides=parse_overrides([f'packing.session_context_packing="{setting}"']))
        assert set(opted["session_context"]) == set(counts)
        assert opted["records"] == row["records"] and opted["evidence"] == row["evidence"]


def test_gate_summary_and_comparison_report_session_expansion_counts_by_category():
    before = gate_fixture([_session_row("q1", "multi-hop", 4, 0, 3, records=20),
                           _session_row("q2", "single-hop", 2, 0, 2, records=20)])
    after = gate_fixture([_session_row("q1", "multi-hop", 8, 3, 5, records=20),
                          _session_row("q2", "single-hop", 6, 2, 4, records=20)])
    summary = before["benchmarks"]["locomo"]["summary"]["session_context_records"]
    assert summary == {"reached": 6, "added": 0, "promoted": 5, "reached_share": .15, "added_share": 0.0,
                       "promoted_share": .125}
    assert "Packed records through session expansion: 14 reached (35.0% of packed records)" in gate_markdown(after)

    result = compare_gates(before, after, samples=20)
    counts = result["benchmarks"]["locomo"]["session_context_records"]
    assert counts["before"]["multi-hop"]["reached"] == 4 and counts["after"]["multi-hop"]["added"] == 3
    assert counts["after"]["all"]["reached"] == 14
    markdown = comparison_markdown(result)
    assert "| locomo | multi-hop | 4 (20.0%) / 0 / 3 | 8 (40.0%) / 3 / 5 |" in markdown
    assert "| locomo | all | 6 (15.0%) / 0 / 5 | 14 (35.0%) / 5 / 9 |" in markdown

    # Reports written before the counts existed, and plain baselines, still compare.
    old = gate_fixture([gate_row("q1", "multi-hop", all_packed=True, records=20),
                        gate_row("q2", "single-hop", all_packed=True, records=20)])
    assert old["benchmarks"]["locomo"]["summary"]["session_context_records"] is None
    legacy = compare_gates(old, after, samples=20)
    assert legacy["benchmarks"]["locomo"]["session_context_records"] is None
    assert "No session expansion counts for locomo" in comparison_markdown(legacy)


def test_gate_config_refuses_session_context_packing_without_session_expansion(tmp_path):
    setting = 'packing.session_context_packing="adjacent"'
    packing = gate_config(tmp_path, parse_overrides([setting])).packing
    assert packing.session_context_packing == "adjacent"
    # The other product defaults stay.
    assert (packing.context_format, packing.multipath_ordering, packing.session_context_rank_fusion_score_decay) == (
        "reader", "balanced", .6)
    for disabled in ("packing.session_context_window=0", "packing.session_context_top_k=0"):
        with pytest.raises(ValueError, match="applies only with session expansion"):
            gate_config(tmp_path, parse_overrides([setting, disabled]))


def test_gate_config_refuses_reranker_settings_without_the_reranker(tmp_path):
    # Issue #88: without enable_reranker these settings change nothing.
    rank_order = gate_config(tmp_path, parse_overrides([
        "enable_reranker=true", 'reranker_policy="score_envelope"', "reranker_prior_weight=0",
        "reranker_top_k=300"]))
    assert (rank_order.enable_reranker, rank_order.reranker_policy, rank_order.reranker_prior_weight,
            rank_order.reranker_top_k) == (True, "score_envelope", 0.0, 300)
    # The retrieval defaults stay.
    assert rank_order.scoring.fusion == "rrf" and rank_order.packing.context_format == "reader"
    for setting in ("reranker_prior_weight=0", 'reranker_policy="score_envelope"', "reranker_top_k=300",
                    'reranker_model="other"'):
        with pytest.raises(ValueError, match=r"applies only with enable_reranker=true"):
            gate_config(tmp_path, parse_overrides([setting]))
        with pytest.raises(ValueError, match=r"applies only with enable_reranker=true"):
            gate_config(tmp_path, parse_overrides(["enable_reranker=false", setting]))
    with pytest.raises(ValueError, match=r"reranker_policy, reranker_prior_weight apply only with"):
        gate_config(tmp_path, parse_overrides(["reranker_prior_weight=0", 'reranker_policy="score_envelope"']))
    # A reranker run that would change nothing, and the legacy policy that would mix scales.
    for no_op in (["reranker_top_k=0"], ['reranker_policy="score_envelope"', "reranker_prior_weight=1"]):
        with pytest.raises(ValueError, match="needs a nonzero reranker_top_k and a reranker_prior_weight below 1"):
            gate_config(tmp_path, parse_overrides(["enable_reranker=true", *no_op]))
    with pytest.raises(ValueError, match="needs an envelope reranker_policy"):
        gate_config(tmp_path, parse_overrides(["enable_reranker=true", "reranker_prior_weight=0"]))


def test_gate_records_the_reranker_runtime_that_decides_model_scores(tmp_path):
    from benchmarks.diagnostics.product_packing import _reranker_runtime

    runtime = _reranker_runtime(gate_config(tmp_path, parse_overrides(["enable_reranker=true"])))
    assert set(runtime) == {"sentence-transformers", "transformers", "torch", "device", "model_revision"}
    assert all(value is None or isinstance(value, str) for value in runtime.values())


async def test_gate_times_the_reranker_inside_retrieval_and_replays_its_receipts(
        tmp_path, mock_embeddings, monkeypatch):
    from prme.retrieval.reranker import CrossEncoderReranker

    pack = tmp_path / "pack"
    identity = await make_gate_pack(pack)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    scored = []

    def predict(self, pairs):
        scored.append(len(pairs))
        # Prefers D1:1, which rank fusion puts below D1:3. Both are the multi-path
        # tier's only records, so the packed order changes by score alone. Ranking by
        # position instead could leave tied scores, broken by random node IDs.
        return [0.9 if "sunset" in text else 0.5 if "horse" in text else 0.1 for _, text in pairs]

    monkeypatch.setattr(CrossEncoderReranker, "_predict_sync", predict)
    [fused] = await replay_gate([gate_case(pack, identity)], scratch=scratch)
    assert fused["reranking_seconds"] is None and scored == []
    captures = tmp_path / "captures"
    rows = await replay_gate([gate_case(pack, identity), gate_case(pack, identity)], scratch=scratch,
                             overrides=parse_overrides(["enable_reranker=true", 'reranker_policy="score_envelope"',
                                                        "reranker_prior_weight=0"]), capture_dir=captures)
    # The warm-up scores one pair; each question scores its candidates once. The replay
    # checks that every receipt replays the returned ranking.
    assert scored[0] == 1 and len(scored) == 3
    for row in rows:
        assert 0 < row["reranking_seconds"] <= row["retrieval_seconds"]
    assert rows[0]["context_sha256"] == rows[1]["context_sha256"] != fused["context_sha256"]
    receipt = json.loads((captures / "locomo" / "conv-1-q0000.json").read_bytes())["receipt"]
    assert receipt["execution"]["parameters"]["reranker_prior_weight"] == 0.0
    assert receipt["execution"]["features"]["reranker"]["prior_weight"] == 0.0
    assert pack_identity(pack) == identity


def test_gate_summary_and_comparison_report_reranking_latency():
    # Issue #88: the reranker's own time, beside the retrieval time.
    timing = "after-warm-up-v1"
    before = gate_fixture([gate_row("q1", "multi-hop", all_packed=True), gate_row("q2", "multi-hop", all_packed=False)],
                          timing=timing)
    after = gate_fixture([gate_row("q1", "multi-hop", all_packed=True, seconds=.3, reranking_seconds=.2),
                          gate_row("q2", "multi-hop", all_packed=True, seconds=.5, reranking_seconds=.4)],
                         timing=timing)
    assert summarize_gate(before["rows"])["reranking_seconds"] is None
    assert summarize_gate(after["rows"])["reranking_seconds"] == {"p50": pytest.approx(.3),
                                                                 "p95": pytest.approx(.39)}
    assert "reranking" not in gate_markdown(before)
    assert ("Cross-encoder reranking inside retrieval, p50 / p95 per question: locomo 0.300 / 0.390 s. "
            "Retrieval time includes it.") in gate_markdown(after)
    bench = compare_gates(before, after, samples=20)["benchmarks"]["locomo"]
    assert bench["reranking_seconds"] == {"before": None, "after": {"p50": pytest.approx(.3),
                                                                   "p95": pytest.approx(.39)}}
    markdown = comparison_markdown(compare_gates(before, after, samples=20))
    assert "After, cross-encoder reranking inside retrieval, p50 / p95 per question: locomo 0.300 / 0.390 s." \
        in markdown
    assert "Before, cross-encoder" not in markdown
    # Like retrieval time, reranking time compares only between reports timed after a warm-up.
    untimed = gate_fixture(after["rows"])
    assert compare_gates(before, untimed, samples=20)["benchmarks"]["locomo"]["reranking_seconds"] == {
        "before": None, "after": None}
