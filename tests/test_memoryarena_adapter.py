"""MemoryArena transport and isolation contracts; no task quality claim."""

from functools import partial
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from benchmarks.diagnostics.hybrid_lexical import raw_config
from benchmarks.diagnostics.memoryarena_server import (
    _trace_projection,
    _travel_reference_query,
    create_app,
)
from prme.retrieval.tokenization import count_tokens
from tests.test_durable_ingestion import MockEmbeddingProvider


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider",
                        lambda _: MockEmbeddingProvider())
    return raw_config().model_copy(update={
        "db_path": str(tmp_path / "memory.duckdb"),
        "vector_path": str(tmp_path / "vectors.usearch"),
        "lexical_path": str(tmp_path / "lexical"),
    })


def identity(user="alice"):
    return {"user_id": user, "memory_system_name": "prme"}


@pytest.mark.parametrize("budget", [128, 4096])
def test_native_shapes_and_complete_memory_budget(config, budget):
    app = create_app(config, memory_tokens=budget)
    with TestClient(app) as client:
        assert client.post("/memory/initialize", json=identity()).json() == {
            "status": "ok", **identity(),
        }
        empty = client.post("/memory/wrap_user_prompt", json={**identity(), "question": "Tea?"}).json()
        assert empty["prompt"] == "<memory_context>\nNone\n</memory_context>\nUser: Tea?"
        source = "Nia prefers green tea — her colleague prefers black coffee."
        stored = client.post("/memory/add", json={**identity(), "chunk": source})
        assert stored.status_code == 200 and stored.json()["response"]["event_id"]
        query = "What does Nia prefer?"
        result = client.post("/memory/wrap_user_prompt", json={**identity(), "question": query})
        assert result.status_code == 200
        prompt = result.json()["prompt"]
        suffix = "\nUser: " + query
        assert prompt.endswith(suffix)
        assert count_tokens(prompt[:-len(suffix)], config.packing.tokenizer) <= budget
        if budget == 4096:
            assert source in prompt


def test_trace_projection_keeps_only_named_final_plan():
    source = (
        '{"name":"Alice","query":"large task","scratchpad":[{"tool_results":"huge"}],'
        '"final_plan":"analysis first\\n=== Alice\'s Plan ===\\nDay 1: cobalt rail",'
        '"is_base_person":true}'
    )
    projection, name, is_base = _trace_projection(source)
    assert (name, is_base) == ("Alice", True)
    assert projection == (
        "Traveler: Alice\nTrip request:\nlarge task\nFinal plan:\n"
        "=== Alice's Plan ===\nDay 1: cobalt rail"
    )
    assert "scratchpad" not in projection and "analysis first" not in projection


def test_travel_reference_query_removes_roster_and_keeps_dependencies():
    question = (
        "I am Carol.\n"
        "I'm traveling with Base, Alice, and Bob.\n"
        "For breakfast, I'd like to join Alice.\n"
        "Dinner should cost less than Bob's lunch."
    )
    assert _travel_reference_query(question, "Base") == "Base Bob Alice"


def test_travel_trace_uses_projection_but_retains_raw_source(config):
    app = create_app(config)
    with TestClient(app) as client:
        client.post("/memory/initialize", json=identity())
        base = (
            '{"name":"Base","query":"trip","is_base_person":true,'
            '"final_plan":"=== Base\'s Plan ===\\nDay 1: base route"}'
        )
        alice = (
            '{"name":"Alice","query":"task","scratchpad":[{"tool_results":"private raw"}],'
            '"final_plan":"=== Alice\'s Plan ===\\nDay 1: cobalt rail"}'
        )
        base_event = client.post(
            "/memory/add", json={**identity(), "chunk": base}
        ).json()["response"]["event_id"]
        alice_event = client.post(
            "/memory/add", json={**identity(), "chunk": alice}
        ).json()["response"]["event_id"]
        result = client.post(
            "/memory/wrap_user_prompt",
            json={
                **identity(),
                "question": (
                    "I am Carol.\nI'm traveling with Base and Alice.\n"
                    "For breakfast, I'd like to join Alice."
                ),
            },
        )
        assert result.status_code == 200
        prompt = result.json()["prompt"]
        assert "Day 1: base route" in prompt and "Day 1: cobalt rail" in prompt
        assert "Trip request:\\ntrip" in prompt
        assert "private raw" not in prompt
        owner = app.state.owners["alice"]
        base_source = client.portal.call(
            partial(app.state.engine.get_event, base_event, user_id=owner)
        )
        alice_source = client.portal.call(
            partial(app.state.engine.get_event, alice_event, user_id=owner)
        )
        assert base_source.content == base and alice_source.content == alice


def test_reinitialization_is_fresh_and_retains_previous_source(config):
    app = create_app(config)
    with TestClient(app) as client:
        client.post("/memory/initialize", json=identity())
        old_owner = app.state.owners["alice"]
        event_id = client.post("/memory/add", json={**identity(), "chunk": "Alice owns cobalt telescope."}).json()["response"]["event_id"]
        client.post("/memory/initialize", json=identity("bob"))
        foreign = client.post("/memory/wrap_user_prompt", json={**identity("bob"), "question": "Telescope?"}).json()
        assert "cobalt" not in foreign["prompt"]
        client.post("/memory/initialize", json=identity())
        assert app.state.owners["alice"] != old_owner
        reset = client.post("/memory/wrap_user_prompt", json={**identity(), "question": "Telescope?"}).json()
        assert "cobalt" not in reset["prompt"]
        event = client.portal.call(partial(app.state.engine.get_event, event_id, user_id=old_owner))
        assert event.content == "Alice owns cobalt telescope."


@pytest.mark.parametrize("route,fields", [
    ("add", {"chunk": "Private source"}),
    ("wrap_user_prompt", {"question": "Private source?"}),
])
def test_unknown_or_mismatched_identity_fails(config, route, fields):
    with TestClient(create_app(config)) as client:
        assert client.post("/memory/" + route, json={**identity(), **fields}).status_code == 404
        assert client.post("/memory/initialize", json={**identity(), "memory_system_name": "other"}).status_code == 400
        client.post("/memory/initialize", json=identity())
        assert client.post("/memory/" + route, json={**identity(), **fields, "memory_system_name": "other"}).status_code == 400


def test_index_failure_is_not_reported_as_success(config, monkeypatch):
    app = create_app(config)
    with TestClient(app) as client:
        client.post("/memory/initialize", json=identity())
        monkeypatch.setattr(app.state.engine._vector_index, "index", AsyncMock(side_effect=OSError("offline")))
        response = client.post("/memory/add", json={**identity(), "chunk": "A retained observation"})
        assert response.status_code == 503
        event_id = response.json()["detail"]["event_id"]
        event = client.portal.call(partial(app.state.engine.get_event, event_id, user_id=app.state.owners["alice"]))
        assert event.content == "A retained observation"


def test_tiny_budget_rejected_before_startup(config):
    with pytest.raises(ValueError, match="budget"):
        create_app(config, memory_tokens=16)
