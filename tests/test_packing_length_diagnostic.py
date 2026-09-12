"""Length ablation endpoints and label-blind packing contracts."""

from copy import deepcopy

import pytest

from benchmarks.diagnostics.packing_length import ALPHAS, pack_length, score_capture
from benchmarks.diagnostics.product_packing import measure
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import compute_str, pack_context


def source(text, score, **kwargs):
    return RetrievalCandidate(node=MemoryNode(
        user_id="u", node_type="note", content=text, metadata={"source_turn": text[:5]}, **kwargs),
        composite_score=score, path_count=2, paths=["VECTOR", "LEXICAL"])


@pytest.mark.parametrize("alpha,order", [(0, "score"), (1, "density")])
def test_endpoints_reproduce_product_and_restore_comparator(alpha, order):
    candidates = [source("Long qualified source. " * 100, .99), source("Short source.", .5)]
    before = [c.model_dump(mode="json") for c in candidates]
    cfg = PackingConfig(token_budget=900, overhead_tokens=0, multipath_ordering=order)
    assert pack_length(candidates, cfg, alpha).render() == pack_context(candidates, cfg).render()
    assert before == [c.model_dump(mode="json") for c in candidates]
    from prme.retrieval import packing
    assert packing.compute_str is compute_str


@pytest.mark.parametrize("alpha", ALPHAS)
def test_priority_budget_and_labels_do_not_change_context(alpha):
    pinned = source("Pinned source.", .01, pinned=True)
    other = source("Other source.", 1)
    cfg = PackingConfig(token_budget=1000, overhead_tokens=0, min_fidelity="full")
    cfg = cfg.model_copy(update={"token_budget": pack_context([pinned], cfg).tokens_used})
    bundle = pack_length([other, pinned], cfg, alpha)
    measured = measure(bundle, set(), cfg)
    assert measured["content_source_ids"] == ["Pinne"]
    assert measured["tokens"] <= cfg.token_budget
    positive = score_capture({"arm": deepcopy(measured)}, {"Pinne"})["arm"]
    negative = score_capture({"arm": deepcopy(measured)}, {"Other"})["arm"]
    assert positive["context_sha256"] == negative["context_sha256"]
    assert positive["evidence_recall"] == 1 and negative["evidence_recall"] == 0


def test_exception_restores_comparator(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("packing failed")
    monkeypatch.setattr("benchmarks.diagnostics.packing_length.pack_context", fail)
    with pytest.raises(RuntimeError, match="packing failed"):
        pack_length([], PackingConfig(), .5)
    from prme.retrieval import packing
    assert packing.compute_str is compute_str
