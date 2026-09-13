"""Authored end-to-end capture and strict reader boundaries before dataset use."""

from dataclasses import asdict

import pytest

from benchmarks.diagnostics.personamem_packing import capture_persona, parse_response, reader_payload
from benchmarks.diagnostics.hybrid_lexical import raw_config
from benchmarks.evidence import SourceTurn
from benchmarks.personamem import ChoiceQuestion
from tests.test_durable_ingestion import MockEmbeddingProvider


def test_capture_keeps_raw_sources_and_reproduces_control(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    turns = [SourceTurn("t00000", "", "user", "I choose quiet shaded routes only in summer.", ""),
             SourceTurn("t00001", "", "assistant", "The seasonal condition matters.", "")]
    result = capture_persona(turns, [("authored", "Which summer route fits?")], tmp_path / "pack", config=raw_config())
    assert result["source_count"] == 2 and result["extraction_calls"] == 0
    assert result["repeated_control_identical"] and result["source_memories_unchanged"]
    capture = result["captures"]["authored"]
    assert set(capture["contexts"]) == {"density", "quarter", "score", "no_memory"}
    assert capture["contexts"]["no_memory"]["context"] == ""
    assert all(c["tokens"] <= 4096 for c in capture["contexts"].values())
    assert capture["receipt"]["packing"]["multipath_ordering"] == "density"


def test_reader_does_not_accept_hidden_labels():
    question = asdict(ChoiceQuestion("id", "1", "Which route?", ("Quiet", "Noisy", "Steep", "Crowded")))
    body = reader_payload(question, "Seasonal source memory", "authored-model")
    assert "Seasonal source memory" in body["messages"][1]["content"]
    assert all(k not in body for k in ("correct", "preference", "expanded_persona"))
    for key in ("correct", "preference", "expanded_persona"):
        with pytest.raises(ValueError, match="unexpected fields"):
            reader_payload({**question, key: "hidden"}, "", "authored-model")


@pytest.mark.parametrize("content,expected", [('{"answer":"A"}', "A"), ('{"answer":"Z"}', None),
                                            ('{"answer":"A","reason":"extra"}', None), ('A', None), ('[]', None)])
def test_strict_prediction_scoring(content, expected):
    response = {"done": True, "done_reason": "stop", "prompt_eval_count": 25, "eval_count": 8,
                "message": {"content": content}}
    assert parse_response(response) == expected


def test_incomplete_and_oversized_responses_are_failed_runs():
    response = {"done": True, "done_reason": "length", "prompt_eval_count": 25, "eval_count": 8,
                "message": {"content": '{"answer":"A"}'}}
    with pytest.raises(ValueError, match="truncated"):
        parse_response(response)
    response.update(done_reason="stop", prompt_eval_count=65536)
    with pytest.raises(ValueError, match="accounting"):
        parse_response(response)
