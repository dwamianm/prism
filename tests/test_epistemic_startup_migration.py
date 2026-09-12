"""Opening a pack must preserve explicit modern epistemic assignments."""

from uuid import uuid4

import duckdb
import pytest

from prme import MemoryEngine
from prme.types import EpistemicType, NodeType, SourceType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_restart_preserves_explicit_epistemic_type_and_node_snapshot(config, user):
    async with MemoryEngine.open(config) as engine:
        event = await engine.store(
            "If approved, Alice might use Rust", user_id=user, node_type=NodeType.FACT,
            epistemic_type=EpistemicType.HYPOTHETICAL, source_type=SourceType.SYSTEM_INFERRED,
            confidence=0.27, metadata={"condition": "approval required"},
        )
        original = (await engine.get_event_nodes(event, user_id=user))[0]
    for _ in range(2):
        async with MemoryEngine.open(config) as engine:
            restored = (await engine.get_event_nodes(event, user_id=user))[0]
            assert restored.model_dump(mode="json") == original.model_dump(mode="json")


async def test_legacy_missing_column_is_backfilled_once_without_changing_confidence(config, user):
    if config.backend != "duckdb":
        pytest.skip("PostgreSQL schema shipped with explicit epistemic columns")
    node_id = str(uuid4())
    with duckdb.connect(config.db_path) as conn:
        conn.execute("""
            CREATE TABLE nodes (
                id UUID PRIMARY KEY, node_type VARCHAR NOT NULL, user_id VARCHAR NOT NULL,
                session_id VARCHAR, scope VARCHAR DEFAULT 'personal', content VARCHAR NOT NULL,
                metadata JSON, confidence FLOAT DEFAULT 0.5, salience FLOAT DEFAULT 0.5,
                lifecycle_state VARCHAR DEFAULT 'tentative', valid_from TIMESTAMPTZ DEFAULT current_timestamp,
                valid_to TIMESTAMPTZ, superseded_by UUID, evidence_refs JSON,
                created_at TIMESTAMPTZ DEFAULT current_timestamp, updated_at TIMESTAMPTZ DEFAULT current_timestamp
            )
        """)
        conn.execute(
            "INSERT INTO nodes (id, node_type, user_id, content, confidence) VALUES (?, 'preference', ?, 'Alice prefers Vim', 0.37)",
            [node_id, user],
        )
    async with MemoryEngine.open(config) as engine:
        first = await engine.get_node(node_id)
        assert first.epistemic_type == EpistemicType.ASSERTED
        assert first.confidence == pytest.approx(0.37)
        assert first.metadata["_epistemic_backfill"] is True
    async with MemoryEngine.open(config) as engine:
        assert (await engine.get_node(node_id)).model_dump(mode="json") == first.model_dump(mode="json")
