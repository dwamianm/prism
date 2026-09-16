"""No model calls are needed to verify the comparative evaluator boundary."""

from copy import deepcopy

import pytest

from benchmarks.evidence import longmemeval_sources
from benchmarks.mem0_raw_eval import compare, raw_question, validate_rows


class RawMemory:
    def __init__(self):
        self.rows = []
        self.calls = []

    def add(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        message = messages[0]
        row = {
            "id": str(len(self.rows)),
            "memory": message["content"],
            "role": message["role"],
            "user_id": kwargs["user_id"],
            "metadata": kwargs["metadata"],
        }
        self.rows.append(row)
        return {"results": [row]}

    def get_all(self, **kwargs):
        return {"results": self.rows}

    def search(self, query, **kwargs):
        assert query == "Which source?"
        assert kwargs == {
            "filters": {"user_id": "evaluation"},
            "top_k": 10,
            "threshold": 0,
            "rerank": False,
        }
        return {"results": list(reversed(self.rows))}


def sources():
    return longmemeval_sources(
        {
            "answer": "HIDDEN ANSWER",
            "question_id": "ANSWER_ID",
            "haystack_session_ids": ["ANSWER_SESSION"],
            "haystack_dates": ["2024/01/01 (Mon) 00:00"],
            "haystack_sessions": [
                [
                    {
                        "role": "user",
                        "content": "A complete conditional source.",
                        "has_answer": True,
                    },
                    {"role": "assistant", "content": ""},
                ]
            ],
        }
    )[0]


def test_adapter_preserves_blank_sources_and_never_sends_annotations():
    memory = RawMemory()
    result = raw_question(memory, sources(), "Which source?", k=10)
    assert result["ranked_source_ids"] == ["s0:t1", "s0:t0"]
    assert len(memory.calls) == 2
    for _, kwargs in memory.calls:
        assert kwargs["infer"] is False
        assert kwargs["metadata"]["source_session"] == "session-0"
        assert kwargs["metadata"]["created_at"] == "2024-01-01T00:00:00+00:00"
        assert set(kwargs["metadata"]) == {
            "source_turn",
            "source_session",
            "source_date",
            "created_at",
        }
    assert "HIDDEN" not in repr(memory.calls) and "ANSWER" not in repr(memory.calls)


@pytest.mark.parametrize(
    "corruption", ["content", "owner", "id", "source", "duplicate", "role", "date"]
)
def test_returned_source_corruption_is_rejected(corruption):
    memory = RawMemory()
    turns = sources()
    raw_question(memory, turns, "Which source?", k=10)
    rows = deepcopy(memory.rows)
    if corruption == "duplicate":
        rows.append(rows[0])
    elif corruption in {"content", "owner", "id", "role"}:
        key = {"content": "memory", "owner": "user_id", "id": "id", "role": "role"}[
            corruption
        ]
        rows[0][key] = "corrupted"
    else:
        rows[0]["metadata"][
            "source_turn" if corruption == "source" else "source_date"
        ] = "corrupted"
    with pytest.raises(ValueError):
        validate_rows(rows, {t.id: t for t in turns}, {"s0:t0": "0", "s0:t1": "1"})


def test_comparison_keeps_unlabelled_cases_null_and_rejects_missing_pairs():
    base = {
        "question_id": "q",
        "category": "abstention",
        "source_count": 2,
        "evidence_source_ids": [],
    }
    packing = {"2048": {"evidence_recall": None}}
    reference = {"details": [{**base, "methods": {"prme": {"packing": packing}}}]}
    details = [{**base, "packing": packing}]
    result = compare(reference, details, budgets=[2048])
    assert result["overall"]["2048"]["queries"] == 0
    assert result["overall"]["2048"]["after"] is None
    with pytest.raises(ValueError):
        compare(reference, [], budgets=[2048])
    with pytest.raises(ValueError):
        compare(reference, [{**details[0], "error": "Failure"}], budgets=[2048])
