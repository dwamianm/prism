"""P8 bar 3: the graph candidate stage with its filter, order and cut done inside the walk's own SQL.

The current stage (``candidates._generate_graph_candidates``) turns every row the walk reaches into a
``WalkedNode`` and then keeps 150 of them in Python. On the m3 pack a question with 25 seeds reaches 38,140 rows,
so 37,990 records are built and thrown away. Measured on that pack the decode is about 89 ms of the walk's
137 ms and the SQL fetch is about 43 ms.

This module replaces the stage's walk read with one query that does the work in a different order:

1. The same walk SQL (``prme.storage.seed_walk.seed_walk_sql``), unchanged, so the reached set is the same set.
2. A ``ROW_NUMBER`` window over it in the candidate order the stage uses, ``confidence DESC, created_at DESC,
   node_id ASC``, marking the ``graph_max_candidates`` winners.
3. A select of five columns: id, depth, the winner flag, node type and entity metadata. The 38,000 losing rows
   cross the wire as five narrow columns and are never decoded into records.

Everything else stays where it is. Seeds come from ``_find_seed_entities``, the kept ids are hydrated by
``get_nodes``, and ``_seed_reach`` still walks every reached node, which is what the depth column is for. The
Python filters the stage applies run in the SQL instead, in the same order of effect, so the kept list is the
same list.

``--check`` in ``interleave.py`` compares the stage's candidate ids, in order, against this one's, for every
question, and reports every difference rather than the first.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from prme.index_policy import unindexed_summary_sql

_COLUMNS = "node_id, depth, node_type, user_id, scope, confidence, created_at, valid_from, valid_to, metadata"


def _microseconds(value: datetime) -> int:
    return int(round(value.timestamp() * 1_000_000))


def cheap_walk_sql(
    base_walk_sql: str,
    *,
    limit: int,
    scopes: list[str] | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    first_parameter: int,
) -> tuple[str, list[Any]]:
    """The walk wrapped in a window that marks the kept rows, in five columns, and its extra parameters.

    ``base_walk_sql`` is ``seed_walk_sql``'s text for the same settings, so the reached set is unchanged. The
    conditions are the ones the current stage applies in Python, written as SQL over the walk's own columns:

    - an unindexed hierarchical summary is not a graph candidate (``is_unindexed_summary``, #534);
    - a node outside the requested scopes is skipped;
    - a node outside the temporal window is skipped, except an entity or a preference.

    The order is the stage's own sort key. ``node_id`` is already text, so the third key is the same string.
    Returns the SQL and the parameters the SQL added, numbered from ``first_parameter``.
    """
    conditions = [f"NOT {unindexed_summary_sql('b')}"]
    extra: list[Any] = []
    if scopes:
        values = ", ".join("'" + value + "'" for value in scopes)
        conditions.append(f"scope IN ({values})")
    temporal = []
    if time_from is not None:
        extra.append(_microseconds(time_from))
        temporal.append(f"(valid_to IS NULL OR valid_to > ${first_parameter + len(extra) - 1})")
    if time_to is not None:
        extra.append(_microseconds(time_to))
        temporal.append(f"(valid_from IS NULL OR valid_from <= ${first_parameter + len(extra) - 1})")
    if temporal:
        conditions.append("(node_type IN ('entity', 'preference') OR (" + " AND ".join(temporal) + "))")
    sql = f"""
        WITH b({_COLUMNS}) AS ({base_walk_sql}),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                ORDER BY confidence DESC, created_at DESC, node_id ASC
            ) AS rn
            FROM b
            WHERE {' AND '.join(conditions)}
        )
        SELECT node_id, depth, rn <= {int(limit)} AS kept, node_type,
               CASE WHEN node_type = 'entity' THEN metadata END
        FROM ranked
    """
    return sql, extra


def split_rows(rows: list[tuple]) -> tuple[dict[str, int], list[tuple[str, int]]]:
    """``(depths, kept)``: every reached node's fewest hops, and the kept ids in the window's order."""
    depths: dict[str, int] = {}
    kept: list[tuple[str, int]] = []
    for node_id, depth, is_kept, _node_type, _metadata in rows:
        depths[node_id] = int(depth)
        if is_kept:
            kept.append((node_id, int(depth)))
    return depths, kept


class _ReachNode:
    """The two fields ``_seed_reach`` reads from a walk record: the node type and the metadata."""

    __slots__ = ("id", "node_type", "metadata")

    def __init__(self, node_id: str, node_type: Any, metadata: Any) -> None:
        from prme.types import NodeType

        self.id = node_id
        self.node_type = NodeType(node_type)
        self.metadata = json.loads(metadata) if isinstance(metadata, str) else metadata


_PLAIN = object()


class _PlainNode:
    """A reached node that is not an entity: ``_seed_reach`` reads its type and never its metadata.

    One instance stands for every such node. ``_seed_reach`` reads ``node.metadata`` and
    ``node.node_type`` from the values of the node map and never reads ``node.id``, so a fact, a note and a
    task are indistinguishable to it and share this record.
    """

    __slots__ = ("node_type", "metadata")

    def __init__(self, node_type: Any) -> None:
        from prme.types import NodeType

        self.node_type = NodeType(node_type)
        self.metadata = None


async def cheap_graph_candidates(
    analysis,
    graph_store,
    user_id: str,
    config,
    *,
    scope=None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    seed_reach: bool = False,
    diagnostics=None,
) -> list[dict]:
    """``_generate_graph_candidates`` with the walk's filter, order and cut in SQL.

    Same return shape and same order. Same seeds, same reach count, same hydration. The one read that changes is
    the walk.
    """
    from prme.retrieval.candidates import _find_seed_entities, _seed_reach
    from prme.storage._threading import run_to_completion
    from prme.storage.seed_walk import seed_walk_sql

    seed_nodes = await _find_seed_entities(
        analysis, graph_store, user_id, scope, config.graph_seed_words,
    )
    seed_ids = [str(node.id) for node in seed_nodes]
    if not seed_ids:
        return []
    valid_at = time_from if time_from is not None else time_to
    max_hops = min(config.graph_max_hops, 3)
    hop_proximity = {1: 1.0, 2: 0.7, 3: 0.4}
    scope_values = [value.value for value in scope] if scope else None
    params: list[Any] = [user_id, max_hops, list(seed_ids)]
    if valid_at is not None:
        params.append(valid_at)
    query, extra = cheap_walk_sql(
        seed_walk_sql("duckdb", with_valid_at=valid_at is not None),
        limit=config.graph_max_candidates, scopes=scope_values,
        time_from=time_from, time_to=time_to, first_parameter=len(params) + 1,
    )
    params.extend(extra)

    def read():
        return graph_store._conn.execute(query, params).fetchall()

    async with graph_store._conn_lock:
        rows = await run_to_completion(read)
    depths, kept = split_rows(rows)

    records: dict[str, Any] = {}
    if kept:
        hydrated = await graph_store.get_nodes([node_id for node_id, _ in kept], user_id=user_id)
        records = {str(node.id): node for node in hydrated}
    result = [
        {"node": records[node_id], "graph_proximity": hop_proximity.get(depth, 0.4)}
        for node_id, depth in kept if node_id in records
    ]
    if seed_reach:
        # The reach count reads every reached node's type and, for an entity, its metadata. A non-entity's
        # metadata is never read, so those rows share one stand-in instead of building 38,000 objects.
        reached: dict[str, Any] = {}
        plain = _PlainNode("fact")
        for node_id, _depth, _is_kept, node_type, metadata in rows:
            if node_id in reached:
                continue
            reached[node_id] = _ReachNode(node_id, node_type, metadata) if node_type == "entity" else plain
        try:
            reach = await _seed_reach(graph_store, seed_nodes, reached, max_hops, depths)
        except Exception:
            reach = None
        for candidate in result:
            candidate["graph_seed_reach"] = (reach or {}).get(str(candidate["node"].id))
    return result
