"""CL04/CL05 extend the existing exact copy workflows with real erasure."""
from dataclasses import replace
from datetime import timedelta
import inspect
from uuid import uuid4

import pytest

from prme.privacy.erasure import ErasureRequest
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, logical_dump,
    isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401
from tests.test_privacy_erasure import admit_foreign


async def _workflow_and_purge(function, path, layout, analyzer, native, monkeypatch):
    """Reuse every original setup/assertion; retain its engine for the purge."""
    from tests import test_privacy_journal_records as workflows
    values, close = None, None
    async def opened(*args, **kwargs):
        nonlocal values, close
        values = await journal_engine(*args, **kwargs)
        engine, runtime, policy, custody, *_ = values
        runtime = replace(runtime, key_custody=custody)
        engine._privacy.runtime = runtime
        values = (engine, runtime, policy, custody, *values[4:])
        organize = engine.organize
        async def complete_fixture_jobs(**arguments):
            # Exercise the same public jobs with a finite explicit test budget.
            # Keep every original assertion and report any real skipped job.
            result = await organize(**dict(arguments, budget_ms=60000))
            assert not result.jobs_skipped, result
            return result
        monkeypatch.setattr(engine, "organize", complete_fixture_jobs)
        close = engine.close
        async def keep_fixture_open():
            pass
        monkeypatch.setattr(engine, "close", keep_fixture_open)
        return values
    try:
        with monkeypatch.context() as patch:
            patch.setattr(workflows, "journal_engine", opened)
            arguments = dict(tmp_path=path, layout=layout, analyzer=analyzer, native=native, monkeypatch=patch)
            await function(**{name: arguments[name] for name in inspect.signature(function).parameters})
        engine, runtime, policy, custody, *_ = values
        owner = str(policy.owner_id)
        before = await engine._privacy.repository._query("SELECT content FROM nodes WHERE user_id=?", (owner,))
        markers = {str(row[0]).encode() for row in before if row[0]}
        assert markers and await engine._privacy.repository._query("SELECT id FROM operations", ())
        foreign, event = await admit_foreign(engine, runtime, policy, custody)
        request = ErasureRequest(request_id=uuid4(), owner_all=True)
        await engine.request_erasure(request, user_id=owner)
        runtime.clock.now += timedelta(seconds=1)
        positive = await engine.store("Nora bakes bread. Future admitted source.", retrieval_content="Nora bakes bread.", user_id=owner)
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert status.state == "live_purged" and status.failure is None, status
        assert (await engine.get_event_nodes(positive, user_id=owner))[0].content == "Nora bakes bread."
        assert (await engine.get_event_nodes(event, user_id=str(foreign.owner_id)))[0].content == "Ravi grows plums."
        assert len(await engine._privacy.repository._query("SELECT id FROM nodes WHERE user_id=?", (owner,))) == 1
        dumped = await logical_dump(engine, path / "post-erasure-managed-rows.jsonl")
        assert not any(marker in dumped for marker in markers)
        assert b'PRIVACY_ERASED' in dumped
    finally:
        if close is not None:
            await close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL04_actual_snapshot_families_are_erased(tmp_path, layout, analyzer, native, monkeypatch):
    from tests.test_privacy_journal_records import test_CL04_component_actual_lifecycle_snapshots
    await _workflow_and_purge(test_CL04_component_actual_lifecycle_snapshots, tmp_path, layout, analyzer, native, monkeypatch)


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("workflow", ("profile_admitted_entity_and_keyless_recovery", "summary_generations_and_retirement", "alias_review_and_merge_use_closed_copies"))
async def test_CL05_actual_profile_summary_alias_generations_are_erased(tmp_path, layout, workflow, analyzer, native, monkeypatch):
    from tests import test_privacy_journal_records as workflows
    function = getattr(workflows, "test_CL05_component_" + workflow)
    await _workflow_and_purge(function, tmp_path, layout, analyzer, native, monkeypatch)
