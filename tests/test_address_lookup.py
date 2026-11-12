"""The address read returns the same state as the full scan, and the stored columns agree with it.

Bar 1 of task 23 in its repository form. The probe that covers 300 pairs on a 20,500 fact pack
lives outside the suite, because it needs that pack; these tests cover the same equality on a
small store, and the pieces the probe relies on:

1. the two paths return the same serialized ``AssertionState`` over the shapes that can differ:
   no claim, one claim, several, retired, a case and spacing variant, a second owner, and a page
   boundary reached with ``batch_size=1``;
2. the stored address matches ``normalize_assertion_value`` of the stored metadata, and a node
   with no usable claim stores NULL for both keys;
3. a store without the columns falls back to the full scan and returns the same state;
4. the privacy marker says exactly what the range check asks, on a plain row and on a marked one.
"""

from datetime import datetime, timezone

import duckdb
import pytest

from prme import AssertionStateQuery, MemoryEngine
from prme.retrieval.aggregation import normalize_assertion_value
from prme.retrieval.temporal import get_assertion_state, get_assertion_state_by_address
from prme.storage.assertion_keys import (
    PREDICATE_COLUMN,
    PRIVACY_COLUMN,
    SUBJECT_COLUMN,
    assertion_key,
    privacy_marker,
)
from prme.types import NodeType, Scope
from tests import conftest, test_durable_ingestion
from tests.privacy_journal_support import (  # noqa: F401
    local_analyzer_fixture,
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.fixture
def product_defaults(monkeypatch):
    """The product's own defaults, for the tests that build a store from the configuration."""
    conftest.product_defaults(monkeypatch)
second_user = "assertion-state-second-owner"
reference = datetime(2026, 12, 1, tzinfo=timezone.utc)


async def _store(engine, *, owner, subject="Alice", predicate="lives_in",
                 object_value="Seattle", scope=Scope.PERSONAL, **kwargs):
    content = f"{subject} {predicate} {object_value}."
    receipt = await engine.store_with_receipt(
        content, user_id=owner, node_type=NodeType.FACT, scope=scope, metadata={
            "subject": subject, "predicate": predicate, "object": object_value,
            "polarity": "positive", "evidence_quote": content,
        }, event_time=datetime(2025, 1, 1, tzinfo=timezone.utc), **kwargs)
    return receipt.node


def _query(subject=" alice ", predicate="lives-in", **kwargs):
    return AssertionStateQuery(subject=subject, predicate=predicate, scope=Scope.PERSONAL,
                               valid_at=reference, **kwargs)


async def _both(engine, query, *, owner):
    scanned = await get_assertion_state(engine, query, user_id=owner)
    addressed = await get_assertion_state_by_address(engine, query, user_id=owner)
    return scanned, addressed


async def test_the_address_read_returns_the_same_state_as_the_full_scan(config, user):
    async with MemoryEngine.open(config) as engine:
        # Nothing at the address.
        scanned, addressed = await _both(engine, _query(predicate="no-such-predicate"), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()

        # One claim, then a second one at the same address.
        first = await _store(engine, owner=user, object_value="Seattle")
        scanned, addressed = await _both(engine, _query(), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()
        assert addressed.status == "single" and addressed.matched_records == 1

        second = await _store(engine, owner=user, object_value="Chicago")
        scanned, addressed = await _both(engine, _query(), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()
        assert addressed.status == "multiple"
        assert addressed.current_candidate_count == 2

        # Retired: one superseded claim stays in the timeline with its reason.
        await engine.supersede(str(first.id), str(second.id), user_id=user)
        scanned, addressed = await _both(engine, _query(), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()
        history = {entry.node_id: entry for entry in addressed.timeline}
        assert "superseded_pointer" in history[first.id].exclusion_reasons

        # Archived.
        archived = await _store(engine, owner=user, object_value="Denver")
        await engine.archive(str(archived.id), user_id=user)
        scanned, addressed = await _both(engine, _query(), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()

        # Case and spacing: the stored value is not the queried value, the normalized one is.
        await _store(engine, owner=user, subject="Alice", predicate="Lives In",
                     object_value="Boston")
        scanned, addressed = await _both(engine, _query(), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()
        assert addressed.matched_records == 4


async def test_the_two_paths_agree_across_every_page_size(config, user):
    """A page boundary is where a read that narrows by index could quietly drop a row."""
    async with MemoryEngine.open(config) as engine:
        for index in range(5):
            await _store(engine, owner=user, object_value=f"City {index}")
        for batch_size in (1, 2, 3, 500):
            scanned = await get_assertion_state(engine, _query(), user_id=user,
                                                batch_size=batch_size)
            addressed = await get_assertion_state_by_address(engine, _query(), user_id=user,
                                                             batch_size=batch_size)
            assert scanned.model_dump_json() == addressed.model_dump_json(), batch_size


async def test_a_second_owners_claims_are_not_at_the_first_owners_address(config, user):
    async with MemoryEngine.open(config) as engine:
        await _store(engine, owner=user, object_value="Seattle")
        await _store(engine, owner=second_user, object_value="Chicago")
        scanned, addressed = await _both(engine, _query(), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()
        assert addressed.matched_records == 1
        assert addressed.current_values[0].normalized_object == "seattle"
        mine = await _both(engine, _query(), owner=second_user)
        assert mine[0].model_dump_json() == mine[1].model_dump_json()
        assert mine[1].current_values[0].normalized_object == "chicago"


async def test_the_scan_counts_are_the_same_on_both_paths(config, user):
    """The counts describe the whole queried node type, and the address read takes them from a count."""
    async with MemoryEngine.open(config) as engine:
        await _store(engine, owner=user, object_value="Seattle")
        # A node with no usable claim, which is what missing_structured_assertion counts.
        from uuid import uuid4

        from prme.models.nodes import MemoryNode
        from prme.types import LifecycleState

        now = datetime.now(timezone.utc)
        engine._graph_store._create_node_sync(MemoryNode(
            id=uuid4(), node_type=NodeType.FACT, user_id=user, session_id="no-claim",
            scope=Scope.PERSONAL, content="A fact with no structured claim.",
            metadata={"note": "no subject"}, confidence=0.5, salience=0.5,
            lifecycle_state=LifecycleState.TENTATIVE, valid_from=now, created_at=now,
            updated_at=now))
        scanned, addressed = await _both(engine, _query(), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()
        assert addressed.exclusions.get("missing_structured_assertion") == 1
        assert addressed.scanned_nodes == scanned.scanned_nodes


async def test_the_stored_address_matches_the_normalize_rule(config, user):
    async with MemoryEngine.open(config) as engine:
        node = await _store(engine, owner=user, subject="  Alice  ", predicate="Lives   In",
                            object_value="Seattle")
        stored = engine._graph_store._conn.execute(
            f"SELECT {SUBJECT_COLUMN}, {PREDICATE_COLUMN}, {PRIVACY_COLUMN} FROM nodes "
            "WHERE id=?", [str(node.id)]).fetchone()
        subject_key, predicate_key, marked = stored
        assert subject_key == normalize_assertion_value("subject", "  Alice  ")
        assert predicate_key == normalize_assertion_value("predicate", "Lives   In")
        assert marked is False


def test_a_node_with_no_usable_claim_has_no_stored_address(user):
    from uuid import uuid4

    from prme.models.nodes import MemoryNode
    from prme.types import LifecycleState, NodeType, Scope

    now = datetime.now(timezone.utc)
    node = MemoryNode(
        id=uuid4(), node_type=NodeType.NOTE, user_id=user, session_id="none",
        scope=Scope.PERSONAL, content="A note.", metadata={"note": True}, confidence=0.5,
        salience=0.5, lifecycle_state=LifecycleState.TENTATIVE, valid_from=now,
        created_at=now, updated_at=now)
    assert assertion_key(node) == (None, None)
    assert privacy_marker(node.metadata) is False


def test_the_marker_answers_the_range_check_question():
    from prme.privacy.records import PRIVACY_KEY

    assert privacy_marker(None) is False
    assert privacy_marker({}) is False
    assert privacy_marker({PRIVACY_KEY: None}) is False
    assert privacy_marker({PRIVACY_KEY: {"reference": "x"}}) is True
    # A value that the JSON round trip would change still answers the same question.
    assert privacy_marker({PRIVACY_KEY: {"when": datetime(2025, 1, 1, tzinfo=timezone.utc)}}) is True


async def test_a_store_without_the_columns_falls_back_to_the_scan(tmp_path, product_defaults, user):
    """A pack that predates the columns must still answer, and answer the same.

    The old shape is built by copying the node rows without the three columns, because
    DuckDB 1.4.4 refuses to drop a column from this table. The read must notice at the
    point it needs them and keep the full scan, not fail.
    """
    from prme.storage.assertion_keys import PRIVACY_COLUMN as MARKER
    from prme.storage.assertion_keys import assertion_columns
    from prme.storage.embedding import create_embedding_provider
    from tests.contract.harness import product_config

    settings = product_config(str(tmp_path))
    engine = await MemoryEngine.create(
        settings, embedding_provider=create_embedding_provider(settings.embedding))
    try:
        await _store(engine, owner=user, object_value="Seattle")
        conn = engine._graph_store._conn
        conn.execute(
            f"CREATE TABLE nodes_old AS SELECT * EXCLUDE ({SUBJECT_COLUMN}, {PREDICATE_COLUMN}, "
            f"{MARKER}) FROM nodes")
        conn.execute("DROP TABLE nodes")
        conn.execute("ALTER TABLE nodes_old RENAME TO nodes")

        assert assertion_columns(conn) is False
        assert await engine._graph_store.scan_assertions(
            user_id=user, scope=Scope.PERSONAL, node_types=(NodeType.FACT,),
            subject_key="alice", predicate_key="lives_in") is None

        scanned, addressed = await _both(engine, _query(), owner=user)
        assert scanned.model_dump_json() == addressed.model_dump_json()
        assert addressed.matched_records == 1
    finally:
        await engine.close()


async def test_the_privacy_range_check_still_refuses_on_a_marked_row(tmp_path, product_defaults, user):
    """The stored marker must not let a marked row past the fence that a JSON read would stop."""
    from prme.privacy.policy import PrivacyDenied

    from prme.storage.embedding import create_embedding_provider
    from prme.storage.schema import initialize_database
    from tests.contract.harness import product_config

    db_path = tmp_path / "pack" / "memory.duckdb"
    db_path.parent.mkdir(parents=True)
    conn = duckdb.connect(str(db_path))
    initialize_database(conn)
    conn.close()
    settings = product_config(str(tmp_path))
    engine = await MemoryEngine.create(
        settings, embedding_provider=create_embedding_provider(settings.embedding))
    try:
        from uuid import uuid4

        from prme.models.nodes import MemoryNode
        from prme.types import LifecycleState

        now = datetime.now(timezone.utc)
        engine._graph_store._create_node_sync(MemoryNode(
            id=uuid4(), node_type=NodeType.FACT, user_id=user, session_id="marked",
            scope=Scope.PERSONAL, content="A marked row.",
            metadata={"subject": "marked", "predicate": "located_in", "object": "drawer",
                      "polarity": "positive", "_prme_privacy_v1": {"reference": "test"}},
            confidence=0.5, salience=0.5, lifecycle_state=LifecycleState.TENTATIVE,
            valid_from=now, created_at=now, updated_at=now))
        with pytest.raises(PrivacyDenied):
            await engine.get_assertion_state(_query(predicate="located_in"), user_id=user)
    finally:
        await engine.close()


async def test_no_index_covers_the_stored_columns(config, user):
    """An index on a column ``update_node`` rewrites turns that update into a delete and an insert.

    That write then skips an in-flight ``updated_at`` conflict claim, which is why
    ``idx_nodes_lifecycle`` is dropped in ``schema.py``. Every metadata update rewrites the
    address and the marker, so no index may name them. The supported DuckDB does not use one
    for this read anyway (``prme.storage.assertion_keys`` has the measurement).
    """
    async with MemoryEngine.open(config) as engine:
        rows = engine._graph_store._conn.execute(
            "SELECT index_name, sql FROM duckdb_indexes() WHERE table_name IN ('nodes', 'events')"
        ).fetchall()
    for name, sql in rows:
        for column in (SUBJECT_COLUMN, PREDICATE_COLUMN, PRIVACY_COLUMN):
            assert column not in (sql or ""), (name, sql)


def test_a_store_that_gains_the_columns_survives_an_abrupt_exit(tmp_path):
    """DuckDB 1.4.4 cannot replay an ADD COLUMN on ``nodes`` from the WAL after a crash.

    A new store declares the columns in ``CREATE TABLE``; a pack that predates them gains
    them by ``ALTER`` and must checkpoint at once. Both shapes are opened, left without a
    close in a child process, and opened again.
    """
    import subprocess
    import sys
    import textwrap

    from prme.storage.schema import initialize_database

    old = tmp_path / "old.duckdb"
    with duckdb.connect(str(old)) as conn:
        initialize_database(conn)
        conn.execute(
            f"CREATE TABLE nodes_old AS SELECT * EXCLUDE ({SUBJECT_COLUMN}, {PREDICATE_COLUMN}, "
            f"{PRIVACY_COLUMN}) FROM nodes")
        conn.execute("DROP TABLE nodes")
        conn.execute("ALTER TABLE nodes_old RENAME TO nodes")
        conn.execute("CHECKPOINT")
    new = tmp_path / "new.duckdb"
    for path in (old, new):
        script = textwrap.dedent(f"""
            import os, duckdb
            from prme.storage.schema import initialize_database
            conn = duckdb.connect({str(path)!r})
            initialize_database(conn)
            os._exit(0)
        """)
        subprocess.run([sys.executable, "-c", script], check=True)
        with duckdb.connect(str(path)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info('nodes')").fetchall()}
        assert {SUBJECT_COLUMN, PREDICATE_COLUMN, PRIVACY_COLUMN} <= columns, path



@pytest.mark.parametrize("layout", ["directory", "pack"])
async def test_a_condition_evaluation_keeps_the_privacy_marker(tmp_path, layout, analyzer, native):
    """A raw writer that rewrites ``nodes.metadata`` outside ``update_node`` must keep the marker.

    Condition evaluation copies the old metadata into its own UPDATE and does not touch
    ``has_privacy_marker``, so the marker stays right only because the privacy key survives the
    copy. Any new raw UPDATE that adds the privacy key to an existing row must also set the
    marker, or the range check would read FALSE and miss the row. The node here is admitted
    through the real privacy runtime, so it carries a privacy key that names a saved record.
    """
    from prme.privacy.records import PRIVACY_KEY
    from prme.types import EpistemicType
    from tests.privacy_journal_support import SOURCE_ONLY, journal_engine

    engine, _runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        text = "If the release is approved, deploy Atlas."
        event = await engine.store(
            f"{text} {SOURCE_ONLY}", retrieval_content=text, user_id=owner,
            epistemic_type=EpistemicType.CONDITIONAL,
            metadata={"condition": "the release is approved", "condition_state": "unknown"})
        node = (await engine.get_event_nodes(event, user_id=owner))[0]

        async def stored():
            rows = await engine._privacy.repository._query(
                f"SELECT {PRIVACY_COLUMN}, metadata FROM nodes WHERE id=?", (str(node.id),))
            return rows[0]

        marked, metadata = await stored()
        assert PRIVACY_KEY in metadata and marked is True
        updated = await engine.evaluate_condition(
            str(node.id), "true", user_id=owner, reason="Approval checked.", actor_id=owner)
        assert updated.metadata["condition_state"] == "true"
        marked, metadata = await stored()
        assert '"condition_state": "true"' in metadata.replace('":"', '": "')
        assert PRIVACY_KEY in metadata and marked is True
    finally:
        await engine.close()
