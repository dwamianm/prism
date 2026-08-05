"""Tests for background opportunistic maintenance scheduling (issue #62).

The pass used to be awaited inside retrieve()/ingest(), putting its full time
budget on the caller's latency. It now runs as a tracked background task that
engine.close() drains.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

from prme.config import OrganizerConfig, PRMEConfig
from prme.organizer.maintenance import MaintenanceRunner
from prme.organizer.models import MaintenanceResult
from prme.storage.engine import MemoryEngine


class _StubEngine:
    """Minimal stand-in: the runner only needs an object to hold on to."""


@pytest.fixture
def runner():
    return MaintenanceRunner(_StubEngine(), OrganizerConfig())


async def test_schedule_returns_before_the_pass_completes(runner, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow():
        started.set()
        await release.wait()
        return MaintenanceResult()

    monkeypatch.setattr(runner, "_run_maintenance", _slow)

    t0 = time.perf_counter()
    runner.schedule()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 50, "schedule() must not wait for the pass"
    await started.wait()
    assert runner._task is not None and not runner._task.done()

    release.set()
    await runner.drain()
    assert runner._task.done()


async def test_schedule_does_not_stack_passes(runner, monkeypatch):
    release = asyncio.Event()
    calls = 0

    async def _slow():
        nonlocal calls
        calls += 1
        await release.wait()
        return MaintenanceResult()

    monkeypatch.setattr(runner, "_run_maintenance", _slow)

    runner.schedule()
    first_task = runner._task
    await asyncio.sleep(0)
    runner.schedule()
    runner.schedule()

    assert runner._task is first_task

    release.set()
    await runner.drain()
    assert calls == 1


async def test_schedule_respects_the_cooldown(runner, monkeypatch):
    calls = 0

    async def _fast():
        nonlocal calls
        calls += 1
        return MaintenanceResult()

    monkeypatch.setattr(runner, "_run_maintenance", _fast)

    runner.schedule()
    await runner.drain()
    runner.schedule()
    await runner.drain()

    assert calls == 1, "second pass is inside the cooldown window"


async def test_schedule_is_a_noop_when_disabled(monkeypatch):
    runner = MaintenanceRunner(
        _StubEngine(), OrganizerConfig(opportunistic_enabled=False)
    )

    async def _fail():
        raise AssertionError("maintenance must not run when disabled")

    monkeypatch.setattr(runner, "_run_maintenance", _fail)

    runner.schedule()
    await runner.drain()
    assert runner._task is None


async def test_background_failure_does_not_escape(runner, monkeypatch, caplog):
    async def _boom():
        raise RuntimeError("maintenance exploded")

    monkeypatch.setattr(runner, "_run_maintenance", _boom)

    runner.schedule()
    await runner.drain()

    assert runner._task is not None and runner._task.exception() is None
    assert runner._last_maintained_at > 0


async def test_drain_without_a_scheduled_pass(runner):
    await runner.drain()  # must not raise


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine():
    with tempfile.TemporaryDirectory(prefix="prme_maint_") as d:
        tmp = Path(d)
        lexical_path = tmp / "lexical_index"
        lexical_path.mkdir()
        eng = await MemoryEngine.create(PRMEConfig(
            db_path=str(tmp / "memory.duckdb"),
            vector_path=str(tmp / "vectors.usearch"),
            lexical_path=str(lexical_path),
        ))
        yield eng
        if not eng._closed:
            await eng.close()


async def test_retrieve_does_not_await_maintenance(engine, monkeypatch):
    release = asyncio.Event()
    ran = asyncio.Event()

    async def _slow():
        ran.set()
        await release.wait()
        return MaintenanceResult()

    monkeypatch.setattr(engine._maintenance_runner, "_run_maintenance", _slow)

    await engine.store("a note about the parser", user_id="alice")
    await engine.retrieve("parser", user_id="alice")

    # The pass is in flight, not finished, and retrieve() already returned.
    await ran.wait()
    assert not engine._maintenance_runner._task.done()

    release.set()
    await engine.close()


async def test_close_drains_the_scheduled_pass(engine, monkeypatch):
    finished = False

    async def _pass():
        nonlocal finished
        await asyncio.sleep(0.05)
        finished = True
        return MaintenanceResult()

    monkeypatch.setattr(engine._maintenance_runner, "_run_maintenance", _pass)

    await engine.store("a note about the parser", user_id="alice")
    await engine.retrieve("parser", user_id="alice")
    await engine.close()

    assert finished, "close() must wait for the in-flight maintenance pass"
