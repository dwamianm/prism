"""Fail-closed contracts for the registered LongMemEval-S baseline."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.integrations import run_longmemeval_s_baseline as runner


def _case() -> dict:
    return {
        "question_id": "q1",
        "question_type": "multi-session",
        "question": "Where did we decide to meet?",
        "answer": "The library",
        "question_date": "2025/01/03 (Fri) 10:00",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": [
            "2025/01/01 (Wed) 10:00",
            "2025/01/02 (Thu) 10:00",
        ],
        "haystack_sessions": [
            [
                {"role": "user", "content": "Where should we meet?"},
                {
                    "role": "assistant",
                    "content": "The library works.",
                    "has_answer": True,
                },
            ],
            [
                {
                    "role": "user",
                    "content": "Let's use the west entrance.",
                    "has_answer": True,
                }
            ],
        ],
        "answer_session_ids": ["s1", "s2"],
    }


def test_evidence_metrics_require_every_labeled_session_and_turn() -> None:
    case = _case()
    partial = runner._evidence_metrics(
        case,
        [
            {
                "source_session_id": "s1",
                "source_session_position": 0,
                "source_turn_index": 1,
            }
        ],
    )
    complete = runner._evidence_metrics(
        case,
        [
            {
                "source_session_id": "s1",
                "source_session_position": 0,
                "source_turn_index": 1,
            },
            {
                "source_session_id": "s2",
                "source_session_position": 1,
                "source_turn_index": 0,
            },
        ],
    )

    assert partial == {
        "applicable": True,
        "required_sessions": 2,
        "retrieved_required_sessions": 1,
        "any_session_recall": True,
        "complete_session_recall": False,
        "required_turns": 2,
        "retrieved_required_turns": 1,
        "any_turn_recall": True,
        "complete_turn_recall": False,
    }
    assert complete["complete_session_recall"] is True
    assert complete["complete_turn_recall"] is True
    assert runner._evidence_metrics({**case, "question_id": "q1_abs"}, []) == {
        "applicable": False
    }


def test_reader_prompt_is_the_frozen_official_cot_template() -> None:
    prompt = runner._reader_prompt("MEMORY", "DATE", "QUESTION")

    assert prompt.startswith(
        "I will give you several history chats between you and a user. Please "
        "answer the question based on the relevant chat history."
    )
    assert (
        "first extract all the relevant information, and then reason over the "
        "information to get the answer" in prompt
    )
    assert prompt.endswith(
        "History Chats:\n\nMEMORY\n\nCurrent Date: DATE\n"
        "Question: QUESTION\nAnswer (step by step):"
    )


def test_saved_capture_detects_content_checksum_and_pack_changes(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "memory.duckdb").write_bytes(b"durable-pack")
    identity = {"registration_sha256": "a" * 64, "case_sha256": "b" * 64}
    saved = {
        "complete": True,
        "identity": identity,
        "context": "retrieved context",
        "context_sha256": runner._sha256(b"retrieved context"),
        "pack": runner._tree_identity(pack),
    }
    saved["capture_sha256"] = runner._sha256(runner._canonical(saved))

    runner._validate_saved_capture(saved, identity=identity, pack_path=pack)

    changed_context = json.loads(json.dumps(saved))
    changed_context["context"] = "changed"
    with pytest.raises(ValueError, match="saved capture differs"):
        runner._validate_saved_capture(
            changed_context, identity=identity, pack_path=pack
        )

    (pack / "memory.duckdb").write_bytes(b"changed-pack")
    with pytest.raises(ValueError, match="saved capture differs"):
        runner._validate_saved_capture(saved, identity=identity, pack_path=pack)


async def test_provider_call_uses_the_registered_model_and_does_not_store() -> None:
    calls: list[dict] = []

    class Responses:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output_text="answer",
                model_dump=lambda **_kwargs: {"id": "response-1"},
            )

    client = SimpleNamespace(responses=Responses())
    result = await runner._provider_call(client, "prompt", max_output_tokens=12)

    assert result["text"] == "answer"
    assert result["attempts"] == 1
    assert calls == [
        {
            "model": runner.MODEL,
            "input": "prompt",
            "reasoning": {"effort": runner.REASONING_EFFORT},
            "max_output_tokens": 12,
            "store": False,
        }
    ]


def test_case_identity_binds_question_and_full_history() -> None:
    case = _case()
    changed = json.loads(json.dumps(case))
    changed["haystack_sessions"][0][0]["content"] = "changed"

    assert runner._case_identity(case) != runner._case_identity(changed)
