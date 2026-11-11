"""Caller-stated facts: ``ingest(content, assertions=[...])`` writes typed claims with no model call.

A structured source (a robot's perception stack, a form) states its facts, and ingestion turns each into the
claim typed extraction would make from a sentence saying it (``prme.ingestion.assertions``). These tests cover
the validation before admission, the records it derives compared with a typed extraction's, that no model is
asked, that a stated source never joins a model batch, the durable job and the saved record, ``reingest()``
with settings of its own, the ledger reading the claims, the refusals, and ``MemoryClient``. No model call.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prme import MemoryEngine
from prme.client import MemoryClient
from prme.ingestion.assertions import (
    ASSERTIONS_KEY,
    STRUCTURED_MODEL,
    STRUCTURED_PROVIDER,
    prepare_assertions,
    render_content,
    structured_result,
)
from prme.retrieval.beliefs import BELIEF_PATH
from prme.types import LifecycleState, NodeType
from tests import test_durable_ingestion
from tests.test_derivation_version import canonical
from tests.test_durable_ingestion import MockEmbeddingProvider

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
MUG = {"subject": "red mug", "subject_type": "product", "relation": "located_in", "object": "kitchen counter",
       "object_type": "location", "status": "current"}
SENTENCE = "The red mug is on the kitchen counter."


def assertion(**changes) -> dict:
    return {**MUG, **changes}


class RecordingProvider:
    """An extraction provider that records every request and answers from a table, or refuses every call."""

    def __init__(self, answers: dict | None = None, *, model_name: str = "recording-1") -> None:
        self.answers = answers or {}
        self.batches: list[list[str]] = []
        self.provider_name, self.model_name = "recording", model_name

    async def extract(self, content, *, role="user", **_options):
        raise AssertionError(f"no single-turn extraction expected: {content!r}")

    async def extract_batch(self, turns, *, memories=(), typed=False):
        self.batches.append([turn.content for turn in turns])
        empty = {"entities": [], "facts": [], "relationships": []}
        return {"turns": [{**self.answers.get(turn.content, empty), "turn": turn.index} for turn in turns]}

    async def repair_pronouns(self, window, claims, *, speaker=None):
        raise AssertionError("no pronoun repair expected")


@pytest.fixture
def provider(monkeypatch):
    """The extraction provider every engine of the test gets; replace ``provider.answers`` to answer turns."""
    made = RecordingProvider()
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: made)
    return made


def batched(config):
    return config.model_copy(update={"extraction_batch_target_tokens": 2048})


async def facts(engine, owner, *, every=False):
    states = list(LifecycleState) if every else None
    nodes = await engine._graph_store.query_nodes(user_id=owner, node_type=NodeType.FACT, lifecycle_states=states,
                                                  limit=1000)
    return sorted(nodes, key=lambda node: (node.event_time or node.created_at, str(node.id)))


# --- validation before admission -----------------------------------------------------------------------------------


def test_content_is_rendered_when_absent_and_names_every_subject_and_object():
    for absent in (None, "", "   "):
        content, prepared = prepare_assertions(absent, [MUG, {
            "subject": "red mug", "subject_type": "product", "relation": "has", "predicate": "temperature",
            "object": "hot"}])
        assert content == "Red mug is located in kitchen counter. Red mug temperature: hot."
        assert prepared[0] == {**MUG, "polarity": "positive"} and prepared[1]["predicate"] == "temperature"


def test_rendered_content_shows_a_negation_a_role_and_a_status_other_than_current():
    content, _ = prepare_assertions(None, [
        assertion(polarity="negative"),
        {"subject": "Sam", "subject_type": "person", "relation": "kin_of", "object": "Priya",
         "object_type": "person", "role": "sister", "status": "past"},
    ])
    assert content == ("It is not the case that red mug is located in kitchen counter. "
                       "Sam is related to Priya (sister, past).")


@pytest.mark.parametrize("subject, content", [
    ("the red mug", SENTENCE),
    ("The red mug", "Red mug: kitchen counter."),
    ("red mug", SENTENCE),
    ("a red mug", "Seen: red mug on the kitchen counter."),
])
def test_a_leading_article_is_dropped_and_the_mention_holds_with_or_without_it(subject, content):
    stored, prepared = prepare_assertions(content, [assertion(subject=subject, object="the kitchen counter")])
    assert stored == content
    assert (prepared[0]["subject"], prepared[0]["object"]) == ("red mug", "kitchen counter")


def test_a_literal_value_keeps_its_words():
    _, prepared = prepare_assertions("The tea is a bit hot.", [{
        "subject": "the tea", "subject_type": "product", "relation": "has", "predicate": "temperature",
        "object": "a bit hot"}])
    assert (prepared[0]["subject"], prepared[0]["object"]) == ("tea", "a bit hot")


@pytest.mark.parametrize("changes, message", [
    ({"relation": "sits_on"}, "is not in the vocabulary"),
    ({"status": "maybe"}, "status 'maybe' is not one of"),
    ({"polarity": "neutral"}, "polarity"),
    ({"role": "Sister!"}, "role 'Sister!'"),
    ({"subject_type": "robot"}, "subject_type 'robot'"),
    ({"object_type": None}, "object_type must be one of"),
    ({"object_type": "gadget"}, "object_type must be one of"),
    ({"predicate": "place"}, "predicate is only for relation 'has'"),
    ({"relation": "has", "object_type": None}, "relation 'has' needs a predicate"),
    ({"relation": "has", "predicate": "place"}, "relation 'has' takes a literal object"),
    ({"subject": ""}, "subject: must be nonempty text"),
    ({"confidence": 1.0}, "confidence"),
    ({"subject": "blue cup"}, "does not mention its subject 'blue cup'"),
    ({"object": "sink"}, "does not mention its object 'sink'"),
])
def test_a_bad_assertion_is_refused_with_its_index_and_reason(changes, message):
    with pytest.raises(ValueError, match="assertions\\[1\\]") as refused:
        prepare_assertions(SENTENCE, [MUG, assertion(**changes)])
    assert message in str(refused.value)


def test_an_empty_list_is_refused():
    with pytest.raises(ValueError, match="at least one"):
        prepare_assertions(SENTENCE, [])


def test_the_result_holds_the_entities_and_one_typed_fact_per_assertion():
    content, prepared = prepare_assertions(None, [MUG, assertion(relation="owns", subject="Sam",
                                                                   subject_type="person", object="red mug",
                                                                   object_type="product", status=None)])
    result = structured_result(content, {ASSERTIONS_KEY: {"version": 1, "items": prepared}})
    assert [(entity.name, entity.entity_type) for entity in result.entities] == [
        ("red mug", "product"), ("kitchen counter", "location"), ("Sam", "person")]
    assert [(fact.subject, fact.predicate, fact.object, fact.relation, fact.status) for fact in result.facts] == [
        ("red mug", "located_in", "kitchen counter", "located_in", "current"),
        ("Sam", "owns", "red mug", "owns", None)]
    assert all(fact.evidence_quote is None and fact.epistemic_type == "asserted" for fact in result.facts)
    assert render_content([]) == ""


# --- the records -----------------------------------------------------------------------------------------------------


async def test_a_stated_fact_derives_the_records_typed_extraction_derives(tmp_path, config, user, provider):
    """The same claim twice: from a typed extraction (the option on) and stated (the option off)."""
    content, prepared = prepare_assertions(SENTENCE, [MUG])
    stated = structured_result(content, {ASSERTIONS_KEY: {"version": 1, "items": prepared}})
    answer = stated.model_dump(mode="json", exclude_none=True)
    # A model's claim cites its passage; here the passage is the whole one-sentence content, as a stated claim reads.
    answer["facts"] = [{**fact, "evidence_quote": SENTENCE} for fact in answer["facts"]]
    provider.answers = {SENTENCE: {**answer, "relationships": []}}

    async def dump(settings, owner, **ingest_options):
        # Each side has its own owner: on PostgreSQL both write to the one test database.
        async with MemoryEngine.open(settings) as engine:
            await engine.ingest(SENTENCE, user_id=owner, role="system", session_id="s1", event_time=T0,
                                wait_for_extraction=True, **ingest_options)
            nodes = await engine.query_nodes(user_id=owner, limit=1000)
            edges = await engine._graph_store.get_edges(node_ids=[str(node.id) for node in nodes])
            events = await engine.get_events(user_id=owner, limit=100)
            records = canonical(events, nodes, edges)
            for row in (*records["nodes"], *records["edges"]):
                row.pop("user_id", None)
                if isinstance(row.get("metadata"), dict):
                    row["metadata"].pop(ASSERTIONS_KEY, None)
            return records, nodes

    typed = batched(config).model_copy(update={"enable_typed_extraction": True})
    extracted, _ = await dump(typed, user)
    asked = list(provider.batches)
    plain = batched(config).model_copy(update={
        "db_path": str(tmp_path / "other.duckdb"), "vector_path": str(tmp_path / "other.usearch"),
        "lexical_path": str(tmp_path / "other-lexical"), "enable_typed_extraction": False})
    (tmp_path / "other-lexical").mkdir()
    from_assertions, nodes = await dump(plain, user + "-stated", assertions=[MUG])

    assert asked == [[SENTENCE]] and provider.batches == asked  # the stated source asked nothing
    assert from_assertions == extracted
    claim = next(node for node in nodes if node.node_type == NodeType.FACT)
    # Typed fields are written for a stated claim with typed extraction off.
    assert (claim.metadata["relation"], claim.metadata["status"]) == ("located_in", "current")


async def test_a_stated_source_is_never_sent_to_the_model_and_never_joins_a_batch(config, user, provider):
    async with MemoryEngine.open(batched(config)) as engine:
        ids = await engine.ingest_batch([
            {"content": "I moved my keys to the hallway shelf.", "role": "user"},
            {"content": SENTENCE, "role": "system", "assertions": [MUG]},
            {"content": "Then I made tea.", "role": "user"},
        ], user_id=user, session_id="s1", wait_for_extraction=True)
        records = [await engine._event_store.get_extraction(event_id, user_id=user) for event_id in ids]
        event = await engine._event_store.get(ids[1])
        # The saved record answers a second load with no request (as after a restart).
        loaded = await engine._pipeline._extract_or_load(event)
    assert provider.batches == [["I moved my keys to the hallway shelf.", "Then I made tea."]]
    assert [(record.provider, record.model) for record in records] == [
        ("recording", "recording-1"), (STRUCTURED_PROVIDER, STRUCTURED_MODEL), ("recording", "recording-1")]
    assert loaded.model_dump(mode="json") == records[1].result


async def test_the_durable_job_of_a_stated_source_completes_with_no_model(config, user, provider):
    async with MemoryEngine.open(batched(config)) as engine:
        event_id = await engine.ingest(SENTENCE, user_id=user, assertions=[MUG])  # queued, not waited for
        result = await engine._pipeline.process_extractions(user_id=user)
        status = await engine._event_store.extraction_work.status(event_id, user_id=user)
        claims = await facts(engine, user)
    assert (result.processed, result.failed, status.status) == (1, 0, "complete")
    assert provider.batches == [] and [claim.metadata["relation"] for claim in claims] == ["located_in"]


async def test_reingest_derives_a_stated_source_again_with_no_model_and_keeps_it_current_across_models(
        tmp_path, config, user, provider, monkeypatch):
    settings = batched(config).model_copy(update={"enable_reingest": True})
    async with MemoryEngine.open(settings) as engine:
        await engine.ingest(SENTENCE, user_id=user, event_time=T0, assertions=[MUG], wait_for_extraction=True)
        await engine.ingest(None, user_id=user, event_time=T0 + timedelta(hours=1),
                            assertions=[assertion(object="dishwasher")], wait_for_extraction=True)
        await engine.ingest("I made tea.", user_id=user, event_time=T0 + timedelta(hours=2), wait_for_extraction=True)
        asked = len(provider.batches)
        first = await engine.reingest(user)
        again = await engine.reingest(user)
        claims = await facts(engine, user)
    # Three sources, one model request: the model source alone is asked.
    assert (first.total, first.processed, first.failed, first.model_calls) == (3, 3, 0, 1)
    assert len(provider.batches) == asked + 1
    assert (again.current, again.processed, again.model_calls) == (3, 0, 0)
    assert sorted(claim.metadata["object"] for claim in claims) == ["dishwasher", "kitchen counter"]
    assert all(claim.metadata["relation"] == "located_in" for claim in claims)

    other = RecordingProvider(model_name="recording-2")
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: other)
    async with MemoryEngine.open(settings) as engine:
        changed = await engine.reingest(user)
    # A new model makes the model source stale; the stated sources stay current.
    assert (changed.current, changed.processed, changed.model_calls) == (2, 1, 1)


async def test_the_ledger_reads_stated_claims_with_no_special_case(config, user, provider):
    settings = config.model_copy(update={
        "enable_belief_state": True, "packing": config.packing.model_copy(update={"graph_seed_words": True})})
    async with MemoryEngine.open(settings) as engine:
        for minutes, place in ((0, "kitchen counter"), (10, "dishwasher"), (20, "office desk")):
            await engine.ingest(f"The red mug is on the {place}.", user_id=user, role="system",
                                event_time=T0 + timedelta(minutes=minutes), assertions=[assertion(object=place)],
                                wait_for_extraction=True)
        response = await engine.retrieve("Where is the red mug now?", user_id=user,
                                         reference_time=T0 + timedelta(hours=1))
    placed = [(result.belief_state, result.node.content) for result in response.results if BELIEF_PATH in result.paths]
    assert placed == [("current", "The red mug is on the office desk."),
                      ("earlier", "The red mug is on the dishwasher."),
                      ("earlier", "The red mug is on the kitchen counter.")]


async def test_an_exactly_repeated_stated_fact_merges_into_one_current_claim(config, user, provider):
    """A patrol's repeat: with enable_claim_merge one claim stays current, citing every observation."""
    settings = config.model_copy(update={"enable_claim_merge": True})
    async with MemoryEngine.open(settings) as engine:
        for minutes in (0, 10, 20):
            await engine.ingest(SENTENCE, user_id=user, role="system", session_id="patrol",
                                event_time=T0 + timedelta(minutes=minutes), assertions=[MUG],
                                wait_for_extraction=True)
        current = await facts(engine, user)
        every = await facts(engine, user, every=True)
    assert len(current) == 1 and len(current[0].evidence_refs) == 3
    assert sorted(node.lifecycle_state.value for node in every) == ["superseded", "superseded", "tentative"]


# --- refusals and the client -----------------------------------------------------------------------------------------


async def test_assertions_are_refused_under_a_privacy_policy_before_anything_is_admitted(config, user, provider):
    from tests.privacy_fakes import make_runtime, policy_for

    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="privacy policy"):
            await engine.ingest(SENTENCE, user_id=user, assertions=[MUG], policy=policy_for(make_runtime()))
        with pytest.raises(ValueError, match="messages\\[0\\]: assertions are not available under a privacy"):
            await engine.ingest_batch([{"content": SENTENCE, "role": "system", "assertions": [MUG]}],
                                      user_id=user, policy=policy_for(make_runtime()))
        assert await engine.get_events(user_id=user, limit=10) == []


async def test_a_bad_message_in_a_batch_admits_none_of_the_batch(config, user, provider):
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="messages\\[1\\]: assertions\\[0\\]: the content does not mention"):
            await engine.ingest_batch([
                {"content": SENTENCE, "role": "system", "assertions": [MUG]},
                {"content": "The kettle is on.", "role": "system", "assertions": [MUG]},
            ], user_id=user)
        assert await engine.get_events(user_id=user, limit=10) == []


async def test_the_reserved_metadata_key_is_refused(config, user, provider):
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="reserved"):
            await engine.ingest(SENTENCE, user_id=user, metadata={ASSERTIONS_KEY: {"version": 1, "items": [MUG]}})
        with pytest.raises(ValueError, match="messages\\[0\\]: metadata.prme_assertions_v1 is reserved"):
            await engine.ingest_batch([{"content": SENTENCE, "role": "user",
                                        "metadata": {ASSERTIONS_KEY: {}}}], user_id=user)
        with pytest.raises(ValueError, match="content is required"):
            await engine.ingest(None, user_id=user)
        with pytest.raises(ValueError, match="list of mappings"):
            await engine.ingest(SENTENCE, user_id=user, assertions=MUG)
        assert await engine.get_events(user_id=user, limit=10) == []


def test_the_client_passes_assertions_through(tmp_path, provider):
    client = MemoryClient(str(tmp_path / "pack"))
    try:
        client.ingest(None, user_id="robot", assertions=[MUG])
        claims = client.query_nodes(user_id="robot", node_type=NodeType.FACT)
    finally:
        client.close()
    assert [(claim.metadata["relation"], claim.metadata["object"]) for claim in claims] == [
        ("located_in", "kitchen counter")]
