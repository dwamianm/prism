"""Workspaces whose namespaces are single-file packs (issue #247).

MemoryWorkspace(single_file_packs=True) keeps each namespace in one
packs/<namespace id>/memory.prme file. Namespaces keep their isolation and
their identity binding across eviction and reopening, an initialized
namespace whose pack is not found is refused rather than recreated, and a
pack path in the workspace configuration never replaces a namespace's pack.
"""

import os
import shutil
from contextlib import asynccontextmanager

import pytest

from prme import PRMEConfig
from prme.storage.namespace_identity import NamespaceIdentityError
from prme.workspace import MemoryWorkspace, WorkspaceError
from tests.test_durable_ingestion import MockEmbeddingProvider


@pytest.fixture
def config():
    return PRMEConfig(database_url=None, namespace_id=None, duckdb_threads=1,
                      organizer={"opportunistic_enabled": False}, materialization_budget_ms=5000)


@asynccontextmanager
async def workspace(path, config, *, max_open=1, single_file_packs=True):
    async with MemoryWorkspace.open(path, config=config, embedding_provider=MockEmbeddingProvider(),
                                    max_open=max_open, single_file_packs=single_file_packs) as value:
        yield value


async def contents(memory, query):
    response = await memory.retrieve(query, user_id="owner")
    return [item.node.content for item in response.results]


async def test_namespaces_are_isolated_single_files_across_eviction_and_reopening(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        for name in ["a", "b"]:
            async with ws.namespace(name) as memory:
                await memory.store(f"Project {name} ships on Friday", user_id="owner")
        # With max_open=1, opening "b" closed "a"; this reads a's file again.
        async with ws.namespace("a", create=False) as memory:
            assert await contents(memory, "ships on Friday") == ["Project a ships on Friday"]
        ids = {info.name: info.id for info in await ws.list_namespaces()}
    for identifier in ids.values():
        assert os.listdir(tmp_path / "packs" / str(identifier)) == ["memory.prme"]

    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("b", create=False) as memory:
            assert memory.namespace.id == ids["b"]
            assert await contents(memory, "ships on Friday") == ["Project b ships on Friday"]


async def test_a_swapped_pack_file_is_rejected(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        for name in ["a", "b"]:
            async with ws.namespace(name) as memory:
                await memory.store(name, user_id="owner")
        ids = {info.name: info.id for info in await ws.list_namespaces()}
    shutil.copyfile(tmp_path / "packs" / str(ids["b"]) / "memory.prme",
                    tmp_path / "packs" / str(ids["a"]) / "memory.prme")
    async with workspace(tmp_path, config) as ws:
        with pytest.raises(NamespaceIdentityError, match="does not match"):
            async with ws.namespace("a"):
                pytest.fail("Swapped pack was opened")


async def test_a_missing_pack_file_is_refused_in_either_layout(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("important") as memory:
            info = memory.namespace
            await memory.store("durable source", user_id="owner")
    pack = tmp_path / "packs" / str(info.id) / "memory.prme"

    # The registry does not record the layout: opened as directory packs, the
    # initialized namespace has no memory.duckdb and is refused, not recreated.
    async with workspace(tmp_path, config, single_file_packs=False) as ws:
        with pytest.raises(WorkspaceError, match="database is missing"):
            async with ws.namespace("important"):
                pass
    assert os.listdir(pack.parent) == ["memory.prme"]

    pack.unlink()
    async with workspace(tmp_path, config) as ws:
        with pytest.raises(WorkspaceError, match="database is missing"):
            async with ws.namespace("important"):
                pass
    assert os.listdir(pack.parent) == []


@pytest.mark.parametrize("single_file_packs", [False, True])
async def test_a_configured_pack_path_never_replaces_a_namespace_pack(tmp_path, config, single_file_packs):
    shared = tmp_path / "shared.prme"
    config = config.model_copy(update={"pack_path": str(shared)})
    async with workspace(tmp_path / "workspace", config, max_open=2, single_file_packs=single_file_packs) as ws:
        for name in ["a", "b"]:
            async with ws.namespace(name) as memory:
                await memory.store(f"Project {name} note", user_id="owner")
        async with ws.namespace("a") as memory:
            assert await contents(memory, "note") == ["Project a note"]
    assert not shared.exists()
