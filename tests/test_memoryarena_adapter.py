"""MemoryArena transport and isolation contracts; no task quality claim."""

from functools import partial
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from benchmarks.diagnostics.hybrid_lexical import raw_config
from benchmarks.diagnostics.memoryarena_server import create_app
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
