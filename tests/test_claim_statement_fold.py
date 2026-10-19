"""A folded record keeps its rank, with packing.folded_records_keep_rank on and off.

A claim statement is what ``enable_ingest_claim_fixes`` gives a claim whose sentences another claim of the same
message already holds. It is short and repeats the names of a question, so it outranks the claim it restates, and
the reader context's fold of a claim into its source note moves the claim's text down to the note's place
(prism-monorepo issues 258 and 259). No test here calls a model.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.schema import ExtractionResult
from prme.models.nodes import MemoryNode
from prme.retrieval.claim_statements import FOLD_REASON, fold_claim_statements
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.scoring import score_and_rank
from prme.types import LifecycleState, NodeType
from tests import test_durable_ingestion
from tests.test_fold_repeated_text import (
    OTHER, SENTENCE as TURN_SENTENCE, claims, lines, packed_ids, reader, record, turn,
)

config = test_durable_ingestion.config
user = test_durable_ingestion.user

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
SENTENCE = "My partner Jordan and I are planning a trip to Japan in late March next year."


def candidate(content, *, semantic, evidence, node_type=NodeType.FACT, statement=False, **metadata):
    node = MemoryNode(
        id=uuid4(), user_id="owner", session_id="s1", node_type=node_type, content=content,
        lifecycle_state=LifecycleState.STABLE, confidence=0.8, confidence_base=0.8, salience=0.5, salience_base=0.5,
        created_at=NOW, updated_at=NOW, last_reinforced_at=NOW, evidence_refs=[evidence] if evidence else [],
        metadata={"grounding_method": "claim_statement_v1" if statement else "claim_sentences_v1", **metadata},
    )
    return RetrievalCandidate(node=node, paths=["VECTOR"], path_count=1, semantic_score=semantic)


def scored(*candidates):
    ranked, _ = score_and_rank(list(candidates), now=NOW)
    return ranked


def by_id(ranked, item):
    return next(c for c in ranked if c.node.id == item.node.id)


class TestFold:
    def setup_method(self):
        self.event = uuid4()
        self.claim = candidate(SENTENCE, semantic=0.5, evidence=self.event, subject="I", object="Japan")
        self.statement = candidate("Alex Rivera planning trip to Japan.", semantic=0.9, evidence=self.event,
                                   statement=True, subject="I", object="Japan")
        self.other = candidate("Unrelated memory about rust.", semantic=0.7, evidence=uuid4())

    def test_the_claim_takes_the_better_score_and_the_statement_leaves(self):
        ranked = scored(self.claim, self.statement, self.other)
        statement_score = by_id(ranked, self.statement).composite_score
        assert ranked[0].node.id == self.statement.node.id

        folded, excluded, changed = fold_claim_statements(ranked)

        assert changed and [c.node.id for c in folded] == [self.claim.node.id, self.other.node.id]
        assert folded[0].composite_score == statement_score > by_id(ranked, self.claim).composite_score
        [gone] = excluded
        assert (gone.node_id, gone.reason) == (self.statement.node.id, FOLD_REASON)
        assert gone.composite_score == statement_score

    def test_the_new_score_replays_and_names_the_statement_as_its_lineage(self):
        ranked = scored(self.claim, self.statement, self.other)
        [folded, _] = fold_claim_statements(ranked)[0]
        provenance = folded.score_provenance
        assert provenance.replay_score() == folded.composite_score
        assert provenance.adjustments[-1].kind == "claim_statement_fold"
        assert provenance.adjustments[-1].source_node_id == self.statement.node.id
        assert folded.score_trace is None
        assert folded.context_relevance >= self.statement.semantic_score

    def test_a_statement_that_scores_lower_just_leaves(self):
        low = candidate("Alex Rivera planning trip to Japan.", semantic=0.1, evidence=self.event, statement=True,
                        subject="I", object="Japan")
        ranked = scored(self.claim, low, self.other)
        before = by_id(ranked, self.claim)

        folded, excluded, changed = fold_claim_statements(ranked)

        assert not changed and [e.node_id for e in excluded] == [low.node.id]
        assert by_id(folded, self.claim).composite_score == before.composite_score
        assert by_id(folded, self.claim).score_provenance is before.score_provenance
        assert [c.node.id for c in folded] == [c.node.id for c in ranked if c.node.id != low.node.id]

    def test_the_best_of_several_statements_counts(self):
        weaker = candidate("The speaker planning trip.", semantic=0.8, evidence=self.event, statement=True,
                           subject="I", object="Japan")
        ranked = scored(self.claim, self.statement, weaker)
        folded, excluded, _ = fold_claim_statements(ranked)
        assert [c.node.id for c in folded] == [self.claim.node.id]
        assert {e.node_id for e in excluded} == {self.statement.node.id, weaker.node.id}
        assert folded[0].composite_score == by_id(ranked, self.statement).composite_score
        assert folded[0].score_provenance.adjustments[-1].source_node_id == self.statement.node.id

    def test_a_statement_with_no_claim_that_holds_its_object_stays(self):
        stranger = candidate("Alex Rivera likes tea.", semantic=0.9, evidence=self.event, statement=True,
                             subject="I", object="tea")
        ranked = scored(self.claim, stranger, self.other)
        folded, excluded, changed = fold_claim_statements(ranked)
        assert folded is ranked and excluded == [] and not changed

    def test_a_claim_of_another_event_is_not_its_source(self):
        elsewhere = candidate(SENTENCE, semantic=0.5, evidence=uuid4(), subject="I", object="Japan")
        ranked = scored(elsewhere, self.statement)
        folded, excluded, _ = fold_claim_statements(ranked)
        assert folded is ranked and excluded == []

    def test_a_statement_without_evidence_stays(self):
        loose = candidate("Alex Rivera planning trip to Japan.", semantic=0.9, evidence=None, statement=True,
                          subject="I", object="Japan")
        loose_claim = candidate(SENTENCE, semantic=0.5, evidence=None, subject="I", object="Japan")
        ranked = scored(loose, loose_claim)
        assert fold_claim_statements(ranked)[0] is ranked

    def test_notes_and_entities_are_never_the_source(self):
        note = candidate(SENTENCE, semantic=0.5, evidence=self.event, node_type=NodeType.NOTE)
        entity = candidate("Japan", semantic=0.5, evidence=self.event, node_type=NodeType.ENTITY)
        ranked = scored(note, entity, self.statement)
        assert fold_claim_statements(ranked)[0] is ranked

    def test_a_claim_that_holds_the_subject_too_wins(self):
        only_object = candidate("Japan is far.", semantic=0.9, evidence=self.event, subject="Dana", object="Japan")
        statement = candidate("Jordan going to Japan.", semantic=0.95, evidence=self.event, statement=True,
                              subject="Jordan", object="Japan")
        both = candidate(SENTENCE, semantic=0.2, evidence=self.event)
        ranked = scored(only_object, both, statement)
        folded, _, _ = fold_claim_statements(ranked)
        assert {c.node.id for c in folded} == {only_object.node.id, both.node.id}
        assert by_id(folded, both).composite_score == by_id(ranked, statement).composite_score
        assert by_id(folded, only_object).composite_score == by_id(ranked, only_object).composite_score

    def test_a_word_inside_a_longer_word_does_not_hold_the_object(self):
        statement = candidate("Alex Rivera dislikes ham.", semantic=0.9, evidence=self.event, statement=True,
                              subject="I", object="ham")
        claim = candidate("I dislike hamburgers.", semantic=0.5, evidence=self.event)
        ranked = scored(claim, statement)
        assert fold_claim_statements(ranked)[0] is ranked

    def test_case_and_spacing_do_not_matter(self):
        statement = candidate("Alex Rivera has pet Biscuit.", semantic=0.9, evidence=self.event, statement=True,
                              subject="I", object="  BISCUIT ")
        claim = candidate("Our golden retriever,   biscuit, is 3.", semantic=0.5, evidence=self.event)
        folded, excluded, changed = fold_claim_statements(scored(claim, statement))
        assert changed and [c.node.id for c in folded] == [claim.node.id] and len(excluded) == 1

    def test_a_list_without_statements_comes_back_unchanged(self):
        ranked = scored(self.claim, self.other)
        assert fold_claim_statements(ranked) == (ranked, [], False)


class TestOption:
    def test_it_is_on_in_the_product_defaults_and_off_in_a_bare_packing_config(self, monkeypatch):
        from tests.conftest import STRUCTURE_OPTIONS

        for name in STRUCTURE_OPTIONS:
            monkeypatch.delenv(name)
        # On since 2026-10-19 in the alpha channel (0.16.0a3); the fast gate decided it.
        assert PackingConfig().folded_records_keep_rank is False
        assert PRMEConfig(_env_file=None).packing.folded_records_keep_rank is True
        assert "folded_records_keep_rank" not in PackingConfig().model_dump()
        assert PackingConfig(folded_records_keep_rank=True).model_dump()["folded_records_keep_rank"] is True

    def test_an_environment_variable_turns_it_on(self, monkeypatch):
        monkeypatch.setenv("PRME_PACKING__FOLDED_RECORDS_KEEP_RANK", "true")
        assert PRMEConfig(_env_file=None).packing.folded_records_keep_rank is True


def order_of(bundle):
    return [candidate.node.id.int for values in bundle.sections.values() for candidate in values]


class TestPackingKeepsTheRank:
    """A record that folds earlier packed records takes the place of the earliest one."""

    def pack(self, *records, **updates):
        packing = reader(fold_repeated_text=True, multipath_ordering="score", **updates)
        return pack_context(list(records), packing)

    def test_the_turn_is_packed_at_its_own_place_without_the_option(self):
        other = record(20, OTHER, .8, source=2)
        bundle = self.pack(claims(.9)[0], other, turn(.7))
        assert order_of(bundle) == [20, 1]

    def test_the_turn_takes_the_place_of_the_claim_it_folds_with_the_option(self):
        other = record(20, OTHER, .8, source=2)
        bundle = self.pack(claims(.9)[0], other, turn(.7), folded_records_keep_rank=True)
        assert order_of(bundle) == [1, 20]
        assert packed_ids(bundle) == {1, 20} and {i.int for i in bundle.excluded_ids} == {10}
        assert bundle.rendered_context.count(TURN_SENTENCE) == 1

    def test_it_takes_the_earliest_of_several_folded_records(self):
        first, second = claims(.9, .8)
        bundle = self.pack(first, record(20, OTHER, .85, source=2), second, turn(.7), folded_records_keep_rank=True)
        assert order_of(bundle) == [1, 20]

    def test_a_record_that_folds_nothing_keeps_its_place(self):
        stays = record(10, TURN_SENTENCE, .9, source=2)
        other = record(20, OTHER, .8, source=3)
        bundle = self.pack(stays, other, turn(.7), folded_records_keep_rank=True)
        assert order_of(bundle) == [10, 20, 1]

    def test_the_tokens_and_the_records_do_not_change(self):
        records = [claims(.9)[0], record(20, OTHER, .8, source=2), turn(.7)]
        off, on = self.pack(*records), self.pack(*records, folded_records_keep_rank=True)
        assert packed_ids(off) == packed_ids(on) and off.tokens_used == on.tokens_used
        assert sorted(lines(off)) == sorted(lines(on))


def fact(subject, predicate, obj, **extra):
    return {"subject": subject, "predicate": predicate, "object": obj, "polarity": "positive",
            "evidence_quote": SENTENCE, **extra}


EXTRACTED = ExtractionResult.model_validate({
    "entities": [{"name": "Jordan", "entity_type": "person"}, {"name": "Japan", "entity_type": "location"}],
    "facts": [fact("I", "has_partner", "Jordan", object_entity_type="person"),
              fact("I", "planning_trip_to", "Japan", object_entity_type="location")],
})


def statements_in(response):
    return [c for c in response.results if (c.node.metadata or {}).get("grounding_method") == "claim_statement_v1"]


async def retrieve_after_ingest(base, user, *, fold):
    """Ingest one sentence that yields two claims, then ask about the trip."""
    enabled = base.model_copy(update={
        "enable_ingest_claim_fixes": True,
        "packing": base.packing.model_copy(update={"folded_records_keep_rank": fold}),
    })
    async with MemoryEngine.open(enabled) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=EXTRACTED)
        await engine.ingest(SENTENCE, user_id=user, session_id="trip", wait_for_extraction=True)
        await engine.process_pending(user_id=user, budget_ms=5000)
        response = await engine.retrieve("Who is planning a trip to Japan?", user_id=user, min_score=0)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
    return response, receipt


async def test_a_statement_leaves_the_results_when_the_option_is_on(config, user):
    off, off_receipt = await retrieve_after_ingest(config, f"{user}-off", fold=False)
    statements = statements_in(off)
    [claim] = [c for c in off.results if c.node.content == SENTENCE and c.node.node_type == NodeType.FACT]
    assert len(statements) == 1 and not off.excluded
    assert "folded_records_keep_rank" not in off_receipt.packing.model_dump()

    on, on_receipt = await retrieve_after_ingest(config, f"{user}-on", fold=True)
    [kept] = [c for c in on.results if c.node.content == SENTENCE and c.node.node_type == NodeType.FACT]
    assert not statements_in(on)
    assert [e.reason for e in on.excluded] == [FOLD_REASON]
    # The claim scores at least as well as the better of the two did before.
    assert kept.composite_score >= max(claim.composite_score, statements[0].composite_score)
    assert on.excluded[0].composite_score <= kept.composite_score
    assert on_receipt.packing.folded_records_keep_rank is True
    assert on_receipt.replay_ranking() == tuple(c.node.id for c in on.results)
    assert statements[0].node.content not in on.bundle.render()


async def test_the_option_changes_nothing_for_memories_without_statements(config, user):
    async with MemoryEngine.open(config.model_copy(update={
        "packing": config.packing.model_copy(update={"folded_records_keep_rank": True}),
    })) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=EXTRACTED)
        await engine.ingest(SENTENCE, user_id=user, session_id="trip", wait_for_extraction=True)
        await engine.process_pending(user_id=user, budget_ms=5000)
        on = await engine.retrieve("Who is planning a trip to Japan?", user_id=user, min_score=0)
        engine._retrieval_pipeline._packing_config = engine._retrieval_pipeline._packing_config.model_copy(
            update={"folded_records_keep_rank": False})
        off = await engine.retrieve("Who is planning a trip to Japan?", user_id=user, min_score=0)
    assert [c.node.id for c in on.results] == [c.node.id for c in off.results]
    assert [c.composite_score for c in on.results] == [c.composite_score for c in off.results]
    assert on.excluded == off.excluded == []
