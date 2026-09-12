"""A mostly passing simulation suite must never report process success."""

import json
from unittest.mock import AsyncMock

import pytest

from scripts import run_simulations as script
from simulations.harness import CheckpointResult, SimCheckpoint, SimulationReport


def report(passed):
    checkpoint = SimCheckpoint(1, "query", [], [], "test checkpoint")
    return SimulationReport("fixture", {}, [
        CheckpointResult(checkpoint, status, [], [], [], []) for status in passed
    ], sum(passed) / len(passed) if passed else 0, 0, 0)


@pytest.mark.parametrize("outcomes,code", [([True] * 74, 0), ([True] * 71 + [False] * 3, 1), ([], 1)])
async def test_exit_status_requires_all_checks(monkeypatch, tmp_path, outcomes, code):
    monkeypatch.setitem(script.SCENARIOS, "fixture", object())
    monkeypatch.setattr(script.SimulationRunner, "run", AsyncMock(return_value=report(outcomes)))
    output = tmp_path / "results.json"
    assert await script.run_scenarios(["fixture"], output=output) == code
    result = json.loads(output.read_text())
    assert result["successful"] == (code == 0)
    assert result["passed"] == sum(outcomes) and result["total"] == len(outcomes)


async def test_scenario_error_preserves_remaining_results(monkeypatch, tmp_path):
    monkeypatch.setattr(script, "SCENARIOS", {"broken": object(), "good": object()})
    monkeypatch.setattr(script.SimulationRunner, "run", AsyncMock(side_effect=[RuntimeError("private"), report([True])]))
    output = tmp_path / "results.json"
    assert await script.run_scenarios(["broken", "good"], output=output) == 1
    result = json.loads(output.read_text())
    assert result["passed"] == 1 and not result["complete"]
    assert result["errors"] == {"broken": "RuntimeError"}
    assert "private" not in output.read_text()
