"""Sync and HTTP callers can retain an identity across uncertain outcomes."""

from uuid import UUID, uuid4

import httpx
import pytest

from prme import MemoryClient, MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig
from prme.client import config_from_directory
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


def test_sync_confirmation_retries_after_reopening(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "prme.storage.engine.create_embedding_provider",
        lambda _: test_durable_ingestion.MockEmbeddingProvider(),
    )
    config = config_from_directory(str(tmp_path))
    config.organizer.opportunistic_enabled = False
    request_id = uuid4()
    with MemoryClient(config=config) as client:
        source = client.store("An observation", user_id="alice")
        node = client.get_event_nodes(source, user_id="alice")[0]
        client.reinforce(str(node.id), user_id="alice", request_id=request_id)
        after = client.get_node(str(node.id), user_id="alice")
    with MemoryClient(config=config) as client:
        client.reinforce(str(node.id), user_id="alice", request_id=request_id)
        assert client.get_node(str(node.id), user_id="alice") == after
        assert after.reinforcement_boost == pytest.approx(0.15)


async def test_http_retries_bind_owned_evidence_and_reject_conflicts(config, user):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        evidence = await engine.store("Supporting evidence", user_id=user)
        other = await engine.store("Different evidence", user_id=user)
        app = create_app(
            config.model_copy(
                update={"api": APIConfig(user_keys={user: "owner-token"})}
            )
        )
        app.state.engine = engine
        headers = {
            "Authorization": "Bearer owner-token",
            "Idempotency-Key": str(uuid4()),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            path = f"/v1/nodes/{node.id}/reinforce"
            first = await client.put(
                path, headers=headers, json={"evidence_id": evidence}
            )
            assert first.status_code == 200
            after = await engine.get_node(str(node.id), user_id=user)
            assert first.json()["reinforcement_boost"] == pytest.approx(.15)
            assert first.json()["confidence_base"] == after.confidence_base
            assert first.json()["last_reinforced_at"] == after.last_reinforced_at.isoformat()
            again = await client.put(
                path, headers=headers, json={"evidence_id": evidence}
            )
            assert again.status_code == 200 and again.json() == first.json()
            conflict = await client.put(
                path, headers=headers, json={"evidence_id": other}
            )
            assert conflict.status_code == 409
            malformed = await client.put(
                path, headers={**headers, "Idempotency-Key": "invalid"}
            )
            assert malformed.status_code == 422
            assert await engine.get_node(str(node.id), user_id=user) == after
            assert UUID(evidence) in after.evidence_refs
