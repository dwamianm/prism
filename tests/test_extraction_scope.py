"""Model output must not grant itself a different memory namespace."""

from unittest.mock import AsyncMock

from prme import MemoryEngine
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.types import NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_model_scope_is_advisory_and_entities_are_namespace_local(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        result = ExtractionResult(
            entities=[ExtractedEntity(name="Alice", entity_type="person", scope="organisation")],
            facts=[ExtractedFact(subject="Alice", predicate="uses", object="Python", scope="system")],
        )
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=result)
        await engine.ingest("Alice uses Python", user_id=user, scope=Scope.PERSONAL, wait_for_extraction=True)
        private = await engine.query_nodes(user_id=user)
        assert private and all(n.scope == Scope.PERSONAL for n in private)
        await engine.ingest("Alice uses Python", user_id=user, scope=Scope.PROJECT, wait_for_extraction=True)
        entities = await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)
        assert len(entities) == 2
        assert {n.scope for n in entities} == {Scope.PERSONAL, Scope.PROJECT}
        facts = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        assert {n.scope for n in facts} == {Scope.PERSONAL, Scope.PROJECT}
        assert all(n.metadata["suggested_scope"] == "system" for n in facts)
