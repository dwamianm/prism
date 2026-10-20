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
        memory_request = ErasureRequest(request_id=uuid4(), event_ids=(UUID(erased),))
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
        operations = await repository._query("SELECT op_type,payload FROM operations", ())
        assert any(kind == "PRIVACY_ERASED" for kind, _ in operations)
        assert not any("Mara likes tea." in json.dumps(payload) for _, payload in operations)
        assert any("Mara likes apples." in json.dumps(payload) for _, payload in operations)
    finally:
        await engine.close()
