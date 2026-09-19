from benchmarks.diagnostics.memoryarena_structured_presentation import audit_rows


def test_structured_presentation_audit_restores_only_declared_components():
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
    result = audit_rows(rows)
    assert result == {
        "record_count": 2,
        "target_count": 6,
        "replacement_count": 6,
        "source_argument_pointer_count": 6,
        "failure_count": 0,
        "failures": [],
        "complete": True,
    }
