"""Synthetic contract checks for the MemoryAgentBench PRME adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest

from benchmarks.integrations import memoryagentbench as adapter


class FakeCompletions:
    def __init__(self) -> None:
        self.messages = None
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        self.messages = kwargs["messages"]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="Place Order once"))
            ],
            usage=SimpleNamespace(prompt_tokens=321, completion_tokens=4),
        )


class FakeAgent:
    pass


def fake_agent(
    root: Path, *, budget: int = 4096, context_format: str = "auditable"
) -> FakeAgent:
    completions = FakeCompletions()
    reader = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    agent = FakeAgent()
    agent.sub_dataset = "eventqa_65536"
    agent.agent_name = "Agentic_memory_prme"
    agent.agent_save_to_folder = str(root / "agent")
    agent.output_dir = str(root / "outputs")
    agent.model = "gpt-4o-mini"
    agent.temperature = 0.0
    agent.max_tokens = 40
    agent._reader = reader
    agent._reader_completions = completions
    agent._create_oai_client = lambda: reader
    agent._extract_retrieval_query = lambda message: message.rsplit("Question:", 1)[-1]
    agent._create_standard_response = (
        lambda output, input_tokens, output_tokens, memory_time, query_time: {
            "output": output,
            "input_len": input_tokens,
            "output_len": output_tokens,
            "memory_construction_time": memory_time,
            "query_time_len": query_time,
        }
    )
    adapter.initialize_prme_agent(
        agent,
        {
            "retrieve_num": 20,
            "prme_result_limit": 20,
            "prme_token_budget": budget,
            "prme_max_chunk_chars": 512,
            "prme_user_id": "test-owner",
            "prme_context_format": context_format,
            "reader_reasoning_effort": "none",
            "reader_seed": 42,
        },
    )
    return agent


@pytest.fixture
def upstream_modules(monkeypatch):
    utils = ModuleType("utils")
    eval_data = ModuleType("utils.eval_data_utils")
    templates = ModuleType("utils.templates")
    eval_data.format_chat = lambda message, system_message: [
        {"role": "system", "content": system_message},
        {"role": "user", "content": message},
    ]
    templates.get_template = lambda sub_dataset, kind, agent_name: "memory system"
    monkeypatch.setitem(sys.modules, "utils", utils)
    monkeypatch.setitem(sys.modules, "utils.eval_data_utils", eval_data)
    monkeypatch.setitem(sys.modules, "utils.templates", templates)


@pytest.mark.parametrize("context_format", ["auditable", "compact"])
def test_adapter_preserves_text_and_round_trips_pack(
    tmp_path: Path, upstream_modules, context_format: str
) -> None:
    source = (
        "A" * 700
        + "\n\nThe checkout page says to click Place Order once because confirmation is delayed.\n"
    )
    agent = fake_agent(tmp_path, context_format=context_format)
    try:
        assert "".join(adapter._split_units(source, 512)) == source
        assert all(len(chunk) <= 512 for chunk in adapter._split_units(source, 512))
        adapter.handle_prme_agent(agent, source, True, None, 7)
        adapter.save_prme_agent(agent)

        manifest_path = agent.prme_pack_path / adapter._MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] == "complete"
        assert manifest["stored_nodes"] == 2
        assert manifest["source_chunks"][0]["sha256"]
        assert manifest["query_reference_time"]

        result = adapter.handle_prme_agent(
            agent,
            "Question: What should be clicked only once?",
            False,
            3,
            7,
        )
        assert result["output"] == "Place Order once"
        assert result["input_len"] == 321
        assert result["memory_construction_time"] > 0
        assert "Place Order once" in agent._reader_completions.messages[1]["content"]
        assert agent._reader_completions.kwargs["reasoning_effort"] == "none"
        assert agent._reader_completions.kwargs["seed"] == 42
        assert agent._reader_completions.kwargs["max_tokens"] == 40
        retrieval = json.loads(
            (
                tmp_path
                / "outputs"
                / "prme_retrievals"
                / "eventqa_65536"
                / "query_3_context_7.json"
            ).read_text(encoding="utf-8")
        )
        assert retrieval["context_sha256"]
        assert retrieval["request_id"]
        assert retrieval["adapter_schema_version"] == 4
        assert retrieval["context_format"] == context_format
        assert retrieval["reader_reasoning_effort"] == "none"
        assert retrieval["reader_seed"] == 42
        assert retrieval["sub_dataset"] == "eventqa_65536"
        assert retrieval["query_id"] == 3
        assert retrieval["context_id"] == 7
        assert retrieval["receipt_persisted"] is True
        assert 0 < retrieval["context_token_count"] <= retrieval["token_budget"]
        assert retrieval["included_count"] > 0
        assert (
            retrieval["query_sha256"]
            == hashlib.sha256(
                b"Question: What should be clicked only once?"
            ).hexdigest()
        )
        assert (
            retrieval["manifest_sha256"]
            == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        )
    finally:
        adapter._close_client(agent)

    reopened = fake_agent(tmp_path, context_format=context_format)
    try:
        adapter.load_prme_agent(reopened)
        result = adapter.handle_prme_agent(
            reopened,
            "Question: Why is the confirmation relevant?",
            False,
            4,
            7,
        )
        assert result["output"] == "Place Order once"
        assert result["memory_construction_time"] == 0
    finally:
        adapter._close_client(reopened)


def test_adapter_rejects_incomplete_and_changed_packs(tmp_path: Path) -> None:
    agent = fake_agent(tmp_path)
    try:
        adapter.handle_prme_agent(agent, "incomplete evidence", True, None, 1)
    finally:
        adapter._close_client(agent)

    incomplete = fake_agent(tmp_path)
    with pytest.raises(RuntimeError, match="incompatible or incomplete"):
        adapter.load_prme_agent(incomplete)

    manifest_path = incomplete.prme_pack_path / adapter._MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "complete"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    changed = fake_agent(tmp_path, budget=2048)
    with pytest.raises(RuntimeError, match="incompatible or incomplete"):
        adapter.load_prme_agent(changed)


def test_adapter_rejects_unknown_context_format(tmp_path: Path) -> None:
    agent = FakeAgent()
    agent.sub_dataset = "eventqa_65536"
    agent.agent_save_to_folder = str(tmp_path / "agent")
    with pytest.raises(ValueError, match="prme_context_format"):
        adapter.initialize_prme_agent(agent, {"prme_context_format": "opaque"})


@pytest.mark.parametrize(
    "config,match",
    [
        ({"reader_reasoning_effort": "maximum"}, "reader_reasoning_effort"),
        ({"reader_seed": True}, "reader_seed"),
        ({"reader_seed": "42"}, "reader_seed"),
    ],
)
def test_adapter_rejects_invalid_reader_settings(
    tmp_path: Path, config: dict[str, object], match: str
) -> None:
    agent = FakeAgent()
    agent.sub_dataset = "eventqa_65536"
    agent.agent_save_to_folder = str(tmp_path / "agent")
    with pytest.raises(ValueError, match=match):
        adapter.initialize_prme_agent(agent, config)
