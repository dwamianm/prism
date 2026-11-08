"""Typed extraction: each claim's relation, role and status in the graph oracle's vocabulary (dwamianm/prism#400).

With ``enable_typed_extraction`` (off by default) the extraction request asks for a relation from the closed
vocabulary and a role and status, the response schema admits them, and a claimed relation outside the vocabulary is
dropped with its role and status. The admitted fields are stored on the claim and its HAS_FACT link, and relation
routing reads them instead of mapping the open predicate. These tests replay recorded model output and call no model.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import UUID

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.admitted_statements import project_admitted_edge
from prme.ingestion.extraction import (
    BatchTurn,
    _TypedCitedExtractionResult,
    _TypedResolvingCitedExtractionResult,
    _batch_result_model,
    admit_relation,
    batch_request_messages,
    batch_request_sha256,
    typed_extraction_guidance,
    validate_batch_output,
    validate_raw_output,
)
from prme.ingestion.pipeline import IngestionPipeline
from prme.ingestion.reingest import extraction_settings
from prme.ingestion.schema import ExtractedFact
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.privacy.policy import PrivacyDenied
from prme.retrieval.relation_walk import walk_route
from prme.retrieval.relations import RELATIONS, RelationStep
from prme.types import EdgeType, NodeType
from tests.test_durable_ingestion import MockEmbeddingProvider

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = json.loads((FIXTURES / "extraction" / "typed_batch_v1.json").read_text())
TURN_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


# --- the request and the schema ---------------------------------------------------------------------


def test_the_guidance_names_every_relation_of_the_vocabulary():
    guidance = typed_extraction_guidance()
    assert all(name in guidance for name in RELATIONS) and "TYPED RELATIONS" in guidance
    assert "Never invent a name" in guidance and "status" in guidance


def test_the_typed_request_carries_the_guidance_and_its_digest_differs():
    turn = BatchTurn(index=1, role="user", content="Alex works at Northwind.", speaker=None, time=TURN_TIME)
    untyped = batch_request_messages([turn])
    typed = batch_request_messages([turn], typed=True)
    assert "TYPED RELATIONS" not in json.dumps(untyped)
    assert "TYPED RELATIONS" in json.dumps(typed)
    assert batch_request_sha256([turn]) != batch_request_sha256([turn], typed=True)
    # A turn asked neither keeps the digest it had before the option.
    assert batch_request_sha256([turn]) == batch_request_sha256([turn], typed=False)


def test_the_typed_schema_asks_for_relation_role_and_status():
    schema = _TypedCitedExtractionResult.model_json_schema()["$defs"]["_TypedCitedFact"]
    assert {"relation", "role", "status"} <= set(schema["properties"])
    assert all(name not in schema.get("required", []) for name in ("relation", "role", "status"))


@pytest.mark.parametrize("relation, role, status, expected", [
    ("works_at", "senior engineer", "current", ("works_at", "senior engineer", "current")),
    ("KIN_OF", "Sister", "PAST", ("kin_of", "sister", "past")),
    ("has", None, "current", ("has", None, "current")),
    ("invented", "sister", "past", (None, None, None)),
    (None, "sister", "past", (None, None, None)),
    ("", "", "", (None, None, None)),
    ("works_at", "a role word so long that it cannot be a qualifier of the far end at all", "current",
     ("works_at", None, "current")),
    ("works_at", "engineer", "yesterday", ("works_at", "engineer", None)),
])
def test_admit_relation_keeps_a_stated_relation_and_drops_an_invented_one(relation, role, status, expected):
    fact = ExtractedFact(subject="Alex", predicate="role", object="Northwind",
                         relation=relation, role=role, status=status)
    assert admit_relation(fact) == expected


def test_a_typed_batch_keeps_the_relation_in_the_checked_result():
    turns = [BatchTurn(index=1, role="user", content=RECORDED["turns"][0]["content"], speaker=None, time=TURN_TIME)]
    result = validate_batch_output(RECORDED["response"], turns, typed=True)[1]
    works = next(fact for fact in result.facts if fact.predicate == "works_at")
    assert (works.relation, works.role, works.status) == ("works_at", "senior backend engineer", "current")
    assert isinstance(result, _TypedCitedExtractionResult)
    assert _batch_result_model(correcting=False, typed=True).__name__ == "_TypedBatchExtractionResult"


def _settings(tmp_path: Path, monkeypatch, typed: bool) -> dict:
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    pack = tmp_path / ("on" if typed else "off")
    (pack / "lexical").mkdir(parents=True)
    config = PRMEConfig(db_path=str(pack / "memory.duckdb"), lexical_path=str(pack / "lexical"),
                        vector_path=str(pack / "vectors.usearch"), organizer={"opportunistic_enabled": False},
                        extraction_batch_target_tokens=2048, enable_typed_extraction=typed)

    async def read() -> dict:
        async with MemoryEngine.open(config) as engine:
            return extraction_settings(engine._pipeline)

    return asyncio.run(read())


def test_the_extraction_settings_name_the_option_only_while_it_is_on(tmp_path, monkeypatch):
    off = _settings(tmp_path, monkeypatch, typed=False)
    on = _settings(tmp_path, monkeypatch, typed=True)
    assert "typed_extraction" not in off and "TYPED RELATIONS" not in json.dumps(off)
    assert on["typed_extraction"] is True and on["request"] != off["request"]


# --- the stored claim and its link -------------------------------------------------------------------


def _config(tmp_path: Path, typed: bool) -> PRMEConfig:
    (tmp_path / "lexical").mkdir()
    return PRMEConfig(
        db_path=str(tmp_path / "memory.duckdb"), lexical_path=str(tmp_path / "lexical"),
        vector_path=str(tmp_path / "vectors.usearch"), organizer={"opportunistic_enabled": False},
        materialization_budget_ms=5000, extraction_batch_target_tokens=2048, enable_reingest=True,
        enable_typed_extraction=typed,
    )


def _stub(monkeypatch) -> None:
    by_text = {turn["content"]: entry for turn, entry in zip(RECORDED["turns"], RECORDED["response"]["turns"])}

    async def extract_batch(self, turns, **_):
        return {"turns": [{**by_text[turn.content], "turn": turn.index} for turn in turns]}

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    monkeypatch.setattr("prme.ingestion.extraction.InstructorExtractionProvider.extract_batch", extract_batch)


def _claims(nodes) -> list:
    return [node for node in nodes if (node.metadata or {}).get("extraction_kind") in ("fact", "relationship")]


@asynccontextmanager
async def _stored(tmp_path, monkeypatch, typed: bool, owner: str):
    _stub(monkeypatch)
    async with MemoryEngine.open(_config(tmp_path, typed)) as engine:
        for turn in RECORDED["turns"]:
            await engine.store(turn["content"], user_id=owner, role=turn["role"], session_id=f"{owner}-session")
        yield engine, owner


async def test_the_admitted_fields_are_stored_on_the_claim_and_its_has_fact_link(tmp_path, monkeypatch):
    async with _stored(tmp_path, monkeypatch, True, "typed-owner") as (engine, owner):
        result = await engine.reingest(owner)
        assert (result.total, result.processed, result.failed) == (len(RECORDED["turns"]), len(RECORDED["turns"]), 0)
        claims = _claims(await engine.query_nodes(user_id=owner, limit=1000))
        works = next(node for node in claims if node.metadata.get("predicate") == "works_at")
        assert (works.metadata["relation"], works.metadata["role"], works.metadata["status"]) == (
            "works_at", "senior backend engineer", "current")
        assert (works.metadata["subject"], works.metadata["object"]) == ("Alex Rivera", "Northwind Logistics")
        edges = await engine._graph_store.get_edges(node_ids=[str(works.id)])
        link = next(edge for edge in edges if edge.edge_type == EdgeType.HAS_FACT)
        assert link.metadata == {"relation": "works_at", "role": "senior backend engineer", "status": "current"}
        # A literal attribute keeps ``has``; a family word keeps its role.
        literal = next(node for node in claims if node.metadata.get("predicate") == "is_a")
        assert (literal.metadata["relation"], literal.metadata.get("role")) == ("has", None)
        marriage = next(node for node in claims if node.metadata.get("predicate") == "married_to")
        assert marriage.metadata["relation"] == "partner_of"


async def test_without_the_option_a_claim_keeps_only_its_predicate(tmp_path, monkeypatch):
    async with _stored(tmp_path, monkeypatch, False, "untyped-owner") as (engine, owner):
        result = await engine.reingest(owner)
        assert result.failed == 0 and result.processed == len(RECORDED["turns"])
        claims = _claims(await engine.query_nodes(user_id=owner, limit=1000))
        assert claims and all("relation" not in node.metadata and "role" not in node.metadata
                              and "status" not in node.metadata for node in claims)
        for node in claims:
            edges = await engine._graph_store.get_edges(node_ids=[str(node.id)])
            assert all("relation" not in (edge.metadata or {})
                       for edge in edges if edge.edge_type == EdgeType.HAS_FACT)


# --- routing reads the stored relation ---------------------------------------------------------------


async def _typed_graph(engine, owner: str, predicate: str, relation: str) -> tuple[str, str]:
    maya = await engine._graph_store.create_node(MemoryNode(
        user_id=owner, node_type=NodeType.ENTITY, content="Maya Okafor"))
    theo = await engine._graph_store.create_node(MemoryNode(user_id=owner, node_type=NodeType.ENTITY, content="Theo"))
    claim = await engine._graph_store.create_node(MemoryNode(
        user_id=owner, node_type=NodeType.FACT, content="Maya's dependents.",
        metadata={"extraction_kind": "fact", "subject": "Maya", "predicate": predicate, "object": "Theo",
                  "polarity": "positive", "relation": relation}))
    await engine._graph_store.create_edge(MemoryEdge(
        source_id=UUID(maya), target_id=UUID(claim), edge_type=EdgeType.HAS_FACT, user_id=owner,
        metadata={"relation": relation}))
    await engine._graph_store.create_edge(MemoryEdge(
        source_id=UUID(claim), target_id=UUID(theo), edge_type=EdgeType.MENTIONS, user_id=owner))
    return maya, claim


async def _walks(tmp_path, predicate: str, relation: str) -> tuple[dict, dict]:
    from benchmarks.diagnostics.product_packing import gate_config

    pack = tmp_path / "pack"
    (pack / "lexical_index").mkdir(parents=True)
    async with MemoryEngine.open(gate_config(pack)) as engine:
        maya, claim = await _typed_graph(engine, "owner", predicate, relation)
        partner = await walk_route(engine._graph_store, ((RelationStep("partner_of"),),), user_id="owner",
                                   seed_ids=[maya])
        kin = await walk_route(engine._graph_store, ((RelationStep("kin_of"),),), user_id="owner", seed_ids=[maya])
    return partner.scores, kin.scores


async def test_routing_reads_the_stored_relation_instead_of_the_open_predicate(tmp_path):
    # The normalizer cannot map ``has_dependents``; the stored relation is what the route asks for.
    partner, kin = await _walks(tmp_path, "has_dependents", "partner_of")
    assert partner and kin == {}


async def test_a_stored_relation_overrides_a_predicate_that_maps_another_way(tmp_path):
    partner, kin = await _walks(tmp_path, "is_child_of", "partner_of")
    assert partner and kin == {}


async def _status_walk(tmp_path, link_metadata: dict, *, node_relation: bool = True) -> dict:
    from benchmarks.diagnostics.product_packing import gate_config

    pack = tmp_path / "pack"
    (pack / "lexical_index").mkdir(parents=True)
    async with MemoryEngine.open(gate_config(pack)) as engine:
        maya = await engine._graph_store.create_node(MemoryNode(
            user_id="owner", node_type=NodeType.ENTITY, content="Maya Okafor"))
        theo = await engine._graph_store.create_node(MemoryNode(
            user_id="owner", node_type=NodeType.ENTITY, content="Theo"))
        metadata = {"extraction_kind": "fact", "subject": "Maya", "predicate": "has_dependents",
                    "object": "Theo", "polarity": "positive", "status": "superseded"}
        if node_relation:
            metadata["relation"] = "partner_of"
        claim = await engine._graph_store.create_node(MemoryNode(
            user_id="owner", node_type=NodeType.FACT, content="Maya's dependents.", metadata=metadata))
        await engine._graph_store.create_edge(MemoryEdge(
            source_id=UUID(maya), target_id=UUID(claim), edge_type=EdgeType.HAS_FACT, user_id="owner",
            metadata=link_metadata))
        await engine._graph_store.create_edge(MemoryEdge(
            source_id=UUID(claim), target_id=UUID(theo), edge_type=EdgeType.MENTIONS, user_id="owner"))
        walk = await walk_route(engine._graph_store, ((RelationStep("partner_of"),),), user_id="owner",
                                seed_ids=[maya])
    return walk.scores


async def test_a_typed_claim_is_retired_by_its_lifecycle_not_the_model_status(tmp_path):
    # The model's status label is stored, but only the claim's lifecycle retires it from a routed question.
    scores = await _status_walk(tmp_path, {"relation": "partner_of", "status": "superseded"})
    assert scores


async def test_a_graph_oracle_claim_with_superseded_status_is_not_read(tmp_path):
    # The oracle's hand-written graph types the claim and marks it superseded on the link itself.
    scores = await _status_walk(tmp_path, {"relation": "partner_of", "status": "superseded", "shape": "claim"},
                                node_relation=False)
    assert scores == {}


async def test_a_role_is_read_at_the_end_the_predicate_names(tmp_path):
    """An ``is_child_of`` claim puts the child at the subject end; a role-gated route pivoting on the parent accepts it."""
    from benchmarks.diagnostics.product_packing import gate_config

    pack = tmp_path / "pack"
    (pack / "lexical_index").mkdir(parents=True)
    async with MemoryEngine.open(gate_config(pack)) as engine:
        maya = await engine._graph_store.create_node(MemoryNode(
            user_id="owner", node_type=NodeType.ENTITY, content="Maya Okafor"))
        nico = await engine._graph_store.create_node(MemoryNode(
            user_id="owner", node_type=NodeType.ENTITY, content="Nico"))
        claim = await engine._graph_store.create_node(MemoryNode(
            user_id="owner", node_type=NodeType.FACT, content="Nico is Maya's child.",
            metadata={"extraction_kind": "fact", "subject": "Nico", "predicate": "is_child_of",
                      "object": "Maya", "polarity": "positive", "relation": "kin_of", "role": "child"}))
        await engine._graph_store.create_edge(MemoryEdge(
            source_id=UUID(nico), target_id=UUID(claim), edge_type=EdgeType.HAS_FACT, user_id="owner",
            metadata={"relation": "kin_of", "role": "child"}))
        await engine._graph_store.create_edge(MemoryEdge(
            source_id=UUID(claim), target_id=UUID(maya), edge_type=EdgeType.MENTIONS, user_id="owner"))
        walk = await walk_route(engine._graph_store,
                                ((RelationStep("kin_of", ("child", "daughter", "son")),),),
                                user_id="owner", seed_ids=[maya])
    assert str(claim) in walk.scores


# --- the single-turn, correcting and cache paths -----------------------------------------------------


def test_the_single_turn_typed_model_keeps_the_resolved_text_and_the_relation():
    raw = {"entities": [{"name": "Alex Rivera", "entity_type": "person"},
                        {"name": "Northwind Logistics", "entity_type": "organization"}],
           "facts": [{"subject": "Alex Rivera", "predicate": "works_at", "object": "Northwind Logistics",
                      "polarity": "positive", "evidence_quote": "Alex Rivera works at Northwind Logistics.",
                      "resolved_text": "Alex Rivera works at Northwind Logistics.",
                      "relation": "works_at", "role": "backend engineer", "status": "current"}],
           "relationships": []}
    result = validate_raw_output(raw, "Alex Rivera works at Northwind Logistics.", resolving=True, typed=True)
    assert isinstance(result, _TypedResolvingCitedExtractionResult)
    assert (result.facts[0].resolved_text, result.facts[0].relation, result.facts[0].role) == (
        "Alex Rivera works at Northwind Logistics.", "works_at", "backend engineer")


def test_the_typed_correcting_batch_model_carries_the_relation_and_the_memory_label():
    schema = _batch_result_model(correcting=True, typed=True).model_json_schema()["$defs"][
        "_TypedCorrectingCitedFact"]["properties"]
    assert {"relation", "role", "status", "replaces_memory"} <= set(schema)


def test_the_typed_cache_keys_differ_from_the_untyped_ones(tmp_path):
    from types import SimpleNamespace

    from benchmarks.diagnostics.extracted_packs import ExtractionCache

    provider = SimpleNamespace(model_name="test/model", _model="test/model", _temperature=0.0,
                               _reasoning_effort=None)
    cache = ExtractionCache(tmp_path)
    turn = BatchTurn(index=1, role="user", content="Alex works at Northwind.", speaker=None, time=TURN_TIME)
    assert cache._batch_key(provider, [turn]) != cache._batch_key(provider, [turn], typed=True)
    assert cache._key(provider, turn.content, "user") != cache._key(provider, turn.content, "user", typed=True)


class _UntypedProvider:
    """A provider written against the earlier signature: no ``typed`` keyword anywhere."""

    provider_name = "test"
    model_name = "test/model"

    async def extract(self, content, *, role="user", context=(), resolve=None, speaker=None,
                      plural_first_person=False):
        raise NotImplementedError

    async def extract_batch(self, turns, *, memories=()):
        raise NotImplementedError


def test_the_derivation_settings_ignore_the_option_when_the_provider_cannot_take_it():
    pipeline = IngestionPipeline(event_store=None, graph_store=None, vector_index=None, lexical_index=None,
                                 extraction_provider=_UntypedProvider(), write_queue=None,
                                 extraction_batch_target_tokens=2048, typed_extraction=True)
    assert pipeline._typed_batch is False and pipeline._typed_single is False and pipeline._typed_claims is False
    settings = extraction_settings(pipeline)
    assert "typed_extraction" not in settings


def test_a_typed_has_fact_edge_projects_without_the_routing_hint():
    edge = MemoryEdge(source_id=UUID(int=1), target_id=UUID(int=2), edge_type=EdgeType.HAS_FACT, user_id="owner",
                      metadata={"relation": "works_at", "role": "engineer", "status": "current"})
    assert project_admitted_edge(edge).metadata is None
    with pytest.raises(PrivacyDenied):
        project_admitted_edge(edge.model_copy(update={"metadata": {"relation": "free form text"}}))


async def test_turning_the_option_on_rederives_a_stored_source_with_the_relation(tmp_path, monkeypatch):
    """Criterion 3: turning the option on makes the source stale and reingest() derives the fields."""
    _stub(monkeypatch)
    pack = tmp_path / "pack"
    (pack / "lexical").mkdir(parents=True)
    base = dict(db_path=str(pack / "memory.duckdb"), lexical_path=str(pack / "lexical"),
                vector_path=str(pack / "vectors.usearch"), organizer={"opportunistic_enabled": False},
                materialization_budget_ms=5000, extraction_batch_target_tokens=2048, enable_reingest=True)
    owner = "transition-owner"
    async with MemoryEngine.open(PRMEConfig(**base, enable_typed_extraction=False)) as engine:
        for turn in RECORDED["turns"]:
            await engine.store(turn["content"], user_id=owner, role=turn["role"], session_id="transition")
        assert (await engine.reingest(owner)).processed == len(RECORDED["turns"])
        off_claims = _claims(await engine.query_nodes(user_id=owner, limit=1000))
        assert off_claims and all("relation" not in node.metadata for node in off_claims)
    async with MemoryEngine.open(PRMEConfig(**base, enable_typed_extraction=True)) as engine:
        rederived = await engine.reingest(owner)
        on_claims = _claims(await engine.query_nodes(user_id=owner, limit=1000))
    assert rederived.processed == len(RECORDED["turns"])
    works = next(node for node in on_claims
                 if node.metadata.get("predicate") == "works_at" and "relation" in node.metadata)
    assert (works.metadata["relation"], works.metadata["role"]) == ("works_at", "senior backend engineer")
