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
    ASSERTION_VERSION,
    ASSERTIONS_KEY,
    STRUCTURED_MODEL,
    STRUCTURED_PROVIDER,
    Held,
    prepare_assertions,
    render_content,
    stated_text,
    structured_result,
)
from prme.retrieval.beliefs import BELIEF_PATH
from prme.types import EdgeType, LifecycleState, NodeType, Scope
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
        assert content == "Red mug is located in kitchen counter. Red mug's temperature is hot."
        assert prepared[0] == {**MUG, "polarity": "positive", "found": {"subject": "text", "object": "text"}}
        assert prepared[1]["predicate"] == "temperature"


def test_rendered_content_shows_a_negation_a_role_and_a_status_other_than_current():
    content, _ = prepare_assertions(None, [
        assertion(polarity="negative"),
        {"subject": "Sam", "subject_type": "person", "relation": "kin_of", "object": "Priya",
         "object_type": "person", "role": "sister", "status": "past"},
    ])
    assert content == "Red mug is not located in kitchen counter. Sam used to be related to Priya (sister)."


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
    ({"subject": "blue cup"}, "does not mention its subject 'blue cup', and it is neither the speaker nor an entity"),
    ({"object": "sink"}, "does not mention its object 'sink'"),
    ({"time": "May 4"}, "time 'May 4' must be an ISO date"),
    ({"time": "2026-05-04T09:30:00"}, "must be an ISO date (2026-05-04) or an ISO datetime with its offset"),
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
    result = structured_result(content, {ASSERTIONS_KEY: {"version": ASSERTION_VERSION, "items": prepared}})
    assert [(entity.name, entity.entity_type) for entity in result.entities] == [
        ("red mug", "product"), ("kitchen counter", "location"), ("Sam", "person")]
    assert [(fact.subject, fact.predicate, fact.object, fact.relation, fact.status) for fact in result.facts] == [
        ("red mug", "located_in", "kitchen counter", "located_in", "current"),
        ("Sam", "owns", "red mug", "owns", None)]
    assert all(fact.evidence_quote == content and fact.epistemic_type == "asserted" for fact in result.facts)
    assert render_content([]) == ""


# --- the records -----------------------------------------------------------------------------------------------------


async def test_a_stated_fact_derives_the_records_typed_extraction_derives(tmp_path, config, user, provider):
    """The same claim twice: from a typed extraction (the option on) and stated (the option off)."""
    content, prepared = prepare_assertions(SENTENCE, [MUG])
    stated = structured_result(content, {ASSERTIONS_KEY: {"version": ASSERTION_VERSION, "items": prepared}})
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
            texts = []
            for row in (*records["nodes"], *records["edges"]):
                row.pop("user_id", None)
                if isinstance(row.get("metadata"), dict):
                    row["metadata"].pop(ASSERTIONS_KEY, None)
                    if row.get("node_type") == "fact":
                        # The claim's text and how it was made are compared apart, below.
                        texts.append((row.pop("content"), row["metadata"].pop("grounding_method")))
            return records, nodes, texts

    typed = batched(config).model_copy(update={"enable_typed_extraction": True})
    extracted, _, extracted_texts = await dump(typed, user)
    asked = list(provider.batches)
    plain = batched(config).model_copy(update={
        "db_path": str(tmp_path / "other.duckdb"), "vector_path": str(tmp_path / "other.usearch"),
        "lexical_path": str(tmp_path / "other-lexical"), "enable_typed_extraction": False})
    (tmp_path / "other-lexical").mkdir()
    from_assertions, nodes, stated_texts = await dump(plain, user + "-stated", assertions=[MUG])

    assert asked == [[SENTENCE]] and provider.batches == asked  # the stated source asked nothing
    assert from_assertions == extracted
    # The one difference is how the text was made: a stated claim's text is the sentence naming its object.
    assert extracted_texts == [(SENTENCE, "claim_sentences_v1")]
    assert stated_texts == [(SENTENCE, "stated_sentence_v1")]
    claim = next(node for node in nodes if node.node_type == NodeType.FACT)
    assert claim.metadata["evidence_quote"] == SENTENCE
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


# --- the mention rule: the speaker, the owner's memory and a closure ---------------------------------------------------


def dana(**changes) -> dict:
    return {"subject": "Dana", "subject_type": "person", "relation": "works_at", "object": "Brightpath",
            "object_type": "organization", "status": "current", **changes}


DIET = {"subject": "Dana", "subject_type": "person", "relation": "has", "predicate": "diet", "object": "vegetarian",
        "status": "current"}


@pytest.mark.parametrize("content", ["I start at Brightpath on Monday.", "My first week at Brightpath went well.",
                                     "We both work at Brightpath now."])
def test_the_speaker_is_found_by_the_sources_first_person(content):
    _, prepared = prepare_assertions(content, [dana()], speaker="Dana")
    assert prepared[0]["found"] == {"subject": "speaker", "object": "text"}


@pytest.mark.parametrize("content, speaker, subject", [
    ("I start at Brightpath on Monday.", None, "Dana"),       # no speaker
    ("Brightpath called back.", "Dana", "Dana"),               # the speaker does not speak of themselves
    ("I start at Brightpath on Monday.", "Dana", "Sam"),       # someone the source does not name
])
def test_without_the_speaker_speaking_an_unnamed_subject_is_refused(content, speaker, subject):
    with pytest.raises(ValueError, match=f"assertions\\[0\\]: the content does not mention its subject '{subject}', "
                                         "and it is neither the speaker nor an entity the owner's memory holds"):
        prepare_assertions(content, [dana(subject=subject)], speaker=speaker)


def test_an_entity_the_memory_holds_is_found_by_its_name_and_type():
    chose = {"subject": "the Jordan", "subject_type": "person", "relation": "decided", "object": "Tokyo",
             "object_type": "location"}
    held = Held(entities=frozenset({("jordan", "person")}))
    _, prepared = prepare_assertions("We chose Tokyo.", [chose], held=held)
    assert (prepared[0]["subject"], prepared[0]["found"]) == ("Jordan", {"subject": "memory", "object": "text"})
    with pytest.raises(ValueError, match="its subject 'Jordan'"):
        prepare_assertions("We chose Tokyo.", [{**chose, "subject_type": "location"}], held=held)


def test_a_literal_value_is_in_the_text_or_repeats_a_live_claims_value():
    closed = {**DIET, "status": "past"}
    held = Held(values=frozenset({("dana", "person", "has:diet", "vegetarian")}))
    _, prepared = prepare_assertions("I eat fish now.", [closed], speaker="Dana", held=held)
    assert prepared[0]["found"] == {"subject": "speaker", "object": "closure"}
    for changes in ({"object": "vegan"}, {"predicate": "eating habits"}):
        with pytest.raises(ValueError, match="repeats no live claim's value at the same subject and attribute"):
            prepare_assertions("I eat fish now.", [{**closed, **changes}], speaker="Dana", held=held)
    # A literal value is never found as the speaker or in memory, only in the text or as a closure.
    with pytest.raises(ValueError, match="its object 'Dana'"):
        prepare_assertions("I eat fish now.", [{**DIET, "object": "Dana"}], speaker="Dana",
                           held=Held(entities=frozenset({("dana", "person")})))


async def facts_with_text(engine, owner):
    return [(claim.content, claim.metadata["object"], claim.lifecycle_state.value) for claim in
            await facts(engine, owner, every=True)]


async def stated_turns(engine, owner):
    """Three turns: the first names Jordan and Dana's diet, the second states a choice of Jordan's by "we", the third
    closes the diet. Returns the events in order."""
    trip = {"subject": "Jordan", "subject_type": "person", "relation": "visits", "object": "Japan",
            "object_type": "location", "status": "planned"}
    await engine.ingest("I'm Dana. My partner Jordan and I are going to Japan. I've been vegetarian for years.",
                        user_id=owner, role="user", speaker="Dana", event_time=T0, assertions=[trip, DIET],
                        wait_for_extraction=True)
    await engine.ingest("We chose Tokyo.", user_id=owner, role="user", speaker="Dana",
                        event_time=T0 + timedelta(days=1), wait_for_extraction=True, assertions=[
                            {"subject": "Jordan", "subject_type": "person", "relation": "decided", "object": "Tokyo",
                             "object_type": "location"}])
    await engine.ingest("I eat fish now, so I'm pescatarian.", user_id=owner, role="user", speaker="Dana",
                        event_time=T0 + timedelta(days=2), wait_for_extraction=True,
                        assertions=[{**DIET, "object": "pescatarian"}, {**DIET, "status": "past"}])
    return sorted(await engine.get_events(user_id=owner, limit=10), key=lambda event: event.event_time)


async def test_admission_reads_the_owners_memory_in_the_sources_scope(config, user, provider):
    async with MemoryEngine.open(config) as engine:
        events = await stated_turns(engine, user)
        chose = events[1].metadata[ASSERTIONS_KEY]["items"][0]
        assert chose["found"] == {"subject": "memory", "object": "text"}
        assert [item["found"] for item in events[2].metadata[ASSERTIONS_KEY]["items"]] == [
            {"subject": "speaker", "object": "text"}, {"subject": "speaker", "object": "closure"}]
        # Jordan's choice hangs on the Jordan the first turn named.
        jordan = next(node for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY, limit=100)
                      if node.content == "Jordan")
        edges = await engine._graph_store.get_edges(source_id=str(jordan.id))
        claims = await engine._graph_store.get_nodes([str(edge.target_id) for edge in edges
                                                      if edge.edge_type == EdgeType.HAS_FACT])
        assert sorted(claim.content for claim in claims) == ["My partner Jordan and I are going to Japan.",
                                                             "We chose Tokyo."]
        stated = [{key: value for key, value in chose.items() if key != "found"}]
        for options in ({"scope": Scope.PROJECT}, {"user_id": user + "-other"}):
            with pytest.raises(ValueError, match="its subject 'Jordan', and it is neither"):
                await engine.ingest("We chose Tokyo.", **{"user_id": user, **options}, assertions=stated)
        # Nothing was admitted by the refusals.
        assert len(await engine.get_events(user_id=user, limit=10)) == 3


async def test_a_rebuild_from_the_log_alone_accepts_what_admission_accepted(tmp_path, config, user, provider):
    """The later turns' events alone, replayed into an empty pack, derive the claims admission accepted."""
    async with MemoryEngine.open(config) as engine:
        events = await stated_turns(engine, user)
        records = [await engine._event_store.get_extraction(str(event.id), user_id=user) for event in events]
        replayed_ids = {event.id for event in events[1:]}
        before = sorted(claim.content for claim in await facts(engine, user, every=True)
                        if replayed_ids & set(claim.evidence_refs))
    empty = config.model_copy(update={
        "db_path": str(tmp_path / "rebuilt.duckdb"), "vector_path": str(tmp_path / "rebuilt.usearch"),
        "lexical_path": str(tmp_path / "rebuilt-lexical")})
    (tmp_path / "rebuilt-lexical").mkdir()
    async with MemoryEngine.open(empty) as engine:
        # No Jordan entity and no diet claim: the recorded verdicts decide, as the log holds them.
        for event in events[1:]:
            replayed = await engine._pipeline.ingest(
                event.content, user_id=user + "-rebuilt", role=event.role, session_id=event.session_id,
                metadata=event.metadata, event_time=event.event_time, wait_for_extraction=True)
            again = await engine._event_store.get_extraction(replayed, user_id=user + "-rebuilt")
            original = records[events.index(event)]
            assert (again.provider, again.model, again.result) == (original.provider, original.model, original.result)
        rebuilt = await facts_with_text(engine, user + "-rebuilt")
    # The closure's value is not in its turn, so its text is the whole turn, as the new value's is.
    assert sorted(text for text, *_ in rebuilt) == before == [
        "I eat fish now, so I'm pescatarian.", "I eat fish now, so I'm pescatarian.", "We chose Tokyo."]


async def test_reingest_derives_the_admitted_claims_again_with_no_model(config, user, provider):
    settings = config.model_copy(update={"enable_reingest": True})
    async with MemoryEngine.open(settings) as engine:
        await stated_turns(engine, user)
        before = sorted(text for text, _, state in await facts_with_text(engine, user) if state != "superseded")
        result = await engine.reingest(user)
        after = sorted(text for text, _, state in await facts_with_text(engine, user) if state != "superseded")
    assert (result.failed, result.model_calls) == (0, 0) and provider.batches == []
    assert after == before


def test_a_recorded_verdict_that_no_longer_holds_fails_the_source():
    content, prepared = prepare_assertions("I start at Brightpath on Monday.", [dana()], speaker="Dana")
    metadata = {ASSERTIONS_KEY: {"version": ASSERTION_VERSION, "items": prepared}}
    with pytest.raises(ValueError, match="does not mention its subject 'Dana'"):
        structured_result(content, metadata)  # the speaker was not stored with the source
    from prme.models.speaker import SPEAKER_METADATA_KEY

    assert structured_result(content, {**metadata, SPEAKER_METADATA_KEY: "Dana"}).facts[0].subject == "Dana"
    unrecorded = {ASSERTIONS_KEY: {"version": ASSERTION_VERSION, "items": [{**prepared[0], "found": None}]}}
    with pytest.raises(ValueError, match="do not record how each end was found"):
        structured_result(content, unrecorded)


# --- time ----------------------------------------------------------------------------------------------------------


def test_time_is_an_iso_date_or_a_datetime_with_its_offset():
    _, prepared = prepare_assertions(None, [dana(time="2026-05-04"), dana(time=" 2026-05-04T09:30:00-06:00 ")])
    assert [item["time"] for item in prepared] == ["2026-05-04", "2026-05-04T09:30:00-06:00"]


async def test_a_stated_time_is_the_claims_stated_time_and_without_one_the_sources(config, user, provider):
    async with MemoryEngine.open(config) as engine:
        await engine.ingest("I start at Brightpath on May 4, and Sam works at Northwind.", user_id=user, role="user",
                            speaker="Dana", event_time=T0, wait_for_extraction=True, assertions=[
                                dana(status="planned", time="2026-05-04"),
                                dana(subject="Sam", object="Northwind")])
        claims = {claim.metadata["subject"]: claim for claim in await facts(engine, user)}
    start = datetime(2026, 5, 4, tzinfo=timezone.utc)
    assert (claims["Dana"].event_time, claims["Dana"].valid_from) == (start, start)
    assert claims["Dana"].metadata["resolved_date"] == start.isoformat()
    assert (claims["Sam"].event_time, claims["Sam"].valid_from) == (T0, T0)
    assert "resolved_date" not in claims["Sam"].metadata


# --- the claim's text ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("changes, text", [
    ({}, "Dana works at Brightpath."),
    ({"status": None}, "Dana works at Brightpath."),
    ({"role": "analytics manager", "time": "2026-05-04"},
     "Dana works at Brightpath as analytics manager (since 2026-05-04)."),
    ({"status": "past", "object": "Northwind"}, "Dana used to work at Northwind."),
    ({"status": "superseded", "object": "Northwind"}, "Dana used to work at Northwind."),
    ({"status": "planned", "time": "2026-05-04"}, "Dana is going to work at Brightpath (from 2026-05-04)."),
    ({"status": "done", "relation": "visits", "object": "Japan", "object_type": "location", "time": "2026-03-20"},
     "Dana visited Japan (on 2026-03-20)."),
    ({"polarity": "negative", "relation": "owns", "object": "car", "object_type": "product"},
     "Dana does not own car."),
    ({"polarity": "negative", "relation": "lives_in", "object": "Denver", "object_type": "location",
      "status": "done"}, "Dana did not live in Denver."),
    ({"polarity": "negative", "relation": "allergic_to", "object": "peanuts", "object_type": "concept"},
     "Dana is not allergic to peanuts."),
    ({"polarity": "negative", "relation": "visits", "object": "Hiroshima", "object_type": "location",
      "status": "planned"}, "Dana is not going to visit Hiroshima."),
    ({"relation": "kin_of", "object": "Rachel", "object_type": "person", "role": "sister"},
     "Dana is related to Rachel (sister)."),
    ({"relation": "has", "predicate": "diet", "object": "pescatarian", "object_type": None},
     "Dana's diet is pescatarian."),
    ({"relation": "has", "predicate": "diet", "object": "vegetarian", "object_type": None, "status": "past"},
     "Dana's diet used to be vegetarian."),
    ({"relation": "has", "predicate": "half_marathon_time", "object": "1:58:30", "object_type": None,
      "status": "done", "time": "2026-05-17"}, "Dana's half marathon time was 1:58:30 (on 2026-05-17)."),
    ({"relation": "has", "predicate": "dental_coverage", "object": "included", "object_type": None,
      "polarity": "negative"}, "Dana's dental coverage is not included."),
])
def test_a_claim_sentence_says_each_status_in_plain_words(changes, text):
    content, _ = prepare_assertions(None, [dana(**changes)])
    assert content == text


@pytest.mark.parametrize("content, changes, text", [
    # The shortest run of sentences that names the object; the role and the time it does not show, in one
    # parenthesis, the role as the rendered sentence says it.
    ("I got the offer. Two weeks in at Brightpath and I like my team. Sam is jealous.",
     {"role": "analytics manager", "time": "2026-05-04"},
     "Two weeks in at Brightpath and I like my team. (as analytics manager; since 2026-05-04)"),
    # A field the sentence shows is not added again.
    ("I'm an analytics manager at Brightpath since 2026-05-04.", {"role": "analytics manager", "time": "2026-05-04"},
     "I'm an analytics manager at Brightpath since 2026-05-04."),
    ("Brightpath hired me.", {}, "Brightpath hired me."),
    ("Brightpath hired me.", {"status": "planned", "time": "2026-05-04"}, "Brightpath hired me. (from 2026-05-04)"),
    ("We had a girl last Thursday!", {"relation": "kin_of", "object": "Ivy", "object_type": "person",
                                      "role": "niece", "time": "2026-03-12"},
     "We had a girl last Thursday! (niece; since 2026-03-12)"),
])
def test_a_stated_claims_text_is_its_sentence_with_the_fields_it_does_not_show(content, changes, text):
    item = dana(**changes)
    assert stated_text(content, relation=item["relation"], obj=item["object"], role=item.get("role"),
                       status=item.get("status"), time=item.get("time")) == text


async def test_a_stated_claims_text_is_its_sentence_and_the_turn_stays_its_evidence(config, user, provider):
    turn = "Two weeks in at Brightpath and I already like my team."
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest(turn, user_id=user, role="user", speaker="Dana", event_time=T0,
                                       wait_for_extraction=True,
                                       assertions=[dana(role="analytics manager", time="2026-05-04")])
        await engine.ingest(None, user_id=user, event_time=T0 + timedelta(hours=1), wait_for_extraction=True,
                            assertions=[dana(subject="Sam", object="Northwind", role="engineer")])
        event = await engine._event_store.get(event_id)
        stated, rendered = await facts(engine, user)
    assert event.content == turn
    assert stated.content == f"{turn} (as analytics manager; since 2026-05-04)"
    assert (stated.metadata["evidence_quote"], stated.metadata["grounding_method"]) == (turn, "stated_sentence_v1")
    assert (stated.metadata["relation"], stated.metadata["role"], stated.metadata["status"]) == (
        "works_at", "analytics manager", "current")
    # With no content, the content and the claim's text are the rendered sentence.
    assert (rendered.content, rendered.metadata["grounding_method"]) == (
        "Sam works at Northwind as engineer.", "stated_fields_v1")
    assert rendered.metadata["evidence_quote"] == rendered.content


async def test_claims_of_one_sentence_share_its_text_and_stay_separate_claims(config, user, provider):
    """Claim merge reads the claim's fields, never its text: two claims of one sentence stay two claims."""
    turn = "My husband is Theo and my child is Nico."
    settings = config.model_copy(update={"enable_claim_merge": True})
    async with MemoryEngine.open(settings) as engine:
        await engine.ingest(turn, user_id=user, role="user", speaker="Maya", event_time=T0, wait_for_extraction=True,
                            assertions=[
                                dana(subject="Maya", relation="partner_of", object="Theo", object_type="person"),
                                dana(subject="Maya", relation="kin_of", object="Nico", object_type="person",
                                     role="child")])
        claims = await facts(engine, user, every=True)
    # The sentence shows the role "child", so neither claim adds anything: both have the sentence as their text.
    assert sorted((claim.content, claim.metadata["object"], claim.lifecycle_state.value) for claim in claims) == [
        (turn, "Nico", "tentative"), (turn, "Theo", "tentative")]


async def test_the_reader_keeps_a_stated_claim_whose_parenthesis_its_turn_lacks(config, user, provider):
    """fold_repeated_text folds a record only into one whose text holds it: a claim that adds a field stays."""
    turn = "I finished the Cherry Creek Half on Sunday in 1:58:30! Melanie ran it with me."
    settings = config.model_copy(update={"packing": config.packing.model_copy(update={
        "context_format": "reader", "fold_repeated_text": True})})
    async with MemoryEngine.open(settings) as engine:
        await engine.ingest(turn, user_id=user, role="user", speaker="Dana", event_time=T0, wait_for_extraction=True,
                            assertions=[
                                {"subject": "Dana", "subject_type": "person", "relation": "has",
                                 "predicate": "race_time", "object": "1:58:30", "status": "done",
                                 "time": "2026-05-17"},
                                dana(relation="friend_of", object="Melanie", object_type="person")])
        response = await engine.retrieve("What was Dana's half marathon time?", user_id=user,
                                         reference_time=T0 + timedelta(days=1))
    context = response.bundle.rendered_context
    dated = "I finished the Cherry Creek Half on Sunday in 1:58:30! (on 2026-05-17)"
    assert dated in context
    # The other claim's text is its sentence alone, which the turn shows: it folds into the turn.
    assert context.count("Melanie ran it with me.") == 1
