"""ER03/ER04 CLI subchecks through the owned engine and current ledger."""
import asyncio
from dataclasses import replace
import json
import sys
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.cli import build_parser
from prme.privacy.cli import command, erasure_command
from prme.privacy.erasure import ErasureRequest
from prme.privacy.policy import PrivacyDenied
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


def engine_factory():
    raise AssertionError("explicit test-owned factory required")


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER03_ER04_cli_current_subject_denial_and_managed_status(tmp_path, layout, analyzer, native, monkeypatch, capsys):
    import structlog
    monkeypatch.setattr(structlog._config._CONFIG, "logger_factory", structlog.PrintLoggerFactory(file=sys.stderr))
    engine, runtime, policy, custody, *_ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    config, owner = engine._config, str(policy.owner_id)
    event = await engine.store("Mara likes tea. CLI source indigo.", retrieval_content="Mara likes tea.", user_id=owner)
    positive = await engine.store("Mara likes apples.", user_id=owner)
    entered, release = asyncio.Event(), asyncio.Event()
    get, paused = engine._privacy.repository.get, False
    async def loaded(*args, **kwargs):
        nonlocal paused
        result = await get(*args, **kwargs)
        if not paused:
            paused = True
            entered.set()
            await release.wait()
        return result
    async def existing():
        return engine
    monkeypatch.setattr("tests.test_privacy_erasure_cli.engine_factory", existing)
    monkeypatch.setattr(engine._privacy.repository, "get", loaded)
    reference = "tests.test_privacy_erasure_cli:engine_factory"
    args = build_parser().parse_args(["privacy-keys", "unlock", "--registry", str(custody.path),
        "--project", str(policy.project_id), "--owner", owner, "--engine-factory", reference,
        "--event", event, "--audit", str(tmp_path / "audit.jsonl"), "--action", "reveal"])
    task = asyncio.create_task(command(args))
    request = ErasureRequest(request_id=uuid4(), event_ids=(UUID(event),))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        await engine.request_erasure(request, user_id=owner)
        before = native.reads
        release.set()
        with pytest.raises(PrivacyDenied, match="privacy_erased"):
            await asyncio.wait_for(task, 10)
        assert native.reads == before and engine._closed
        assert "CLI source indigo" not in capsys.readouterr().out
        opened = []
        async def fresh():
            value = await MemoryEngine.create(config, embedding_provider=runtime.embedder, privacy_runtime=runtime)
            opened.append(value)
            return value
        monkeypatch.setattr("tests.test_privacy_erasure_cli.engine_factory", fresh)
        base = ["--owner", owner, "--engine-factory", reference]
        await erasure_command(build_parser().parse_args(["privacy", "status", *base, "--request", str(request.request_id)]))
        assert json.loads(capsys.readouterr().out)["state"] == "blocked_from_access"
        await erasure_command(build_parser().parse_args(["privacy", "purge", *base, "--budget-ms", "60000"]))
        [status] = json.loads(capsys.readouterr().out)
        assert status["state"] == "live_purged" and not status["physical_wipe"]
        assert status["backup_copies"] == status["external_copies"] == "not_managed"
        assert all(value._closed for value in opened)
        engine = await fresh()
        assert (await engine.get_event_nodes(positive, user_id=owner))[0].content == "Mara likes apples."
        await engine.close()
        def synchronous():
            from prme import MemoryClient
            from prme.privacy.ledger import LocalErasureLedger
            with MemoryClient(config=config, embedding_provider=runtime.embedder,
                    privacy_runtime=replace(runtime, erasure_ledger=LocalErasureLedger(runtime.erasure_ledger.path))) as client:
                request = ErasureRequest(request_id=uuid4(), event_ids=(UUID(positive),))
                assert client.request_erasure(request, user_id=owner).state == "blocked_from_access"
                assert client.get_erasure_status(request.request_id, user_id=owner).logical_live_copies == "pending"
                assert all(status.state == "live_purged" for status in client.process_erasures(user_id=owner, budget_ms=60000))
                assert client.expire_privacy(user_id=owner, budget_ms=60000)
        await asyncio.to_thread(synchronous)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.close()
