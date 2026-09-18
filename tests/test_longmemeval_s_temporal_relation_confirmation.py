from benchmarks.diagnostics import longmemeval_s_temporal_relation_confirmation as trial


def test_confirmation_protocol_freezes_disjoint_stages_and_gate() -> None:
    protocol = trial._protocol()

    assert protocol["stages"] == ["resolve", "gate", "prepare", "reader", "judge"]
    assert "judge is the first stage" in protocol["answer_isolation"]
    assert protocol["jev"]["minimum_operand_probability"] == 0.85
    assert protocol["jev"]["threshold_fixed_from_development"] is True
    assert protocol["gate"]["accepted_hints"] == ">= 10"
    assert protocol["gate"]["accepted_hint_losses"] == 0
    assert protocol["gate"]["accepted_hint_wins"] == ">= 3"


def test_confirmation_source_type_rule_preserves_abstention_controls() -> None:
    assert trial._source_question_type("ordinary", "temporal-reasoning") == (
        "temporal-reasoning"
    )
    assert trial._source_question_type("example_abs", "temporal-reasoning") == (
        "abstention"
    )


def test_self_hash_rejects_mutation() -> None:
    value = {"kind": "example", "count": 2}
    value["result_sha256"] = trial._sha256(trial.paired.canonical(value))

    assert trial._validate_self_hash(value) is True
    value["count"] = 3
    assert trial._validate_self_hash(value) is False
