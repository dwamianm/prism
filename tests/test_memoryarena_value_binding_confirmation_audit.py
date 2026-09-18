"""Matched typed-value confirmation gates remain preregistered and causal."""

from benchmarks.diagnostics.memoryarena_value_binding_confirmation_audit import (
    compare_confirmation_audits,
)


def _audit(passed: int, *, executed: int = 0):
    return {
        "coverage": {"complete": True},
        "qualified_changed_values": {"passed": passed, "total": 100},
        "tool_boundary_resolution": {
            "trace_coverage_complete": True,
            "executed_qualified_argument_count": executed,
        },
    }


def test_confirmation_comparison_requires_broad_and_targeted_gates():
    result = compare_confirmation_audits(
        control=_audit(70),
        candidate=_audit(77),
        paired_result={"gates": {"passed": True}},
        decision_rules={
            "maximum_executed_qualified_arguments": 0,
            "minimum_candidate_minus_control_exact_qualified_values": 5,
        },
    )

    assert result["comparison"][
        "candidate_minus_control_exact_qualified_values"
    ] == 7
    assert result["gates"]["passed"] is True


def test_confirmation_comparison_rejects_unsafe_or_small_gain():
    result = compare_confirmation_audits(
        control=_audit(70),
        candidate=_audit(73, executed=1),
        paired_result={"gates": {"passed": True}},
        decision_rules={
            "maximum_executed_qualified_arguments": 0,
            "minimum_candidate_minus_control_exact_qualified_values": 5,
        },
    )

    assert result["gates"]["candidate_execution_safe"] is False
    assert result["gates"]["candidate_output_gain"] is False
    assert result["gates"]["passed"] is False
