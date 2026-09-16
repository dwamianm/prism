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
            "--registration",
            "/tmp/registration.json",
            "--ollama-api-base-url",
            "http://127.0.0.1:11434",
            "--",
            "--domain",
            "web",
        ]
    )

    assert args.reader_reasoning_effort == "none"
    assert args.checkpoint_filename == "reader.jsonl"
    assert args.registration == Path("/tmp/registration.json")
    assert args.ollama_api_base_url == "http://127.0.0.1:11434"
    assert harness_args == ["--domain", "web"]

    with pytest.raises(SystemExit):
        runner._parse_launcher_args(
            ["/tmp/upstream", "--checkpoint-filename", "nested/reader.jsonl", "--", "--domain", "web"]
        )


def test_execution_manifest_is_immutable_and_rejects_legacy_outputs(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    value = {
        "schema_version": 1,
        "kind": "longmemeval-v2-execution",
        "registration_sha256": None,
        "source": {"prme_revision": "a" * 40},
    }

    path = runner._write_or_verify_execution_manifest(output, value)
    assert json.loads(path.read_text()) == value
    assert runner._write_or_verify_execution_manifest(output, value) == path
    with pytest.raises(RuntimeError, match="execution source does not match"):
        runner._write_or_verify_execution_manifest(
            output, {**value, "registration_sha256": "b" * 64}
        )

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / runner.DEFAULT_CHECKPOINT_FILENAME).write_text("{}\n")
    with pytest.raises(RuntimeError, match="cannot attribute existing outputs"):
        runner._write_or_verify_execution_manifest(legacy, value)


def test_registered_execution_manifest_binds_clean_source_trees(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    upstream = tmp_path / "upstream"
    adapter_source = project / "adapter.py"
    config_source = project / "config.json"
    compact_config_source = project / "compact-config.json"
    installed_adapter = upstream / "memory_modules" / "prme.py"
    installed_config = upstream / "evaluation" / "memory_configs" / "prme.json"
    installed_compact_config = (
        upstream / "evaluation" / "memory_configs" / "prme_compact.json"
    )
    baseline_config = upstream / "evaluation" / "memory_configs" / "no_retrieval.json"
    harness = upstream / "evaluation" / "harness.py"
    for path, content in (
        (adapter_source, "adapter\n"),
        (config_source, "{}\n"),
        (compact_config_source, '{"compact": true}\n'),
        (installed_adapter, "adapter\n"),
        (installed_config, "{}\n"),
        (installed_compact_config, '{"compact": true}\n'),
        (baseline_config, '{"memory_type": "no_retrieval"}\n'),
        (harness, "def main(): pass\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    prme_revision = "a" * 40
    upstream_revision = "b" * 40
    reader_runtime = {
        "provider": "ollama",
        "api_base_url": "http://127.0.0.1:11434",
        "server_version": "0.34.1",
        "model": "reader",
        "resolved_model": "reader",
        "model_digest_sha256": "c" * 64,
        "model_size_bytes": 1024,
        "details": {"format": "gguf"},
        "capabilities": ["completion"],
        "requires": "0.17.1",
    }
    monkeypatch.setattr(runner.installer, "_ADAPTER_SOURCE", adapter_source)
    monkeypatch.setattr(runner.installer, "_CONFIG_SOURCE", config_source)
    monkeypatch.setattr(
        runner.installer, "_COMPACT_CONFIG_SOURCE", compact_config_source
    )
    monkeypatch.setattr(
        runner,
        "_git_identity",
        lambda root: (
            (prme_revision, [])
            if root == project
            else (
                upstream_revision,
                [
                    "evaluation/memory_configs/prme.json",
                    "evaluation/memory_configs/prme_compact.json",
                    "memory_modules/__init__.py",
                    "memory_modules/prme.py",
                ],
            )
        ),
    )
    registration = tmp_path / "registration.json"
    registration.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source": {
                    "prme_revision": prme_revision,
                    "upstream_revision": upstream_revision,
                },
                "reader": {"runtime_identity": reader_runtime},
            }
        )
    )
    saved_memory = tmp_path / "saved-memory"
    (saved_memory / "prme_pack").mkdir(parents=True)
    (saved_memory / "memory_config.json").write_text("{}\n")
    (saved_memory / "prme_pack" / "index.bin").write_bytes(b"index")
    monkeypatch.setattr(
        runner,
        "_ollama_reader_identity",
        lambda api_base_url, model: reader_runtime,
    )

    manifest = runner._build_execution_manifest(
        upstream,
        {"project_root": str(project), "revision": upstream_revision},
        registration,
        "evaluation/memory_configs/prme_compact.json",
        saved_memory,
        reader_model="reader",
        ollama_api_base_url="http://127.0.0.1:11434",
    )

    assert manifest["schema_version"] == 3
    assert manifest["registration_sha256"] == runner._digest(registration)
    assert manifest["reader_runtime"] == reader_runtime
    assert manifest["source"]["prme_worktree_changes"] == []
    assert (
        manifest["source"]["adapter_source_sha256"]
        == manifest["source"]["adapter_installed_sha256"]
    )
    assert manifest["invocation"] == {
        "memory_config_path": "evaluation/memory_configs/prme_compact.json",
        "memory_config_sha256": runner._digest(installed_compact_config),
        "load_memory_dir": str(saved_memory),
        "memory_artifact": runner._directory_identity(saved_memory),
        "memory_payload_artifact": runner._directory_identity(saved_memory / "prme_pack"),
    }

    changed = json.loads(registration.read_text())
    changed["reader"]["runtime_identity"]["model_digest_sha256"] = "d" * 64
    registration.write_text(json.dumps(changed))
    with pytest.raises(RuntimeError, match="reader runtime identity"):
        runner._build_execution_manifest(
            upstream,
            {"project_root": str(project), "revision": upstream_revision},
            registration,
            "evaluation/memory_configs/prme_compact.json",
            saved_memory,
            reader_model="reader",
            ollama_api_base_url="http://127.0.0.1:11434",
        )


def test_ollama_reader_identity_binds_server_and_model(monkeypatch) -> None:
    def endpoint(_base_url, path, payload=None):
        if path == "/api/version":
            return {"version": "0.34.1"}
        if path == "/api/tags":
            return {
                "models": [
                    {
                        "name": "reader:latest",
                        "model": "reader:latest",
                        "digest": "a" * 64,
                        "size": 123,
                        "details": {
                            "format": "gguf",
                            "parameter_size": "1B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                ]
            }
        assert payload == {"model": "reader:latest", "verbose": False}
        return {"capabilities": ["vision", "completion"], "requires": "0.17.1"}

    monkeypatch.setattr(runner, "_ollama_endpoint", endpoint)

    identity = runner._ollama_reader_identity(
        "http://127.0.0.1:11434/", "reader:latest"
    )

    assert identity == {
        "provider": "ollama",
        "api_base_url": "http://127.0.0.1:11434",
        "server_version": "0.34.1",
        "model": "reader:latest",
        "resolved_model": "reader:latest",
        "model_digest_sha256": "a" * 64,
        "model_size_bytes": 123,
        "details": {
            "format": "gguf",
            "parameter_size": "1B",
            "quantization_level": "Q4_K_M",
        },
        "capabilities": ["completion", "vision"],
        "requires": "0.17.1",
    }


def test_execution_manifest_rejects_missing_selected_config(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    upstream = tmp_path / "upstream"
    adapter = project / "adapter.py"
    config = project / "config.json"
    compact = project / "compact.json"
    for path in (adapter, config, compact):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    for source, destination in (
        (adapter, upstream / "memory_modules" / "prme.py"),
        (config, upstream / "evaluation" / "memory_configs" / "prme.json"),
        (
            compact,
            upstream / "evaluation" / "memory_configs" / "prme_compact.json",
        ),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    harness = upstream / "evaluation" / "harness.py"
    harness.write_text("pass\n")
    monkeypatch.setattr(runner.installer, "_ADAPTER_SOURCE", adapter)
    monkeypatch.setattr(runner.installer, "_CONFIG_SOURCE", config)
    monkeypatch.setattr(runner.installer, "_COMPACT_CONFIG_SOURCE", compact)
    monkeypatch.setattr(runner, "_git_identity", lambda root: ("a" * 40, []))

    with pytest.raises(RuntimeError, match="configuration is unavailable"):
        runner._build_execution_manifest(
            upstream,
            {"project_root": str(project), "revision": "a" * 40},
            None,
            "evaluation/memory_configs/missing.json",
            None,
        )


def test_directory_identity_rejects_symlinks_and_changes_with_content(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    first = memory / "a"
    first.write_text("one")
    identity = runner._directory_identity(memory)
    assert identity["file_count"] == 1
    assert identity["bytes"] == 3

    first.write_text("two")
    assert runner._directory_identity(memory)["sha256"] != identity["sha256"]
    (memory / "link").symlink_to(first)
    with pytest.raises(RuntimeError, match="contains a symlink"):
        runner._directory_identity(memory)


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
