"""A protected store keeps the write path's lexical document rule (#247, #327).

A protected store publishes a node stored directly with store() through a
journaled plan, so that node owns a derivation artifact row like an extracted
one. Rebuilds, restores and single-file pack opens must still give a directly
stored entity its lexical document, and an extracted entity none.
"""

import pytest

from prme import MemoryEngine
from prme.types import NodeType
from tests.privacy_fakes import config_for, make_runtime, policy_for

NAME = "Caroline"


async def lexical_ids(engine, owner):
    return {hit["node_id"] for hit in await engine._lexical_index.search(NAME, owner, limit=20)}


@pytest.mark.parametrize("layout", ["directory", "pack"])
async def test_a_protected_directly_stored_entity_keeps_its_lexical_document(tmp_path, layout):
    runtime = make_runtime()
    policy = policy_for(runtime)
    owner = str(policy.owner_id)
    runtime.extractor.result = {"entities": [{"name": NAME, "entity_type": "person"}], "facts": [],
                                "relationships": [], "summary": None}
    config = config_for(tmp_path, layout=layout, policy=policy)
    async with MemoryEngine.open(config, embedding_provider=runtime.embedder, privacy_runtime=runtime) as engine:
        stored = await engine.store(f"{NAME} runs the platform team.", user_id=owner, node_type=NodeType.ENTITY)
        direct = str((await engine.get_event_nodes(stored, user_id=owner))[0].id)
        ingested = await engine.ingest(f"{NAME} said hello.", user_id=owner, wait_for_extraction=True)
        extracted = {str(node.id) for node in await engine.get_event_nodes(ingested, user_id=owner)
                     if node.node_type == NodeType.ENTITY}
        assert extracted and direct not in extracted
        written = await lexical_ids(engine, owner)
        assert direct in written and not extracted & written
        if layout == "directory":
            # The protected rebuild re-adds every lexical document it owes.
            await engine._lexical_index.delete_by_node_id(direct)
            assert direct not in await lexical_ids(engine, owner)
            await engine.rebuild_indexes(user_id=owner)
            assert await lexical_ids(engine, owner) == written
    # A single-file pack loads its lexical index from the graph on every open.
    async with MemoryEngine.open(config, embedding_provider=runtime.embedder, privacy_runtime=runtime) as engine:
        assert await lexical_ids(engine, owner) == written
