"""Startup backfill migration for existing nodes without epistemic_type.

Backfills nodes that were created before the epistemic_type field was
added. Uses heuristics based on node_type to assign best-guess epistemic
types. Preserves existing confidence values per user decision. Marks
backfilled nodes with metadata to distinguish them from creation-typed
nodes. Idempotent -- skips nodes already marked with _epistemic_backfill.

Reference: CONTEXT.md locked decisions on existing data migration.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from prme.epistemic.inference import infer_epistemic_type
from prme.types import NodeType

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


async def backfill_epistemic_types(
    graph_store: object,
    engine_conn: "duckdb.DuckDBPyConnection | None" = None,
) -> int:
    """Backfill epistemic_type on existing nodes using heuristic inference.

    Queries nodes that have NOT been marked with _epistemic_backfill in
    their metadata. For each, infers the best-guess epistemic_type from
    node_type and updates the node in DuckDB. Existing confidence values
    are preserved (per user decision).

    Each backfilled node is marked with metadata:
      {"_epistemic_backfill": true, "_epistemic_backfill_method": "heuristic_v1"}

    An EPISTEMIC_TYPE_ASSIGNED operation is logged for each backfilled node.

    The function is idempotent -- nodes already marked are skipped.

    Args:
        graph_store: The DuckPGQGraphStore instance (used to access _conn).
        engine_conn: Optional DuckDB connection override. If None, uses
            graph_store._conn.

    Returns:
        Count of nodes backfilled.
    """
    conn = engine_conn or getattr(graph_store, "_conn", None)
    if conn is None:
        logger.warning(
            "backfill_epistemic_types: no DuckDB connection available, skipping"
        )
        return 0

    def _backfill_sync() -> int:
        # Find candidates: nodes where metadata does not contain _epistemic_backfill.
        # We check for nodes where metadata IS NULL or metadata does NOT contain
        # the backfill marker. This covers both pre-migration nodes (NULL metadata)
        # and nodes with metadata that hasn't been backfilled yet.
        rows = conn.execute("""
            SELECT id, node_type, metadata, epistemic_type
            FROM nodes
            WHERE metadata IS NULL
               OR CAST(metadata AS VARCHAR) NOT LIKE '%_epistemic_backfill%'
        """).fetchall()

        if not rows:
            logger.debug("backfill_epistemic_types: no candidates found")
            return 0

        backfilled = 0
        for row in rows:
            node_id = str(row[0])
            node_type_str = row[1]
            raw_metadata = row[2]
            current_epistemic = row[3]

            # Parse node_type
            try:
                node_type = NodeType(node_type_str)
            except ValueError:
                logger.warning(
                    "backfill_epistemic_types: unknown node_type %r for node %s, skipping",
                    node_type_str,
                    node_id,
                )
                continue

            # Infer best-guess epistemic type
            inferred_type = infer_epistemic_type(node_type)

            # Parse existing metadata
            if raw_metadata is None:
                metadata = {}
            elif isinstance(raw_metadata, str):
                try:
                    metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    metadata = {}
            elif isinstance(raw_metadata, dict):
                metadata = raw_metadata
            else:
                metadata = {}

            # Skip if already backfilled (double-check)
            if metadata.get("_epistemic_backfill"):
                continue

            # Add backfill markers
            metadata["_epistemic_backfill"] = True
            metadata["_epistemic_backfill_method"] = "heuristic_v1"
            metadata_json = json.dumps(metadata)

            # Update node: epistemic_type + metadata markers
            # CRITICAL: Do NOT recalculate confidence (user decision)
            conn.execute(
                """
                UPDATE nodes
                SET epistemic_type = ?,
                    metadata = ?,
                    updated_at = current_timestamp
                WHERE id = ?
                """,
                [inferred_type.value, metadata_json, node_id],
            )

            # Log EPISTEMIC_TYPE_ASSIGNED operation
            logger.info(
                "epistemic_type_assigned",
                extra={
                    "op_type": "EPISTEMIC_TYPE_ASSIGNED",
                    "target_id": node_id,
                    "epistemic_type": inferred_type.value,
                    "previous_type": current_epistemic,
                    "assignment_method": "backfill",
                    "backfill_heuristic": "heuristic_v1",
                },
            )
            backfilled += 1

        return backfilled

    count = await asyncio.to_thread(_backfill_sync)
    if count > 0:
        logger.info(
            "backfill_epistemic_types: backfilled %d nodes", count
        )
    return count
