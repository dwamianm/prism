from benchmarks.diagnostics.memoryarena_value_binding_coverage import audit_rows


def test_binding_coverage_audits_stationary_and_compound_city_fields():
    rows = [{
        "id": 7,
        "base_person": {"daily_plans": [
            {"current_city": "from Seattle to Dallas(Texas)"},
            {"current_city": "from Dallas(Texas) to Houston(Texas)"},
            {"current_city": "Houston(Texas)"},
        ]},
        "answers": [{"daily_plans": [
            {"current_city": "Dallas(Texas)"},
            {"current_city": "from Dallas(Texas) to Seattle"},
        ]}],
    }]
    result = audit_rows(rows, ("Dallas(Texas)", "Houston(Texas)"))
    assert result == {
        "record_count": 2,
        "current_city_field_count": 5,
        "qualified_field_count": 5,
        "compound_qualified_field_count": 3,
        "expected_qualified_occurrence_count": 6,
        "expected_distinct_binding_count": 3,
        "emitted_distinct_binding_count": 3,
        "missing": [],
        "unexpected": [],
        "complete": True,
    }
