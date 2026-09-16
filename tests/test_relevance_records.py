"""Scoped learning inputs describe actual saved retrievals across restart."""
import asyncio
import hashlib
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.models.relevance import RelevanceSubmission
from prme.types import Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def capture(engine, user):
    await engine.store("The telescope is blue.", user_id=user, scope=Scope.PROJECT)
    await engine.store("Another owner's private source", user_id=user + "-other")
    response = await engine.retrieve("telescope", user_id=user, scope=Scope.PROJECT, min_score=0)
    assert response.results and response.metadata.receipt_persisted
    receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
    assert receipt is not None
    return response, receipt


async def test_pipeline_adds_only_evidence_backed_guidance_by_default(config, user):
    reference_time = datetime(2026, 9, 14, tzinfo=timezone.utc)
    async with MemoryEngine.open(config) as engine:
        await engine.store(
            "I returned from the trip one week ago.",
            user_id=user,
            event_time=reference_time,
        )
        temporal = await engine.retrieve(
            "How many days ago did I return from the trip?",
            user_id=user,
            min_score=0,
            reference_time=reference_time,
            include_cross_scope=False,
        )
        assert temporal.bundle.context_guidance is not None
        assert temporal.bundle.render().startswith("QUESTION TIME: 2026-09-14T00:00:00+00:00")
        assert temporal.metadata.aggregation_coverage is None
        saved = await engine.get_retrieval_receipt(
            str(temporal.metadata.request_id), user_id=user
        )
        assert saved.schema_version == 12
        assert saved.packing.context_guidance_mode == "temporal"
        assert saved.context_sha256 == hashlib.sha256(temporal.bundle.render().encode()).hexdigest()

        current = await engine.retrieve(
            "Which trip do I remember right now?",
            user_id=user,
            min_score=0,
            reference_time=reference_time,
            include_cross_scope=False,
        )
        assert current.bundle.context_guidance is None


async def test_pipeline_context_guidance_can_be_disabled(config, user):
    config = config.model_copy(update={
        "packing": config.packing.model_copy(update={"context_guidance_mode": "off"})
    })
    reference_time = datetime(2026, 9, 14, tzinfo=timezone.utc)
    async with MemoryEngine.open(config) as engine:
        await engine.store("The trip ended yesterday.", user_id=user, event_time=reference_time)
        response = await engine.retrieve(
            "How many days ago did the trip end?",
            user_id=user,
            min_score=0,
            reference_time=reference_time,
            include_cross_scope=False,
        )
        assert response.bundle.context_guidance is None
        saved = await engine.get_retrieval_receipt(
            str(response.metadata.request_id), user_id=user
        )
        assert saved.schema_version == 12
        assert saved.packing.context_guidance_mode == "off"


async def test_pipeline_experimental_guidance_requires_explicit_opt_in(config, user):
    config = config.model_copy(update={
        "packing": config.packing.model_copy(update={"context_guidance_mode": "all"})
    })
    async with MemoryEngine.open(config) as engine:
        await engine.store("I enjoy quiet neighborhood restaurants.", user_id=user)
        response = await engine.retrieve(
            "What restaurant should I choose?",
            user_id=user,
            min_score=0,
            include_cross_scope=False,
        )
        assert response.bundle.context_guidance is not None
        assert response.bundle.context_guidance.startswith("PERSONALIZATION TASK:")
        saved = await engine.get_retrieval_receipt(
            str(response.metadata.request_id), user_id=user
        )
        assert saved.packing.context_guidance_mode == "all"


@pytest.mark.parametrize("ordering", ["density", "score", "balanced"])
async def test_saved_receipt_and_labels_survive_graph_change_and_restart(config, user, ordering):
    config = config.model_copy(update={"packing": config.packing.model_copy(update={"multipath_ordering": ordering})})
    async with MemoryEngine.open(config) as engine:
        response, receipt = await capture(engine, user)
        assert receipt.context_sha256 == hashlib.sha256(response.bundle.render().encode()).hexdigest()
        assert receipt.scoring.version_id == response.metadata.scoring_config_version
        assert receipt.reference_time == response.metadata.reference_time
        assert receipt.scopes == (Scope.PROJECT,)
        assert receipt.schema_version == 12
        assert receipt.packing.multipath_ordering == ordering
        assert receipt.replay_ranking() == tuple(r.node.id for r in response.results)
        assert [(c.node_id, c.score, c.trace) for c in receipt.candidates] == [
            (r.node.id, r.composite_score, r.score_trace) for r in response.results]
        assert all(c.has_content for c in receipt.candidates if c.in_context)
        nid = receipt.candidates[0].node_id
        submission = RelevanceSubmission(request_id=receipt.request_id, labels={nid: True}, surface="context")
        record = await engine.record_relevance(submission, user_id=user)
        assert record.receipt_checksum == receipt.checksum
        assert await engine.get_retrieval_receipt(str(receipt.request_id), user_id=user + "-other") is None
        assert await engine.get_relevance(str(record.feedback_id), user_id=user + "-other") is None
        await engine._graph_store.update_node(str(nid), confidence_base=0.2, metadata={"changed_after_retrieval": True})
        await engine.archive(str(nid), user_id=user)
        # Judgments and snapshots describe the original exposure, not today's graph.
        assert await engine.record_relevance(submission, user_id=user) == record
    async with MemoryEngine.open(config) as engine:
        assert await engine.get_retrieval_receipt(str(receipt.request_id), user_id=user) == receipt
        assert await engine.get_relevance(str(record.feedback_id), user_id=user) == record
        assert await engine.list_relevance(user_id=user) == [record]
        assert await engine.list_relevance(user_id=user + "-other") == []
        assert engine._config.scoring.version_id == receipt.scoring.version_id
        assert len(engine._feedback_tracker) == 0


@pytest.mark.parametrize("version", [1, 2, 3, 4])
async def test_legacy_receipt_retains_checksum_and_accepts_feedback(config, user, version):
    import json
    from pathlib import Path
    raw = (Path(__file__).parent / f"fixtures/relevance/receipt-v{version}.json").read_text()
    original = json.loads(raw)
    request_id = str(uuid4())
    raw = raw.replace(json.dumps(original["user_id"]), json.dumps(user)).replace(original["request_id"], request_id)
    original_checksum = hashlib.sha256(raw.encode()).hexdigest()
    async with MemoryEngine.open(config) as engine:
        await engine._relevance._query(
            "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
            "VALUES ($1, 'RETRIEVAL_REQUEST', $2, $3, $4, now())",
            str(uuid4()), request_id,
            json.dumps({"receipt": raw, "receipt_checksum": original_checksum}), user)
        saved = await engine.get_retrieval_receipt(request_id, user_id=user)
        assert saved.schema_version == version and saved.checksum == original_checksum
        record = await engine.record_relevance(RelevanceSubmission(request_id=saved.request_id,
            labels={saved.candidates[0].node_id: True}), user_id=user)
    async with MemoryEngine.open(config) as engine:
        restored = await engine.get_relevance(str(record.feedback_id), user_id=user)
        assert restored.receipt_checksum == original_checksum
        assert await engine.get_retrieval_receipt(request_id, user_id=user + "-other") is None


async def test_pipeline_receipt_replays_neural_session_expansion_after_restart(config, user, monkeypatch):
    from prme.retrieval import pipeline
    from prme.retrieval.reranker import CrossEncoderReranker

    original_generate = pipeline.generate_candidates

    async def trigger_only(*args, **kwargs):
        candidates, counts = await original_generate(*args, **kwargs)
        return [c for c in candidates if "trigger" in c.node.content], counts

    async with MemoryEngine.open(config) as engine:
        await engine.store("telescope trigger", user_id=user, scope=Scope.PROJECT, session_id="session")
        await engine.store("telescope adjacent answer", user_id=user, scope=Scope.PROJECT, session_id="session")
        await engine.store("telescope foreign scope", user_id=user, scope=Scope.PERSONAL, session_id="session")
        monkeypatch.setattr(pipeline, "generate_candidates", trigger_only)
        reranker = CrossEncoderReranker()
        monkeypatch.setattr(reranker, "_predict_sync", Mock(return_value=[.9]))
        engine._retrieval_pipeline._reranker = reranker
        response = await engine.retrieve("What telescope do I currently use?", user_id=user, scope=Scope.PROJECT)
        assert response.metadata.receipt_persisted
        saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert saved.ranking_policy == "score_id"
        assert len(saved.candidates) == 2
        assert saved.replay_ranking() == tuple(c.node.id for c in response.results)
        assert all(c.scope == Scope.PROJECT for c in saved.candidates)
        inherited = next(c for c in response.results if "SESSION_CONTEXT" in c.paths)
        p = saved.score_provenance[inherited.node.id]
        assert p.weights.w_recency == .25
        assert [op.kind for op in p.adjustments] == ["neural_blend", "session_decay"]
    async with MemoryEngine.open(config) as engine:
        restored = await engine.get_retrieval_receipt(str(saved.request_id), user_id=user)
        assert restored.checksum == saved.checksum
        assert restored.replay_ranking() == saved.replay_ranking()


async def test_concurrent_retries_converge_and_conflicting_reuse_is_rejected(config, user):
    async with MemoryEngine.open(config) as engine:
        _, receipt = await capture(engine, user)
        submission = RelevanceSubmission(request_id=receipt.request_id, labels={receipt.candidates[0].node_id: True})
        first, second = await asyncio.gather(*(engine.record_relevance(submission, user_id=user) for _ in range(2)))
        assert first == second
        with pytest.raises(ValueError, match="different relevance"):
            await engine.record_relevance(submission.model_copy(update={"labels": {receipt.candidates[0].node_id: False}}), user_id=user)
        assert await engine.list_relevance(user_id=user) == [first]


async def test_invalid_or_foreign_relevance_writes_nothing(config, user):
    async with MemoryEngine.open(config) as engine:
        _, receipt = await capture(engine, user)
        own_node = receipt.candidates[0].node_id
        for request_id, owner, labels in [
            (receipt.request_id, user + "-other", {own_node: True}),
            (uuid4(), user, {own_node: True}),
            (receipt.request_id, user, {uuid4(): False}),
        ]:
            with pytest.raises(ValueError):
                await engine.record_relevance(RelevanceSubmission(request_id=request_id, labels=labels), user_id=owner)
        assert await engine.list_relevance(user_id=user) == []
        assert await engine.list_relevance(user_id=user + "-other") == []


async def test_receipt_logging_failure_is_visible_without_failing_retrieval(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue", user_id=user)
        monkeypatch.setattr("prme.models.relevance.make_receipt", Mock(side_effect=OSError("receipt logging unavailable")))
        response = await engine.retrieve("telescope", user_id=user, min_score=0)
        assert response.results and not response.metadata.receipt_persisted
        assert await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user) is None


async def test_context_positive_labels_cannot_credit_reference_only_entries(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("telescope " * 1000, user_id=user)
        response = await engine.retrieve("telescope", user_id=user, token_budget=400, min_score=0)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        candidate = next(c for c in receipt.candidates if c.in_context)
        assert not candidate.has_content
        with pytest.raises(ValueError, match="positive labels require source content"):
            await engine.record_relevance(RelevanceSubmission(request_id=receipt.request_id,
                labels={candidate.node_id: True}, surface="context"), user_id=user)
        accepted = await engine.record_relevance(RelevanceSubmission(request_id=receipt.request_id,
            labels={candidate.node_id: False}, surface="context"), user_id=user)
        assert accepted.labels == {candidate.node_id: False}


async def test_uuid_pagination_is_scoped_and_validated(config, user):
    async with MemoryEngine.open(config) as engine:
        _, receipt = await capture(engine, user)
        records = [await engine.record_relevance(RelevanceSubmission(request_id=receipt.request_id,
            labels={receipt.candidates[0].node_id: bool(i % 2)}), user_id=user) for i in range(3)]
        expected = sorted(records, key=lambda r: str(r.feedback_id))
        first = await engine.list_relevance(user_id=user, limit=2)
        second = await engine.list_relevance(user_id=user, limit=2, after_id=str(first[-1].feedback_id))
        assert first + second == expected
        for limit in (0, -1, True, 1001):
            with pytest.raises(ValueError, match="limit"):
                await engine.list_relevance(user_id=user, limit=limit)


def test_relevance_labels_are_explicit_booleans():
    from pydantic import ValidationError
    for labels in ({}, {uuid4(): "yes"}, {uuid4(): 1}):
        with pytest.raises(ValidationError):
            RelevanceSubmission(request_id=uuid4(), labels=labels)


async def test_receipt_corruption_is_rejected_and_legacy_logs_remain_unattributed(config, user):
    import json
    async with MemoryEngine.open(config) as engine:
        _, receipt = await capture(engine, user)
        await engine._relevance._query(
            "UPDATE operations SET payload = $1 WHERE op_type = 'RETRIEVAL_REQUEST' AND target_id = $2 AND actor_id = $3",
            json.dumps({"receipt": receipt.model_dump_json(), "receipt_checksum": "0" * 64}), str(receipt.request_id), user)
        with pytest.raises(ValueError, match="checksum"):
            await engine.get_retrieval_receipt(str(receipt.request_id), user_id=user)
        legacy_id = str(uuid4())
        await engine._relevance._query(
            "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
            "VALUES ($1, 'RETRIEVAL_REQUEST', $2, $3, $4, now())",
            str(uuid4()), legacy_id, json.dumps({"request_id": legacy_id}), user)
        assert await engine.get_retrieval_receipt(legacy_id, user_id=user) is None


async def test_process_exit_after_feedback_commit_preserves_original_receipt(config, user, tmp_path):
    import os
    import subprocess
    import sys
    if config.backend != "duckdb":
        pytest.skip("Local process-exit recovery; PostgreSQL uses atomic insert/retry checks")
    feedback_id = str(uuid4())
    request_path = tmp_path / "request-id.txt"
    script = '''
import asyncio, os, sys
from pathlib import Path
from prme import MemoryEngine, PRMEConfig, RelevanceSubmission
from tests.test_durable_ingestion import MockEmbeddingProvider
import prme.storage.engine as module
module.create_embedding_provider = lambda _: MockEmbeddingProvider()
async def main():
    db, vector, lexical, user, fid, rid_path = sys.argv[1:]
    config = PRMEConfig(database_url=None, encryption_enabled=False, db_path=db,
                        vector_path=vector, lexical_path=lexical,
                        organizer={"opportunistic_enabled": False},
                        extraction={"provider": "ollama", "model": "unused"})
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue", user_id=user)
        response = await engine.retrieve("telescope", user_id=user, min_score=0)
        assert response.metadata.receipt_persisted
        rid = str(response.metadata.request_id)
        Path(rid_path).write_text(rid)
        original = engine._relevance._query
        async def crash(sql, *args):
            result = await original(sql, *args)
            if sql.startswith("INSERT INTO operations"):
                os._exit(43)
            return result
        engine._relevance._query = crash
        await engine.record_relevance(RelevanceSubmission(request_id=rid, feedback_id=fid,
            labels={response.results[0].node.id: True}), user_id=user)
asyncio.run(main())
'''
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, config.db_path,
        config.vector_path, config.lexical_path, user, feedback_id, str(request_path)],
        capture_output=True, timeout=60, env=os.environ.copy())
    assert result.returncode == 43, result.stderr.decode()
    async with MemoryEngine.open(config) as engine:
        receipt = await engine.get_retrieval_receipt(request_path.read_text(), user_id=user)
        record = await engine.get_relevance(feedback_id, user_id=user)
        assert record is not None and record.receipt_checksum == receipt.checksum
        assert await engine.record_relevance(RelevanceSubmission(request_id=receipt.request_id,
            feedback_id=feedback_id, labels=record.labels), user_id=user) == record
        assert await engine.list_relevance(user_id=user) == [record]


async def test_independent_connections_record_one_retry_identity(config, user):
    from prme.storage.relevance import RelevanceRepository
    async with MemoryEngine.open(config) as engine:
        _, receipt = await capture(engine, user)
        connection = None
        if config.backend == "duckdb":
            import duckdb
            connection = duckdb.connect(config.db_path)
            other = RelevanceRepository(conn=connection)
        else:
            other = RelevanceRepository(pool=engine._pool)
        try:
            for _ in range(5):
                submission = RelevanceSubmission(request_id=receipt.request_id, labels={receipt.candidates[0].node_id: True})
                first, second = await asyncio.gather(engine.record_relevance(submission, user_id=user), other.record(submission, user_id=user))
                assert first == second
        finally:
            if connection is not None:
                connection.close()
        assert len(await engine.list_relevance(user_id=user)) == 5
