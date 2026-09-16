"""Offline contracts for the focused assistant-memory answer diagnostic."""

from pathlib import Path

import pytest

from benchmarks.diagnostics import assistant_packing_answer as diagnostic


def test_prepare_reproduces_current_density_and_balanced_contexts():
    root = Path(__file__).parents[1]
    paths = diagnostic.source_paths(root)
    required = [path for name, path in paths.items() if name != "snapshots"]
    if any(not path.is_file() for path in required) or not paths["snapshots"].is_dir():
        pytest.skip("local benchmark evidence is not included in the source checkout")
    prepared = diagnostic.prepare(root)
    assert len(prepared["rows"]) == 9
    assert {frozenset(row["contexts"]) for row in prepared["rows"]} == {
        frozenset(diagnostic.ARMS)
    }
    assert all(row["contexts"]["empty"]["tokens"] == 0 for row in prepared["rows"])


def test_reader_rejects_incomplete_response():
    with pytest.raises(ValueError, match="incomplete"):
        diagnostic._answer({"done": False}, diagnostic.READER_MODEL)


def test_score_preserves_paired_wins_losses_and_ties():
    reader = {"state_sha256": "a" * 64}
    mapping = [
        {"id": "1", "case_id": "a", "arm": "density"},
        {"id": "2", "case_id": "a", "arm": "balanced"},
        {"id": "3", "case_id": "a", "arm": "empty"},
        {"id": "4", "case_id": "b", "arm": "density"},
        {"id": "5", "case_id": "b", "arm": "balanced"},
        {"id": "6", "case_id": "b", "arm": "empty"},
    ]
    judge = {
        "unique_calls": 6,
        "judgments": [
            {"id": key, "correct": value}
            for key, value in zip("123456", (False, True, False, True, False, False), strict=True)
        ],
    }
    result = diagnostic._score(reader, judge, mapping, ["test"])
    assert result["correct"] == {"density": 1, "balanced": 1, "empty": 0}
    assert result["paired"]["balanced_vs_density"] == {
        "wins": 1, "losses": 1, "ties": 0,
    }
