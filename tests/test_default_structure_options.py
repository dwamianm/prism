"""The structure options are on in the product defaults: the issue 258 repro with no option set.

Since 2026-10-19, in the alpha channel (0.16.0a3 and 0.16.0a4), ``enable_ingest_claim_fixes``, ``enable_mention_links``,
``enable_owner_name_carry``, ``packing.graph_seed_words``, ``packing.folded_records_keep_rank`` and
``packing.session_context_skip_entities`` are defaults, and ``enable_owner_group_links`` after them
(prism-monorepo issue 275).
``tests/conftest.py`` starts every other test with them off; this module removes those variables and checks what
a project that sets nothing gets. The tests of each option compare it on against off.
"""

import pytest

from prme import MemoryEngine
from prme.ingestion.group_links import is_group_link
from prme.ingestion.schema import ExtractionResult
from prme.types import EdgeType, NodeType
from tests import test_durable_ingestion
from tests.conftest import STRUCTURE_OPTIONS
from tests.test_ingest_names_and_claims import CITIES, DROPPED, SENTENCE, batched, claims, ingest_trip

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.fixture
def product_defaults(monkeypatch):
    for name in STRUCTURE_OPTIONS:
        monkeypatch.delenv(name)


async def test_the_structure_options_are_on(product_defaults, config):
    assert config.enable_ingest_claim_fixes and config.enable_mention_links and config.enable_owner_name_carry
    assert config.packing.graph_seed_words and config.packing.folded_records_keep_rank
    assert config.packing.session_context_skip_entities
    assert config.enable_owner_group_links


def test_a_packing_config_built_in_code_keeps_the_packing_options_off(product_defaults):
    from prme.retrieval.config import PackingConfig

    bare = PackingConfig()
    assert not (bare.graph_seed_words or bare.folded_records_keep_rank or bare.session_context_skip_entities)
    # An off setting stays out of a dump, so a receipt of a retrieval that does not use it keeps its bytes.
    assert "session_context_skip_entities" not in bare.model_dump()


async def test_the_repro_with_the_defaults_names_the_owner_links_the_entities_and_folds_the_statement(
        product_defaults, config, user):
    async with MemoryEngine.open(batched(config)) as engine:
        event_ids = await ingest_trip(engine, user)
        first = await claims(engine, user, event_ids[0])
        later = await claims(engine, user, event_ids[3])
        nodes = await engine.query_nodes(user_id=user)
        entities = {node.content: node for node in nodes if node.node_type == NodeType.ENTITY}
        japan = await engine._graph_store.get_edges(target_id=str(entities["Japan"].id), edge_type=EdgeType.MENTIONS)
        by_id = {node.id: node for node in nodes}
        we = {}
        for edge in await engine._graph_store.get_edges(node_ids=[str(node_id) for node_id in by_id]):
            if is_group_link(edge):
                we.setdefault(by_id[edge.source_id].content, set()).add(by_id[edge.target_id].content)
        response = await engine.retrieve("Who is Alex Rivera's partner Jordan?", user_id=user)
        lower_case = await engine.retrieve("which cities will alex and jordan visit in japan?", user_id=user)

    # The turn that says who it is binds its I, and a later turn that names nobody takes the owner's name.
    assert first and all(subject.content == "Alex Rivera" for _, subject in first)
    assert later and all(subject.content == "Alex Rivera" and node.metadata["speaker_reference"]["basis"] == "owner_name"
                         for node, subject in later)
    assert [name for name in entities if name == "I"] == []
    # Claims and notes that name an entity are linked to it.
    assert any(edge.metadata.get("basis") == "text_mention" for edge in japan)
    # The two "We" claims are linked to the owner and to the partner the introduction names.
    assert we == {CITIES: {"Alex Rivera", "Jordan"}, DROPPED: {"Alex Rivera", "Jordan"}}
    # No two nodes of an event share a content, and retrieval folds the statement into the claim it restates.
    for event_id in event_ids:
        contents = [node.content for node in nodes if event_id in {str(ref) for ref in node.evidence_refs}]
        assert len(contents) == len(set(contents)), contents
    returned = [result.node.content for result in response.results]
    assert SENTENCE in returned and "Alex Rivera partner Jordan." not in returned
    # A question typed in lower case still seeds the graph.
    assert lower_case.metadata.candidates_generated["GRAPH"] > 0


async def test_a_provider_written_against_the_earlier_signature_still_works_with_the_defaults(
        product_defaults, config, user):
    """The claim fixes name plural_first_person only to a provider that takes it, so turning them on breaks none."""
    asked = []

    async def extract(content, *, role="user"):
        asked.append(content)
        return ExtractionResult(entities=[], facts=[])

    async with MemoryEngine.open(config) as engine:
        assert engine._pipeline._claim_fixes is True
        engine._pipeline._extraction_provider.extract = extract
        await engine.ingest("We like tea.", user_id=user, wait_for_extraction=True)
    assert asked == ["We like tea."]
