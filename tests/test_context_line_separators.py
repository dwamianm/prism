"""Issue 108: record boundaries survive arbitrary stored Unicode text."""

from benchmarks.coding.ticket108_checks import check_budgets, check_records


def test_auditable_and_compact_records_roundtrip_without_extra_lines():
    check_records()


def test_escaped_context_obeys_exact_packing_budget():
    check_budgets()
