"""CL06 component checks, using actual guarded retrieval and SQL receipt writers."""

import hashlib
from uuid import uuid4

import pytest

from prme.models.relevance import AnswerCitationSubmission, RelevanceSubmission
from prme.models.privacy_receipts import (
    ProtectedCitationRecord,
    ProtectedRetrievalReceipt,
)
from prme.privacy.policy import PrivacyDenied
from tests.privacy_journal_support import (
    LAYOUTS,
    MEMORY,
    SOURCE,
    SOURCE_ONLY,
    journal_engine,
    operation_bytes,
    isolated_privacy_postgres,
    local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL06_component_minimized_receipt_labels_and_citations(
    tmp_path, layout, analyzer, native
):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    query = "tea for alice@example.com"
    try:
        await engine.store(SOURCE, retrieval_content=MEMORY, user_id=owner)
        response = await engine.retrieve(query, user_id=owner, min_score=0)
        assert response.metadata.receipt_persisted and response.results
        receipt = await engine.get_retrieval_receipt(
            str(response.metadata.request_id), user_id=owner
        )
        assert isinstance(receipt, ProtectedRetrievalReceipt)
        assert receipt.replay_ranking() == tuple(
            item.node.id for item in response.results
        )
        assert receipt.manifest.dependencies
        candidate = next(
            item for item in receipt.candidates if item.in_context and item.has_content
        )
        labels = RelevanceSubmission(
            request_id=receipt.request_id, labels={candidate.node_id: True}
        )
        label = await engine.record_relevance(labels, user_id=owner)
        assert label == await engine.record_relevance(labels, user_id=owner)
        assert (
            await engine.get_relevance(str(label.feedback_id), user_id=owner) == label
        )
        submission = AnswerCitationSubmission(
            request_id=receipt.request_id,
            answer_id=str(uuid4()),
            cited_node_ids=(candidate.node_id,),
        )
        citation = await engine.record_answer_citations(submission, user_id=owner)
        assert isinstance(citation, ProtectedCitationRecord)
        assert citation == await engine.record_answer_citations(
            submission, user_id=owner
        )
        assert (
            await engine.get_answer_citations(str(citation.citation_id), user_id=owner)
            == citation
        )
        assert await engine.list_answer_citations(user_id=owner) == [citation]
        with pytest.raises(PrivacyDenied):
            await engine.record_answer_citations(
                submission.model_copy(update={"answer_id": SOURCE_ONLY}), user_id=owner
            )
        with pytest.raises(PrivacyDenied):
            await engine.record_answer_citations(
                submission.model_copy(update={"answer_sha256": "0" * 64}), user_id=owner
            )
        with pytest.raises(PrivacyDenied):
            await engine.evaluate_learning(user_id=owner)
        raw = await operation_bytes(engine)
        for marker in (
            SOURCE_ONLY,
            query,
            "alice@example.com",
            hashlib.sha256(query.encode()).hexdigest(),
            hashlib.sha256(MEMORY.encode()).hexdigest(),
            hashlib.sha256(response.bundle.render().encode()).hexdigest(),
        ):
            assert marker.encode() not in raw
        assert "query" not in receipt.model_dump()
        assert "execution" not in receipt.model_dump()
    finally:
        await engine.close()
