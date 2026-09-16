"""Answer-time memory citations are scoped, immutable, and retry-safe."""

import asyncio
import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from prme import AnswerCitationSubmission, MemoryEngine
from prme.storage.citations import CitationConflict
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def retrieve(engine: MemoryEngine, owner: str):
    await engine.store("The telescope is blue.", user_id=owner)
    response = await engine.retrieve("telescope", user_id=owner, min_score=0)
    receipt = await engine.get_retrieval_receipt(
        str(response.metadata.request_id), user_id=owner,
    )
    assert receipt is not None
    cited = next(
        candidate for candidate in receipt.candidates
        if candidate.in_context and candidate.has_content
    )
    return receipt, cited.node_id


async def test_answer_citations_survive_graph_changes_restart_and_exact_retry(config, user):
    citation_id = uuid4()
    async with MemoryEngine.open(config) as engine:
        receipt, node_id = await retrieve(engine, user)
        submission = AnswerCitationSubmission(
            citation_id=citation_id,
            request_id=receipt.request_id,
            answer_id="assistant-message-42",
            cited_node_ids=(node_id,),
            answer_sha256=hashlib.sha256(b"The telescope is blue.").hexdigest(),
            method="application_verified",
        )
        record = await engine.record_answer_citations(submission, user_id=user)
        assert record.receipt_checksum == receipt.checksum
        assert record.context_sha256 == receipt.context_sha256
        assert await engine.record_answer_citations(submission, user_id=user) == record
        assert await engine.get_answer_citations(str(citation_id), user_id=user + "-other") is None

        await engine.archive(str(node_id), user_id=user)
        assert await engine.record_answer_citations(submission, user_id=user) == record

    async with MemoryEngine.open(config) as engine:
        assert await engine.get_answer_citations(str(citation_id), user_id=user) == record
        assert await engine.list_answer_citations(user_id=user) == [record]
        assert await engine.list_answer_citations(user_id=user + "-other") == []
        assert await engine.record_answer_citations(submission, user_id=user) == record
        with pytest.raises(CitationConflict, match="different answer citation"):
            await engine.record_answer_citations(
                submission.model_copy(update={"answer_id": "different-answer"}),
                user_id=user,
            )


async def test_citations_require_owned_content_that_was_in_saved_context(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("telescope " * 1000, user_id=user)
        response = await engine.retrieve(
            "telescope", user_id=user, token_budget=400, min_score=0,
        )
        receipt = await engine.get_retrieval_receipt(
            str(response.metadata.request_id), user_id=user,
        )
        reference_only = next(
            candidate for candidate in receipt.candidates if not candidate.has_content
        )
        for request_id, owner, node_ids in (
            (receipt.request_id, user + "-other", (reference_only.node_id,)),
            (uuid4(), user, (reference_only.node_id,)),
            (receipt.request_id, user, (uuid4(),)),
            (receipt.request_id, user, (reference_only.node_id,)),
        ):
            with pytest.raises(ValueError):
                await engine.record_answer_citations(
                    AnswerCitationSubmission(
                        request_id=request_id,
                        answer_id="answer",
                        cited_node_ids=node_ids,
                    ),
                    user_id=owner,
                )
        assert await engine.list_answer_citations(user_id=user) == []
        assert await engine.list_answer_citations(user_id=user + "-other") == []


async def test_empty_citation_set_is_explicit_answer_telemetry(config, user):
    async with MemoryEngine.open(config) as engine:
        receipt, _ = await retrieve(engine, user)
        record = await engine.record_answer_citations(
            AnswerCitationSubmission(
                request_id=receipt.request_id,
                answer_id="answer-with-no-memory-support",
            ),
            user_id=user,
        )
        assert record.cited_node_ids == ()
        assert record.method == "model_reported"


def test_answer_citation_input_rejects_ambiguous_identity_and_digest():
    node_id = uuid4()
    for values in (
        {"answer_id": "   "},
        {"answer_id": "answer", "cited_node_ids": (node_id, node_id)},
        {"answer_id": "answer", "answer_sha256": "not-a-digest"},
    ):
        with pytest.raises(ValidationError):
            AnswerCitationSubmission(request_id=uuid4(), **values)


async def test_independent_connections_converge_on_one_citation_identity(config, user):
    from prme.storage.citations import CitationRepository

    async with MemoryEngine.open(config) as engine:
        receipt, node_id = await retrieve(engine, user)
        connection = None
        if config.backend == "duckdb":
            import duckdb

            connection = duckdb.connect(config.db_path)
            other = CitationRepository(conn=connection)
        else:
            other = CitationRepository(pool=engine._pool)
        try:
            for index in range(5):
                submission = AnswerCitationSubmission(
                    request_id=receipt.request_id,
                    answer_id=f"concurrent-answer-{index}",
                    cited_node_ids=(node_id,),
                )
                first, second = await asyncio.gather(
                    engine.record_answer_citations(submission, user_id=user),
                    other.record(submission, user_id=user),
                )
                assert first == second
        finally:
            if connection is not None:
                connection.close()
        assert len(await engine.list_answer_citations(user_id=user)) == 5
