"""Regression tests for issue #60.

Stages that run after Stage 4 (session context expansion, cross-scope hints)
and the two supplementary lexical scans used to append nodes without
re-applying the scope, bi-temporal, and epistemic filters. Each test here
drives the full engine and asserts a specific leak stays closed.

The existing scope tests do not cover these paths because they never issue a
proper-noun query (which triggers entity expansion) or an aggregation query
(which triggers the exhaustive keyword scan).
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine
from prme.types import EpistemicType, Scope


@pytest.fixture
def leak_config():
    with tempfile.TemporaryDirectory(prefix="prme_leak_") as d:
        tmp = Path(d)
        lexical_path = tmp / "lexical_index"
        lexical_path.mkdir()
        yield PRMEConfig(
            db_path=str(tmp / "memory.duckdb"),
            vector_path=str(tmp / "vectors.usearch"),
            lexical_path=str(lexical_path),
        )


@pytest_asyncio.fixture
async def engine(leak_config):
    eng = await MemoryEngine.create(leak_config)
    yield eng
    await eng.close()


async def test_entity_and_aggregation_scans_respect_scope(engine):
    """A scoped aggregation query must not return other scopes' nodes."""
    await engine.store(
        "Project Falcon ships in November.",
        user_id="bob", session_id="s1", scope=Scope.PROJECT,
    )
    await engine.store(
        "Project Falcon is my secret personal side hustle.",
        user_id="bob", session_id="s2", scope=Scope.PERSONAL,
    )

    # Proper noun plus "how many" exercises both entity expansion and the
    # exhaustive aggregation keyword scan.
    response = await engine.retrieve(
        "How many times was Project Falcon mentioned?",
        user_id="bob",
        scope=Scope.PROJECT,
    )

    assert response.results, "expected the in-scope node to be retrieved"
    assert all(c.node.scope == Scope.PROJECT for c in response.results)


async def test_session_expansion_respects_epistemic_filter(engine):
    """HYPOTHETICAL neighbours must not ride into the bundle on expansion."""
    await engine.store(
        "The quarterly budget review is scheduled for Tuesday.",
        user_id="alice", session_id="s1",
    )
    await engine.store(
        "Hypothetically we could move the whole company to Mars next year.",
        user_id="alice", session_id="s1",
        epistemic_type=EpistemicType.HYPOTHETICAL,
    )

    response = await engine.retrieve("quarterly budget review", user_id="alice")

    assert response.results
    assert all(
        c.node.epistemic_type != EpistemicType.HYPOTHETICAL
        for c in response.results
    )
    assert "Mars" not in str(response.bundle.model_dump())


async def test_session_expansion_respects_knowledge_at(engine):
    """A point-in-time query must not surface post-cutoff session turns."""
    await engine.store(
        "The quarterly budget review is scheduled for Tuesday.",
        user_id="alice", session_id="s1",
    )
    cutoff = datetime.now(timezone.utc)
    await asyncio.sleep(0.05)
    await engine.store(
        "Correction: the budget review moved to Friday.",
        user_id="alice", session_id="s1",
    )

    response = await engine.retrieve(
        "quarterly budget review", user_id="alice", knowledge_at=cutoff,
    )

    assert response.results
    assert all(c.node.created_at <= cutoff for c in response.results)


async def test_session_expansion_respects_scope(engine):
    """Expansion must not pull adjacent turns from outside the scope filter."""
    await engine.store(
        "Project Falcon ships in November.",
        user_id="bob", session_id="shared", scope=Scope.PROJECT,
    )
    await engine.store(
        "Unrelated personal note about the November holidays.",
        user_id="bob", session_id="shared", scope=Scope.PERSONAL,
    )

    response = await engine.retrieve(
        "shipping date", user_id="bob", scope=Scope.PROJECT,
    )

    assert all(c.node.scope == Scope.PROJECT for c in response.results)


async def test_cross_scope_hints_respect_epistemic_filter(engine):
    """Hints cross the scope boundary; they do not cross the epistemic one."""
    await engine.store(
        "Project Falcon ships in November.",
        user_id="bob", session_id="p1", scope=Scope.PROJECT,
    )
    await engine.store(
        "Hypothetically Project Falcon might get cancelled and rebranded.",
        user_id="bob", session_id="p2", scope=Scope.PERSONAL,
        epistemic_type=EpistemicType.HYPOTHETICAL,
    )

    response = await engine.retrieve(
        "Project Falcon shipping date", user_id="bob", scope=Scope.PROJECT,
    )

    assert all(
        c.node.epistemic_type != EpistemicType.HYPOTHETICAL
        for c in response.cross_scope_hints
    )


async def test_unscoped_retrieval_still_sees_every_scope(engine):
    """The scope forwarding must not filter when no scope was requested."""
    await engine.store(
        "Project Falcon ships in November.",
        user_id="bob", session_id="s1", scope=Scope.PROJECT,
    )
    await engine.store(
        "Project Falcon is my personal side hustle.",
        user_id="bob", session_id="s2", scope=Scope.PERSONAL,
    )

    response = await engine.retrieve(
        "How many times was Project Falcon mentioned?", user_id="bob",
    )

    scopes = {c.node.scope for c in response.results}
    assert scopes == {Scope.PROJECT, Scope.PERSONAL}
