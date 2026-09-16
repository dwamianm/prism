"""Counterexample reader comparisons keep judgments outside model inputs."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from benchmarks.diagnostics.formatter_fidelity import compare, render_cases


async def test_expectations_remain_evaluator_only():
    before = render_cases()
    for case in before["cases"]:
        case["expected"] = "private-evaluator-sentinel"
    reader = AsyncMock(return_value="Answer")
    result = await compare(before, deepcopy(before), reader)
    assert reader.await_count == len(before["cases"]) * 2
    assert "private-evaluator-sentinel" not in repr(reader.await_args_list)
    assert all(case["expected"] == "private-evaluator-sentinel" for case in result["cases"])
    assert result["accuracy"] is None and result["judge_status"] == "not_run"


@pytest.mark.parametrize("field", ["fixture_sha256", "protocol", "question", "expected", "duplicate_id"])
async def test_mismatched_protocol_or_cases_do_not_call_reader(field):
    before = render_cases()
    after = deepcopy(before)
    if field in ("fixture_sha256", "protocol"):
        after[field] = "changed"
    elif field == "duplicate_id":
        after["cases"][1]["id"] = after["cases"][0]["id"]
    else:
        after["cases"][-1][field] = "changed"
    reader = AsyncMock()
    with pytest.raises(ValueError):
        await compare(before, after, reader)
    reader.assert_not_awaited()


async def test_reader_failure_cannot_yield_a_successful_partial_report():
    report = render_cases()
    reader = AsyncMock(side_effect=RuntimeError("Provider failure"))
    with pytest.raises(RuntimeError):
        await compare(report, report, reader)
