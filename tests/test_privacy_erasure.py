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


@pytest.mark.parametrize("layout", ["directory", "postgres"])
async def test_ER02_scoped_impact_is_read_only_and_confirms_actual_closure(tmp_path, layout, analyzer, native, monkeypatch):
    """Existing ER02 mixed-support family; no held ordinary reader execution."""
    from datetime import timedelta
    import hashlib
    import os
    from pathlib import Path
    from urllib.parse import urlsplit
    from prme import MemoryEngine
    from prme.privacy.erasure import ErasureIntent
    from prme.privacy.journals import JOURNAL_KEY
    from prme.privacy.retry_commitments import NativeRetryCommitter
    from prme.storage.privacy_migration import migrate_privacy_pack
    from prme.types import Scope
    from tests.privacy_journal_support import logical_dump
    from tests.privacy_portability_support import fresh_config
    from tests.test_privacy_migration import mixed_legacy_family, FIXTURE

    template, runtime, policy, custody, *_ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    runtime = replace(runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    owner = str(policy.owner_id)
    # The established migration route supplies real admitted generated records.
    # It is fixture setup, not a public organizer/classifier success claim.
    async with fresh_config(base, tmp_path / "legacy", "directory") as original:
        async with MemoryEngine.open(original.model_copy(update={"privacy_policy": None}), embedding_provider=runtime.embedder) as source:
            direct, fact, entity, generated, visual, survivor = await mixed_legacy_family(source, runtime, owner)
        async with fresh_config(base, tmp_path / "target", "directory") as target:
            if layout == "postgres":
                url = os.environ["PRME_TEST_DATABASE_URL"]
                parts = urlsplit(url)
                assert parts.hostname == "127.0.0.1" and parts.port == 55432 and parts.path == "/privacy_engine_325_impact_test"
                target = type(target).model_validate({**target.model_dump(), "database_url": url})
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                job_path=tmp_path / "job", unavailable_legacy_history=True, allow_reembedding=True,
                remove_event_ids=() if visual is None else (visual.id,))
            async with MemoryEngine.open(target, privacy_runtime=runtime, embedding_provider=runtime.embedder) as engine:
                repo, erasure = engine._privacy.repository, engine._privacy.erasure
                event_id = UUID(str(direct.evidence_refs[0]))
                independent_event = await engine.store("Mara grows basil. Independent source bronze.",
                    retrieval_content="Mara grows basil.", user_id=owner)
                candidates = await repo._query("SELECT id,evidence_refs FROM nodes WHERE user_id=?", (owner,))
                independent_id = next(UUID(str(identifier)) for identifier, refs in candidates
                    if UUID(independent_event) in {UUID(str(value)) for value in (json.loads(refs) if isinstance(refs, str) else refs)})
                await engine.reinforce(str(independent_id), evidence_id=str(event_id), user_id=owner)
                foreign, foreign_event = await admit_foreign(engine, runtime, policy, custody)
                expected_nodes = {direct.id, fact.id, *(item[0].id for item in generated)}
                raw = await engine._graph_store.get_node(str(independent_id))
                assert event_id in raw.evidence_refs and UUID(independent_event) in raw.evidence_refs
                request = ErasureRequest(request_id=uuid4(), node_ids=(direct.id,))
                evidence_request = ErasureRequest(request_id=uuid4(), mode="evidence", event_ids=(event_id,))
                all_request = ErasureRequest(request_id=uuid4(), owner_all=True)
                before = await logical_dump(engine, tmp_path / "before.json")
                _, head = await erasure.repository.state()
                ledger_bytes = Path(runtime.erasure_ledger.path).read_bytes()
                registry_bytes = Path(custody.path).read_bytes()
                native_before = (native.reads, native.writes, dict(native.values))
                async def forbidden(*args, **kwargs):
                    raise AssertionError("preview attempted durable authority mutation or unwrap")
                with monkeypatch.context() as patch:
                    patch.setattr(runtime.erasure_ledger, "append_intent", forbidden)
                    patch.setattr(erasure.repository, "apply", forbidden)
                    patch.setattr(runtime.protector, "open", forbidden)
                    impact = await engine.preview_erasure(request, user_id=owner)
                    assert await engine.preview_erasure(request.model_copy(update={"request_id": uuid4()}), user_id=owner) == impact
                    evidence = await engine.preview_erasure(evidence_request, user_id=owner)
                    assert evidence.counts.model_dump() == {"sources": 1, "memories": 0, "derivatives": 0, "independent_survivors": 0}
                    assert evidence.facts_may_remain and not evidence.physical_wipe
                    assert impact.counts.model_dump() == {"sources": 1, "memories": 6, "derivatives": 5, "independent_survivors": 1}
                    assert not impact.facts_may_remain and impact.ledger_head == head
                    whole_owner = await engine.preview_erasure(all_request, user_id=owner)
                    assert whole_owner.counts.model_dump() == {"sources": 4, "memories": 9, "derivatives": 6, "independent_survivors": 0}
                    summary = await engine.preview_erasure(ErasureRequest(request_id=uuid4(), node_ids=(generated[-1][0].id,)), user_id=owner)
                    assert summary.counts.sources == 2 and summary.counts.memories == 7
                    for invalid, invalid_owner in ((request, str(foreign.owner_id)),
                            (request.model_copy(update={"scope": Scope.PROJECT}), owner),
                            (ErasureRequest(request_id=uuid4(), event_ids=(UUID(foreign_event),)), owner)):
                        with pytest.raises(PrivacyDenied, match="^privacy_boundary_mismatch$"):
                            await engine.preview_erasure(invalid, user_id=invalid_owner)
                assert await logical_dump(engine, tmp_path / "after.json") == before
                assert Path(runtime.erasure_ledger.path).read_bytes() == ledger_bytes
                assert Path(custody.path).read_bytes() == registry_bytes
                assert (native.reads, native.writes, native.values) == native_before
                assert await erasure.repository.intents() == ()
                output = impact.model_dump_json() + evidence.model_dump_json()
                for value in (*FIXTURE["admitted"], FIXTURE["source_only"], FIXTURE["prohibited"]):
                    assert value not in output and hashlib.sha256(value.encode()).hexdigest() not in output

                # Invalid typed dependencies refuse before any count or append.
                summary_id = generated[-1][0].id
                original_metadata = (await repo._query("SELECT metadata FROM nodes WHERE id=?", (str(summary_id),)))[0][0]
                metadata = json.loads(original_metadata) if isinstance(original_metadata, str) else original_metadata
                foreign_node = (await repo._query("SELECT id FROM nodes WHERE user_id=?", (str(foreign.owner_id),)))[0][0]
                for bad_id in (uuid4(), UUID(str(foreign_node))):
                    changed = json.loads(json.dumps(metadata))
                    changed[JOURNAL_KEY]["source_node_ids"].append(str(bad_id))
                    await repo._query("UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(changed), str(summary_id)))
                    with pytest.raises(PrivacyDenied, match="^privacy_migration_required$"):
                        await engine.preview_erasure(ErasureRequest(request_id=uuid4(), node_ids=(summary_id,)), user_id=owner)
                await repo._query("UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(metadata), str(summary_id)))
                assert await erasure.repository.intents() == ()

                # The migrated family has no extraction work; establish a real
                # queued source before freezing the generation comparison.
                pending_source = await engine.ingest("Mara enjoys pears. Queued source copper.", user_id=owner)
                generation_before = await engine.preview_erasure(request, user_id=owner)
                claim = await engine._event_store.extraction_work.claim(user_id=owner, event_id=pending_source)
                assert claim is not None and claim.generation > 0
                changed = await engine.preview_erasure(request, user_id=owner)
                assert changed.counts == generation_before.counts == impact.counts
                assert changed.revision != generation_before.revision
                with pytest.raises(PrivacyDenied, match="^privacy_erasure_conflict$"):
                    await engine.request_erasure(request, user_id=owner, expected_impact=generation_before)
                with pytest.raises(PrivacyDenied, match="^privacy_erasure_conflict$"):
                    await engine.request_erasure(all_request, user_id=owner, expected_impact=changed)
                stamp = runtime.clock.now
                runtime.clock.now = stamp + timedelta(hours=1)
                with pytest.raises(PrivacyDenied, match="^privacy_erasure_conflict$"):
                    await engine.request_erasure(request, user_id=owner, expected_impact=changed)
                runtime.clock.now = stamp
                runtime.authority.revisions[policy.policy_id] = policy.revision + 1
                with pytest.raises(PrivacyDenied, match="^privacy_stale_policy$"):
                    await engine.request_erasure(request, user_id=owner, expected_impact=changed)
                runtime.authority.revisions.clear()
                assert await erasure.repository.intents() == ()
                async with runtime.erasure_ledger.guard():
                    assert await runtime.erasure_ledger.current_head(head.store_id) == head
                    pending_request = ErasureRequest(request_id=uuid4(), mode="evidence", event_ids=(UUID(str(fact.evidence_refs[0])),))
                    pending = await runtime.erasure_ledger.append_intent(ErasureIntent(request=pending_request, store_id=head.store_id,
                        project_id=runtime.project_id, owner_id=policy.owner_id, created_at=runtime.clock()))
                before_pending = await logical_dump(engine, tmp_path / "pending-before.json")
                with pytest.raises(PrivacyDenied, match="^privacy_ledger_stale$"):
                    await engine.preview_erasure(request, user_id=owner)
                assert await logical_dump(engine, tmp_path / "pending-after.json") == before_pending
                with pytest.raises(PrivacyDenied, match="^privacy_erasure_conflict$"):
                    await engine.request_erasure(request, user_id=owner, expected_impact=changed)
                async with runtime.erasure_ledger.guard():
                    assert await runtime.erasure_ledger.current_head(head.store_id) == pending.head
                assert len(await erasure.repository.intents()) == 1
                confirmed = await engine.preview_erasure(request, user_id=owner)
                actual = request.model_copy(update={"request_id": uuid4()})
                ack = await engine.request_erasure(actual, user_id=owner, expected_impact=confirmed)
                assert ack.state == "blocked_from_access"
                assert await engine.request_erasure(actual, user_id=owner, expected_impact=confirmed) == ack
                statuses = await engine.process_erasures(user_id=owner, budget_ms=60000)
                final = next(status for status in statuses if status.request_id == actual.request_id)
                assert final.failure is None and final.state == "live_purged", final
                remaining = {UUID(str(row[0])) for row in await repo._query("SELECT id FROM nodes", ())}
                assert not remaining.intersection(expected_nodes)
                assert remaining == {survivor.id, entity.id, independent_id, UUID(str(foreign_node))}
                kept = await engine._graph_store.get_node(str(independent_id))
                assert kept.content == "Mara grows basil." and event_id not in kept.evidence_refs
                assert UUID(independent_event) in kept.evidence_refs
                assert (await engine._graph_store.get_node(str(survivor.id))).content == survivor.content
                assert (await engine._graph_store.get_node(str(entity.id))).content == entity.content
                assert await engine.request_erasure(actual, user_id=owner, expected_impact=confirmed) == final
