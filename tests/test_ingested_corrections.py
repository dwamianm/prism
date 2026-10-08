"""Kio correction cases use source-backed plans on both storage backends."""

import json

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from prme import MemoryClient, MemoryEngine
from prme.api.app import create_app
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from mcp.shared.memory import create_connected_server_and_client_session
from prme.ingestion.errors import ExtractionError
from prme.ingestion.schema import ExtractionResult
from prme.models import Event
from prme.models.owner_reference import OWNER_REFERENCE_KEY, owner_reference_id
from prme.types import EdgeType, LifecycleState, NodeType, Scope, SourceType
from tests.test_durable_ingestion import config, user  # noqa: F401

T0 = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def fact(obj, *, subject="I", predicate="uses", quote=None, **extra):
    return {"subject": subject, "predicate": predicate, "object": obj,
            "polarity": "positive", "epistemic_type": "asserted", "evidence_quote": quote, **extra}


def result(source, facts):
    return ExtractionResult.model_validate({
        "facts": [{**f, "evidence_quote": f["evidence_quote"] or source} for f in facts],
    })


async def say(engine, owner, source, facts, **options):
    engine._pipeline._extraction_provider.extract = AsyncMock(return_value=result(source, facts))
    return await engine.ingest(source, user_id=owner, wait_for_extraction=True, **options)


async def all_facts(engine, owner):
    return await engine.query_nodes(user_id=owner, node_type=NodeType.FACT,
                                    lifecycle_states=list(LifecycleState))


@pytest.mark.parametrize("wording", ["same", "different"])
@pytest.mark.parametrize("attribution", ["named", "owner", "unbound"])
async def test_reported_pairs_keep_only_current_treatment_when_attributed(config, user, wording, attribution):  # noqa: F811
    options = {"speaker": "Sam"} if attribution == "named" else {"first_person_owner": attribution == "owner"}
    old_text = "I use an insulin pump." if wording == "same" else "I have type 1 diabetes and I use an insulin pump."
    old_facts = [fact("insulin pump", quote="I use an insulin pump.")]
    if wording == "different":
        old_facts.insert(0, fact("type 1 diabetes", predicate="has", quote=old_text))
    new_text = "I don't use an insulin pump anymore." if wording == "same" else "Actually, I don't use a pump anymore. I switched to injections."
    new_facts = [fact("insulin pump" if wording == "same" else "pump", polarity="negative",
                      temporal_intent="update", quote=new_text.split(". ")[0] + ("." if wording == "different" else ""))]
    if wording == "different":
        new_facts.append(fact("injections", predicate="switched_to", temporal_intent="update",
                              replaces_object="pump", quote="I switched to injections."))
    async with MemoryEngine.open(config) as engine:
        first = await say(engine, user, old_text, old_facts, event_time=T0, **options)
        second = await say(engine, user, new_text, new_facts, event_time=T0 + timedelta(minutes=1), **options)
        nodes = await all_facts(engine, user)
        old = next(n for n in nodes if n.metadata["object"] == "insulin pump" and n.metadata["polarity"] == "positive")
        negative = next(n for n in nodes if n.metadata["polarity"] == "negative")
        if attribution == "unbound":
            assert old.lifecycle_state == LifecycleState.TENTATIVE
            assert old.superseded_by is None
        else:
            assert old.lifecycle_state == LifecycleState.SUPERSEDED
            assert old.superseded_by == negative.id
            assert old.valid_to == negative.valid_from == T0 + timedelta(minutes=1)
        if wording == "different":
            diagnosis = next(n for n in nodes if n.metadata["object"] == "type 1 diabetes")
            assert diagnosis.lifecycle_state == LifecycleState.TENTATIVE
        # Raw sources retain their full text; consumers can explicitly exclude them.
        await engine.process_pending(user_id=user)
        assert (await engine.get_event(first, user_id=user)).content == old_text
        notes = await engine.query_nodes(user_id=user, node_type=NodeType.NOTE)
        assert {n.content for n in notes} == {old_text, new_text}
        response = await engine.retrieve("insulin pump injections prescriptions", user_id=user,
                                        exclude_node_types={NodeType.NOTE, NodeType.ENTITY},
                                        source_types={SourceType.USER_STATED})
        if attribution != "unbound":
            assert old.id not in {r.node.id for r in response.results}
            assert negative.id in {r.node.id for r in response.results}
        plan = await engine._event_store.get_derivation_plan(second, user_id=user)
        assert plan.materialization_policy == "speech_act_v14"
        saved_checksum = plan.checksum
    async with MemoryEngine.open(config) as engine:
        assert (await engine._event_store.get_derivation_plan(second, user_id=user)).checksum == saved_checksum
        restored = await engine.get_node(str(old.id), user_id=user, include_superseded=True)
        assert restored.lifecycle_state == old.lifecycle_state


async def test_switch_action_can_replace_use_with_bounded_shorthand_only(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await say(engine, user, "I use an insulin pump. I own an insulin pump. I like an insulin pump.",
                  [fact("insulin pump", predicate=p) for p in ("uses", "owns", "likes")], first_person_owner=True)
        await say(engine, user, "I switched from a pump to injections.",
                  [fact("injections", predicate="switched_to", temporal_intent="update", replaces_object="pump")],
                  first_person_owner=True)
        nodes = await all_facts(engine, user)
        assert next(n for n in nodes if n.metadata["predicate"] == "uses").lifecycle_state == LifecycleState.SUPERSEDED
        assert all(n.lifecycle_state == LifecycleState.TENTATIVE for n in nodes if n.metadata["predicate"] in {"owns", "likes", "switched_to"})


@pytest.mark.parametrize("guard", ["ambiguous", "substring", "quantity", "unknown_polarity", "conditional", "condition_mislabel", "other_source", "other_scope", "other_owner", "historical"])
async def test_uncertain_correction_does_not_retire_a_prior_claim(config, user, guard):  # noqa: F811
    obj = "pumper" if guard == "substring" else "2 insulin pumps" if guard == "quantity" else "insulin pump"
    source = f"I use {obj}."
    old_facts = [fact(obj, polarity="unknown" if guard == "unknown_polarity" else "positive")]
    if guard == "ambiguous":
        source += " I use a water pump."
        old_facts.append(fact("water pump"))
    async with MemoryEngine.open(config) as engine:
        await say(engine, user, source, old_facts, first_person_owner=True, event_time=T0)
        extra = {"polarity": "negative", "temporal_intent": "update"}
        new_source = "I don't use a pump anymore."
        if guard in {"conditional", "condition_mislabel"}:
            new_source = "If approved, I don't use a pump anymore."
            extra.update(epistemic_type="conditional" if guard == "conditional" else "asserted", condition="If approved")
        options = {"first_person_owner": True, "event_time": T0 + timedelta(minutes=1)}
        if guard == "historical":
            options["event_time"] = T0 - timedelta(days=1)
        if guard == "other_scope":
            options["scope"] = Scope.PROJECT
        if guard == "other_source":
            options.update(first_person_owner=False, role="assistant")
        await say(engine, user + "-other" if guard == "other_owner" else user,
                  new_source, [fact("pump", **extra)], **options)
        old = [n for n in await all_facts(engine, user) if n.metadata["object"] in {obj, "water pump"}]
        assert old and all(n.lifecycle_state == LifecycleState.TENTATIVE and n.superseded_by is None for n in old)


async def test_unflagged_pasted_text_never_binds_to_the_owner(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await say(engine, user, "I use an insulin pump.", [fact("insulin pump")], first_person_owner=True)
        await say(engine, user, "I don't use an insulin pump anymore.",
                  [fact("insulin pump", polarity="negative", temporal_intent="update")])
        nodes = await all_facts(engine, user)
        assert all(n.lifecycle_state == LifecycleState.TENTATIVE for n in nodes)
        owner_id = owner_reference_id(user, "personal")
        subjects = []
        for n in nodes:
            [edge] = await engine._graph_store.get_edges(target_id=str(n.id), edge_type=EdgeType.HAS_FACT)
            subjects.append(edge.source_id)
        assert owner_id in subjects and len(set(subjects)) == 2


async def test_owner_reference_forms_and_batch_admission(config, user):  # noqa: F811
    sources = [("My preference is Python.", "my"), ("Call me Sam.", "me"), ("I prefer Rust.", "I")]
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=[
            result(text, [fact("Sam" if form == "me" else "Python" if form == "my" else "Rust", subject=form,
                               predicate="prefers", fact_type="preference")]) for text, form in sources
        ])
        events = await engine.ingest_batch([
            {"content": text, "role": "user", "first_person_owner": True} for text, _ in sources
        ], user_id=user, wait_for_extraction=True)
        for event_id in events:
            source = await engine.get_event(event_id, user_id=user)
            assert source.metadata[OWNER_REFERENCE_KEY] is True
            nodes = await engine.get_event_nodes(event_id, user_id=user)
            [claim] = [n for n in nodes if n.node_type == NodeType.PREFERENCE]
            [edge] = await engine._graph_store.get_edges(target_id=str(claim.id), edge_type=EdgeType.HAS_FACT)
            assert edge.source_id == owner_reference_id(user, "personal")
            assert claim.metadata["owner_reference"] == {"user_id": user, "fields": ["subject"]}
        entities = await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)
        assert len([n for n in entities if n.metadata.get("identity_status") == "owner_reference"]) == 1


@pytest.mark.parametrize("options", [
    {"first_person_owner": "true"}, {"first_person_owner": True, "speaker": "Sam"},
    {"first_person_owner": True, "role": "assistant"},
    {"metadata": {OWNER_REFERENCE_KEY: True}},
])
async def test_invalid_attribution_rejects_before_admitting_any_batch_member(config, user, options):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError):
            await engine.ingest("I use Python.", user_id=user, **options)
        with pytest.raises(ValueError):
            await engine.ingest_batch([
                {"content": "First member", "role": "user"},
                {"content": "I use Python.", "role": "user", **options},
            ], user_id=user)
        assert await engine._event_store.get_by_user(user_id=user) == []


async def test_v13_missing_plan_recovery_does_not_adopt_correction_rules(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await say(engine, user, "I use an insulin pump.", [fact("insulin pump")], speaker="Sam")
        event_id = await engine.ingest_fast("I don't use an insulin pump anymore.", user_id=user, speaker="Sam")
        event = await engine.get_event(event_id, user_id=user)
        inference = result(event.content, [fact("insulin pump", polarity="negative", temporal_intent="update")])
        old = await engine._pipeline._prepare_plan(inference, event, materialization_policy="speech_act_v13")
        new = await engine._pipeline._prepare_plan(inference, event, materialization_policy="speech_act_v14")
        assert old.replacements == () and len(new.replacements) == 1
        # The rule depends on a caller's declaration, never just an old metadata value.
        owner_event = Event(content=event.content, user_id=user, role="user", metadata={OWNER_REFERENCE_KEY: True})
        legacy = await engine._pipeline._prepare_plan(inference, owner_event, materialization_policy="speech_act_v13")
        assert all(n.metadata.get("identity_status") != "owner_reference" for n in legacy.nodes)


async def test_failed_commit_preserves_old_claim_and_saved_plan_retries_after_restart(config, user, monkeypatch):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await say(engine, user, "I use an insulin pump.", [fact("insulin pump")], first_person_owner=True)
        [old] = await all_facts(engine, user)
        engine._pipeline._retry_delays = ()
        graph = engine._graph_store
        if hasattr(graph, "_pool"):
            original = graph._create_edge_on_connection
            async def fail_after_replacement(conn, edge):
                output = await original(conn, edge)
                if edge.edge_type == EdgeType.SUPERSEDES:
                    raise RuntimeError("injected post-retirement failure")
                return output
            method = "_create_edge_on_connection"
        else:
            original = graph._create_edge_sync
            def fail_after_replacement(edge):
                output = original(edge)
                if edge.edge_type == EdgeType.SUPERSEDES:
                    raise RuntimeError("injected post-retirement failure")
                return output
            method = "_create_edge_sync"
        with monkeypatch.context() as fault:
            fault.setattr(graph, method, fail_after_replacement)
            with pytest.raises(ExtractionError) as failure:
                await say(engine, user, "I don't use an insulin pump anymore.",
                          [fact("insulin pump", polarity="negative", temporal_intent="update")], first_person_owner=True)
        event_id = failure.value.event_id
        [unchanged] = await all_facts(engine, user)
        assert unchanged.id == old.id and unchanged.superseded_by is None
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        assert len(plan.replacements) == 1
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=AssertionError("must reuse inference"))
        await engine.retry_extraction(event_id, user_id=user)
        processed = await engine.process_extractions(user_id=user)
        assert processed.processed == 1 and processed.failed == 0
        assert (await engine._event_store.get_derivation_plan(event_id, user_id=user)).checksum == plan.checksum
        restored = await engine.get_node(str(old.id), include_superseded=True, user_id=user)
        assert restored.lifecycle_state == LifecycleState.SUPERSEDED
        assert restored.superseded_by == plan.replacements[0].source_id


async def test_http_owner_declaration_and_validation(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=result("I use Python.", [fact("Python")]))
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            body = {"content": "I use Python.", "user_id": user, "first_person_owner": True, "wait_for_extraction": True}
            response = await client.post("/v1/ingest", json=body)
            assert response.status_code == 200
            assert (await engine.get_event(response.json()["event_id"], user_id=user)).metadata[OWNER_REFERENCE_KEY] is True
            for changes in ({"first_person_owner": "true"}, {"role": "assistant"}, {"speaker": "Sam"}, {"metadata": {OWNER_REFERENCE_KEY: True}}):
                response = await client.post("/v1/ingest", json={**body, **changes})
                assert response.status_code == 422


def test_sync_client_owner_declaration(config, user):  # noqa: F811
    with MemoryClient(config=config) as client:
        provider = client._engine._pipeline._extraction_provider
        provider.extract = AsyncMock(side_effect=[
            result("I use an insulin pump.", [fact("insulin pump")]),
            result("I don't use an insulin pump anymore.", [fact("insulin pump", polarity="negative", temporal_intent="update")]),
        ])
        first = client.ingest("I use an insulin pump.", user_id=user, first_person_owner=True)
        client.ingest("I don't use an insulin pump anymore.", user_id=user, first_person_owner=True)
        [old] = [n for n in client.get_event_nodes(first, user_id=user) if n.node_type == NodeType.FACT]
        assert old.lifecycle_state == LifecycleState.SUPERSEDED
        assert client.get_event(first, user_id=user).metadata[OWNER_REFERENCE_KEY] is True


async def test_mcp_owner_declaration_is_strict_and_persisted(config, user, monkeypatch):  # noqa: F811
    provider = type("ScriptedProvider", (), {"extract": AsyncMock(return_value=ExtractionResult())})()
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: provider)
    config.mcp = MCPConfig(user_id=user)
    server = create_mcp_server(config=config)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
        await session.initialize()
        tools = await session.list_tools()
        schema = next(t.inputSchema for t in tools.tools if t.name == "memory_ingest")
        assert schema["properties"]["first_person_owner"]["type"] == "boolean"
        response = await session.call_tool("memory_ingest", {"content": "I use Python.", "first_person_owner": True})
        event_id = json.loads(response.content[0].text)["event_id"]
        source = await session.call_tool("memory_get_event", {"event_id": event_id})
        assert json.loads(source.content[0].text)["metadata"][OWNER_REFERENCE_KEY] is True
        for changes in ({"role": "assistant"}, {"speaker": "Sam"}, {"first_person_owner": "true"}):
            response = await session.call_tool("memory_ingest", {"content": "I use Python.", "first_person_owner": True, **changes})
            assert response.isError or "error" in json.loads(response.content[0].text)


async def test_plural_references_stay_local_and_object_references_can_bind(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        for text in ("We use Python.", "We don't use Python anymore."):
            await say(engine, user, text,
                      [fact("Python", subject="We", polarity="negative" if "don't" in text else "positive",
                            temporal_intent="update" if "don't" in text else None)], first_person_owner=True)
        await say(engine, user, "Sam called me.", [fact("me", subject="Sam", predicate="called")], first_person_owner=True)
        nodes = await all_facts(engine, user)
        plural = [n for n in nodes if n.metadata["subject"] == "We"]
        assert len(plural) == 2 and all(n.lifecycle_state == LifecycleState.TENTATIVE for n in plural)
        subjects = []
        for node in plural:
            [edge] = await engine._graph_store.get_edges(target_id=str(node.id), edge_type=EdgeType.HAS_FACT)
            subjects.append(edge.source_id)
            assert "owner_reference" not in node.metadata
        assert len(set(subjects)) == 2
        obj = next(n for n in nodes if n.metadata["object"] == "me")
        assert obj.metadata["owner_reference"] == {"user_id": user, "fields": ["object"]}
        [edge] = await engine._graph_store.get_edges(source_id=str(obj.id), edge_type=EdgeType.MENTIONS)
        assert edge.target_id == owner_reference_id(user, "personal")
