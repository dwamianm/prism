"""Integration test: full MemoryEngine with PostgreSQL backend.

Requires PRME_TEST_DATABASE_URL environment variable.
"""

from __future__ import annotations

import os

import pytest

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine
from prme.types import NodeType, Scope

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("PRME_TEST_DATABASE_URL"),
        reason="PRME_TEST_DATABASE_URL not set",
    ),
]


@pytest.fixture
async def pg_engine():
    config = PRMEConfig(
        database_url=os.environ["PRME_TEST_DATABASE_URL"],
    )
    assert config.backend == "postgres"
    engine = await MemoryEngine.create(config)
    yield engine
    await engine.close()


async def test_store_and_retrieve(pg_engine):
    """Store content and verify it's retrievable via get_event and get_node."""
    event_id = await pg_engine.store(
        "Paris is the capital of France",
        user_id="integration-test-user",
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
    )
    assert event_id is not None

    event = await pg_engine.get_event(event_id)
    assert event is not None
    assert event.content == "Paris is the capital of France"


async def test_promote_lifecycle(pg_engine):
    """Store, then promote from tentative to stable."""
    from prme.types import LifecycleState

    _event_id = await pg_engine.store(
        "Water boils at 100 degrees Celsius",
        user_id="integration-test-user",
        node_type=NodeType.FACT,
    )

    nodes = await pg_engine.query_nodes(user_id="integration-test-user")
    target = [n for n in nodes if "boils" in n.content]
    assert len(target) >= 1

    node = target[0]
    assert node.lifecycle_state == LifecycleState.TENTATIVE

    await pg_engine.promote(str(node.id))
    promoted = await pg_engine.get_node(str(node.id))
    assert promoted is not None
    assert promoted.lifecycle_state == LifecycleState.STABLE


async def test_config_backend_property():
    """Verify config.backend dispatches correctly."""
    config_duckdb = PRMEConfig()
    assert config_duckdb.backend == "duckdb"

    config_pg = PRMEConfig(database_url="postgresql://localhost/test")
    assert config_pg.backend == "postgres"


async def test_reformulation_receives_the_extraction_connection_settings():
    """The PostgreSQL engine hands reformulation the extraction endpoint and credential."""
    from pydantic import SecretStr

    from prme.config import ExtractionConfig

    config = PRMEConfig(
        database_url=os.environ["PRME_TEST_DATABASE_URL"],
        extraction=ExtractionConfig(
            provider="openai",
            model="example-model",
            api_key=SecretStr("configured-key"),
            base_url="https://gateway.invalid/v1",
            timeout=7.5,
        ),
    )
    engine = await MemoryEngine.create(config)
    try:
        pipeline = engine._retrieval_pipeline
        assert pipeline._query_reformulation_provider == "openai"
        assert pipeline._query_reformulation_model == "example-model"
        assert pipeline._query_reformulation_api_key.get_secret_value() == "configured-key"
        assert pipeline._query_reformulation_base_url == "https://gateway.invalid/v1"
        assert pipeline._query_reformulation_timeout == 7.5
    finally:
        await engine.close()
