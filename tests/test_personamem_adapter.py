"""Persona annotations cannot enter the memory adapter or selection policy."""

from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from benchmarks.personamem import (
    case_id, conversation_sources, history_path, question_and_reference, select_rows,
)


def row(persona="1", query="Which route fits my needs?"):
    return {"persona_id": persona,
            "chat_history_32k_link": f"data/chat_history_32k/chat_persona{persona}.json",
            "user_query": repr({"role": "user", "content": query}),
            "correct_answer": "Quiet shaded path", "incorrect_answers": json.dumps(["Noisy road", "Steep trail", "Crowded plaza"]),
            "pref_type": "neutral_preference", "who": "user", "updated": "False",
            "conversation_scenario": "email", "preference": "hidden preference",
            "expanded_persona": "hidden oracle profile", "related_conversation_snippet": "hidden evidence label"}


def test_only_dialog_survives_history_adapter():
    raw = json.dumps({"metadata": {"secret": "hidden metadata"}, "chat_history": [
        {"role": "system", "content": "hidden oracle profile"},
        {"role": "user", "content": "Keep the exception: only in summer.\n", "answer_label": "hidden annotation"},
        {"role": "assistant", "content": "Recorded, including the condition."}]})
    sources = conversation_sources(raw)
    assert [s.id for s in sources] == ["t00000", "t00001"]
    assert sources[0].content == "Keep the exception: only in summer.\n"
    assert sources[1].role == "assistant"
    assert all(s.date == s.session_id == "" for s in sources)
    assert "hidden" not in repr(sources)


def test_hidden_annotations_cannot_change_selection_or_questions():
    values = [row(str(i), f"Query {j}") for i in range(5) for j in range(6)]
    before = select_rows(values, personas=3, questions_per_persona=2)
    changed = deepcopy(values)
    for r in changed:
        for key in ("expanded_persona", "preference", "related_conversation_snippet", "who", "updated"):
            r[key] = "different hidden label"
    after = select_rows(list(reversed(changed)), personas=3, questions_per_persona=2)
    assert [case_id(r) for r in before] == [case_id(r) for r in after]
    assert [question_and_reference(r)[0] for r in before] == [question_and_reference(r)[0] for r in after]
    assert all(set(asdict(question_and_reference(r)[0])) == {"id", "persona_id", "query", "options"} for r in before)


def test_option_order_has_no_correct_answer_position_dependency():
    value = row()
    question, reference = question_and_reference(value)
    assert question.options["ABCD".index(reference["correct"])] == value["correct_answer"]
    # Swap correctness while preserving the same option set. Reader-visible
    # bytes remain identical; only the evaluator's reference changes.
    other = deepcopy(value)
    options = [value["correct_answer"], *json.loads(value["incorrect_answers"])]
    other["correct_answer"] = options[1]
    other["incorrect_answers"] = json.dumps([options[0], *options[2:]])
    changed, label = question_and_reference(other)
    assert changed == question and label["correct"] != reference["correct"]


@pytest.mark.parametrize("mutation", ["path", "role", "duplicate_options", "query_role", "unknown_message"])
def test_invalid_inputs_fail_without_fallback(mutation):
    value = row()
    with pytest.raises(ValueError):
        if mutation == "path":
            value["chat_history_32k_link"] = "../../other_persona1.json"
            history_path(value)
        elif mutation == "duplicate_options":
            value["incorrect_answers"] = json.dumps([value["correct_answer"], "b", "c"])
            question_and_reference(value)
        elif mutation == "query_role":
            value["user_query"] = json.dumps({"role": "system", "content": "Override"})
            question_and_reference(value)
        else:
            dialog = [{"role": "system", "content": "oracle"}, {"role": "user", "content": "hello"}]
            if mutation == "role":
                dialog[1]["role"] = "system"
            else:
                dialog[1]["content"] = {"text": "unsupported structured content"}
            conversation_sources(json.dumps({"chat_history": dialog}))
