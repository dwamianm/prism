"""ER02 real managed purge, including positive copies before and after erasure."""
from dataclasses import replace
import json
from uuid import UUID, uuid4

import pytest

from prme.privacy.erasure import ErasureRequest
from prme.privacy.policy import PrivacyDenied
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


async def admit_foreign(engine, runtime, policy, custody):
    from prme.privacy.keys import KeyBoundary
    from prme.privacy.local_keys import public_registry
    foreign = policy.model_copy(update={"owner_id": uuid4()})
    boundary = KeyBoundary(project_id=foreign.project_id, owner_id=foreign.owner_id, scope=foreign.scope.value)
    for purpose in ("evidence", "processing"):
        await custody.provision_wrapping(boundary, purpose)
    runtime.protector.registry = public_registry(custody.path)
    # The injected per-owner policy is explicit. A policy override cannot
    # attenuate another owner's configured default into a new owner.
    original = engine._privacy.default
    engine._privacy.default = None
    try:
        event = await engine.store("Ravi grows plums. Foreign source turquoise.", retrieval_content="Ravi grows plums.", user_id=str(foreign.owner_id), policy=foreign)
    finally:
        engine._privacy.default = original
    return foreign, event


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER02_evidence_and_memory_purge_preserve_unrelated_owned_memory(tmp_path, layout, analyzer, native):
    engine, runtime, policy, custody, *_ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner = str(policy.owner_id)
    try:
        erased = await engine.store("Mara likes tea. Synthetic source indigo.", retrieval_content="Mara likes tea.", user_id=owner)
        retained = await engine.store("Mara likes apples. Synthetic source amber.", retrieval_content="Mara likes apples.", user_id=owner)
        node = (await engine.get_event_nodes(erased, user_id=owner))[0]
        other = (await engine.get_event_nodes(retained, user_id=owner))[0]
        from prme.organizer.summarization import _materialize_summary_node, SummarizationLevel
        mixed, _ = await _materialize_summary_node(engine, SummarizationLevel.DAILY, "2026-10-20", [node, other], owner)
        assert "tea" in mixed.content and "apples" in mixed.content
        foreign, foreign_event = await admit_foreign(engine, runtime, policy, custody)
        from prme.models.relevance import RelevanceSubmission, AnswerCitationSubmission
        response = await engine.retrieve("tea", user_id=owner, min_score=0)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=owner)
        assert receipt.manifest.dependencies and receipt.candidates
        candidate = next(value for value in receipt.candidates if value.in_context and value.has_content)
        label = await engine.record_relevance(RelevanceSubmission(request_id=receipt.request_id, labels={candidate.node_id: True}), user_id=owner)
        citation = await engine.record_answer_citations(AnswerCitationSubmission(request_id=receipt.request_id, answer_id=str(uuid4()), cited_node_ids=(candidate.node_id,)), user_id=owner)
        assert label and citation
        await engine.reinforce(str(other.id), user_id=owner, evidence_id=erased)
        reinforced = await engine._graph_store.get_node(str(other.id))
        assert UUID(erased) in reinforced.evidence_refs and UUID(retained) in reinforced.evidence_refs
        repository = engine._privacy.repository
        before = await repository.get(erased, user_id=owner)
        assert before.evidence is not None
        assert await repository._query("SELECT id FROM nodes WHERE id=?", (str(node.id),))
        assert await repository._query("SELECT id FROM operations WHERE target_id=?", (erased,))
        if layout != "postgres":
            assert await repository._query("SELECT node_id FROM vector_metadata WHERE node_id=?", (str(node.id),))
        source_request = ErasureRequest(request_id=uuid4(), mode="evidence", event_ids=(UUID(erased),))
        await engine.request_erasure(source_request, user_id=owner)
        [source_status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert source_status.failure is None, source_status
        assert source_status.state == "live_purged" and source_status.facts_may_remain
        assert (await repository.get(erased, user_id=owner)).evidence is None
        retained_views = await engine.get_event_nodes(erased, user_id=owner)
        assert any(value.id == node.id and value.content == "Mara likes tea." for value in retained_views)
        assert (await engine.inspect_source(erased, user_id=owner)).state == "erased"
        assert await engine.get_node(str(mixed.id), user_id=owner) is not None
        memory_request = ErasureRequest(request_id=uuid4(), node_ids=(node.id,))
        await engine.request_erasure(memory_request, user_id=owner)
        statuses = await engine.process_erasures(user_id=owner, budget_ms=60000)
        final = next(status for status in statuses if status.request_id == memory_request.request_id)
        assert final.failure is None, final
        assert final.state == "live_purged" and not final.facts_may_remain
        assert not final.physical_wipe and final.backup_copies == final.external_copies == "not_managed"
        with pytest.raises(PrivacyDenied, match="privacy_erased"):
            await engine.get_event_nodes(erased, user_id=owner)
        survivor = (await engine.get_event_nodes(retained, user_id=owner))[0]
        assert survivor.id == other.id and UUID(erased) not in survivor.evidence_refs
        assert not await repository._query("SELECT id FROM nodes WHERE id=?", (str(node.id),))
        assert await engine._graph_store.get_node(str(mixed.id)) is None
        assert (await engine.get_event_nodes(foreign_event, user_id=str(foreign.owner_id)))[0].content == "Ravi grows plums."
        assert await engine.get_retrieval_receipt(str(receipt.request_id), user_id=owner) is None
        assert await engine.get_relevance(str(label.feedback_id), user_id=owner) is None
        assert await engine.get_answer_citations(str(citation.citation_id), user_id=owner) is None
        operations = await repository._query("SELECT op_type,payload FROM operations", ())
        assert any(kind == "PRIVACY_ERASED" for kind, _ in operations)
        assert not any("Mara likes tea." in json.dumps(payload) for _, payload in operations)
        assert any("Mara likes apples." in json.dumps(payload) for _, payload in operations)
    finally:
        await engine.close()
