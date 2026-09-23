from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from benchmarks.diagnostics import opt_in_reformulation_merge as policy
from prme.models.nodes import MemoryNode
from prme.retrieval.models import RetrievalCandidate
from prme.types import NodeType, Scope


def candidate(index, paths, semantic=0, lexical=0):
    stamp = datetime(2026, 9, 22, tzinfo=timezone.utc)
    node = MemoryNode(
        id=UUID(int=index),
        user_id="authored",
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        content="The telescope is blue.",
        created_at=stamp,
        updated_at=stamp,
        valid_from=stamp,
        last_reinforced_at=stamp,
    )
    return RetrievalCandidate(
        node=node,
        paths=paths,
        path_count=len(paths),
        semantic_score=semantic,
        lexical_score=lexical,
    )


def test_overlap_changes_signals_without_counting_queries_as_backends():
    base = candidate(1, ["VECTOR"], 0.3)
    alt = candidate(1, ["LEXICAL"], 0, 0.9)
    merged, trace = policy.merge_signals([base], [[alt], [alt]])
    assert merged[0].paths == ["LEXICAL", "VECTOR"]
    assert merged[0].path_count == 2
    assert (merged[0].semantic_score, merged[0].lexical_score) == (0.3, 0.9)
    assert trace["changed_existing_ids"] == [str(base.node.id)]
    assert trace["added_ids"] == []
    assert base.paths == ["VECTOR"] and base.lexical_score == 0


def test_same_backend_repeat_never_creates_multipath_bonus():
    base = candidate(1, ["VECTOR"], 0.3)
    alt = candidate(1, ["VECTOR"], 0.8)
    merged, _ = policy.merge_signals([base], [[alt], [alt]])
    assert merged[0].path_count == 1 and merged[0].semantic_score == 0.8


@pytest.mark.parametrize(
    "field,value",
    [("content", "different claim"), ("user_id", "foreign"), ("scope", Scope.PROJECT)],
)
def test_snapshot_owner_or_scope_collision_fails_atomically(field, value):
    base = candidate(1, ["VECTOR"], 0.3)
    other = candidate(1, ["LEXICAL"], 0, 0.9)
    setattr(other.node, field, value)
    before = base.model_dump(mode="json")
    with pytest.raises(ValueError, match="source snapshots"):
        policy.merge_signals([base], [[candidate(2, ["VECTOR"], 0.9), other]])
    assert base.model_dump(mode="json") == before


def test_new_candidates_preserve_source_and_repeated_merge_is_idempotent():
    base = candidate(1, ["VECTOR"], 0.3)
    alt = candidate(2, ["LEXICAL"], 0, 0.9)
    merged, trace = policy.merge_signals([base], [[alt]])
    repeated, _ = policy.merge_signals(merged, [[alt]])
    assert trace["added_ids"] == [str(alt.node.id)]
    assert merged[1].node.model_dump(mode="json") == alt.node.model_dump(mode="json")
    assert [c.model_dump(mode="json") for c in merged] == [
        c.model_dump(mode="json") for c in repeated
    ]


def test_nonfinite_signal_fails_without_mutation():
    base = candidate(1, ["VECTOR"], 0.3)
    other = candidate(1, ["LEXICAL"], 0, float("nan"))
    with pytest.raises(ValueError, match="Non-finite"):
        policy.merge_signals([base], [[other]])
    assert base.semantic_score == 0.3 and base.lexical_score == 0


async def test_alternate_backend_failure_does_not_commit_partial_merge(monkeypatch):
    monkeypatch.setattr(
        "prme.retrieval.reformulation.reformulate_query",
        AsyncMock(return_value=["telescopes"]),
    )
    monkeypatch.setattr(policy, "analyze_query", AsyncMock(return_value=object()))

    async def failure(*args, **kwargs):
        kwargs["diagnostics"].backend_failures["VECTOR"] = "backend_error"
        return [candidate(2, ["LEXICAL"], 0, 0.9)], {}

    monkeypatch.setattr(policy, "generate_candidates", failure)
    engine = SimpleNamespace(
        _query_reformulation_provider="ollama",
        _query_reformulation_model="authored",
        _query_reformulation_count=2,
        _temporal_languages=["en"],
        _graph_store=None,
        _vector_index=None,
        _lexical_index=None,
        _research_merge_trace=[],
    )
    values = [candidate(1, ["VECTOR"], 0.3)]
    with pytest.raises(RuntimeError, match="backend failed"):
        await policy.expand_with_merge(
            engine,
            "telescope",
            candidates=values,
            user_id="authored",
            scope=[Scope.PERSONAL],
            time_from=None,
            time_to=None,
            retrieval_mode=None,
            config=None,
        )
    assert len(values) == 1 and not engine._research_merge_trace


async def test_complete_authored_assay_and_frozen_input_rejection(
    tmp_path, monkeypatch
):
    from benchmarks.diagnostics import opt_in_reformulation_merge_study as assay
    from benchmarks.diagnostics.register_opt_in_interactions import (
        clean_config,
        file_sha,
    )

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
    config = clean_config()
    config.update(
        db_path="{pack}/memory.duckdb",
        vector_path="{pack}/vectors.usearch",
        lexical_path="{pack}/lexical_index",
        duckdb_threads=1,
        organizer={**config["organizer"], "opportunistic_enabled": False},
    )
    reform = {**config, "enable_query_reformulation": True}
    monkeypatch.setattr(assay, "BASE", tmp_path / "original")
    monkeypatch.setattr(
        "prme.retrieval.reformulation.reformulate_query", assay.cached_reformulation
    )
    record = {
        "query": case["question"],
        "alternatives": ["blue telescope", "color preference"],
        "options": {
            "provider": reform["extraction"]["provider"],
            "model": reform["extraction"]["model"],
            "count": reform["query_reformulation_count"],
        },
        "calls": 0,
    }
    frozen = {}
    for arm, configuration in [("baseline", config), ("query_reformulation", reform)]:
        folder = assay.BASE / arm / case["question_id"]
        folder.mkdir(parents=True)
        token = assay.CACHED.set(record)
        try:
            with assay.study.matched_admission():
                await assay.study.capture(
                    case, {"config": configuration, "stratum": "fresh"}, folder, None
                )
        finally:
            assay.CACHED.reset(token)
        (folder / "result.json").write_text('{"status":"complete"}')
        (folder / "feature-observations.json").write_text(
            json.dumps(
                {
                    "reformulations": [
                        {
                            "query": record["query"],
                            "alternatives": record["alternatives"],
                        }
                    ]
                    * 2
                }
            )
        )
        frozen[arm] = {
            "authored": {
                "capture_sha256": file_sha(folder / "capture.json"),
                "feature_observations_sha256": file_sha(
                    folder / "feature-observations.json"
                ),
            }
        }
    reg = {
        "configuration": config,
        "reformulation_configuration": reform,
        "input_cases": frozen,
    }
    root = tmp_path / "new-source"
    row = await assay.evaluate(case, reg, root)
    assert row["status"] == "complete"
    result = json.loads((root / "cases/authored.json").read_text())
    assert set(result["arms"]) == set(assay.ARMS)
    assert all(
        v["evidence"]["complete_source_literal_recall"] and v["context_tokens"] <= 3996
        for v in result["arms"].values()
    )
    assert (
        result["new_hosted_model_calls"] == 0
        and result["local_query_embeddings_reexecuted"]
    )
    summary, prepared, gate, paired = assay.finish_source(root, [row])
    assert summary["merged_reformulation"]["complete_source_questions"] == 1
    assert not gate
    assert paired["baseline"]["complete_source"]["ci95"] == [0.0, 0.0]
    assert paired["baseline"]["mean_source_fraction"]["difference"] == 0.0
    assert len(prepared["control"]) == len(prepared["candidate"]) == 1
    (assay.BASE / "query_reformulation/authored/feature-observations.json").write_text(
        "{}"
    )
    with pytest.raises(assay.study.old.ResearchFailure, match="Input capture changed"):
        await assay.evaluate(case, reg, tmp_path / "changed-source")
