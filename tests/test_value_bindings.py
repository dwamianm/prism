"""Typed presentation/lookup values stay source-bound and resolve safely."""

from uuid import uuid4

import pytest

from prme import MemoryEngine, MemoryValueBinding, PRMEConfig
from prme.models.nodes import MemoryNode
from prme.models.value_bindings import VALUE_BINDINGS_METADATA_KEY
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.types import NodeType, RepresentationLevel
from tests.test_durable_ingestion import MockEmbeddingProvider


@pytest.fixture
def config(tmp_path):
    return PRMEConfig(
        db_path=str(tmp_path / "memory.duckdb"),
        vector_path=str(tmp_path / "vectors.usearch"),
        lexical_path=str(tmp_path / "lexical"),
        organizer={"opportunistic_enabled": False},
    )


def city_binding(*, lookup: str = "Salt Lake City") -> MemoryValueBinding:
    return MemoryValueBinding(
        reference="current-city-1",
        kind="city",
        presentation="Salt Lake City(Utah)",
        lookup=lookup,
    )


async def test_store_retrieve_and_restart_preserve_typed_value_binding(config):
    source = '{"plan":"Current City: Salt Lake City(Utah)"}'
    projection = "Confirmed plan\nCurrent City: Salt Lake City(Utah)"
    provider = MockEmbeddingProvider()
    async with MemoryEngine.open(config, embedding_provider=provider) as engine:
        receipt = await engine.store_with_receipt(
            source,
            retrieval_content=projection,
            value_bindings=[city_binding()],
            user_id="alice",
        )
        event = await engine.get_event(str(receipt.event_id), user_id="alice")
        assert event.content == source
        assert event.metadata[VALUE_BINDINGS_METADATA_KEY] == (
            receipt.node.metadata[VALUE_BINDINGS_METADATA_KEY]
        )
        response = await engine.retrieve(
            "Salt Lake City", user_id="alice", token_budget=4096
        )
        visible = response.bundle.value_bindings()
        assert len(visible) == 1
        assert visible[0].node_id == receipt.node_id
        assert visible[0].presentation == "Salt Lake City(Utah)"
        assert visible[0].lookup == "Salt Lake City"
        rendered = response.bundle.render_value_bindings()
        assert '"presentation":"Salt Lake City(Utah)"' in rendered
        assert '"lookup":"Salt Lake City"' in rendered

        arguments = {
            "city": "Salt Lake City(Utah)",
            "nested/key": ["Salt Lake City(Utah)"],
            "sentence": "Fly to Salt Lake City(Utah) tomorrow",
        }
        resolution = response.bundle.resolve_tool_arguments(arguments)
        assert resolution.arguments == {
            "city": "Salt Lake City",
            "nested/key": ["Salt Lake City"],
            "sentence": "Fly to Salt Lake City(Utah) tomorrow",
        }
        assert [item.json_pointer for item in resolution.replacements] == [
            "/city",
            "/nested~1key/0",
        ]
        assert [item.operation for item in resolution.binding_uses] == [
            "replaced",
            "replaced",
        ]
        assert arguments["city"] == "Salt Lake City(Utah)"

        already_lookup = response.bundle.resolve_tool_arguments(
            {"city": "Salt Lake City"}
        )
        assert already_lookup.changed is False
        assert already_lookup.arguments == {"city": "Salt Lake City"}
        assert len(already_lookup.binding_uses) == 1
        assert already_lookup.binding_uses[0].operation == "already_lookup"
        assert already_lookup.binding_uses[0].presentation == (
            "Salt Lake City(Utah)"
        )

    async with MemoryEngine.open(
        config, embedding_provider=MockEmbeddingProvider()
    ) as reopened:
        nodes = await reopened.get_event_nodes(str(receipt.event_id), user_id="alice")
        assert nodes[0].metadata[VALUE_BINDINGS_METADATA_KEY][0]["lookup"] == (
            "Salt Lake City"
        )


@pytest.mark.parametrize(
    "source,projection,binding,error",
    [
        (
            "Current City: Boise",
            "Current City: Salt Lake City(Utah)",
            city_binding(),
            "absent from source",
        ),
        (
            "Current City: Salt Lake City(Utah)",
            "Current City: Boise",
            city_binding(),
            "absent from retrieval",
        ),
    ],
)
async def test_store_rejects_unbound_presentation_before_admission(
    config, source, projection, binding, error
):
    async with MemoryEngine.open(
        config, embedding_provider=MockEmbeddingProvider()
    ) as engine:
        with pytest.raises(ValueError, match=error):
            await engine.store(
                source,
                retrieval_content=projection,
                value_bindings=[binding],
                user_id="alice",
            )
        assert await engine.get_events("alice") == []


async def test_store_rejects_reserved_metadata_and_duplicate_binding_identity(config):
    source = "Salt Lake City(Utah) and Boise(Idaho)"
    async with MemoryEngine.open(
        config, embedding_provider=MockEmbeddingProvider()
    ) as engine:
        with pytest.raises(ValueError, match="reserved"):
            await engine.store(
                source,
                user_id="alice",
                metadata={VALUE_BINDINGS_METADATA_KEY: []},
            )
        with pytest.raises(ValueError, match="Duplicate value binding reference"):
            await engine.store(
                source,
                user_id="alice",
                value_bindings=[
                    city_binding(),
                    MemoryValueBinding(
                        reference="current-city-1",
                        kind="city",
                        presentation="Boise(Idaho)",
                        lookup="Boise",
                    ),
                ],
            )
        assert await engine.get_events("alice") == []


def _bundle(*nodes: MemoryNode, visible: bool = True) -> MemoryBundle:
    candidates = [
        RetrievalCandidate(
            node=node,
            representation=RepresentationLevel.FULL,
            rendered_text=node.content if visible else f"note:{node.id}",
        )
        for node in nodes
    ]
    return MemoryBundle(sections={"stable_facts": candidates})


def test_resolution_rejects_conflicting_visible_lookup_values():
    def node(lookup: str) -> MemoryNode:
        return MemoryNode(
            id=uuid4(),
            user_id="alice",
            node_type=NodeType.NOTE,
            content="Salt Lake City(Utah)",
            metadata={
                VALUE_BINDINGS_METADATA_KEY: [
                    city_binding(lookup=lookup).model_dump(mode="json")
                ]
            },
        )

    bundle = _bundle(node("Salt Lake City"), node("SLC"))
    with pytest.raises(ValueError, match="Ambiguous lookup"):
        bundle.resolve_tool_arguments({"city": "Salt Lake City(Utah)"})


def test_resolution_omits_ambiguous_reverse_lookup_match():
    def node(presentation: str) -> MemoryNode:
        binding = MemoryValueBinding(
            reference=presentation.replace("(", "-").replace(")", "").replace(" ", "-"),
            kind="city",
            presentation=presentation,
            lookup="Springfield",
        )
        return MemoryNode(
            id=uuid4(),
            user_id="alice",
            node_type=NodeType.NOTE,
            content=presentation,
            metadata={
                VALUE_BINDINGS_METADATA_KEY: [binding.model_dump(mode="json")]
            },
        )

    bundle = _bundle(node("Springfield(Illinois)"), node("Springfield(Oregon)"))
    resolution = bundle.resolve_tool_arguments({"city": "Springfield"})
    assert resolution.arguments == {"city": "Springfield"}
    assert resolution.binding_uses == ()


def test_resolution_rejects_cross_direction_value_collision():
    def node(reference: str, presentation: str, lookup: str) -> MemoryNode:
        binding = MemoryValueBinding(
            reference=reference,
            kind="city",
            presentation=presentation,
            lookup=lookup,
        )
        return MemoryNode(
            id=uuid4(),
            user_id="alice",
            node_type=NodeType.NOTE,
            content=presentation,
            metadata={
                VALUE_BINDINGS_METADATA_KEY: [binding.model_dump(mode="json")]
            },
        )

    bundle = _bundle(
        node("short", "NYC", "New York"),
        node("alternate", "New York City", "NYC"),
    )
    with pytest.raises(ValueError, match="both presentation and lookup"):
        bundle.resolve_tool_arguments({"city": "NYC"})


def test_binding_is_unavailable_when_presentation_was_not_packed():
    node = MemoryNode(
        user_id="alice",
        node_type=NodeType.NOTE,
        content="Salt Lake City(Utah)",
        metadata={
            VALUE_BINDINGS_METADATA_KEY: [city_binding().model_dump(mode="json")]
        },
    )
    bundle = _bundle(node, visible=False)
    assert bundle.value_bindings() == ()
    resolution = bundle.resolve_tool_arguments({"city": "Salt Lake City(Utah)"})
    assert resolution.changed is False
    assert resolution.arguments == {"city": "Salt Lake City(Utah)"}


def test_read_rejects_binding_metadata_detached_from_node_content():
    node = MemoryNode(
        user_id="alice",
        node_type=NodeType.NOTE,
        content="Boise(Idaho)",
        metadata={
            VALUE_BINDINGS_METADATA_KEY: [city_binding().model_dump(mode="json")]
        },
    )
    with pytest.raises(ValueError, match="unbound value binding"):
        _bundle(node).value_bindings()
