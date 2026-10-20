"""Frozen PV-05 adapter subchecks; original erasure/export scenarios stay pending.

No authority fake here is counted as the actual PV-06 ledger integration gate.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import logging

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme.privacy.policy import PrivacyDenied
from tests.privacy_journal_support import (
    LAYOUTS,
    SOURCE,
    SOURCE_ONLY,
    journal_engine,
    processing_keys,
    operation_bytes,
    isolated_privacy_postgres,  # noqa: F401
    local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL08_component_diagnostics_and_actual_public_adapters(
    tmp_path, layout, analyzer, native, monkeypatch, caplog, capsys
):
    from prme.config import APIConfig, MCPConfig
    from prme.api.app import create_app
    from prme.ingestion.entity_merge import logger as entity_logger
    from prme.mcp.server import create_mcp_server
    from prme.privacy.diagnostics import protected_diagnostics
    from prme.storage import profile_publication

    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path, layout, analyzer
    )
    owner = str(policy.owner_id)
    error_type = type(SOURCE_ONLY, (RuntimeError,), {})
    caplog.set_level(logging.DEBUG)
    try:
        await engine.ingest(SOURCE, user_id=owner)
        with processing_keys(runtime, custody, processing):
            assert (await engine.process_extractions(user_id=owner)).processed == 1
        # Actual SQL publication is interrupted after validation. On DuckDB,
        # native staging has already completed under its real work fence.
        checkpoint = profile_publication._validate

        def fail_sql(*args):
            checkpoint(*args)
            raise error_type(SOURCE_ONLY)

        monkeypatch.setattr(profile_publication, "_validate", fail_sql)
        with pytest.raises(
            PrivacyDenied, match="privacy_processing_unavailable"
        ) as failed:
            await engine.consolidate_knowledge(user_id=owner, entity_names=["Mara"])
        assert SOURCE_ONLY not in str(failed.value)
        result = await engine.process_profiles(user_id=owner)
        assert result["failed"] == 1 and set(result["errors"].values()) == {
            "privacy_work_failed"
        }
        assert (await engine.profile_jobs(user_id=owner))[0][
            "last_error"
        ] == "privacy_work_failed"
        monkeypatch.setattr(profile_publication, "_validate", checkpoint)
        assert (await engine.process_profiles(user_id=owner))["processed"] == 1
        # Provider and malformed schema responses are separate real claimed
        # attempts. Neither exception class nor returned content is a status.
        original_extract = runtime.extractor.extract

        async def provider_error(content, snapshot, *, guard):
            async with guard():
                raise error_type(SOURCE_ONLY)

        for failure in ("provider", "schema"):
            event = await engine.ingest(SOURCE, user_id=owner)
            if failure == "provider":
                monkeypatch.setattr(runtime.extractor, "extract", provider_error)
            else:
                monkeypatch.setattr(runtime.extractor, "extract", original_extract)
                runtime.extractor.result = {"entities": SOURCE_ONLY}
            with processing_keys(runtime, custody, processing):
                assert (await engine.process_extractions(user_id=owner)).processed == 0
            status = await engine.extraction_status(event, user_id=owner)
            assert SOURCE_ONLY not in status.model_dump_json()
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
        # Same real protected organizer, through HTTP and MCP dispatch.
        config = engine._config.model_copy(
            update={
                "api": APIConfig(user_keys={owner: "fixture-owner"}),
                "mcp": MCPConfig(user_id=owner),
            }
        )
        engine._config = config
        app = create_app(
            config, privacy_runtime=runtime, embedding_provider=runtime.embedder
        )
        app.state.engine = engine
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
            headers={"Authorization": "Bearer fixture-owner"},
        ) as client:
            response = await client.post(
                "/v1/organize", json={"jobs": ["snapshot_generation"]}
            )
            assert response.status_code == 200, response.text
            assert (
                SOURCE_ONLY not in response.text
                and "snapshot_generation" in response.text
            )

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(
            server._mcp_server, raise_exceptions=True
        ) as session:
            await session.initialize()
            response = await session.call_tool(
                "memory_organize", {"jobs": "snapshot_generation"}
            )
            assert not response.isError
            body = json.loads(response.content[0].text)
            assert "error" not in body and SOURCE_ONLY not in json.dumps(body)
        # Context-local suppression must not mute concurrent legacy diagnostics.
        entered, legacy_finished = asyncio.Event(), asyncio.Event()

        async def protected_log():
            with protected_diagnostics():
                entered.set()
                await legacy_finished.wait()
                entity_logger.warning("private=%s", SOURCE_ONLY, exc_info=True)

        async def legacy_log():
            await entered.wait()
            entity_logger.info("legacy_diagnostic_positive_324")
            legacy_finished.set()

        await asyncio.gather(protected_log(), legacy_log())
        from tests.privacy_journal_support import logical_dump

        dumped = await logical_dump(engine, tmp_path / "managed-logical-rows.jsonl")
        assert SOURCE_ONLY.encode() not in dumped
        native_close = engine._vector_index.close

        async def fail_native_close():
            await native_close()
            raise error_type(SOURCE_ONLY)

        monkeypatch.setattr(engine._vector_index, "close", fail_native_close)
        await engine.close()
        captured = capsys.readouterr()
        combined = caplog.text + captured.out + captured.err
        assert "legacy_diagnostic_positive_324" in combined
        assert SOURCE_ONLY not in combined
        # The original observed protected extraction name log is also closed.
        assert "name=Mara" not in captured.out + captured.err
        for artifact in tmp_path.rglob("*"):
            if artifact.is_file():
                assert SOURCE_ONLY.encode() not in artifact.read_bytes(), artifact.name
    finally:
        await engine.close()
