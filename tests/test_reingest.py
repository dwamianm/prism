"""reingest() re-derives one owner's stored sources after an engine change (prism-monorepo #278)."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from prme import MemoryClient, MemoryEngine, ReingestBusyError, ReingestDisabledError, ReingestResult
from prme.api.app import create_app
from prme.config import APIConfig, MCPConfig
from prme.ingestion.errors import ExtractionError
from prme.mcp.server import create_http_app
from prme.types import EdgeType, LifecycleState, NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 3, 2, 9, tzinfo=timezone.utc)
JAPAN = "Alex chose Tokyo, Kyoto, and Osaka for the trip to Japan."
DENVER = "Jordan moved to Denver last spring."
PUPPY = "Melanie adopted a puppy named Oscar."
LAKE = "Caroline paints sunsets over the lake."
TEXTS = [LAKE, PUPPY, DENVER, JAPAN]


class Scripted:
    """A batched model whose answers for a source's text can change between runs.

    ``says[text]`` lists the (subject, predicate, object) claims it finds in a
    turn with that text; a text it knows nothing about gets an empty entry.
    """

    def __init__(self, says=None, fail=None, places=None):
        self.says = dict(says or {})
        self.places = dict(places or {})
        self.calls = []
        self.fail = fail

    async def extract_batch(self, turns):
        self.calls.append([turn.content for turn in turns])
        if self.fail and self.fail(turns):
            raise ExtractionError("scripted failure", reason_code="APIConnectionError")
        entries = []
        for turn in turns:
            claims = self.says.get(turn.content, [])
            entities = {subject: "person" for subject, _, _ in claims}
            entities.update({name: "location" for name in self.places.get(turn.content, [])})
            entries.append({
                "turn": turn.index,
                "entities": [{"name": name, "entity_type": kind} for name, kind in entities.items()],
                "facts": [{"subject": subject, "subject_entity_type": "person", "predicate": predicate,
                           "object": value, "polarity": "positive", "epistemic_type": "observed",
                           "evidence_quote": turn.content} for subject, predicate, value in claims],
                "relationships": [],
            })
        return {"turns": entries}


FOUND = {
    LAKE: [("Caroline", "paints", "sunsets")],
    PUPPY: [("Melanie", "adopted", "a puppy named Oscar")],
    DENVER: [("Jordan", "moved_to", "Denver")],
    JAPAN: [("Alex", "chose_cities", "Tokyo, Kyoto, and Osaka")],
}


def settings(config, **changes):
    return config.model_copy(update={"extraction_batch_target_tokens": 512, "enable_reingest": True, **changes})


def install(engine, model):
    engine._pipeline._extraction_provider.extract_batch = model.extract_batch
    engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=AssertionError("one call per turn"))
    return model


async def store_all(engine, user, texts=TEXTS, *, sessions=("s1", "s1", None, None)):
    return [
        await engine.store(text, user_id=user, session_id=session, speaker="Alex",
                           event_time=T0 + timedelta(hours=hours))
        for hours, (text, session) in enumerate(zip(texts, sessions))
    ]


async def events_snapshot(engine, user):
    """The saved events and their raw notes, exactly as stored."""
    events = await engine.get_events(user_id=user, limit=1000)
    notes = await engine.query_nodes(user_id=user, node_type=NodeType.NOTE, limit=1000)
    return (sorted(event.model_dump_json() for event in events), sorted(note.model_dump_json() for note in notes))


async def claims(engine, user, *states):
    nodes = await engine.query_nodes(user_id=user, node_type=NodeType.FACT, limit=1000,
                                     **({"lifecycle_states": list(states)} if states else {}))
    return nodes


async def test_a_plainly_stored_memory_gets_entities_claims_and_links(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        events = await store_all(engine, user)
        before = await events_snapshot(engine, user)
        assert await engine.count_nodes(user_id=user) == 4

        dry = await engine.reingest(user, dry_run=True)
        # The first two share a session and a request; the others go alone.
        assert (dry.total, dry.current, dry.selected, dry.processed, dry.remaining) == (4, 0, 4, 0, 4)
        assert dry.model_calls == 3 and dry.source_tokens > 0 and dry.dry_run and dry.cursor is None
        assert model.calls == [] and await engine.count_nodes(user_id=user) == 4

        result = await engine.reingest(user)
        assert isinstance(result, ReingestResult)
        assert (result.total, result.selected, result.processed, result.failed, result.remaining) == (4, 4, 4, 0, 0)
        assert result.model_calls == 3 and result.source_tokens == dry.source_tokens and result.cursor is None
        assert model.calls == [[LAKE, PUPPY], [DENVER], [JAPAN]]

        stored = await claims(engine, user)
        assert sorted(node.content for node in stored) == sorted(TEXTS)
        assert {str(node.evidence_refs[0]) for node in stored} == set(events)
        entities = await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY, limit=100)
        assert sorted(node.content for node in entities) == ["Alex", "Caroline", "Jordan", "Melanie"]
        # Each claim hangs off its subject entity.
        for node in stored:
            subject = next(entity for entity in entities if entity.content == node.metadata["subject"])
            linked = await engine._graph_store.get_edges(source_id=str(subject.id), target_id=str(node.id))
            assert [edge.edge_type for edge in linked] == [EdgeType.HAS_FACT]
            assert node.metadata["reingest"]["source_event_id"] == str(node.evidence_refs[0])
        # The sources and their raw notes are exactly as stored.
        assert await events_snapshot(engine, user) == before
        # A source's own derivation journal is untouched: no work row and no receipt appeared.
        assert await engine.extraction_status(events[0], user_id=user) is None
        assert await engine._event_store.get_derivation_plan(events[0], user_id=user) is None


async def test_a_second_run_does_nothing(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        await store_all(engine, user)
        first = await engine.reingest(user)
        nodes = [node.model_dump_json() for node in await engine.query_nodes(user_id=user, limit=1000)]
        calls = len(model.calls)

        again = await engine.reingest(user)
        assert (again.total, again.current, again.selected, again.processed, again.remaining) == (4, 4, 0, 0, 0)
        assert again.model_calls == 0 and len(model.calls) == calls and again.fingerprint == first.fingerprint
        assert [node.model_dump_json() for node in await engine.query_nodes(user_id=user, limit=1000)] == nodes
        assert (await engine.reingest(user, dry_run=True)).model_calls == 0

    # The marker is saved with the memory, so a new engine over the same data agrees.
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        assert (await engine.reingest(user)).processed == 0 and model.calls == []


async def test_a_limit_and_its_cursor_continue_a_run(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        events = await store_all(engine, user, sessions=(None, None, None, None))
        first = await engine.reingest(user, limit=2)
        assert (first.selected, first.processed, first.remaining) == (2, 2, 2) and first.cursor
        assert len(await claims(engine, user)) == 2
        assert model.calls == [[LAKE], [PUPPY]]

        # Event order: the cursor and a fresh call both take the next two.
        for cursor in (first.cursor, None):
            dry = await engine.reingest(user, limit=2, cursor=cursor, dry_run=True)
            assert dry.selected == 2 and dry.remaining == 2 and dry.cursor is None
        second = await engine.reingest(user, limit=1, cursor=first.cursor)
        assert (second.selected, second.processed, second.remaining) == (1, 1, 1) and second.cursor
        last = await engine.reingest(user, cursor=second.cursor)
        assert (last.processed, last.remaining, last.cursor) == (1, 0, None)
        assert model.calls == [[LAKE], [PUPPY], [DENVER], [JAPAN]]
        assert {str(node.evidence_refs[0]) for node in await claims(engine, user)} == set(events)


async def test_a_run_that_stopped_continues_without_asking_the_model_again(config, user, monkeypatch):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        await store_all(engine, user, sessions=(None, None, None, None))
        real = engine._graph_store.commit_reingest
        failures = []

        async def flaky(plan, record):
            # The process stops after the model answered for the third memory and before its commit.
            if not failures and DENVER in [node.content for node in plan.nodes]:
                failures.append(plan.event_id)
                raise RuntimeError("process stopped")
            return await real(plan, record)

        monkeypatch.setattr(engine._graph_store, "commit_reingest", flaky)
        stopped = await engine.reingest(user)
        assert (stopped.processed, stopped.failed, stopped.remaining) == (3, 1, 1)
        assert stopped.model_calls == 4 and len(await claims(engine, user)) == 3

        asked = len(model.calls)
        finished = await engine.reingest(user)
        # Its saved model output planned and committed it: no request, no duplicate.
        assert (finished.processed, finished.failed, finished.remaining, finished.model_calls) == (1, 0, 0, 0)
        assert len(model.calls) == asked
        assert sorted(node.content for node in await claims(engine, user)) == sorted(TEXTS)


async def test_a_failed_request_leaves_its_sources_for_the_next_run(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND, fail=lambda turns: DENVER in turns[0].content))
        await store_all(engine, user, sessions=(None, None, None, None))
        partial = await engine.reingest(user)
        assert (partial.processed, partial.failed, partial.remaining) == (3, 1, 1)
        assert partial.model_calls == 4
        # A call without a cursor tries the source again.
        model.fail = None
        rest = await engine.reingest(user)
        assert (rest.processed, rest.remaining, rest.model_calls) == (1, 0, 1)


async def test_a_provider_that_does_not_answer_ends_the_run(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND, fail=lambda turns: True))
        await store_all(engine, user, sessions=(None, None, None, None))
        result = await engine.reingest(user)
        # Three requests in a row failed: the fourth source was not tried.
        assert (result.processed, result.failed, result.selected, result.remaining) == (0, 3, 3, 4)
        assert len(model.calls) == 3 and result.cursor is not None
        assert await engine.count_nodes(user_id=user) == 4


async def test_an_ingested_source_gets_new_records_and_the_old_ones_point_to_them(config, user):
    async with MemoryEngine.open(settings(config, extraction_batch_target_tokens=0)) as engine:
        old = Scripted({JAPAN: [("Alex", "chose_cities", "Tokyo"), ("Alex", "chose_cities", "Kyoto")]})
        async def extract(text, **_):
            return await _single(old, text)

        engine._pipeline._extraction_provider.extract = extract
        event_id = await engine.ingest(JAPAN, user_id=user, speaker="Alex", event_time=T0, wait_for_extraction=True)
        await engine.process_pending(user_id=user)
        first = await claims(engine, user)
        assert sorted(node.metadata["object"] for node in first) == ["Kyoto", "Tokyo"]
    before = None
    async with MemoryEngine.open(settings(config)) as engine:
        better = install(engine, Scripted({JAPAN: [("Alex", "chose_cities", "Tokyo"), ("Alex", "chose_cities", "Osaka")]}))
        before = await events_snapshot(engine, user)
        note_before = (await engine.get_node(event_id)).model_dump_json()

        result = await engine.reingest(user)
        assert (result.total, result.processed, result.model_calls) == (1, 1, 1)
        assert better.calls == [[JAPAN]]

        current = await claims(engine, user)
        assert sorted(node.metadata["object"] for node in current) == ["Osaka", "Tokyo"]
        everything = {node.id: node for node in await claims(engine, user, *LifecycleState)}
        tokyo_old = next(node for node in first if node.metadata["object"] == "Tokyo")
        kyoto_old = next(node for node in first if node.metadata["object"] == "Kyoto")
        tokyo_new = next(node for node in current if node.metadata["object"] == "Tokyo")
        # The claim the new derivation states again is superseded by it, with an edge that says so.
        assert everything[tokyo_old.id].lifecycle_state == LifecycleState.SUPERSEDED
        assert everything[tokyo_old.id].superseded_by == tokyo_new.id
        edges = await engine._graph_store.get_edges(source_id=str(tokyo_new.id), target_id=str(tokyo_old.id))
        assert [edge.edge_type for edge in edges] == [EdgeType.SUPERSEDES]
        # The one it no longer states is archived, with a pointer to the claims that replaced it.
        archived = everything[kyoto_old.id]
        assert archived.lifecycle_state == LifecycleState.ARCHIVED
        pointer = archived.metadata["reingest"]
        assert pointer["source_event_id"] == event_id and pointer["operation_id"]
        assert set(pointer["replaced_by"]) == {str(node.id) for node in current}
        # Neither shows up in retrieval, and the source and its note are exactly as they were.
        found = await engine.retrieve("Which cities did we choose in Japan?", user_id=user)
        shown = {node.node.id for node in found.results}
        assert not shown & {tokyo_old.id, kyoto_old.id} and {node.id for node in current} <= shown
        assert await events_snapshot(engine, user) == before
        assert (await engine.get_node(event_id)).model_dump_json() == note_before
        # The first derivation's own records stand, and the old claims keep their evidence.
        assert (await engine.extraction_status(event_id, user_id=user)).status == "complete"
        assert await engine.get_extraction(event_id, user_id=user) is not None
        assert [str(ref) for ref in kyoto_old.evidence_refs] == [event_id]


async def test_a_claim_merged_from_two_sources_is_replaced_by_one_merged_claim(config, user):
    first, second = "Alex lives in Paris.", "As I said, Alex lives in Paris."
    says = {first: [("Alex", "lives_in", "Paris")], second: [("Alex", "lives_in", "Paris")]}
    async with MemoryEngine.open(settings(config, extraction_batch_target_tokens=0)) as engine:
        old = Scripted(says)

        async def extract(text, **_):
            return await _single(old, text)

        engine._pipeline._extraction_provider.extract = extract
        events = [await engine.ingest(text, user_id=user, event_time=T0 + timedelta(hours=hours),
                                      wait_for_extraction=True) for hours, text in enumerate((first, second))]
        [merged] = await claims(engine, user)
        assert [str(ref) for ref in merged.evidence_refs] == events
    async with MemoryEngine.open(settings(config)) as engine:
        install(engine, Scripted(says))
        result = await engine.reingest(user)
        assert (result.total, result.processed, result.failed) == (2, 2, 0)
        [current] = await claims(engine, user)
        # The two statements are one current record again, and the earlier ones are history.
        assert current.id != merged.id and [str(ref) for ref in current.evidence_refs] == events
        everything = await claims(engine, user, *LifecycleState)
        assert len(everything) == 4 and sum(node.lifecycle_state == LifecycleState.SUPERSEDED for node in everything) == 3
        assert (await engine.get_node(str(merged.id), include_superseded=True)).superseded_by is not None
        assert (await engine.reingest(user)).processed == 0


async def test_the_note_of_a_stored_memory_is_linked_to_the_entities_its_text_names(config, user):
    cities = ["Tokyo", "Kyoto", "Osaka"]
    async with MemoryEngine.open(settings(config, enable_mention_links=True)) as engine:
        install(engine, Scripted({JAPAN: [("Alex", "chose_cities", "Tokyo, Kyoto, and Osaka")]}, places={JAPAN: cities}))
        event_id = await engine.store(JAPAN, user_id=user, event_time=T0)
        [note] = await engine.query_nodes(user_id=user, node_type=NodeType.NOTE)
        assert await engine._graph_store.get_edges(source_id=str(note.id)) == []
        await engine.reingest(user)
        entities = {node.content: node for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)}
        linked = {edge.target_id for edge in await engine._graph_store.get_edges(source_id=str(note.id))}
        assert {entities[name].id for name in cities} <= linked
        # The note itself is the same node it was.
        assert (await engine.get_node(str(note.id))).model_dump_json() == note.model_dump_json()
        assert [str(ref) for ref in note.evidence_refs] == [event_id]


async def test_a_source_another_run_committed_first_keeps_that_run_and_makes_nothing_more(config, user):
    from prme.ingestion.reingest import Reingester, derivation_settings, extraction_settings, _digest

    async with MemoryEngine.open(settings(config, enable_mention_links=True)) as engine:
        install(engine, Scripted(FOUND, places={JAPAN: ["Tokyo", "Kyoto", "Osaka"]}))
        [event_id] = await store_all(engine, user, texts=[JAPAN], sessions=(None,))
        await engine.reingest(user)
        nodes = await engine.count_nodes(user_id=user)
        edges = len(await engine._graph_store.get_edges(node_ids=[str(node.id) for node in await engine.query_nodes(
            user_id=user, limit=1000)]))
        pipeline = engine._pipeline
        key = _digest(extraction_settings(pipeline))
        saved = await pipeline._event_store.reingest.saved_extractions([event_id], user_id=user, extraction_key=key)
        event = await engine.get_event(event_id, user_id=user)
        # A second process planned the same source from the same output and lost the race to commit it.
        await Reingester(pipeline)._derive(event, saved[event_id], _digest(derivation_settings(pipeline)), key)
        assert await engine.count_nodes(user_id=user) == nodes
        assert len(await engine._graph_store.get_edges(node_ids=[str(node.id) for node in await engine.query_nodes(
            user_id=user, limit=1000)])) == edges


async def _single(model, text):
    from prme.ingestion.schema import ExtractionResult

    entry = (await model.extract_batch([_Turn(text)]))["turns"][0]
    return ExtractionResult.model_validate({"entities": entry["entities"], "facts": entry["facts"]})


class _Turn:
    def __init__(self, content):
        self.index, self.content = 1, content


async def test_only_the_named_owners_sources_are_touched(config, user):
    other = f"{user}-other"
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        await store_all(engine, user)
        await store_all(engine, other)
        theirs = await events_snapshot(engine, other)
        nodes = sorted(node.model_dump_json() for node in await engine.query_nodes(user_id=other, limit=1000))

        result = await engine.reingest(user)
        assert (result.total, result.processed) == (4, 4)
        assert sorted(node.model_dump_json() for node in await engine.query_nodes(user_id=other, limit=1000)) == nodes
        assert await events_snapshot(engine, other) == theirs
        assert all(node.user_id == user for node in await claims(engine, user))
        assert len(model.calls) == 3

        both = await engine.reingest(other)
        assert (both.total, both.current, both.processed) == (4, 0, 4)
        # Entities are per owner: each owner has their own.
        mine = {node.id for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY, limit=100)}
        yours = {node.id for node in await engine.query_nodes(user_id=other, node_type=NodeType.ENTITY, limit=100)}
        assert mine and yours and not mine & yours


async def test_a_scope_limits_the_sources(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        install(engine, Scripted(FOUND))
        await engine.store(LAKE, user_id=user, event_time=T0)
        await engine.store(PUPPY, user_id=user, scope=Scope.PROJECT, event_time=T0 + timedelta(hours=1))
        result = await engine.reingest(user, scope="project")
        assert (result.total, result.processed, result.scope) == (1, 1, Scope.PROJECT)
        rest = await engine.reingest(user)
        assert (rest.total, rest.current, rest.processed) == (2, 1, 1)


async def test_the_option_off_refuses(config, user):
    async with MemoryEngine.open(config.model_copy(update={"extraction_batch_target_tokens": 512})) as engine:
        install(engine, Scripted(FOUND))
        await store_all(engine, user)
        with pytest.raises(ReingestDisabledError, match="enable_reingest"):
            await engine.reingest(user)
        with pytest.raises(ReingestDisabledError):
            await engine.reingest(user, dry_run=True)
        assert await engine.count_nodes(user_id=user) == 4


async def test_bad_calls_are_refused_before_any_work(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        await store_all(engine, user)
        with pytest.raises(ValueError, match="cursor"):
            await engine.reingest(user, cursor="not a cursor")
        with pytest.raises(ValueError, match="limit"):
            await engine.reingest(user, limit=-1)
        with pytest.raises(ValueError, match="user_id"):
            await engine.reingest("")
        assert (await engine.reingest(user, limit=0)).selected == 0
        assert model.calls == []
    async with MemoryEngine.open(settings(config, extraction_batch_target_tokens=0)) as engine:
        with pytest.raises(ValueError, match="batched extraction"):
            await engine.reingest(user)
    async with MemoryEngine.open(settings(config)) as engine:
        engine._pipeline = None
        with pytest.raises(ValueError, match="pipeline"):
            await engine.reingest(user)


async def test_a_change_of_settings_makes_every_source_stale_again_without_duplicates(config, user, monkeypatch):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        await store_all(engine, user)
        first = await engine.reingest(user)
        active = sorted(node.content for node in await claims(engine, user))
        # The engine version, the model, the prompt and each option that decides what is derived count.
        engine._pipeline._link_mentions = not engine._pipeline._link_mentions
        stale = await engine.reingest(user, dry_run=True)
        # An option that only decides what is derived plans again from the model output the first run saved.
        assert stale.current == 0 and stale.fingerprint != first.fingerprint and stale.model_calls == 0
        engine._pipeline._link_mentions = not engine._pipeline._link_mentions
        monkeypatch.setattr(type(engine._pipeline._extraction_provider), "model_name", "another-model")
        asks = await engine.reingest(user, dry_run=True)
        assert asks.current == 0 and asks.model_calls == 3
        redone = await engine.reingest(user)
        assert (redone.processed, redone.remaining, redone.model_calls) == (4, 0, 3) and len(model.calls) == 6
        # The same statements replaced themselves: no duplicates, and the first records are history.
        assert sorted(node.content for node in await claims(engine, user)) == active
        assert len(await claims(engine, user, LifecycleState.SUPERSEDED)) == 4
        assert (await engine.reingest(user)).processed == 0


async def test_memories_whose_own_extraction_is_unfinished_are_left_alone(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        engine._pipeline._batch_flush_seconds = 3600.0
        await engine.store(LAKE, user_id=user, event_time=T0, extract=True)
        await engine.store(PUPPY, user_id=user, event_time=T0 + timedelta(hours=1))
        result = await engine.reingest(user)
        # The first has a queued job that has not run, so a second derivation beside it would double it.
        assert (result.total, result.unfinished, result.processed) == (1, 1, 1)
        assert model.calls == [[PUPPY]]
        assert (await engine.process_extractions(user_id=user)).processed == 1
        done = await engine.reingest(user)
        assert (done.total, done.unfinished, done.processed) == (2, 0, 1)


async def test_events_without_a_memory_are_not_sources(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        install(engine, Scripted(FOUND))
        await store_all(engine, user, texts=[LAKE], sessions=(None,))
        await engine._append_control_event("chat opened", user_id=user, session_id="s9",
                                           scope=Scope.PERSONAL, metadata={})
        assert (await engine.reingest(user, dry_run=True)).total == 1


async def test_a_second_run_in_the_same_engine_for_the_owner_is_refused(config, user):
    release = asyncio.Event()
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        waiting = model.extract_batch

        async def slow(turns):
            await release.wait()
            return await waiting(turns)

        engine._pipeline._extraction_provider.extract_batch = slow
        await store_all(engine, user)
        running = asyncio.create_task(engine.reingest(user))
        await asyncio.sleep(0.2)
        with pytest.raises(ReingestBusyError):
            await engine.reingest(user)
        # Another owner is not held up by it.
        assert (await engine.reingest(f"{user}-other", dry_run=True)).total == 0
        release.set()
        assert (await running).processed == 4
        assert (await engine.reingest(user)).processed == 0


async def test_the_sync_client_exposes_reingest(config, user):
    with MemoryClient(config=settings(config)) as client:
        client._engine._pipeline._extraction_provider.extract_batch = Scripted(FOUND).extract_batch
        client.store(LAKE, user_id=user)
        assert client.reingest(user, dry_run=True).model_calls == 1
        result = client.reingest(user, limit=5)
        assert isinstance(result, ReingestResult) and (result.processed, result.remaining) == (1, 0)


def app_for(config, engine, owner):
    config.api = APIConfig(user_keys={owner: "owner-token", f"{owner}-other": "other-token"})
    app = create_app(config)
    app.state.engine = engine
    return app


async def test_the_http_route_is_owned_validated_and_off_unless_enabled(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Scripted(FOUND))
        await store_all(engine, user)
        app = app_for(config, engine, user)
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     headers={"Authorization": "Bearer owner-token"}) as client:
            assert (await client.post("/v1/reingest", json={"user_id": f"{user}-other"})).status_code == 403
            assert (await client.post("/v1/reingest", json={"limit": -1})).status_code == 422
            assert (await client.post("/v1/reingest", json={"cursor": "bad"})).status_code == 422
            dry = await client.post("/v1/reingest", json={"dry_run": True})
            assert dry.status_code == 200 and dry.json()["model_calls"] == 3 and dry.json()["processed"] == 0
            assert model.calls == []
            first = (await client.post("/v1/reingest", json={"limit": 2})).json()
            assert first["processed"] == 2 and first["remaining"] == 2 and first["cursor"]
            last = (await client.post("/v1/reingest", json={"cursor": first["cursor"]})).json()
            assert last["processed"] == 2 and last["remaining"] == 0 and last["cursor"] is None
        other = httpx.AsyncClient(transport=transport, base_url="http://test",
                                  headers={"Authorization": "Bearer other-token"})
        async with other:
            assert (await other.post("/v1/reingest", json={})).json()["total"] == 0
        engine._config = engine._config.model_copy(update={"enable_reingest": False})
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     headers={"Authorization": "Bearer owner-token"}) as client:
            refused = await client.post("/v1/reingest", json={})
            assert refused.status_code == 409 and "enable_reingest" in refused.json()["detail"]


async def test_the_mcp_tool_uses_the_callers_identity(config, user, monkeypatch):
    on = settings(config)
    on.mcp = MCPConfig(user_keys={user: "first-token", f"{user}-other": "second-token"})
    async with MemoryEngine.open(on) as engine:
        await store_all(engine, user)
    from tests.test_concurrency import MockExtractionProvider

    provider = MockExtractionProvider()
    model = Scripted(FOUND)
    provider.extract_batch = model.extract_batch
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: provider)
    app = create_http_app(on)
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8000",
                                     headers=headers) as client:
            async def call(arguments, token="first-token"):
                response = await client.post("/mcp", headers={"Authorization": "Bearer " + token}, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "memory_reingest", "arguments": arguments},
                })
                assert response.status_code == 200
                return json.loads(response.json()["result"]["content"][0]["text"])

            assert (await call({"dry_run": True}))["model_calls"] == 3 and model.calls == []
            assert (await call({}, "second-token"))["total"] == 0
            done = await call({"limit": 10})
            assert (done["processed"], done["remaining"]) == (4, 0)
            assert "error" in await call({"cursor": "bad"})


async def test_the_command_line_needs_the_option_and_an_owner(config, user, monkeypatch, capsys):
    from prme import cli

    async with MemoryEngine.open(settings(config)) as engine:
        await store_all(engine, user)

    async def open_engine(path, *, enabled=True):
        engine = await MemoryEngine.create(settings(config) if enabled else config)
        install(engine, Scripted(FOUND))
        return engine

    monkeypatch.setattr(cli, "_create_engine", open_engine)
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["reingest", config.db_path])
    capsys.readouterr()
    args = parser.parse_args(["reingest", config.db_path, "--user-id", user, "--dry-run", "--format", "json"])
    await args.func(args)
    assert '"model_calls": 3' in capsys.readouterr().out
    args = parser.parse_args(["reingest", config.db_path, "--user-id", user, "--limit", "10", "--format", "json"])
    await args.func(args)
    assert '"processed": 4' in capsys.readouterr().out
