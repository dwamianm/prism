from __future__ import annotations

from benchmarks.diagnostics import paired_context_eval as paired


def test_reader_jobs_counterbalance_and_bind_both_arms() -> None:
    contexts = {
        arm: {
            "context": arm,
            "sha256": paired.digest(arm.encode()),
        }
        for arm in ("control", "candidate")
    }
    prepared = {
        "rows": [
            {
                "question_id": "q1",
                "question": "Question?",
                "question_date": "2023/01/01 (Sun) 00:00",
                "contexts": contexts,
            }
        ]
    }
    jobs = paired.reader_jobs(
        prepared,
        ("control", "candidate"),
        model="reader:cloud",
        options={"num_ctx": 65536, "num_predict": 100},
        system_prompt="answer",
    )

    assert {(row["question_id"], arm) for row, arm, _body, _key in jobs} == {
        ("q1", "control"),
        ("q1", "candidate"),
    }
    assert all(body["think"] is False for _row, _arm, body, _key in jobs)
    assert len({key for _row, _arm, _body, key in jobs}) == 2


def test_reader_jobs_cover_three_arms_once_per_question() -> None:
    arms = ("control", "same", "fill")
    prepared = {
        "rows": [
            {
                "question_id": "q1",
                "question": "Question?",
                "question_date": "2023/01/01 (Sun) 00:00",
                "contexts": {
                    arm: {"context": arm, "sha256": paired.digest(arm.encode())}
                    for arm in arms
                },
            }
        ]
    }

    jobs = paired.reader_jobs(
        prepared,
        arms,
        model="reader:cloud",
        options={"num_ctx": 65536, "num_predict": 100},
        system_prompt="answer",
    )

    assert jobs == paired.reader_jobs(
        prepared,
        arms,
        model="reader:cloud",
        options={"num_ctx": 65536, "num_predict": 100},
        system_prompt="answer",
    )
    assert {arm for _row, arm, _body, _key in jobs} == set(arms)
    assert len(jobs) == 3


def test_paired_metrics_report_direction_and_category() -> None:
    cases = [
        {
            "id": "a:old",
            "question_id": "a",
            "arm": "old",
            "category": "temporal-reasoning",
        },
        {
            "id": "a:new",
            "question_id": "a",
            "arm": "new",
            "category": "temporal-reasoning",
        },
        {
            "id": "b:old",
            "question_id": "b",
            "arm": "old",
            "category": "temporal-reasoning",
        },
        {
            "id": "b:new",
            "question_id": "b",
            "arm": "new",
            "category": "temporal-reasoning",
        },
    ]
    judgments = {
        "complete": True,
        "prior_failed_attempts": [],
        "judgments": [
            {"id": "a:old", "correct": False},
            {"id": "a:new", "correct": True},
            {"id": "b:old", "correct": True},
            {"id": "b:new", "correct": True},
        ],
    }

    result = paired.paired_metrics(
        cases, judgments, control_arm="old", candidate_arm="new"
    )

    assert result["overall"] == {
        "questions": 2,
        "control_correct": 1,
        "candidate_correct": 2,
        "paired_wins": 1,
        "paired_losses": 0,
        "paired_ties": 1,
        "accuracy_delta": 0.5,
    }
    assert result["categories"]["temporal-reasoning"] == result["overall"]


def test_paired_metrics_select_two_arms_from_three_arm_judgments() -> None:
    cases = [
        {
            "id": f"q:{arm}",
            "question_id": "q",
            "arm": arm,
            "category": "multi-session",
        }
        for arm in ("control", "same", "fill")
    ]
    judgments = {
        "complete": True,
        "prior_failed_attempts": [],
        "judgments": [
            {"id": "q:control", "correct": False},
            {"id": "q:same", "correct": False},
            {"id": "q:fill", "correct": True},
        ],
    }

    result = paired.paired_metrics(
        cases, judgments, control_arm="same", candidate_arm="fill"
    )

    assert result["overall"]["paired_wins"] == 1
    assert result["overall"]["candidate_correct"] == 1


def test_judge_cases_reject_missing_arm() -> None:
    predictions = {
        "rows": [
            {
                "question_id": "q1",
                "arm": "control",
                "hypothesis": "answer",
            }
        ]
    }
    references = [
        {
            "question_id": "q1",
            "question": "Question?",
            "question_type": "temporal-reasoning",
            "answer": "answer",
        }
    ]

    try:
        paired.judge_cases(predictions, references, ("control", "candidate"))
    except ValueError as exc:
        assert "coverage" in str(exc)
    else:
        raise AssertionError("missing arm was accepted")
