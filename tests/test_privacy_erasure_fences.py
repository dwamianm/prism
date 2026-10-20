"""Production ledger/backend fence subchecks; model and policy inputs are fixtures."""
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy.erasure import ErasureRequest
from prme.privacy.ledger import LocalErasureLedger
from prme.privacy.policy import PrivacyDenied
from tests.privacy_fakes import open_engine
from tests.privacy_source_support import backend_layout, isolated_privacy_postgres  # noqa: F401


async def test_exact_source_fence_is_durable_and_keeps_same_owner_positive(tmp_path, layout):
    engine, runtime, policy = await open_engine(tmp_path, layout=layout)
    owner = str(policy.owner_id)
    try:
        event = await engine.store("Synthetic erased memory navy blue", user_id=owner)
        unaffected = await engine.store("Synthetic retained memory apple green", user_id=owner)
        original = await engine._privacy.repository.get(event, user_id=owner)
        assert len(await engine.get_event_nodes(event, user_id=owner)) == 1
        request = ErasureRequest(request_id=uuid4(), event_ids=(UUID(event),))
        status = await engine.request_erasure(request, user_id=owner)
        assert status.state == "blocked_from_access" and not status.physical_wipe
        assert await engine.request_erasure(request, user_id=owner) == status
        with pytest.raises(PrivacyDenied, match="privacy_erased"):
            async with engine._privacy.guard(original.snapshot, "publication", records=(original,)):
                pytest.fail("stale publication entered")
        with pytest.raises(PrivacyDenied, match="privacy_erased"):
            await engine.get_event_nodes(event, user_id=owner)
        assert len(await engine.get_event_nodes(unaffected, user_id=owner)) == 1
        config = engine._config
    finally:
        await engine.close()
    # Independent reload, no in-memory deny set or restored ledger in the pack.
    runtime = replace(runtime, erasure_ledger=LocalErasureLedger(runtime.erasure_ledger.path))
    engine = await MemoryEngine.create(config, embedding_provider=runtime.embedder, privacy_runtime=runtime)
    try:
        with pytest.raises(PrivacyDenied, match="privacy_erased"):
            await engine.get_event_nodes(event, user_id=owner)
        assert len(await engine.get_event_nodes(unaffected, user_id=owner)) == 1
    finally:
        await engine.close()
