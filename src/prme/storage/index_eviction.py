"""Evicting a retired node from the derived search indexes.

The vector and lexical indexes hold the documents of active nodes alone.
These paths remove a node's documents here when they retire it: the
engine's ``supersede()``, ``archive()`` and ``resolve_contradiction()``, the
organizer's archival, deduplication, alias, consolidation and summarization
jobs, and a derivation that ``ingest()`` commits with replacements, such as the earlier
copies of a repeated claim (#209). The graph is the source of truth, so the
eviction is best effort and the ``index_compaction`` organizer job retries
whatever it left behind (#41). Evicting on these paths keeps the documents
a session's indexes hold equal to the ones ``rebuild_indexes`` and the open
of a single-file pack build from the graph (#247); the lexical index itself
keeps its BM25 statistics in step (``LexicalIndex.flush``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.storage.write_queue import WriteQueue

logger = logging.getLogger(__name__)


async def delete_from_indexes(write_queue: WriteQueue, lexical_index, vector_index, node_id: str) -> None:
    """Delete lexical first, retaining vector metadata as a retry anchor."""
    await write_queue.submit(
        lambda: lexical_index.delete_by_node_id(node_id),
        label=f"evict.lexical:{node_id}",
    )
    await write_queue.submit(
        lambda: vector_index.delete_by_node_id(node_id),
        label=f"evict.vector:{node_id}",
    )


async def evict_from_indexes(write_queue: WriteQueue, lexical_index, vector_index, node_id: str) -> None:
    """Best-effort lifecycle eviction; compaction retries remaining drift.

    A lexical failure retains vector metadata so maintenance can discover
    the unfinished deletion. Retired graph state already prevents retrieval.
    """
    try:
        await delete_from_indexes(write_queue, lexical_index, vector_index, node_id)
    except Exception:
        logger.warning("evict.index_failed", extra={"node_id": node_id}, exc_info=True)
