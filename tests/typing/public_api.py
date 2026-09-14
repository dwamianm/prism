"""Static consumer contract; checked by mypy rather than executed."""

from datetime import datetime, timezone
from typing import assert_type

from prme import AnswerCitationRecord, AnswerCitationSubmission, RelevanceRecord, RelevanceSubmission, RetrievalReceipt, ExtractionRecord, ExtractionStatus, ExtractionProcessingResult, MemoryClient, RetrievalResponse
from prme.models import Event, MemoryNode, ProcessingResult
from prme.organizer.models import OrganizeResult


def consume(client: MemoryClient) -> None:
    assert_type(client.promote("node-id", user_id="alice"), None)
    assert_type(client.archive("node-id", user_id="alice"), None)
    assert_type(client.ingest("Alice used Rust yesterday", user_id="alice",
                              event_time=datetime(2024, 3, 10, tzinfo=timezone.utc),
                              metadata={"source": "import"}), str)
    assert_type(client.get_retrieval_receipt("request-id", user_id="alice"), RetrievalReceipt | None)
    assert_type(client.get_relevance("feedback-id", user_id="alice"), RelevanceRecord | None)
    assert_type(client.list_relevance(user_id="alice"), list[RelevanceRecord])
    assert_type(client.retrieve("preferences", user_id="alice"), RetrievalResponse)
    assert_type(client.get_node("node-id"), MemoryNode | None)
    assert_type(client.query_nodes(user_id="alice"), list[MemoryNode])
    assert_type(client.get_events("alice"), list[Event])
    assert_type(client.get_event("event-id", user_id="alice"), Event | None)
    assert_type(client.get_extraction("event-id", user_id="alice"), ExtractionRecord | None)
    assert_type(client.get_event_nodes("event-id", user_id="alice"), list[MemoryNode])
    assert_type(client.process_pending(user_id="alice"), ProcessingResult)
    assert_type(client.extraction_status("event-id", user_id="alice"), ExtractionStatus | None)
    assert_type(client.retry_extraction("event-id", user_id="alice", replan=True), ExtractionStatus | None)
    assert_type(client.process_extractions(user_id="alice"), ExtractionProcessingResult)
    assert_type(client.organize(user_id="alice"), OrganizeResult)
    for node in client.iter_nodes(user_id="alice"):
        assert_type(node, MemoryNode)


def submit_relevance(client: MemoryClient, submission: RelevanceSubmission) -> None:
    assert_type(client.record_relevance(submission, user_id="alice"), RelevanceRecord)


def submit_citations(client: MemoryClient, submission: AnswerCitationSubmission) -> None:
    assert_type(client.record_answer_citations(submission, user_id="alice"), AnswerCitationRecord)
    assert_type(client.get_answer_citations(str(submission.citation_id), user_id="alice"), AnswerCitationRecord | None)
    assert_type(client.list_answer_citations(user_id="alice"), list[AnswerCitationRecord])


async def postgres_workspace_consumer() -> None:
    from prme import MemoryWorkspace, NamespaceMemory, NamespaceInfo, PRMEConfig
    async with MemoryWorkspace.open_postgres(PRMEConfig(), name="app", max_connections=3) as workspace:
        assert_type(await workspace.list_namespaces(), list[NamespaceInfo])
        async with workspace.namespace("project") as memory:
            assert_type(memory, NamespaceMemory)
            assert_type(await memory.retrieve("query", user_id="alice"), RetrievalResponse)
