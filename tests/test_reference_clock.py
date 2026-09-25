"""Public historical-query clock contract, independent of benchmark labels."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from prme import MemoryEngine
from prme.retrieval.config import ScoringWeights
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_same_clock_replays_scores_and_is_logged(config, user):  # noqa: F811
    # Salience decay moves the weighted formula's score traces; rank fusion does not compute salience.
    config = config.model_copy(update={"scoring": ScoringWeights()})
    async with MemoryEngine.open(config) as engine:
        await engine.store("The release uses Python", user_id=user)
        base = datetime.now(timezone.utc) + timedelta(days=1)
        first = await engine.retrieve("release language", user_id=user, reference_time=base)
        later = await engine.retrieve("release language", user_id=user, reference_time=base + timedelta(days=365))
        again = await engine.retrieve("release language", user_id=user, reference_time=base)
        assert first.metadata.reference_time == base
        assert first.score_traces == again.score_traces
        assert [c.node.id for c in first.results] == [c.node.id for c in again.results]
        assert first.score_traces[0].salience > later.score_traces[0].salience
        if engine._conn is not None:
            payload = engine._conn.execute(
                "SELECT payload FROM operations WHERE target_id = ?",
                [str(first.metadata.request_id)],
            ).fetchone()[0]
            assert json.loads(payload)["reference_time"] == base.isoformat()


async def test_engine_rejects_naive_reference_time(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="reference_time must include a timezone"):
            await engine.retrieve("hello", user_id=user, reference_time=datetime(2024, 1, 1))


async def test_query_dates_do_not_exclude_later_ingested_evidence(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await engine.store(
            "We chose Python for the release", user_id=user,
            event_time=datetime(2024, 5, 10, tzinfo=timezone.utc),
        )
        result = await engine.retrieve(
            "What did we decide on May 10 2024?", user_id=user,
            reference_time=datetime(2024, 5, 12, tzinfo=timezone.utc),
        )
        assert result.metadata.candidates_generated["VECTOR"] > 0
        assert result.results
        assert result.filter_metadata.time_to is None


@pytest.mark.parametrize("query", ["decision notes", "How many decision notes are there?"])
async def test_explicit_validity_filter_covers_all_paths(config, user, query):  # noqa: F811
    from prme.types import Scope

    async with MemoryEngine.open(config) as engine:
        for scope in (Scope.PROJECT, Scope.PERSONAL):
            await engine.store("decision notes about Python", user_id=user, scope=scope, session_id="notes")
        result = await engine.retrieve(
            query, user_id=user, scope=Scope.PROJECT,
            time_to=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert result.results == []
        assert result.cross_scope_hints == []
