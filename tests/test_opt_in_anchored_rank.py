from unittest.mock import Mock

import pytest

from benchmarks.diagnostics.opt_in_anchored_rank import (
    AnchoredRankEnvelopeReranker,
    original_anchor,
)
from benchmarks.diagnostics.opt_in_rank_envelope import RankEnvelopeReranker
from prme.retrieval.config import PackingConfig
from prme.retrieval.execution import reranker_identity
from prme.retrieval.packing import pack_context
from prme.types import NodeType
from tests.test_opt_in_rank_envelope import inputs, receipt


async def test_preserves_original_anchor_through_real_balanced_packing_and_replay():
    rows = inputs()
    before = [r.model_dump() for r in rows]
    old, new = RankEnvelopeReranker(), AnchoredRankEnvelopeReranker()
    old._predict_sync = new._predict_sync = Mock(return_value=[0.001, 0.99])
    prior = await old.rerank("evidence", rows, top_k=2)
    current = await new.rerank("evidence", rows, top_k=2)
    one = pack_context(
        [rows[0]],
        PackingConfig(token_budget=5000, overhead_tokens=0, min_fidelity="full"),
    )
    config = PackingConfig(
        token_budget=one.tokens_used, overhead_tokens=0, min_fidelity="full"
    )

    def packed_ids(bundle):
        return [c.node.id for section in bundle.sections.values() for c in section]

    assert packed_ids(pack_context(prior, config)) == [rows[1].node.id]
    bundle = pack_context(current, config)
    assert packed_ids(bundle) == [rows[0].node.id]
    assert sorted(c.composite_score for c in current[:2]) == sorted(
        c.composite_score for c in rows[:2]
    )
    assert current[2] == rows[2]
    assert [r.model_dump() for r in rows] == before
    saved = receipt(current, bundle, config)
    assert saved.schema_version == 13
    assert saved.replay_ranking() == tuple(c.node.id for c in current)
    assert reranker_identity(old) != reranker_identity(new)


@pytest.mark.parametrize(
    "kind", ["instruction", "pinned", "salience", "task", "single_path"]
)
def test_mandatory_and_single_path_records_are_not_ordinary_anchors(kind):
    rows = inputs()
    if kind == "instruction":
        rows[0].node = rows[0].node.model_copy(
            update={"node_type": NodeType.INSTRUCTION}
        )
    if kind == "pinned":
        rows[0].node = rows[0].node.model_copy(update={"pinned": True})
    if kind == "salience":
        rows[0].node = rows[0].node.model_copy(update={"salience": 1.0})
    if kind == "task":
        rows[0].node = rows[0].node.model_copy(update={"node_type": NodeType.TASK})
    if kind == "single_path":
        rows[0].path_count = 1
    assert original_anchor(rows) == rows[1].node.id


async def test_absent_or_out_of_prefix_anchor_retains_previous_policy():
    rows = inputs()
    for c in rows[:2]:
        c.path_count = 1
    old, new = RankEnvelopeReranker(), AnchoredRankEnvelopeReranker()
    old._predict_sync = new._predict_sync = Mock(return_value=[0.001, 0.99])
    assert await new.rerank("x", rows, top_k=2) == await old.rerank("x", rows, top_k=2)
    rows[2].path_count = 1
    assert original_anchor(rows) is None
    assert await new.rerank("x", rows, top_k=2) == await old.rerank("x", rows, top_k=2)


async def test_zero_empty_invalid_and_repeat_boundaries():
    new = AnchoredRankEnvelopeReranker()
    new._predict_sync = Mock(return_value=[0.99, 0.001])
    assert await new.rerank("x", []) == []
    rows = inputs()
    assert await new.rerank("x", rows, top_k=0) == rows
    assert await new.rerank("x", rows, top_k=2) == await new.rerank("x", rows, top_k=2)
    with pytest.raises(ValueError):
        await new.rerank("x", rows, top_k=-1)
    with pytest.raises(ValueError):
        await new.rerank("x", rows, prior_weight=2)


async def test_authored_source_executor_replays_prior_and_rejects_drift(
    tmp_path, monkeypatch
):
    import json
    from benchmarks.diagnostics import opt_in_anchored_rank_study as current
    from benchmarks.diagnostics import opt_in_rank_envelope_study_v2 as prior
    from benchmarks.diagnostics.register_opt_in_interactions import (
        clean_config,
        file_sha,
    )
    from prme.retrieval.reranker import CrossEncoderReranker

    case = {
        "question_id": "authored",
        "question": "Which telescope color do I prefer?",
        "question_date": "2024/01/02 (Tue) 12:00",
        "question_type": "single-session-user",
        "haystack_session_ids": ["authored-session"],
        "haystack_dates": ["2024/01/01 (Mon) 12:00"],
        "haystack_sessions": [
            [
                {
                    "role": "user",
                    "content": "I prefer blue telescopes, not red ones.",
                    "has_answer": True,
                }
            ]
        ],
        "answer_session_ids": ["authored-session"],
    }
    data = clean_config()
    data.update(
        db_path="{pack}/memory.duckdb",
        vector_path="{pack}/vectors.usearch",
        lexical_path="{pack}/lexical_index",
        duckdb_threads=1,
        organizer={**data["organizer"], "opportunistic_enabled": False},
    )
    controls = tmp_path / "controls"
    monkeypatch.setattr(prior, "CONTROL_ROOT", controls)
    monkeypatch.setattr(current, "BASE", controls / "baseline")
    monkeypatch.setattr(current, "PRIOR", tmp_path / "prior")
    monkeypatch.setattr(
        CrossEncoderReranker, "_predict_sync", lambda self, pairs: [0.01] * len(pairs)
    )
    for name, flag in (("baseline", False), ("reranker", True)):
        folder = controls / name / "authored"
        folder.mkdir(parents=True)
        arm = {"config": {**data, "enable_reranker": flag}, "stratum": "fresh"}
        with prior.study.matched_admission():
            await prior.study.capture(case, arm, folder, None)
    earlier = await prior.evaluate(case, arm, current.PRIOR, prior.ObservedReranker())
    result = await current.evaluate(
        case, {"configuration": arm["config"]}, earlier, tmp_path / "current"
    )
    assert result["status"] == "complete"
    record = json.loads((tmp_path / "current/cases/authored.json").read_text())
    assert record["new_model_calls"] == 0
    assert record["arms"]["anchored_rank"]["receipt"]["schema_version"] == 13
    assert all(
        v["evidence"]["complete_source_literal_recall"] for v in record["arms"].values()
    )
    path = current.PRIOR / "cases/authored.json"
    altered = json.loads(path.read_text())
    altered["arms"]["rank_envelope"]["context"] += " drift"
    path.write_text(json.dumps(altered))
    with pytest.raises(current.study.old.ResearchFailure, match="replay differs"):
        await current.evaluate(
            case,
            {"configuration": arm["config"]},
            {**earlier, "case_sha256": file_sha(path)},
            tmp_path / "bad",
        )
