"""Guard the experiment against source leakage and misleading paired results."""

import pytest

from benchmarks.coding.run import execute_action, replace_function, summarize


def test_edit_is_restricted_to_one_function_and_preserves_neighbors():
    original = "import json\n\ndef target(x):\n    return x\n\ndef neighbor():\n    return 42\n"
    changed = replace_function(original, "target", "def target(x):\n    return x + 1\n")
    assert "def neighbor():\n    return 42" in changed
    assert changed.startswith("import json")
    for invalid in [
        "import os\ndef target(x): return x",
        "def other(x): return x",
        "@evil\ndef target(x): return x",
    ]:
        with pytest.raises(ValueError):
            replace_function(original, "target", invalid)


def test_reads_and_searches_use_the_mutated_snapshot_only():
    files = {
        "source.py": "def target():\n    return 'broken'\n",
        "doc.md": "Known contract\n",
    }
    task = {"path": "source.py", "function": "target"}
    assert "broken" in execute_action(
        {"action": "read", "path": "source.py"}, files, task, "unused"
    )
    assert "source.py:2" in execute_action(
        {"action": "search", "query": "broken"}, files, task, "unused"
    )
    for path in ["../secret", "/etc/passwd", "tests/hidden.py"]:
        with pytest.raises(ValueError):
            execute_action({"action": "read", "path": path}, files, task, "unused")


def test_summary_preserves_failures_and_does_not_pair_incomplete_tasks():
    common = {
        "seconds": 2,
        "input_tokens": 10,
        "output_tokens": 3,
        "repeat": 0,
        "status": "complete",
    }
    rows = [
        {**common, "task": "a", "arm": "control", "passed": False},
        {**common, "task": "a", "arm": "memory", "passed": True},
        {**common, "task": "b", "arm": "control", "passed": True},
        {
            **common,
            "task": "b",
            "arm": "memory",
            "passed": False,
            "status": "provider_failure",
        },
        {**common, "task": "unfinished", "arm": "memory", "passed": False},
    ]
    result = summarize(rows)
    assert result["paired_outcomes"] == {"gain": 1, "loss": 1}
    assert result["memory"]["provider_failures"] == 1
    assert result["memory"]["runs"] == 3
    assert result["memory"]["input_tokens"] == 30
