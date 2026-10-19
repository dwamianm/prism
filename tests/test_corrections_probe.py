"""The corrections probe: its committed conversation and the explicit supersede() path."""
import json

import pytest

from benchmarks.diagnostics import corrections_probe
from benchmarks.diagnostics.chat_probe import load_conversation
from benchmarks.diagnostics.corrections_probe import DEFAULT_CONVERSATION, case_report, load_cases


@pytest.fixture
def mock_embeddings(monkeypatch):
    from tests.test_durable_ingestion import MockEmbeddingProvider

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())


def test_the_committed_conversation_loads_with_three_cases_and_a_control():
    conversation = load_conversation(DEFAULT_CONVERSATION)
    cases = load_cases(conversation)
    assert [(case["id"], case["kind"]) for case in cases] == [
        ("move", "changed mind"), ("birthday", "wrong fact fixed later"), ("coffee", "conflict in one session"),
        ("job", "control")]
    turns = [turn["text"] for session in conversation["sessions"] for turn in session["turns"]]
    # The correcting message gives only the new date, so the old one cannot reach a context through it.
    correction = next(text for text in turns if "actually March 13" in text)
    assert "March 3." not in correction and "March 3 " not in correction


def test_a_case_must_name_a_probe_and_say_what_changed():
    conversation = load_conversation(DEFAULT_CONVERSATION)
    broken = {**conversation, "key": {**conversation["key"], "corrections": [
        {"id": "x", "kind": "changed mind", "label": "x", "old": ["a"], "new": ["b"], "probe": "missing"}]}}
    with pytest.raises(ValueError, match="unknown probe"):
        load_cases(broken)
    broken["key"]["corrections"] = [{"id": "x", "kind": "changed mind", "label": "x", "old": ["a"],
                                     "probe": "move-city"}]
    with pytest.raises(ValueError, match="old and new"):
        load_cases(broken)


def test_case_report_separates_source_messages_from_retired_claims():
    case = {"id": "move", "kind": "changed mind", "label": "move", "probe": "move-city",
            "old": ["Austin"], "new": ["Denver"]}
    nodes = [
        {"id": "m1", "type": "note", "state": "stable", "content": "I'm moving to Austin.", "metadata": {}},
        {"id": "c1", "type": "fact", "state": "superseded", "content": "Riley moves to Austin.",
         "metadata": {"subject": "Riley", "predicate": "moves_to", "object": "Austin"}, "superseded_by": "c2"},
        {"id": "c2", "type": "fact", "state": "stable", "content": "Riley moves to Denver.",
         "metadata": {"subject": "Riley", "predicate": "moves_to", "object": "Denver"}},
        {"id": "e1", "type": "entity", "state": "stable", "content": "Austin", "metadata": {}},
    ]
    edges = [{"source": "c2", "target": "c1", "type": "supersedes"}]
    context = 'header\n- "Riley moves to Denver."\n- "I\'m moving to Austin."\n- "Unrelated."'
    report = case_report(case, nodes, edges, context)
    assert report["facts"] == {
        "new_value_active": True, "active_records_with_only_the_old_value": 1,
        "extracted_claims_with_only_the_old_value": 1, "of_them_superseded": 1,
        "context_lines_with_only_the_old_value": 1, "context_lines_with_the_new_value": 1,
        "first_context_line_shows": "new", "old_only_lines_in_the_context": 1,
        "old_value_shown_before_the_new_value": False}
    assert [record["id"] for record in report["records"]] == ["m1", "c1", "c2"]
    assert report["records"][1]["replacement"] == "Riley moves to Denver."
    assert report["edges"] == [{"type": "supersedes", "from": "Riley moves to Denver.", "to": "Riley moves to Austin."}]
    assert report["context_records"] == 3


def _lines_case(*lines):
    """The facts for a case whose context holds exactly these record lines."""
    case = {"id": "move", "kind": "changed mind", "label": "move", "probe": "move-city",
            "old": ["Austin"], "new": ["Denver"]}
    return case_report(case, [], [], "header\n" + "\n".join(f'- {line}' for line in lines))["facts"]


def test_an_old_only_line_shown_before_the_new_value_is_counted():
    facts = _lines_case('"Unrelated."', '[2026-03-02 18:04] "I moved to Austin."', '[2026-04-14 19:30] "Now Denver."')
    assert facts["old_only_lines_in_the_context"] == 1
    assert facts["old_value_shown_before_the_new_value"] is True
    facts = _lines_case('"Now Denver."', '"I moved to Austin."')
    assert (facts["old_only_lines_in_the_context"], facts["old_value_shown_before_the_new_value"]) == (1, False)


def test_an_old_only_line_with_no_new_value_in_the_context_is_shown_before_it():
    facts = _lines_case('"I moved to Austin."')
    assert (facts["old_only_lines_in_the_context"], facts["old_value_shown_before_the_new_value"]) == (1, True)
    facts = _lines_case('"Unrelated."')
    assert (facts["old_only_lines_in_the_context"], facts["old_value_shown_before_the_new_value"]) == (0, False)


def test_a_line_marked_as_replaced_is_not_an_old_only_line():
    facts = _lines_case('[2026-03-02 18:04] [superseded] "I moved to Austin."', '[replaced by a correction] "Austin."',
                        '[2026-04-14 19:30] "Now Denver."')
    assert facts["context_lines_with_only_the_old_value"] == 2
    assert facts["old_only_lines_in_the_context"] == 0
    assert facts["old_value_shown_before_the_new_value"] is False
    # Another tag does not mark it, and neither does the word inside the text.
    facts = _lines_case('[inferred] "I moved to Austin."', '"The [superseded] Austin plan."')
    assert facts["old_only_lines_in_the_context"] == 2


def test_the_report_names_the_two_measures_plainly():
    report = {"conversation": "x", "reference_time": "t", "paths": {"explicit": {"cases": [{
        "label": "move", "kind": "changed mind", "question": "q", "records": [], "edges": [], "context_lines": [],
        "context_records": 0, "tokens_used": 1, "token_budget": 2,
        "facts": {"old_only_lines_in_the_context": 0, "old_value_shown_before_the_new_value": False}}]}}}
    text = corrections_probe.report_markdown(report)
    assert "| old-only lines in the context | 0 |" in text
    assert "| old value shown before the new value | False |" in text


async def test_the_explicit_path_retires_both_old_values_and_keeps_the_conflict(tmp_path, mock_embeddings):
    output = tmp_path / "out"
    report = await corrections_probe.run(output, root=tmp_path / "builds", skip_ingest=True)
    assert list(report["paths"]) == ["explicit"]
    cases = {case["id"]: case for case in report["paths"]["explicit"]["cases"]}
    for case_id in ("move", "birthday"):
        facts = cases[case_id]["facts"]
        assert facts["new_value_active"] is True
        assert facts["active_records_with_only_the_old_value"] == 0
        assert facts["context_lines_with_only_the_old_value"] == 0
        assert facts["old_only_lines_in_the_context"] == 0
        assert facts["old_value_shown_before_the_new_value"] is False
        assert [edge["type"] for edge in cases[case_id]["edges"]] == ["supersedes"]
        assert "superseded" in {record["state"] for record in cases[case_id]["records"]}
    assert cases["coffee"]["facts"]["active_records_per_value"] == {"black": 1, "oat milk": 1}
    assert cases["job"]["facts"]["active_records_per_value"] == {"nurse": 1}
    assert (output / "contexts" / "explicit" / "move-city.txt").read_text().startswith("Q: Where is Riley moving?")
    assert json.loads((output / "report.json").read_text())["paths"]["explicit"]["cases"][0]["id"] == "move"
    assert (output / "report.md").read_text().startswith("# Corrections probe: corrections-v1")
    with pytest.raises(ValueError, match="--replace"):
        await corrections_probe.run(output, root=tmp_path / "builds", skip_ingest=True)
