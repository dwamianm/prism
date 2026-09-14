"""Durable reader behavior for the pinned LongMemEval-V2 launcher."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import ModuleType

import pytest

from benchmarks.integrations import run_longmemeval_v2 as runner


class _Progress:
    def __init__(self, **_kwargs) -> None:
        self.count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def update(self, amount: int) -> None:
        self.count += amount


class _Client:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _args(**overrides) -> argparse.Namespace:
    values = {
        "model": "reader",
        "base_url": "http://reader.test/v1",
        "api_key_env": "TEST_KEY",
        "api_key_file": None,
        "max_completion_tokens": 128,
        "reasoning_effort": "none",
        "temperature": 0,
        "top_p": None,
        "presence_penalty": None,
        "top_k": None,
        "repetition_penalty": None,
        "reader_enable_thinking": False,
        "reader_max_concurrent_requests": 2,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _fake_harness(calls: list[str], clients: list[_Client]) -> ModuleType:
    harness = ModuleType("fake_longmemeval_harness")

    class BadRequestError(Exception):
        pass

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    def create_async_client(*_args) -> _Client:
        client = _Client()
        clients.append(client)
        return client

    async def call_reader_model_async(_client, _args, messages):
        question = messages[-1]["content"]
        calls.append(question)
        return f"answer \\boxed{{{question}}}", {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        }

    harness.BadRequestError = BadRequestError
    harness.require = require
    harness.create_async_client = create_async_client
    harness.call_reader_model_async = call_reader_model_async
    harness.extract_boxed_answer = lambda text: text.split("{", 1)[1].split("}", 1)[0]
    harness.is_unknown = lambda answer: answer == "UNKNOWN"
    harness.tqdm = _Progress
    return harness


def _rows() -> list[dict]:
    return [
        {"question_id": "q1", "messages": [{"role": "user", "content": "one"}]},
        {"question_id": "q2", "messages": [{"role": "user", "content": "two"}]},
    ]


async def test_reader_outputs_resume_without_repeating_requests(tmp_path: Path) -> None:
    calls: list[str] = []
    clients: list[_Client] = []
    harness = _fake_harness(calls, clients)
    checkpoint = tmp_path / runner.DEFAULT_CHECKPOINT_FILENAME

    first = await runner.generate_reader_outputs_checkpointed(
        harness, _args(), _rows(), checkpoint
    )
    second = await runner.generate_reader_outputs_checkpointed(
        harness, _args(), _rows(), checkpoint
    )

    assert first == second
    assert calls == ["one", "two"]
    assert len(clients) == 1
    assert clients[0].closed is True
    records = [json.loads(line) for line in checkpoint.read_text().splitlines()]
    assert [record["question_id"] for record in records] == ["q1", "q2"]
    assert all(record["schema_version"] == 1 for record in records)


async def test_reader_checkpoint_rejects_changed_prompt_or_settings(tmp_path: Path) -> None:
    calls: list[str] = []
    harness = _fake_harness(calls, [])
    checkpoint = tmp_path / runner.DEFAULT_CHECKPOINT_FILENAME
    await runner.generate_reader_outputs_checkpointed(harness, _args(), _rows(), checkpoint)

    changed_rows = _rows()
    changed_rows[0]["messages"][0]["content"] = "changed"
    with pytest.raises(RuntimeError, match="does not match the prompt"):
        await runner.generate_reader_outputs_checkpointed(
            harness, _args(), changed_rows, checkpoint
        )
    with pytest.raises(RuntimeError, match="does not match the prompt"):
        await runner.generate_reader_outputs_checkpointed(
            harness, _args(model="different-reader"), _rows(), checkpoint
        )

    assert calls == ["one", "two"]


async def test_reader_checkpoint_repairs_an_interrupted_final_append(tmp_path: Path) -> None:
    calls: list[str] = []
    harness = _fake_harness(calls, [])
    checkpoint = tmp_path / runner.DEFAULT_CHECKPOINT_FILENAME
    expected = await runner.generate_reader_outputs_checkpointed(
        harness, _args(), _rows(), checkpoint
    )
    with checkpoint.open("ab") as handle:
        handle.write(b'{"schema_version":1,"question_id":"interrupted"')

    resumed = await runner.generate_reader_outputs_checkpointed(
        harness, _args(), _rows(), checkpoint
    )

    assert resumed == expected
    assert checkpoint.read_bytes().endswith(b"\n")
    assert len(checkpoint.read_text().splitlines()) == 2


def test_launcher_parses_none_reasoning_and_plain_checkpoint_name() -> None:
    args, harness_args = runner._parse_launcher_args(
        [
            "/tmp/upstream",
            "--reader-reasoning-effort",
            "none",
            "--checkpoint-filename",
            "reader.jsonl",
            "--",
            "--domain",
            "web",
        ]
    )

    assert args.reader_reasoning_effort == "none"
    assert args.checkpoint_filename == "reader.jsonl"
    assert harness_args == ["--domain", "web"]

    with pytest.raises(SystemExit):
        runner._parse_launcher_args(
            ["/tmp/upstream", "--checkpoint-filename", "nested/reader.jsonl", "--", "--domain", "web"]
        )


def test_resume_prompt_requires_same_question_and_haystack() -> None:
    item = {
        "question_id": "q1",
        "query_invocation_id": "new-invocation",
        "question_text": "Question?",
        "stream_index": 0,
    }
    cached = {
        "q1": {
            **item,
            "query_invocation_id": "original-invocation",
            "haystack_ids": ["trajectory-1"],
            "messages": [],
        }
    }

    assert runner._resume_prompt_row(cached, item, ["trajectory-1"]) is cached["q1"]
    with pytest.raises(RuntimeError, match="current question_text"):
        runner._resume_prompt_row(cached, {**item, "question_text": "Changed"}, ["trajectory-1"])
    with pytest.raises(RuntimeError, match="current haystack"):
        runner._resume_prompt_row(cached, item, ["trajectory-2"])
