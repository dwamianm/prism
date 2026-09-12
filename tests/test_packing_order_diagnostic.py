"""Counterfactual packing uses identical source candidates and enforces replay."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from benchmarks.diagnostics.packing_order import compare
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import compute_str, pack_context


def fixture_report():
    def source(sid, text, score):
        return RetrievalCandidate(node=MemoryNode(user_id="u", node_type="note", content=text,
                                                   metadata={"source_turn": sid}),
                                  composite_score=score, path_count=2, paths=["VECTOR", "LEXICAL"])
    relevant = source("relevant", "The project uses PostgreSQL. " * 100, .95)
    short = source("short", "Thanks.", .5)
    cfg = PackingConfig(token_budget=2000, overhead_tokens=0, min_fidelity="full")
    cfg.token_budget = pack_context([relevant], cfg).tokens_used
    control = pack_context([relevant, short], cfg)
    return {
        "complete": True, "process_exit_code": 0, "token_budget": cfg.token_budget,
        "source": {"engine_config": {"packing": cfg.model_dump(mode="json")}},
        "results": [{"details": [{"question_id": "q", "category": "dynamic_conflict",
                                   "reference_date": "2024-01-01", "question": "Which database?",
                                   "gold_answer": "label-sentinel",
                                   "methods": {"prme_product": {
                                       "context": control.render(), "tokens": control.tokens_used,
                                       "candidate_snapshots": [c.model_dump(mode="json") for c in (relevant, short)],
                                   }}}]}],
    }


async def test_experiment_changes_only_order_and_keeps_labels_out_of_reader():
    report = fixture_report()
    original = deepcopy(report)
    reader = AsyncMock(return_value="Answer")
    result = await compare(report, reader)
    assert report == original
    assert result["baseline_reproduction_passed"]
    variants = result["details"][0]["variants"]
    assert variants["density"]["sources"] == [{"id": "short", "representation": "full"}]
    assert variants["score"]["sources"] == [{"id": "relevant", "representation": "full"}]
    assert all(v["tokens"] <= report["token_budget"] for v in variants.values())
    assert "label-sentinel" not in repr(reader.await_args_list)
    from prme.retrieval import packing
    assert packing.compute_str is compute_str


@pytest.mark.parametrize("mutation", ["context", "tokens", "missing_snapshot", "exit", "later_question"])
async def test_nonreproducing_or_incomplete_input_never_calls_reader(mutation):
    report = fixture_report()
    row = report["results"][0]["details"][0]
    if mutation == "exit":
        report["process_exit_code"] = -6
    elif mutation == "missing_snapshot":
        del row["methods"]["prme_product"]["candidate_snapshots"]
    elif mutation == "later_question":
        bad = deepcopy(row)
        bad["methods"]["prme_product"]["context"] = "changed"
        report["results"][0]["details"].append(bad)
    elif mutation == "context":
        row["methods"]["prme_product"]["context"] = "changed"
    else:
        row["methods"]["prme_product"]["tokens"] += 1
    reader = AsyncMock()
    with pytest.raises(ValueError):
        await compare(report, reader)
    reader.assert_not_awaited()
