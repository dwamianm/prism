from __future__ import annotations

import copy

import pytest

from benchmarks.integrations import run_jev_retrieval_rerank as runner


def _response(scores: list[float] | None = None) -> dict:
    values = scores or [0.1, 0.9, 0.4, 0.4, 0.2]
    return {
        "model": runner.MODEL,
        "answers": {
            f"candidate_{index}": {"type": "noul", "noul": score}
            for index, score in enumerate(values)
        },
        "usage": {"input_tokens": 250, "output_tokens": 35},
    }


def test_response_validation_and_frozen_stable_order() -> None:
    response = runner._validate_response(_response())
    scores = [
        response["answers"][f"candidate_{index}"]["noul"]
        for index in range(runner.CANDIDATES)
    ]

    assert runner._order(scores) == [1, 2, 3, 4, 0]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(model="jev-latest"), "model differs"),
        (
            lambda value: value["answers"].pop("candidate_4"),
            "answer set is invalid",
        ),
        (
            lambda value: value["answers"]["candidate_0"].update(noul=float("nan")),
            "probability is invalid",
        ),
        (
            lambda value: value["usage"].update(input_tokens=True),
            "usage is invalid",
        ),
    ],
)
def test_response_validation_rejects_unbound_outputs(mutation, message: str) -> None:
    response = copy.deepcopy(_response())
    mutation(response)

    with pytest.raises(ValueError, match=message):
        runner._validate_response(response)


def test_exact_source_metrics_preserve_missing_cases() -> None:
    metrics = runner._metrics([1, 2, 5, None])

    assert metrics == {
        "cases": 4,
        "exact_source_present": 3,
        "hit_at_1": 0.25,
        "recall_at_5": 0.75,
        "mrr": pytest.approx((1 + 0.5 + 0.2) / 4),
        "rank_counts": {"1": 1, "2": 1, "3": 0, "4": 0, "5": 1, "missing": 1},
    }


def test_source_rank_uses_exact_source_memory() -> None:
    item = {
        "memory": "exact source",
        "retrieved": ["similar source", "exact source", "other", "more", "last"],
    }

    assert runner._source_rank(item) == 2
    assert runner._source_rank(item, list(reversed(item["retrieved"]))) == 4
    assert runner._source_rank(item, ["similar source"] * runner.CANDIDATES) is None


def test_order_requires_one_score_per_candidate() -> None:
    with pytest.raises(ValueError, match="score count"):
        runner._order([0.9])
