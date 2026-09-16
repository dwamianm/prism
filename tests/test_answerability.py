from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from prme.retrieval import answerability
from prme.retrieval.answerability import (
    AnswerabilityAction,
    AnswerabilityConfig,
    AnswerabilityError,
    AnswerabilityEvaluator,
    AnswerabilityStatus,
    AnswerabilityVerdict,
)
from prme.retrieval.models import MemoryBundle


def _bundle() -> MemoryBundle:
    return MemoryBundle(
        rendered_context="[m1] Craig is a developer.\n[m2] Project A is current.",
        context_references={"m1": uuid4(), "m2": uuid4()},
        included_count=2,
    )


def _raw(*requirements):
    return answerability._RawAssessment(
        requirements=list(requirements),
        reasoning="Checked every independently answerable requirement.",
    )


def _requirement(
    text: str,
    status: AnswerabilityStatus,
    refs: list[str] | None = None,
):
    return answerability._RawRequirement(
        requirement=text,
        status=status,
        evidence_refs=refs or [],
        explanation=f"Evidence decision for {text}",
    )


async def test_empty_bundle_abstains_without_calling_provider(monkeypatch):
    evaluator = AnswerabilityEvaluator()
    monkeypatch.setattr(
        evaluator,
        "_ensure_client",
        lambda: pytest.fail("empty bundle must not initialize a provider"),
    )

    assessment = await evaluator.assess("What is known?", MemoryBundle())

    assert assessment.verdict == AnswerabilityVerdict.INSUFFICIENT
    assert assessment.recommended_action == AnswerabilityAction.ABSTAIN
    assert assessment.should_abstain is True
    assert assessment.model_called is False
    assert assessment.missing_information == ("Evidence needed to answer the question",)


async def test_compound_question_returns_partial_with_resolved_citations():
    bundle = _bundle()
    client = AsyncMock()
    client.create.return_value = _raw(
        _requirement(
            "User background",
            AnswerabilityStatus.SUPPORTED,
            ["m1"],
        ),
        _requirement(
            "Previous projects",
            AnswerabilityStatus.UNSUPPORTED,
        ),
    )
    evaluator = AnswerabilityEvaluator(
        AnswerabilityConfig(
            provider="ollama",
            model="gpt-oss:120b-cloud",
            api_key=SecretStr("not-exposed"),
            base_url="http://127.0.0.1:11434/v1/",
        )
    )
    evaluator._client = client

    assessment = await evaluator.assess(
        "Tell me my background and previous projects",
        bundle,
        answer="Craig is a developer and Project A was previous.",
    )

    assert assessment.verdict == AnswerabilityVerdict.PARTIAL
    assert assessment.recommended_action == AnswerabilityAction.ANSWER_PARTIALLY
    assert assessment.should_abstain is False
    assert assessment.requirements[0].evidence_refs == ("m1",)
    assert assessment.requirements[0].evidence_ids == (bundle.context_references["m1"],)
    assert assessment.missing_information == ("Previous projects",)
    assert assessment.citation_errors == ()
    assert assessment.answer_sha256 is not None
    assert len(assessment.prompt_sha256) == 64
    assert len(assessment.configuration_sha256) == 64
    assert len(assessment.assessment_sha256) == 64
    assert assessment.model_called is True
    assert (
        "DRAFT ANSWER TO VERIFY"
        in client.create.await_args.kwargs["messages"][1]["content"]
    )
    assert "not-exposed" not in repr(evaluator.config)
    assert evaluator.config.base_url == "http://127.0.0.1:11434/v1"


async def test_unknown_citation_fails_closed_to_insufficient():
    client = AsyncMock()
    client.create.return_value = _raw(
        _requirement(
            "Enforced rules",
            AnswerabilityStatus.SUPPORTED,
            ["m999"],
        )
    )
    evaluator = AnswerabilityEvaluator()
    evaluator._client = client

    assessment = await evaluator.assess("Which rules?", _bundle())

    assert assessment.verdict == AnswerabilityVerdict.INSUFFICIENT
    assert assessment.requirements[0].status == AnswerabilityStatus.UNSUPPORTED
    assert assessment.requirements[0].evidence_refs == ()
    assert assessment.citation_errors == (
        "requirement[0] cited unknown context reference 'm999'",
        "requirement[0] supported without 1 valid citation(s)",
    )


async def test_conflict_requires_two_valid_citations_and_surfaces_conflict():
    client = AsyncMock()
    client.create.return_value = _raw(
        _requirement(
            "Current database",
            AnswerabilityStatus.CONFLICTING,
            ["m1", "m2"],
        )
    )
    evaluator = AnswerabilityEvaluator()
    evaluator._client = client

    assessment = await evaluator.assess("Which database is current?", _bundle())

    assert assessment.verdict == AnswerabilityVerdict.CONFLICTING
    assert assessment.recommended_action == AnswerabilityAction.SURFACE_CONFLICT
    assert assessment.should_abstain is True
    assert assessment.conflicts == ("Current database",)
    assert len(assessment.requirements[0].evidence_ids) == 2


async def test_conflict_cannot_cite_one_memory_under_two_refs():
    memory_id = uuid4()
    bundle = MemoryBundle(
        rendered_context="[m1] First rendering.\n[m2] Duplicate rendering.",
        context_references={"m1": memory_id, "m2": memory_id},
        included_count=2,
    )
    client = AsyncMock()
    client.create.return_value = _raw(
        _requirement(
            "Current database",
            AnswerabilityStatus.CONFLICTING,
            ["m1", "m2"],
        )
    )
    evaluator = AnswerabilityEvaluator()
    evaluator._client = client

    assessment = await evaluator.assess("Which database is current?", bundle)

    assert assessment.verdict == AnswerabilityVerdict.INSUFFICIENT
    assert assessment.requirements[0].status == AnswerabilityStatus.UNSUPPORTED
    assert any("more than once" in error for error in assessment.citation_errors)


async def test_provider_failure_is_explicit():
    client = AsyncMock()
    client.create.side_effect = RuntimeError("provider down")
    evaluator = AnswerabilityEvaluator()
    evaluator._client = client

    with pytest.raises(AnswerabilityError, match="provider 'openai'"):
        await evaluator.assess("What is known?", _bundle())


async def test_repeated_outputs_share_evaluation_identity_but_not_result_digest():
    client = AsyncMock()
    client.create.side_effect = [
        _raw(
            _requirement(
                "Background",
                AnswerabilityStatus.SUPPORTED,
                ["m1"],
            )
        ),
        _raw(_requirement("Background", AnswerabilityStatus.UNSUPPORTED)),
    ]
    evaluator = AnswerabilityEvaluator()
    evaluator._client = client
    bundle = _bundle()

    first = await evaluator.assess("What is my background?", bundle)
    second = await evaluator.assess("What is my background?", bundle)

    assert first.evaluation_id == second.evaluation_id
    assert first.assessment_sha256 != second.assessment_sha256


async def test_blank_query_and_answer_are_rejected_before_provider_call():
    evaluator = AnswerabilityEvaluator()

    with pytest.raises(ValueError, match="query must be nonempty"):
        await evaluator.assess("  ", _bundle())
    with pytest.raises(ValueError, match="answer must be nonempty"):
        await evaluator.assess("Question", _bundle(), answer="  ")
