from benchmarks.diagnostics.longmemeval_s_temporal_relation_live_preflight import (
    _case_checks,
    _case_passes,
    _summary,
)


def _observed(**updates):
    value = {
        "status": "accepted",
        "operation": "elapsed_since_question",
        "value": "28 days",
        "evidence_ids": ["evidence-a"],
        "control_context_sha256": "a" * 64,
        "result_context_sha256": "b" * 64,
        "receipt_matches": True,
        "receipt_persisted": True,
        "confirmation_protocol_aligned": True,
        "temporal_receipt": {"status": "accepted"},
    }
    value.update(updates)
    return value


def test_known_accepted_requires_frozen_core_but_tracks_wording_drift() -> None:
    specification = {
        "role": "known_accepted",
        "expected": {
            "routed": True,
            "operation": "elapsed_since_question",
            "value": "28 days",
            "evidence_ids": ["evidence-a"],
            "result_context_sha256": "c" * 64,
        },
    }

    checks = _case_checks(
        specification=specification,
        observed=_observed(),
        baseline_context_sha256="a" * 64,
    )

    assert checks["known_accepted_core_matches"] is True
    assert checks["frozen_result_context_matches"] is False
    assert _case_passes("known_accepted", checks) is True


def test_known_rejection_cannot_change_context_or_become_accepted() -> None:
    specification = {
        "role": "known_rejected",
        "expected": {
            "routed": True,
            "status": "gate_rejected",
        },
    }

    checks = _case_checks(
        specification=specification,
        observed=_observed(),
        baseline_context_sha256="a" * 64,
    )

    assert checks["known_rejection_remains_unchanged"] is False
    assert _case_passes("known_rejected", checks) is False


def test_no_call_control_requires_absent_temporal_receipt_and_exact_context() -> None:
    specification = {
        "role": "ordinary_no_call",
        "expected": {"routed": False},
    }
    observed = _observed(
        status="not_routed",
        operation=None,
        value=None,
        evidence_ids=[],
        result_context_sha256="a" * 64,
        confirmation_protocol_aligned=None,
        temporal_receipt=None,
    )

    checks = _case_checks(
        specification=specification,
        observed=observed,
        baseline_context_sha256="a" * 64,
    )

    assert checks["no_call_is_exact"] is True
    assert _case_passes("ordinary_no_call", checks) is True


def test_live_summary_keeps_provider_usage_and_manual_review_separate() -> None:
    row = {
        "status": "accepted",
        "resolver": {
            "input_tokens": 100,
            "output_tokens": 20,
            "elapsed_ms": 1000,
        },
        "gate": {
            "input_tokens": 30,
            "output_tokens": 2,
            "elapsed_ms": 200,
        },
        "retrieval_seconds": 1.5,
        "manual_review_required": True,
        "automatic_case_passed": True,
    }

    summary = _summary([row], [])

    assert summary["cases"] == 1
    assert summary["manual_reviews_required"] == 1
    assert summary["automatic_cases_passed"] == 1
    assert summary["usage"] == {
        "resolver_input_tokens": 100,
        "resolver_output_tokens": 20,
        "gate_input_tokens": 30,
        "gate_output_tokens": 2,
    }
