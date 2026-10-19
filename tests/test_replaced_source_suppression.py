"""The source text of a retired claim stays out of default retrieval (enable_superseded_source_suppression, #270)."""

from datetime import datetime, timezone
from uuid import UUID

from prme import MemoryEngine, PRMEConfig
from prme.models import MemoryNode
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.replaced_sources import suppress_replaced_sources
from prme.types import LifecycleState, NodeType, RetrievalMode
from tests import test_durable_ingestion
from tests.test_extraction_corrections import (
    LATER, NEW, OLD, OLD_FACT, T0, Scripted, correction, corrections, fact, say,
)

config = test_durable_ingestion.config
user = test_durable_ingestion.user

REFERENCE = datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
QUESTION = "When is Jo's birthday?"
OWNER = "owner"


def on(base, enabled=True):
    return corrections(base).model_copy(update={"enable_superseded_source_suppression": enabled})


def context_lines(response):
    return [line for line in response.bundle.render().splitlines() if line.startswith("- ")]


def only_old(lines):
    return [line for line in lines if "March 3" in line and "March 13" not in line]


async def ask(engine, owner, question=QUESTION, **extra):
    return await engine.retrieve(question, user_id=owner, min_score=0, include_cross_scope=False,
                                 reference_time=REFERENCE, **extra)


async def store_and_correct(engine, owner):
    """Say the old birthday, then correct it in a later message that does not repeat it."""
    model = Scripted({OLD: [OLD_FACT], NEW: correction()})
    old_event = await say(engine, model, OLD, at=T0, owner=owner)
    new_event = await say(engine, model, NEW, at=LATER, owner=owner)
    return old_event, new_event


def test_it_is_off_by_default():
    assert PRMEConfig(_env_file=None).enable_superseded_source_suppression is False


def test_the_environment_can_turn_it_on(monkeypatch):
    monkeypatch.setenv("PRME_ENABLE_SUPERSEDED_SOURCE_SUPPRESSION", "true")
    assert PRMEConfig(_env_file=None).enable_superseded_source_suppression is True


class TestRetrieval:
    async def test_off_the_message_that_said_the_old_value_reaches_the_context(self, config, user):
        async with MemoryEngine.open(on(config, False)) as engine:
            await store_and_correct(engine, user)
            response = await ask(engine, user)
            lines = context_lines(response)
            assert only_old(lines), lines
            assert any("March 13" in line for line in lines)
            assert not [item for item in response.excluded if item.reason.startswith("replaced_source")]

    async def test_on_no_line_holds_only_the_old_value_and_the_correction_stays(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            await store_and_correct(engine, user)
            response = await ask(engine, user)
            lines = context_lines(response)
            assert only_old(lines) == [], lines
            assert any("March 13" in line for line in lines)
            reasons = [item.reason for item in response.excluded if item.reason.startswith("replaced_source:")]
            assert reasons, "the note that said March 3 and its claim are excluded with a reason"
            retired = [node for node in await engine.query_nodes(user_id=user, node_type=NodeType.FACT,
                                                                 lifecycle_states=[LifecycleState.SUPERSEDED])]
            assert {reason.split(":")[1] for reason in reasons} == {str(retired[0].id)}

    async def test_nothing_is_written_and_the_old_message_stays_readable(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            old_event, _ = await store_and_correct(engine, user)

            async def states():
                nodes = await engine.query_nodes(user_id=user, lifecycle_states=list(LifecycleState))
                return {(str(node.id), node.lifecycle_state, node.superseded_by) for node in nodes}

            # The first retrieval finishes the notes' materialization; after it, a retrieval writes no node.
            await ask(engine, user)
            before = await states()
            await ask(engine, user)
            assert await states() == before
            assert LifecycleState.SUPERSEDED in {state for _, state, _ in before}
            assert (await engine.get_event(old_event, user_id=user)).content == OLD
            notes = [node for node in await engine.get_event_nodes(old_event, user_id=user)
                     if node.node_type == NodeType.NOTE]
            assert [(note.content, note.lifecycle_state) for note in notes] == [(OLD, LifecycleState.TENTATIVE)]
            assert (await engine.get_node(str(notes[0].id), user_id=user)).content == OLD

    async def test_explicit_retrieval_keeps_the_old_message(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            await store_and_correct(engine, user)
            response = await ask(engine, user, retrieval_mode=RetrievalMode.EXPLICIT)
            assert only_old(context_lines(response))

    async def test_a_claim_retired_by_hand_takes_its_message_out_too(self, config, user):
        said = {"Dana works at Northwind.": [fact("Dana", "works_at", "Northwind", "Dana works at Northwind.")],
                "Dana joined Brightpath.": [fact("Dana", "joined", "Brightpath", "Dana joined Brightpath.")]}
        model = Scripted(said)
        async with MemoryEngine.open(on(config)) as engine:
            await say(engine, model, "Dana works at Northwind.", at=T0, owner=user)
            await say(engine, model, "Dana joined Brightpath.", at=LATER, owner=user)
            facts = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
            old = next(node for node in facts if node.metadata["object"] == "Northwind")
            new = next(node for node in facts if node.metadata["object"] == "Brightpath")
            question = "Where does Dana work?"

            def stale(response):
                return [line for line in context_lines(response) if "Northwind" in line]

            assert stale(await ask(engine, user, question))
            await engine.supersede(str(old.id), str(new.id), user_id=user, actor_id=user)
            response = await ask(engine, user, question)
            assert stale(response) == []
            assert any("Brightpath" in line for line in context_lines(response))

    async def test_a_message_that_states_something_else_too_loses_only_the_replaced_sentence_s_claims(
            self, config, user):
        text = "Dana likes green tea. Dana lives in Boston."
        move = "Dana relocated to Chicago."
        model = Scripted({text: [fact("Dana", "likes", "green tea", "Dana likes green tea."),
                                 fact("Dana", "lives_in", "Boston", "Dana lives in Boston.")],
                          move: [fact("Dana", "relocated_to", "Chicago", move)]})
        async with MemoryEngine.open(on(config)) as engine:
            await say(engine, model, text, at=T0, owner=user)
            await say(engine, model, move, at=LATER, owner=user)
            facts = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
            boston = next(node for node in facts if node.metadata["object"] == "Boston")
            chicago = next(node for node in facts if node.metadata["object"] == "Chicago")
            await engine.supersede(str(boston.id), str(chicago.id), user_id=user, actor_id=user)
            lines = context_lines(await ask(engine, user, "What does Dana like and where does Dana live?"))
            assert not [line for line in lines if "Boston" in line], lines
            assert [line for line in lines if "Chicago" in line]
            # The sentence about tea is its own claim, and it reaches the context.
            assert [line for line in lines if "Dana likes green tea." in line and "Boston" not in line]

    async def test_the_receipt_records_the_option_only_when_it_is_on(self, config, user):
        for enabled in (False, True):
            async with MemoryEngine.open(on(config, enabled)) as engine:
                owner = f"{user}-{enabled}"
                await store_and_correct(engine, owner)
                response = await ask(engine, owner)
                receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=owner)
                assert receipt.replay_ranking() == tuple(item.node.id for item in response.results)
                assert ("superseded_source_suppression" in receipt.execution.features) is enabled
                assert ("superseded_source_suppression" in receipt.execution.parameters) is enabled
                if enabled:
                    assert receipt.execution.features["superseded_source_suppression"] == {"version": 1}


# --- The rule itself, over nodes and a stub store ------------------------------------------------


def node(number, text, kind=NodeType.NOTE, *, source=None, state=LifecycleState.TENTATIVE, replaced_by=None,
         triple=None, owner=OWNER):
    metadata = ({"subject": triple[0], "predicate": triple[1], "object": triple[2], "polarity": "positive"}
                if triple else {})
    return MemoryNode(id=UUID(int=number), user_id=owner, node_type=kind, content=text, metadata=metadata,
                      evidence_refs=[UUID(int=source or number)], lifecycle_state=state,
                      superseded_by=UUID(int=replaced_by) if replaced_by else None)


class Store:
    """A graph store that answers the lookups the rule makes."""

    def __init__(self, *nodes):
        self.nodes = nodes
        self.lookups = []

    def superseded(self, user_id):
        return [item for item in self.nodes if item.user_id == user_id and item.lifecycle_state == LifecycleState.SUPERSEDED]

    async def count_nodes(self, *, user_id, lifecycle_states):
        assert lifecycle_states == [LifecycleState.SUPERSEDED]
        self.lookups.append("count")
        return len(self.superseded(user_id))

    async def query_nodes(self, *, user_id, lifecycle_states, limit):
        assert lifecycle_states == [LifecycleState.SUPERSEDED]
        self.lookups.append("query")
        return self.superseded(user_id)[:limit]

    async def get_nodes(self, node_ids, *, include_superseded=False):
        assert include_superseded is True
        self.lookups.append("get")
        return [item for item in self.nodes if str(item.id) in node_ids]


async def run(store, *candidates):
    kept, excluded = await suppress_replaced_sources(
        [RetrievalCandidate(node=item, composite_score=1.0, paths=["VECTOR"], path_count=1) for item in candidates],
        graph_store=store, user_id=OWNER)
    return [item.node.id.int for item in kept], {item.node_id.int: item.reason for item in excluded}


# Message 1 says two things; its first sentence's claim (10) was replaced by a claim (20) of message 2.
MESSAGE = node(1, "Jo is my sister. Her birthday is March 3. Send a gift.")
BIRTHDAY = node(10, "Her birthday is March 3.", NodeType.FACT, source=1, state=LifecycleState.SUPERSEDED,
                replaced_by=20, triple=("Jo", "birthday", "March 3"))
SISTER = node(11, "Jo is my sister.", NodeType.FACT, source=1, triple=("Jo", "sister_of", "I"))
SAME_SENTENCE = node(12, "Her birthday is March 3.", NodeType.FACT, source=1, triple=("Jo", "is", "born"))
CORRECTED = node(20, "It's actually March 13.", NodeType.FACT, source=2, triple=("Jo", "birthday", "March 13"))
CORRECTION = node(2, "Sorry, it's actually March 13.")


class TestTheRule:
    async def test_the_message_and_the_claims_of_the_replaced_sentence_go(self):
        store = Store(MESSAGE, BIRTHDAY, SISTER, SAME_SENTENCE, CORRECTED, CORRECTION)
        kept, gone = await run(store, MESSAGE, SAME_SENTENCE, SISTER, CORRECTED, CORRECTION)
        assert kept == [11, 20, 2]
        assert gone == {1: f"replaced_source:{BIRTHDAY.id}", 12: f"replaced_source:{BIRTHDAY.id}"}

    async def test_nothing_changes_when_no_source_has_a_retired_claim(self):
        store = Store(MESSAGE, SISTER, CORRECTED, CORRECTION)
        kept, gone = await run(store, MESSAGE, SISTER, CORRECTED, CORRECTION)
        assert kept == [1, 11, 20, 2] and gone == {}
        # Entities, summaries and the like have no source text to look up.
        store = Store()
        kept, gone = await run(store, node(30, "Jo", NodeType.ENTITY, source=1), node(31, "A summary.", NodeType.SUMMARY))
        assert kept == [30, 31] and gone == {} and store.lookups == []

    async def test_an_owner_with_nothing_retired_costs_one_count(self):
        store = Store(MESSAGE, SISTER)
        assert await run(store, MESSAGE, SISTER) == ([1, 11], {})
        assert store.lookups == ["count"]
        # Three reads in all when something is retired: the count, the retired records, their replacements.
        store = Store(MESSAGE, BIRTHDAY, CORRECTED)
        await run(store, MESSAGE, CORRECTED)
        assert store.lookups == ["count", "query", "get"]

    async def test_a_candidate_that_does_not_hold_the_retired_text_stays(self):
        other = node(3, "Her birthday is in spring.", NodeType.FACT, source=1)
        kept, gone = await run(Store(MESSAGE, BIRTHDAY, CORRECTED, other), other)
        assert kept == [3] and gone == {}

    async def test_a_correction_in_the_same_message_is_not_a_replaced_source(self):
        same = node(21, "It is March 13, not March 3.", NodeType.FACT, source=1, triple=("Jo", "birthday", "March 13"))
        retired = BIRTHDAY.model_copy(update={"superseded_by": same.id})
        kept, gone = await run(Store(MESSAGE, retired, same), MESSAGE)
        assert kept == [1] and gone == {}

    async def test_a_claim_merged_into_a_duplicate_is_not_a_correction(self):
        for twin in (
            # The same statement from another message, worded the same or differently.
            node(20, "Her birthday is March 3.", NodeType.FACT, source=2, triple=("Jo", "birthday", "March 3")),
            node(20, "Jo's birthday: March 3rd.", NodeType.FACT, source=2, triple=("jo", "Birthday", "march 3")),
        ):
            kept, gone = await run(Store(MESSAGE, BIRTHDAY, twin), MESSAGE)
            assert kept == [1] and gone == {}

    async def test_a_replacement_that_is_not_a_statement_does_not_count(self):
        summary = node(20, "Jo's birthday is on file.", NodeType.SUMMARY, source=2)
        kept, gone = await run(Store(MESSAGE, BIRTHDAY, summary), MESSAGE)
        assert kept == [1] and gone == {}

    async def test_a_note_replaced_by_a_note_counts_as_a_correction(self):
        kept, gone = await run(Store(MESSAGE, BIRTHDAY.model_copy(update={"superseded_by": CORRECTION.id}),
                                     CORRECTION), MESSAGE)
        assert kept == [] and gone == {1: f"replaced_source:{BIRTHDAY.id}"}

    async def test_a_name_goes_when_every_message_that_named_it_goes(self):
        austin = node(40, "Austin", NodeType.ENTITY, source=1)
        elsewhere = MemoryNode(id=UUID(int=41), user_id=OWNER, node_type=NodeType.ENTITY, content="March",
                               evidence_refs=[UUID(int=1), UUID(int=2)])
        jo = node(42, "Jo", NodeType.ENTITY, source=1)
        unrelated = node(43, "Seattle", NodeType.ENTITY, source=1)
        store = Store(MESSAGE, BIRTHDAY, CORRECTED, CORRECTION)
        kept, gone = await run(store, MESSAGE, node(44, "March 3", NodeType.ENTITY, source=1), austin, elsewhere, jo,
                               unrelated)
        # "March 3" and "Jo" are in the replaced message only; "March" is also in message 2, which stays
        # but is not a candidate here, so the name has a source that did not go.
        assert gone.keys() == {1, 44, 42}
        assert kept == [40, 41, 43]

    async def test_other_users_claims_are_never_looked_at(self):
        foreign = node(10, "Her birthday is March 3.", NodeType.FACT, source=1, state=LifecycleState.SUPERSEDED,
                       replaced_by=20, owner="someone-else")
        kept, gone = await run(Store(MESSAGE, foreign, CORRECTED), MESSAGE)
        assert kept == [1] and gone == {}
