"""ER01 exact source/memory deadlines before maintenance and across restart."""
from dataclasses import replace
from datetime import timedelta

import pytest

from prme import MemoryEngine
from prme.privacy import MemoryRetention, PrivacyDenied
from prme.privacy.ledger import LocalErasureLedger
from prme.privacy.local_keys import public_registry
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401
from tests.test_privacy_source_routing import selection


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER01_exact_expiry_precedes_purge_and_startup_recovery(tmp_path, layout, analyzer, native):
    engine, runtime, policy, custody, evidence, *_ = await journal_engine(
        tmp_path, layout, analyzer, evidence_lifetime_seconds=10,
        memory_retention=MemoryRetention(mode="erase_after", seconds=20),
    )
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner = str(policy.owner_id)
    try:
        event = await engine.store("Mara likes tea. Synthetic source orchid.", retrieval_content="Mara likes tea.", user_id=owner)
        row = await engine._privacy.repository.get(event, user_id=owner)
        grant = await selection(engine, runtime, owner, (event,), kind="diagnostic")
        with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            runtime.clock.now = row.snapshot.evidence_deadline - timedelta(microseconds=1)
            assert "orchid" in (await engine.read_source(event, user_id=owner, access=grant)).content
            runtime.clock.now = row.snapshot.evidence_deadline
            with pytest.raises(PrivacyDenied):
                await engine.read_source(event, user_id=owner, access=grant)
        # No maintenance has run: ciphertext is present, yet disclosure denied.
        assert (await engine._privacy.repository.get(event, user_id=owner)).evidence is not None
        assert (await engine.get_event_nodes(event, user_id=owner))[0].content == "Mara likes tea."
        runtime.clock.now = row.snapshot.memory_deadline
        with pytest.raises(PrivacyDenied):
            await engine.get_event_nodes(event, user_id=owner)
        config = engine._config
    finally:
        runtime.protector.registry = public_registry(custody.path)
        await engine.close()
    runtime = replace(runtime, erasure_ledger=LocalErasureLedger(runtime.erasure_ledger.path))
    reopened = await MemoryEngine.create(config, embedding_provider=runtime.embedder, privacy_runtime=runtime)
    try:
        with pytest.raises(PrivacyDenied):
            await reopened.get_event_nodes(event, user_id=owner)
        assert (await reopened._privacy.repository.get(event, user_id=owner)).evidence is None
        assert not await reopened._privacy.repository._query("SELECT id FROM nodes WHERE user_id=?", (owner,))
        assert await reopened._privacy.repository._query("SELECT request_id FROM privacy_erasure_jobs", ())
    finally:
        await reopened.close()
