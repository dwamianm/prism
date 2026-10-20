"""CL04 component snapshots on real backends; full source erasure is PV-06."""

from datetime import timedelta
import json
from uuid import uuid4

import pytest

from prme.privacy.journals import SnapshotEnvelope
from prme.types import LifecycleState
from tests.privacy_journal_support import (
    LAYOUTS,
    MEMORY,
    SOURCE,
    SOURCE_ONLY,
    journal_engine,
    isolated_privacy_postgres,
    local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL04_component_actual_lifecycle_snapshots(
    tmp_path, layout, analyzer, native
):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        event_id = await engine.store(SOURCE, retrieval_content=MEMORY, user_id=owner)
        node = (await engine.get_event_nodes(event_id, user_id=owner))[0]
        node_id = str(node.id)
        # A separately classified legacy-shaped metadata leaf must not become
        # a snapshot through one of these mutation siblings.
        raw = await engine._graph_store.get_node(node_id)
        metadata = {**raw.metadata, "evidence_quote": SOURCE_ONLY}
        await engine._privacy.repository._query(
            "UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(metadata), node_id)
        )
        request = str(uuid4())
        await engine.promote(node_id, user_id=owner, request_id=request)
        await engine.promote(node_id, user_id=owner, request_id=request)
        await engine.reinforce(
            node_id, user_id=owner, evidence_id=event_id, request_id=str(uuid4())
        )
        assert (
            await engine.get_node(node_id, user_id=owner)
        ).lifecycle_state == LifecycleState.STABLE
        await engine.archive(node_id, user_id=owner)
        rows = await engine._privacy.repository._query(
            "SELECT payload FROM operations WHERE op_type IN ('LIFECYCLE_CHANGED','REINFORCE')",
            (),
        )
        assert len(rows) == 3
        for row in rows:
            payload = row["payload"] if isinstance(row, dict) else row[0]
            envelope = SnapshotEnvelope.model_validate_json(
                payload if isinstance(payload, str) else json.dumps(payload)
            )
            assert (
                envelope.manifest.dependencies
                and node.id in envelope.manifest.admitted_node_ids
            )
            assert MEMORY in envelope.record and SOURCE_ONLY not in envelope.record
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL04_component_ttl_tombstone_contains_identity_only(
    tmp_path, layout, analyzer, native
):
    from prme.storage.retention import ProtectedRetentionRecord, read_record

    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        event_id = await engine.store(
            SOURCE, retrieval_content=MEMORY, user_id=owner, ttl_days=1
        )
        node = (await engine.get_event_nodes(event_id, user_id=owner))[0]
        assert await engine._graph_store.archive_expired(
            str(node.id),
            user_id=owner,
            evaluated_at=node.created_at + timedelta(days=2),
        )
        rows = await engine._privacy.repository._query(
            "SELECT payload FROM operations WHERE op_type='TOMBSTONE_SWEEP'", ()
        )
        assert len(rows) == 1
        payload = rows[0]["payload"] if isinstance(rows[0], dict) else rows[0][0]
        record = read_record(payload)
        assert isinstance(record, ProtectedRetentionRecord)
        assert record.before.id == node.id and record.original_replay == "unavailable"
        assert (
            MEMORY not in record.model_dump_json()
            and SOURCE_ONLY not in record.model_dump_json()
        )
        assert "content_hash_of_deleted" not in record.model_dump_json()
    finally:
        await engine.close()
