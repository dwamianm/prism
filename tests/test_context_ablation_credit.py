"""Exact packed-context interventions produce citation-checked credit."""

from datetime import datetime, timezone
import hashlib
from uuid import UUID, uuid4

import pytest

from prme import (
    AnswerCitationRecord,
    ablate_context,
    assess_context_presence,
)
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens
from prme.types import NodeType, RepresentationLevel


def candidate(number: int, content: str, *, node_type=NodeType.NOTE):
    return RetrievalCandidate(
        node=MemoryNode(
            id=UUID(int=number),
            user_id="alice",
            node_type=node_type,
            content=content,
            created_at=datetime(2024, 1, number, tzinfo=timezone.utc),
        ),
        composite_score=1 - number / 10,
        path_count=2,
        paths=["VECTOR", "LEXICAL"],
    )


def packed():
    config = PackingConfig(
        token_budget=1000,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
    )
    bundle = pack_context(
        [
            candidate(1, "The launch code is violet."),
            candidate(2, "The observatory is in Chile."),
            candidate(3, "Always answer briefly.", node_type=NodeType.INSTRUCTION),
        ],
        config,
        coverage_notice="Coverage is intentionally bounded.",
    )
    return bundle, config


def citation(bundle, node_id):
    return AnswerCitationRecord(
        citation_id=uuid4(),
        request_id=uuid4(),
        answer_id="answer-1",
        cited_node_ids=(node_id,),
        method="application_verified",
        user_id="alice",
        recorded_at=datetime.now(timezone.utc),
        receipt_checksum="1" * 64,
        context_sha256=hashlib.sha256(bundle.render().encode()).hexdigest(),
    )


def test_ablation_removes_only_requested_entry_and_preserves_baseline():
    bundle, config = packed()
    before = bundle.model_dump_json()
    target = UUID(int=1)

    ablation = ablate_context(bundle, [target])

    assert bundle.model_dump_json() == before
    assert "launch code" in bundle.render()
    assert "launch code" not in ablation.counterfactual.render()
    assert "observatory" in ablation.counterfactual.render()
    assert "Always answer briefly" in ablation.counterfactual.render()
    assert "Coverage is intentionally bounded" in ablation.counterfactual.render()
    assert ablation.counterfactual.included_count == bundle.included_count - 1
    assert target in ablation.counterfactual.excluded_ids
    assert ablation.counterfactual.tokens_used == count_tokens(
        ablation.counterfactual.render(), config.tokenizer
    )
    assert (
        ablation.counterfactual.tokens_used
        + ablation.counterfactual.budget_remaining
        == bundle.tokens_used + bundle.budget_remaining
    )
    assert ablation.baseline_context_sha256 == hashlib.sha256(
        bundle.render().encode()
    ).hexdigest()
    assert ablation.counterfactual_context_sha256 == hashlib.sha256(
        ablation.counterfactual.render().encode()
    ).hexdigest()


@pytest.mark.parametrize(
    ("baseline", "counterfactual", "tier", "value"),
    [
        (True, False, "load_bearing", 1.0),
        (True, True, "cited_non_flipping", 0.6),
        (False, True, "misleading", -1.0),
        (False, False, "cited_wrong_noncuring", 0.0),
    ],
)
def test_presence_credit_has_four_auditable_outcome_tiers(
    baseline, counterfactual, tier, value,
):
    bundle, _ = packed()
    target = UUID(int=1)
    ablation = ablate_context(bundle, [target])

    credit = assess_context_presence(
        ablation,
        citation(bundle, target),
        node_id=target,
        baseline_correct=baseline,
        counterfactual_correct=counterfactual,
        evaluation_id="qwen35b-reader+gemma31b-judge-v1",
        counterfactual_answer_sha256="2" * 64,
    )

    assert credit.tier == tier
    assert credit.value == value
    assert credit.node_id == target
    assert credit.baseline_context_sha256 == ablation.baseline_context_sha256
    assert credit.counterfactual_context_sha256 == (
        ablation.counterfactual_context_sha256
    )


def test_ablation_and_credit_fail_closed_on_ambiguous_inputs():
    bundle, _ = packed()
    target = UUID(int=1)
    other = UUID(int=2)
    for ids in ([], [target, target], [uuid4()]):
        with pytest.raises(ValueError):
            ablate_context(bundle, ids)

    ablation = ablate_context(bundle, [target])
    with pytest.raises(ValueError, match="cited memory"):
        assess_context_presence(
            ablation,
            citation(bundle, other),
            node_id=target,
            baseline_correct=True,
            counterfactual_correct=False,
            evaluation_id="fixed-protocol",
        )

    wrong_context = citation(bundle, target).model_copy(
        update={"context_sha256": "0" * 64}
    )
    with pytest.raises(ValueError, match="contexts do not match"):
        assess_context_presence(
            ablation,
            wrong_context,
            node_id=target,
            baseline_correct=True,
            counterfactual_correct=False,
            evaluation_id="fixed-protocol",
        )

    multi = ablate_context(bundle, [target, other])
    with pytest.raises(ValueError, match="one matching"):
        assess_context_presence(
            multi,
            citation(bundle, target),
            node_id=target,
            baseline_correct=True,
            counterfactual_correct=False,
            evaluation_id="fixed-protocol",
        )
