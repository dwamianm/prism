"""Tests for PgLexicalIndex against a real PostgreSQL instance.

Requires PRME_TEST_DATABASE_URL environment variable.
"""

from __future__ import annotations

import os
import uuid

import pytest

from prme.models.nodes import MemoryNode
from prme.types import NodeType, Scope

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("PRME_TEST_DATABASE_URL"),
        reason="PRME_TEST_DATABASE_URL not set",
    ),
]


@pytest.fixture
async def pg_pool():
    import asyncpg

    url = os.environ["PRME_TEST_DATABASE_URL"]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
async def lexical_index(pg_pool):
    from prme.storage.pg.schema import initialize_pg_database
    from prme.storage.pg.lexical_index import PgLexicalIndex

    await initialize_pg_database(pg_pool)
    return PgLexicalIndex(pg_pool)


@pytest.fixture
async def graph_store(pg_pool):
    from prme.storage.pg.graph_store import PgGraphStore
    return PgGraphStore(pg_pool)


async def test_search_nodes(lexical_index, graph_store):
    """Nodes with content_tsv GENERATED column should be searchable."""
    uid = f"user-{uuid.uuid4().hex[:8]}"
    node = MemoryNode(
        user_id=uid, node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        content="PostgreSQL supports full-text search natively",
    )
    await graph_store.create_node(node)

    results = await lexical_index.search("full-text search", uid, limit=5)
    assert len(results) >= 1
    assert any("full-text" in r["content"].lower() or "search" in r["content"].lower() for r in results)


async def test_index_non_node_content(lexical_index):
    """Non-node content should be indexed in lexical_documents table."""
    uid = f"user-{uuid.uuid4().hex[:8]}"
    fake_node_id = str(uuid.uuid4())

    await lexical_index.index(
        fake_node_id, "Machine learning algorithms are fascinating", uid, "event"
    )

    results = await lexical_index.search("machine learning", uid, limit=5)
    assert len(results) >= 1


async def test_delete_by_node_id(lexical_index):
    uid = f"user-{uuid.uuid4().hex[:8]}"
    fake_node_id = str(uuid.uuid4())

    await lexical_index.index(fake_node_id, "deletable content", uid, "note")
    await lexical_index.delete_by_node_id(fake_node_id)

    results = await lexical_index.search("deletable", uid, limit=5)
    matching = [r for r in results if r["node_id"] == fake_node_id]
    assert len(matching) == 0


async def test_duplicate_documents_do_not_consume_candidate_limit(lexical_index, graph_store):
    """A previously indexed source may later also have a durable graph row."""
    uid = f"user-{uuid.uuid4()}"
    first = MemoryNode(user_id=uid, node_type=NodeType.NOTE, scope=Scope.PERSONAL,
                       content="cobalt cobalt cobalt cobalt")
    second = MemoryNode(user_id=uid, node_type=NodeType.NOTE, scope=Scope.PERSONAL,
                        content="cobalt")
    # This order creates a legitimate source/index copy before graph publication.
    await lexical_index.index(str(first.id), first.content, uid, "note", "personal")
    await graph_store.create_node(first)
    await graph_store.create_node(second)
    for owner, scope in [(uid + "-other", Scope.PERSONAL), (uid, Scope.PROJECT)]:
        await graph_store.create_node(MemoryNode(user_id=owner, scope=scope, node_type=NodeType.NOTE,
                                                 content="cobalt " * 20))
    rows = await lexical_index.search("cobalt", uid, limit=2, scope=["personal"], node_type="note")
    assert [row["node_id"] for row in rows] == [str(first.id), str(second.id)]
    assert len(await lexical_index.search("cobalt", uid, limit=1, scope=["personal"])) == 1


async def test_tied_lexical_candidates_use_identity_before_limit(lexical_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    nodes = [MemoryNode(user_id=uid, node_type=NodeType.NOTE, content="identical telescope") for _ in range(3)]
    # Insert in the opposite order to the required stable identity tie-break.
    for node in sorted(nodes, key=lambda node: str(node.id), reverse=True):
        await graph_store.create_node(node)
    rows = await lexical_index.search("telescope", uid, limit=2)
    assert [row["node_id"] for row in rows] == sorted(str(node.id) for node in nodes)[:2]


# --- Questions that only partly match (prism-monorepo issue 254) -------------

RUST = "Alex is learning Rust on weekends and is reading Rust for Rustaceans."
ALEX_MEMORIES = [
    RUST,
    "Alex prefers Go for services and TypeScript for internal tooling.",
    "Alex rides gravel bikes on weekends, about 60 miles a week.",
]


FILLER = ["the garden gate", "the kitchen table", "the morning train", "the river bank"]


async def _store_notes(graph_store, uid, contents):
    nodes = [MemoryNode(user_id=uid, node_type=NodeType.NOTE, scope=Scope.PERSONAL, content=text)
             for text in contents]
    for node in nodes:
        await graph_store.create_node(node)
    return {node.content: str(node.id) for node in nodes}


async def test_question_that_only_partly_matches_finds_the_memory(lexical_index, graph_store):
    """No memory holds every word of the question, and every memory names Alex."""
    uid = f"user-{uuid.uuid4()}"
    ids = await _store_notes(graph_store, uid, ALEX_MEMORIES)
    # Another memory with the informative term but not the common one, and
    # one more memory, so "alex" is in at least half and "learning" is not.
    piano = (await _store_notes(graph_store, uid, ["Jane is learning piano.", FILLER[0]]))[
        "Jane is learning piano."]
    for question in ("what coding language is alex learning?", "alex learning", "Rust"):
        rows = await lexical_index.search(question, uid, limit=10)
        # "alex" is in three of the five memories, so only "learning" selects
        # candidates. The Rust memory also holds "alex" and ranks first; for
        # "alex learning" it holds every term, so it is the only hit.
        expected = [ids[RUST]] if question != "what coding language is alex learning?" else [
            ids[RUST], piano]
        assert [row["node_id"] for row in rows] == expected, question
        if len(rows) > 1:
            assert rows[0]["score"] > rows[1]["score"]


async def test_hits_rank_by_distinct_terms_before_ts_rank_cd(lexical_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    repeated = "cobalt " * 11 + "cobalt"
    ids = await _store_notes(graph_store, uid, [repeated, "a cobalt glass", "a glass vase", *FILLER])
    rows = await lexical_index.search("cobalt glass vase", uid, limit=10)
    # No memory holds all three terms. Twelve repeats give the first memory
    # the largest ts_rank_cd, but the other two match two terms each.
    assert {row["node_id"] for row in rows[:2]} == {ids["a cobalt glass"], ids["a glass vase"]}
    assert [row["node_id"] for row in rows[2:]] == [ids[repeated]]
    assert rows[1]["score"] > rows[2]["score"]


async def test_full_matches_keep_partial_matches_out(lexical_index, graph_store):
    """A memory holding every term stays the only hit, as with all terms required."""
    uid = f"user-{uuid.uuid4()}"
    ids = await _store_notes(graph_store, uid, [
        "The telescope lens is blue.", "The camera lens was cleaned on Monday.",
        "Lens caps are in the drawer.", *FILLER])
    rows = await lexical_index.search("telescope lens", uid, limit=10)
    assert [row["node_id"] for row in rows] == [ids["The telescope lens is blue."]]


async def test_multi_term_queries_respect_filters_and_index_copies(lexical_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    rust = MemoryNode(user_id=uid, node_type=NodeType.NOTE, scope=Scope.PERSONAL, content=RUST)
    # An index copy written before the graph row, as in the duplicate test above.
    await lexical_index.index(str(rust.id), RUST, uid, "note", "personal")
    await graph_store.create_node(rust)
    await _store_notes(graph_store, uid, [*ALEX_MEMORIES[1:], FILLER[0]])
    # Memories that the filters exclude. They would make "learning" common if
    # they were counted.
    await lexical_index.index(str(uuid.uuid4()), RUST, uid, "event", "personal")
    for owner, scope in [(uid, Scope.PROJECT), (uid + "-other", Scope.PERSONAL)]:
        await graph_store.create_node(MemoryNode(user_id=owner, node_type=NodeType.NOTE,
                                                 scope=scope, content=RUST))
    rows = await lexical_index.search("what coding language is alex learning?", uid,
                                      limit=10, node_type="note", scope=["personal"])
    assert [row["node_id"] for row in rows] == [str(rust.id)]


async def test_only_the_first_query_terms_are_used(lexical_index, graph_store):
    from prme.storage.pg.lexical_index import MAX_QUERY_TERMS

    uid = f"user-{uuid.uuid4()}"
    ids = await _store_notes(graph_store, uid, ALEX_MEMORIES)
    padding = " ".join(f"zq{index}x" for index in range(MAX_QUERY_TERMS))
    assert await lexical_index.search(f"{padding} rustaceans", uid, limit=10) == []
    rows = await lexical_index.search(f"rustaceans {padding}", uid, limit=10)
    assert [row["node_id"] for row in rows] == [ids[RUST]]


async def test_common_terms_are_searched_when_nothing_else_matches(lexical_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    ids = await _store_notes(graph_store, uid, ALEX_MEMORIES)
    rows = await lexical_index.search("alex weekends zeppelin", uid, limit=10)
    # Both matching terms are common, so both are searched and the two
    # memories that hold both rank above the one that holds only "alex".
    assert {row["node_id"] for row in rows[:2]} == {ids[RUST], ids[ALEX_MEMORIES[2]]}
    assert rows[2]["node_id"] == ids[ALEX_MEMORIES[1]]


async def test_single_term_scores_are_ts_rank_cd(lexical_index, graph_store, pg_pool):
    uid = f"user-{uuid.uuid4()}"
    await _store_notes(graph_store, uid, ALEX_MEMORIES + ["Rust Rust Rust"])
    rows = await lexical_index.search("Rust", uid, limit=10)
    expected = await pg_pool.fetch(
        "SELECT id::text AS node_id, ts_rank_cd(content_tsv, plainto_tsquery('english', 'Rust')) AS score "
        "FROM nodes WHERE user_id = $1 AND content_tsv @@ plainto_tsquery('english', 'Rust') "
        "ORDER BY score DESC, id::text COLLATE \"C\"", uid)
    assert [(row["node_id"], row["score"]) for row in rows] == [
        (row["node_id"], float(row["score"])) for row in expected]


async def test_retrieve_ranks_the_partly_matching_memory_first(monkeypatch):
    """The issue's three memory repro through retrieve() on PostgreSQL."""
    from prme.config import PRMEConfig
    from prme.storage.engine import MemoryEngine

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider",
                        lambda _: HashEmbeddingProvider())
    uid = f"user-{uuid.uuid4()}"
    config = PRMEConfig(database_url=os.environ["PRME_TEST_DATABASE_URL"],
                        organizer={"opportunistic_enabled": False})
    async with MemoryEngine.open(config) as engine:
        for text in ALEX_MEMORIES:
            await engine.store(text, user_id=uid)
        responses = {question: await engine.retrieve(question, user_id=uid) for question in (
            "what coding language is alex learning?", "Rust", "alex learning",
            "What is Alex studying on weekends?")}
    for question, response in responses.items():
        assert response.metadata.candidates_generated.get("LEXICAL", 0) > 0, question
        contents = [item.node.content for item in response.results]
        if question.startswith("What is Alex studying"):
            # The Rust and bike memories both hold "alex" and "weekends", so
            # lexical search ties them; it only puts both above the Go memory.
            assert contents.index(RUST) < contents.index(ALEX_MEMORIES[1])
            continue
        assert contents[0] == RUST, question
        assert response.results[0].composite_score > response.results[1].composite_score, question


# --- Term weights: rare words decide, common words do not select ----------------

STATEMENTS = [
    "waters the ferns on Sundays", "paints the garden fence white", "keeps a spare umbrella at work",
    "bakes sourdough every other week", "collects vintage postcards", "walks to the station each morning",
    "sorts the recycling on Tuesdays", "repairs bicycle tires at home", "tastes every tea in the shop",
    "folds paper cranes at lunch", "grows basil on the balcony", "stretches before breakfast",
    "photographs old bridges", "mends socks by lamplight", "brews ginger beer in the cellar",
    "trims the hedge in spring", "hums while washing dishes", "labels every jar in the pantry",
    "catalogs seashells from holidays", "swims laps before sunrise", "plants tulips along the path",
    "polishes brass doorknobs", "knits scarves for the neighbors", "airs the quilts in June",
    "sketches rooftops from the train", "oils the squeaky gate", "pickles beets in autumn",
    "feeds sparrows on the windowsill", "dries lavender in bundles", "winds the hallway clock",
]
CODE_NOTES = ["Alex reviews code before lunch.", "Alex keeps code snippets in a notebook.",
              "Alex's code editor is Neovim."]
CALL_NOTES = ["Alex is on call this week.", "Alex swapped the on-call shift with Priya.",
              "Priya covers the call rotation.", "The pager app is called Beeper."]
MANAGER_NOTES = ["Alex's manager is Dana Okafor.", "Dana manages the platform team."]


async def _store_alex_notes(graph_store, uid, extra=()):
    """Forty-odd memories in which "alex" is common, as for the owner of a store."""
    return await _store_notes(graph_store, uid, [
        RUST, *CODE_NOTES, *CALL_NOTES, *MANAGER_NOTES, *extra,
        *(f"Alex {text}." for text in STATEMENTS), "The garden gate is green.", "The river bank is steep."])


@pytest.fixture
async def weighted_index(pg_pool, lexical_index):
    from prme.storage.pg.lexical_index import PgLexicalIndex

    return PgLexicalIndex(pg_pool, term_weights=True)


async def test_term_weights_are_off_by_default(pg_pool):
    from prme import PRMEConfig
    from prme.storage.pg.lexical_index import PgLexicalIndex

    assert PgLexicalIndex(pg_pool)._term_weights is False
    assert PRMEConfig().lexical_term_weights is False
    assert PRMEConfig(lexical_term_weights=True).lexical_term_weights is True


async def test_the_question_finds_the_memory_with_the_rare_word_alone(lexical_index, weighted_index, graph_store):
    """"code" is in three memories and "alex" in most: only "learning" is rare enough."""
    uid = f"user-{uuid.uuid4()}"
    ids = await _store_alex_notes(graph_store, uid)
    question = "what coding language is alex learning?"
    rows = await weighted_index.search(question, uid, limit=10)
    assert [row["node_id"] for row in rows] == [ids[RUST]]
    # Without term weights the memories that share "code" are candidates too.
    off = await lexical_index.search(question, uid, limit=10)
    assert {row["node_id"] for row in off} == {ids[RUST], *(ids[text] for text in CODE_NOTES)}


async def test_words_that_only_a_few_memories_share_select_nothing(lexical_index, weighted_index, graph_store):
    """Nothing holds "dog", and "called" is only a verb that four memories use."""
    uid = f"user-{uuid.uuid4()}"
    ids = await _store_alex_notes(graph_store, uid)
    question = "What is Alex's dog called?"
    assert await weighted_index.search(question, uid, limit=10) == []
    off = await lexical_index.search(question, uid, limit=10)
    assert {row["node_id"] for row in off} == {ids[text] for text in CALL_NOTES}


async def test_a_word_that_two_memories_hold_selects_them_and_the_name_ranks_them(weighted_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    ids = await _store_alex_notes(graph_store, uid)
    # No memory holds "nowadays", so none holds every word of the question.
    rows = await weighted_index.search("Who is Alex's manager nowadays?", uid, limit=10)
    assert [row["node_id"] for row in rows] == [ids[MANAGER_NOTES[0]], ids[MANAGER_NOTES[1]]]
    assert rows[0]["score"] > rows[1]["score"]
    # With every word in one memory, that memory is the only hit.
    rows = await weighted_index.search("Who is Alex's manager?", uid, limit=10)
    assert [row["node_id"] for row in rows] == [ids[MANAGER_NOTES[0]]]


async def test_one_rare_word_outweighs_two_words_that_eight_memories_hold(lexical_index, weighted_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    both = "A glass vase on the shelf."
    rare = "The zeppelin is parked in the hangar."
    ids = await _store_notes(graph_store, uid, [
        both, rare, *(f"A glass bowl number {n}." for n in range(7)), *(f"A vase of tulips number {n}." for n in range(7)),
        *(f"Alex {text}." for text in STATEMENTS)])
    rows = await weighted_index.search("zeppelin glass vase", uid, limit=10)
    # "glass" and "vase" are in eight memories each, so no memory is a
    # candidate for one of them alone, and no memory holds all three words.
    assert [row["node_id"] for row in rows] == [ids[rare], ids[both]]
    assert rows[0]["score"] > rows[1]["score"]
    off = await lexical_index.search("zeppelin glass vase", uid, limit=20)
    assert off[0]["node_id"] == ids[both] and len(off) == 16


async def test_a_memory_that_holds_every_word_stays_the_only_hit(weighted_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    ids = await _store_alex_notes(graph_store, uid)
    rows = await weighted_index.search("alex learning", uid, limit=10)
    assert [row["node_id"] for row in rows] == [ids[RUST]]


async def test_a_query_with_one_word_returns_every_memory_that_holds_it(weighted_index, graph_store, pg_pool):
    uid = f"user-{uuid.uuid4()}"
    ids = await _store_alex_notes(graph_store, uid)
    rows = await weighted_index.search("code", uid, limit=10)
    assert {row["node_id"] for row in rows} == {ids[text] for text in CODE_NOTES}
    # The order is ts_rank_cd's, as without term weights.
    expected = await pg_pool.fetch(
        "SELECT id::text AS node_id FROM nodes WHERE user_id = $1 AND content_tsv @@ plainto_tsquery('english', 'code') "
        "ORDER BY ts_rank_cd(content_tsv, plainto_tsquery('english', 'code')) DESC, id::text COLLATE \"C\"", uid)
    assert [row["node_id"] for row in rows] == [row["node_id"] for row in expected]
    everyone = await pg_pool.fetchval(
        "SELECT count(*) FROM nodes WHERE user_id = $1 AND content_tsv @@ plainto_tsquery('english', 'alex')", uid)
    assert len(await weighted_index.search("alex", uid, limit=100)) == everyone > 30


async def test_term_weights_count_only_the_memories_that_are_searched(weighted_index, graph_store):
    uid = f"user-{uuid.uuid4()}"
    rust = MemoryNode(user_id=uid, node_type=NodeType.NOTE, scope=Scope.PERSONAL, content=RUST)
    # An index copy written before the graph row, as in the duplicate test above.
    await weighted_index.index(str(rust.id), RUST, uid, "note", "personal")
    await graph_store.create_node(rust)
    await _store_notes(graph_store, uid, [*CODE_NOTES, *CALL_NOTES, *MANAGER_NOTES,
                                          *(f"Alex {text}." for text in STATEMENTS)])
    # Memories that the filters leave out. If they were counted, "learning"
    # would be in four memories and the Rust memory would not be a candidate.
    await weighted_index.index(str(uuid.uuid4()), RUST, uid, "event", "personal")
    for owner, scope in [(uid, Scope.PROJECT), (uid + "-other", Scope.PERSONAL)]:
        await graph_store.create_node(MemoryNode(user_id=owner, node_type=NodeType.NOTE, scope=scope, content=RUST))
    rows = await weighted_index.search("what coding language is alex learning?", uid,
                                       limit=10, node_type="note", scope=["personal"])
    assert [row["node_id"] for row in rows] == [str(rust.id)]


async def test_the_chance_limit_is_a_share_of_the_store_or_two_memories(weighted_index, graph_store, monkeypatch):
    from prme.storage.pg import lexical_index

    uid = f"user-{uuid.uuid4()}"
    cellists = ["Alex plays the cello at night.", "Sam plays the cello on Sundays.", "Jo plays the cello in a quartet."]
    ids = await _store_notes(graph_store, uid, [*cellists, *(f"Alex {text}." for text in STATEMENTS)])
    # "zzzz" is in no memory, so "cello" is the only searched word. Three of 33
    # memories hold it: more than the two memories a small store allows.
    assert await weighted_index.search("cello zzzz", uid, limit=10) == []
    assert await weighted_index.search("cello", uid, limit=10) != []
    # A share of the store that allows three memories lets them through.
    monkeypatch.setattr(lexical_index, "MAX_CHANCE_SHARE", 0.1)
    rows = await weighted_index.search("cello zzzz", uid, limit=10)
    assert {row["node_id"] for row in rows} == {ids[text] for text in cellists}


class HashEmbeddingProvider:
    model_name = "lexical-term-weights-test"
    model_version = "1"
    dimension = 384  # The dimension of the existing PostgreSQL test schema.

    async def embed(self, texts):
        import hashlib

        return [[hashlib.sha256(text.encode()).digest()[i % 32] / 255
                 for i in range(self.dimension)] for text in texts]


async def test_retrieve_follows_the_option_on_a_store_where_the_name_is_common(monkeypatch):
    """Both paths through retrieve(): the question that needs the rare word, and the one that matches only a verb."""
    from prme.config import PRMEConfig
    from prme.storage.engine import MemoryEngine

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: HashEmbeddingProvider())
    uid = f"user-{uuid.uuid4()}"
    memories = [RUST, *CODE_NOTES, *CALL_NOTES, *MANAGER_NOTES, *(f"Alex {text}." for text in STATEMENTS)]
    found = {}
    for option in (False, True):
        config = PRMEConfig(database_url=os.environ["PRME_TEST_DATABASE_URL"], lexical_term_weights=option,
                            organizer={"opportunistic_enabled": False})
        async with MemoryEngine.open(config) as engine:
            assert engine._lexical_index._term_weights is option
            if not option:
                for text in memories:
                    await engine.store(text, user_id=uid)
            for question in ("what coding language is alex learning?", "What is Alex's dog called?"):
                response = await engine.retrieve(question, user_id=uid)
                found[option, question] = (response.metadata.candidates_generated.get("LEXICAL", 0),
                                           [item.node.content for item in response.results])
    rust, dog = "what coding language is alex learning?", "What is Alex's dog called?"
    # Off: the three memories that share "code" are candidates with the Rust memory, and so are the four that share "call".
    assert found[False, rust][0] == 4 and found[False, dog][0] == 4
    # On: the Rust memory is the only candidate, and rank fusion puts it first. The verb selects nothing.
    assert found[True, rust][0] == 1 and found[True, rust][1][0] == RUST
    assert found[True, dog][0] == 0
