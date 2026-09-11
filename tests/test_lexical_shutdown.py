"""Closed lexical indexes must stop touching a portable memory pack (#71)."""

import asyncio
import shutil

import pytest

from prme.storage.lexical_index import LexicalIndex


async def test_closed_index_does_not_recreate_removed_metadata_lock(tmp_path):
    index = LexicalIndex(str(tmp_path))
    await index.index("one", "A useful project memory", "alice")
    await index.search("memory", "alice")
    await index.close()

    # Keep the closed object alive, as MemoryEngine does. The default
    # OnCommit reader used to recreate this file from a delayed callback,
    # racing rmtree or encryption after close had already returned.
    meta_lock = tmp_path / ".tantivy-meta.lock"
    meta_lock.unlink(missing_ok=True)
    deadline = asyncio.get_running_loop().time() + 1.0
    while asyncio.get_running_loop().time() < deadline:
        assert not meta_lock.exists(), "Reader accessed the pack after close"
        await asyncio.sleep(0.01)


async def test_search_sees_commits_from_another_index_instance(tmp_path):
    reader = LexicalIndex(str(tmp_path))
    writer = LexicalIndex(str(tmp_path))
    try:
        assert await reader.search("Neptune", "alice") == []
        await writer.index("one", "Neptune project launched", "alice")
        await writer.flush()
        results = await reader.search("Neptune", "alice")
        assert [result["node_id"] for result in results] == ["one"]
    finally:
        await writer.close()
        await reader.close()


@pytest.mark.parametrize("cycle", range(8))
async def test_pack_can_move_reopen_and_delete_immediately_after_close(tmp_path, cycle):
    path = tmp_path / "original"
    path.mkdir()
    index = LexicalIndex(str(path), commit_interval=2)
    for i in range(6):
        await index.index(str(i), f"Neptune memory {i}", "alice")
    await index.delete_by_node_id("0")
    await index.search("Neptune", "alice")
    await index.close()

    moved = path.rename(tmp_path / "moved")
    reopened = LexicalIndex(str(moved))
    results = await reopened.search("Neptune", "alice")
    assert {result["node_id"] for result in results} == {str(i) for i in range(1, 6)}
    await reopened.close()
    # Neither index is garbage-collected before the directory is removed.
    shutil.rmtree(moved)
    assert not moved.exists()
