from benchmarks.diagnostics import longmemeval_s_temporal_relation_answer as trial


def test_protocol_freezes_semantic_gate_and_bounded_packing() -> None:
    protocol = trial._protocol()

    assert protocol["relation_policy"]["jev_minimum_operand_probability"] == 0.85
    assert protocol["relation_policy"]["accepted_hints"] == 9
    assert protocol["packing"]["same_token_budget"] is True
    assert protocol["packing"]["cited_records_required"] is True
    assert protocol["gate"]["accepted_hint_losses"] == 0
    assert protocol["gate"]["accepted_hint_wins"] == ">= 2"


def test_hint_metrics_counts_only_accepted_questions() -> None:
    prepared = {
        "audits": [
            {"question_id": "a", "hint_accepted": True},
            {"question_id": "b", "hint_accepted": True},
            {"question_id": "c", "hint_accepted": False},
        ]
    }
    cases = [
        {"id": f"{question_id}:{arm}", "question_id": question_id, "arm": arm}
        for question_id in ("a", "b", "c")
        for arm in trial.ARMS
    ]
    judgments = {
        "judgments": [
            {"id": "a:auditable", "correct": False},
            {"id": "a:temporal_relation", "correct": True},
            {"id": "b:auditable", "correct": True},
            {"id": "b:temporal_relation", "correct": True},
            {"id": "c:auditable", "correct": False},
            {"id": "c:temporal_relation", "correct": False},
        ]
    }

    metrics = trial._hint_metrics(prepared, cases, judgments)

    assert metrics == {
        "questions": 2,
        "logical_judgments": 4,
        "control_correct": 1,
        "candidate_correct": 2,
        "paired_wins": 1,
        "paired_losses": 0,
        "paired_ties": 1,
    }
