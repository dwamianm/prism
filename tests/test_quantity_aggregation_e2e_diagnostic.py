from benchmarks.diagnostics import quantity_aggregation_e2e as diagnostic


def _row(case):
    nodes = []
    for index, (value, unit, source_text, polarity, epistemic_types) in enumerate(
        case["expected"]
    ):
        nodes.append(
            {
                "id": f"00000000-0000-0000-0000-{index:012d}",
                "node_type": "fact",
                "epistemic_type": epistemic_types[0],
                "metadata": {
                    "predicate": "raised",
                    "polarity": polarity,
                    "quantity": {
                        "value": value,
                        "unit": unit,
                        "source_text": source_text,
                        "grounding": "object_decimal_v1",
                    },
                },
            }
        )
    return {
        "grounding_policy": "speech_act_v11",
        "materialization_policy": "speech_act_v12",
        "nodes": nodes,
    }


def _aggregation():
    return {
        "matched_quantity_records": 4,
        "distinct_count": 1,
        "groups": [
            {
                "normalized_values": {"unit": "$"},
                "value_count": 4,
                "total": "3750",
                "minimum": "250",
                "maximum": "2000",
                "evidence_count": 4,
                "samples": [
                    {"value": value}
                    for value in ("1000", "250", "500", "2000")
                ],
                "samples_truncated": False,
            }
        ],
        "stored_set_exhaustive": True,
        "source_extraction_coverage": "unknown",
        "semantic_equivalence": "normalized_exact_and_predicate_prefix",
        "real_world_coverage": "unknown",
        "unit_conversion": "none",
        "consistency": "complete_for_unchanged_store",
        "exclusions": {
            "selector_mismatch": 2,
            "condition_filtered": 1,
            "unit_mismatch": 1,
        },
    }


def test_score_accepts_complete_exact_product_path():
    rows = {case["name"]: _row(case) for case in diagnostic.CASES}

    result = diagnostic.score(rows, _aggregation())

    assert result["metrics"]["passed"] is True
    assert all(result["aggregation_checks"].values())


def test_score_rejects_cross_owner_leak_and_approximate_quantity():
    rows = {case["name"]: _row(case) for case in diagnostic.CASES}
    approximate = next(
        case
        for case in diagnostic.CASES
        if case["name"] == "approximate_fundraising"
    )
    rows[approximate["name"]]["nodes"].append(
        {
            "id": "00000000-0000-0000-0000-999999999999",
            "node_type": "fact",
            "epistemic_type": "observed",
            "metadata": {
                "predicate": "raised",
                "polarity": "positive",
                "quantity": {
                    "value": "600",
                    "unit": "$",
                    "source_text": "$600",
                },
            },
        }
    )
    aggregation = _aggregation()
    aggregation["groups"][0]["total"] = "13750"

    result = diagnostic.score(rows, aggregation)

    assert result["metrics"]["passed"] is False
    failed_case = next(
        case
        for case in result["cases"]
        if case["name"] == "approximate_fundraising"
    )
    assert len(failed_case["unexpected"]) == 1
    assert result["aggregation_checks"]["total"] is False
