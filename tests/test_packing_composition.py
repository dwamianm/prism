"""Composed priority keeps existing controls, budgets, and source qualifiers."""

import pytest

from benchmarks.diagnostics.packing_composition import pack
from benchmarks.diagnostics.packing_head import pack_head
from benchmarks.diagnostics.packing_length import pack_length
from prme.retrieval import packing
from prme.retrieval.config import PackingConfig
from tests.test_packing_length_diagnostic import source


@pytest.mark.parametrize("reserve_head,alpha", [(False, 1), (False, 0.25), (True, 1)])
def test_controls_match_existing_policy_and_preserve_inputs(reserve_head, alpha):
    candidates = [
        source("Only if the pilot succeeds. " * 45, 0.99),
        *[source(f"Short source {i}", 0.4) for i in range(8)],
    ]
    config = PackingConfig(
        token_budget=900,
        overhead_tokens=0,
        min_fidelity="full",
        multipath_ordering="density",
    )
    before = [candidate.model_dump(mode="json") for candidate in candidates]
    original = packing.compute_str
    expected = (
        pack_head(candidates, config)
        if reserve_head
        else pack_length(candidates, config, alpha)
    )
    assert (
        pack(candidates, config, reserve_head=reserve_head, alpha=alpha).render()
        == expected.render()
    )
    assert before == [candidate.model_dump(mode="json") for candidate in candidates]
    assert packing.compute_str is original


def test_composition_retains_pin_priority_and_excludes_oversized_sources():
    pin = source("Pinned source.", 0.01, pinned=True)
    head = source("Only if the pilot succeeds. " * 200, 1)
    config = PackingConfig(token_budget=1000, overhead_tokens=0, min_fidelity="full")
    expected = packing.pack_context([pin], config)
    config = config.model_copy(update={"token_budget": expected.tokens_used})
    result = pack([head, pin], config, reserve_head=True, alpha=0.25)
    assert result.render() == expected.render()
    assert head.node.id in result.excluded_ids
    assert result.tokens_used <= config.token_budget


def test_packing_exception_restores_comparator(monkeypatch):
    original = packing.compute_str

    def fail(*args, **kwargs):
        raise RuntimeError("Authored packer failure")

    monkeypatch.setattr(packing, "pack_context", fail)
    with pytest.raises(RuntimeError, match="Authored"):
        pack([], PackingConfig(), reserve_head=True, alpha=0.25)
    assert packing.compute_str is original
