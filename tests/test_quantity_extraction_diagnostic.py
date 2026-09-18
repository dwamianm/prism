from benchmarks.diagnostics import quantity_extraction as diagnostic


def _row(*quantities, grounding="speech_act_v11", materialization="speech_act_v12"):
    return {
        "extraction_grounding_policy": grounding,
        "materialization_policy": materialization,
        "evaluated_nodes": [
            {
                "node_type": "fact",
                "epistemic_type": epistemic,
                "metadata": {
                    "predicate": "measured",
                    "object": object_value,
                    "polarity": polarity,
                    "quantity": {
                        "value": value,
                        "unit": unit,
                        "source_text": source_text,
                        "grounding": "object_decimal_v1",
                    },
                },
            }
            for value, unit, source_text, object_value, polarity, epistemic in quantities
        ],
    }


def test_score_case_requires_exact_quantity_target_and_policy():
    case = diagnostic.CASES[0]
    passed = diagnostic.score_case(
        case,
        _row(("500", "$", "$500", "$500 for the animal shelter", "positive", "observed")),
    )
    assert passed["passed"] is True

    missing = diagnostic.score_case(case, _row())
    assert len(missing["missing"]) == 1
    assert missing["passed"] is False

    detached = diagnostic.score_case(
        case,
        _row(("500", "$", "$500", "$500", "positive", "observed")),
    )
    assert detached["field_mismatches"][0]["fields"] == ["object"]
    assert detached["passed"] is False


def test_score_case_rejects_numeric_identifier_quantity():
    case = next(case for case in diagnostic.CASES if case["name"] == "software_version")
    result = diagnostic.score_case(
        case,
        _row(("12.4", "1", "12.4", "CUDA 12.4", "positive", "observed")),
    )
    assert len(result["unexpected"]) == 1
    assert result["passed"] is False


def test_score_requires_complete_fixed_cohort():
    rows = {
        case["name"]: _row()
        for case in diagnostic.CASES
        if not case["expected"]
    }
    result = diagnostic.score(rows)
    assert result["metrics"]["completed_cases"] < len(diagnostic.CASES)
    assert result["metrics"]["passed"] is False
