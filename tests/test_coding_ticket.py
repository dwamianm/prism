"""Real-ticket tools expose only frozen files and permitted edits."""

import json

import pytest

from benchmarks.coding.ticket108 import TARGET, execute


def test_ticket_tools_read_the_current_private_snapshot_and_bound_edits():
    files = {
        TARGET: "def _render_entry(x):\n    return 'old'\n\ndef neighbor():\n    return 42\n",
        "docs/note.md": "Frozen contract",
    }
    execute(
        {
            "action": "edit",
            "function": "_render_entry",
            "code": "def _render_entry(x):\n    return 'new'",
        },
        files,
        "unused",
    )
    assert "new" in execute({"action": "read", "path": TARGET}, files, "unused")
    assert "neighbor" in files[TARGET]
    assert "new" in execute(
        {"action": "search", "path": "src/", "query": "new"}, files, "unused"
    )
    assert (
        execute({"action": "search", "path": "docs/", "query": "new"}, files, "unused")
        == "No matches"
    )
    for action in (
        {"action": "read", "path": "../secret"},
        {"action": "read", "path": "benchmarks/coding/ticket108_checks.py"},
        {"action": "edit", "function": "neighbor", "code": "def neighbor(): return 1"},
    ):
        with pytest.raises(ValueError):
            execute(action, files, "unused")


def test_file_listing_is_complete_across_pages():
    files = {f"src/{i:03d}.py": "" for i in range(103)}
    first = json.loads(execute({"action": "list", "path": "src/"}, files, "unused"))
    second = json.loads(
        execute(
            {"action": "list", "path": "src/", "offset": first["next_offset"]},
            files,
            "unused",
        )
    )
    assert set(first["paths"] + second["paths"]) == set(files)
    assert second["next_offset"] is None
