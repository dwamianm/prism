"""Actual closed exports; component evidence, not full EM08 restoration proof."""
import json

import pytest

from prme.privacy.portability import PrivacyPortabilityError
from prme.storage.privacy_export import export_protected_pack, read_protected_export
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_closed_default_and_active_exports(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    source_only, memory = "SOURCE_ONLY_EXPORT_326", "Mara likes tea."
    try:
        event = await engine.store(memory + " " + source_only, retrieval_content=memory, user_id=str(policy.owner_id))
        record = await engine._privacy.repository.get(event, user_id=str(policy.owner_id))
        native_reads = native.reads
        async def forbidden(*args, **kwargs):
            pytest.fail("ordinary export attempted private unwrap")
        monkeypatch.setattr(runtime.protector, "open", forbidden)
        for mode in ("protected", "active_memory_only"):
            path = tmp_path / (mode + ".json")
            manifest = await export_protected_pack(engine, path, mode=mode)
            bundle = read_protected_export(path)
            raw = path.read_bytes()
            assert source_only.encode() not in raw and b"BEGIN PRIVATE KEY" not in raw
            assert native.reads == native_reads
            assert manifest.admitted_memory_is_sensitive and not manifest.contains_private_keys
            assert [node.content for node in bundle.nodes] == [memory]
            if mode == "protected":
                assert bundle.admissions[0].evidence == record.evidence
                assert len(bundle.events) == 1 and bundle.operations and bundle.embeddings
            else:
                assert not bundle.events and not bundle.operations and not bundle.work and not bundle.embeddings
                assert bundle.admissions[0].evidence is None and "restoration" in manifest.omitted
            before = raw
            with pytest.raises(PrivacyPortabilityError, match="^privacy_destination_occupied$"):
                await export_protected_pack(engine, path, mode=mode)
            assert path.read_bytes() == before
        # Unknown payload fields and copied source paragraphs are not silently
        # admitted by a permissive generic JSON exporter.
        await engine._privacy.repository._query(
            "UPDATE operations SET payload=? WHERE op_type='DIRECT_STORE_REQUESTED'",
            (json.dumps({"unexpected_source": source_only}),))
        refused = tmp_path / "refused.json"
        with pytest.raises(PrivacyPortabilityError):
            await export_protected_pack(engine, refused)
        assert not refused.exists()
    finally:
        await engine.close()
