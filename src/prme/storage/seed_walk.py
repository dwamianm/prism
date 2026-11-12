"""One graph walk from every seed of a question at once (``walk_from_seeds``).

The graph candidates of a retrieval are every node within ``max_hops`` of an
entity the question names. Walking from each seed in turn repeats the same
nodes once per seed: at 20,500 stated facts a question with 44 seeds made
about 500,000 rows into full records to keep 150 (BOUNDED-GRAPH-WALK.md,
2026-11-12). This walk starts from all seeds together and returns each
reached node once, as a small ``WalkedNode`` that holds only what the graph
filters, the candidate order and the seed reach count read. The caller then
reads full records for the nodes it keeps.

The result is the one the per-seed walks give:

- A node that is not a seed gets its fewest hops from any seed: the fewest
  over the per-seed walks is the fewest from the set of seeds.
- A seed gets its fewest hops from another seed, because a seed's own walk
  leaves the seed out. A walk from all seeds would reach every seed at 0, so
  these depths are found by meeting in the middle on the seeds' one-hop
  sets: 1 when another seed's step reaches it, 2 when another seed's step
  reaches a node its own step reaches, and 3 when a step links a node one
  hop from it to a node one hop from another seed.

Each step follows an edge either way, never an unaccepted alias proposal,
and never onto a node another user owns (#438). A node that no longer
exists belongs to nobody and is passed, as in ``get_neighborhood_with_depth``.
The walk passes through any node; only the nodes it returns must be the
user's, current (not superseded or archived) and, with ``valid_at``, valid
then. The SQL is the same on DuckDB and PostgreSQL except for how it reads
JSON and times.

``walk_links`` reads the links the seed reach count steps along
(``_seed_reach``) as two ids each, with the same edge rule, instead of full
edge records: at 20,500 stated facts that read made about 45,000 edges into
records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from prme.types import NodeType, Scope

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Node types whose metadata the walk returns: an entity's metadata names the
# memory's owner (``_seed_reach``), and a summary's says whether default
# retrieval leaves it out (``is_unindexed_summary``). No other filter reads it.
_METADATA_TYPES = ("entity", "summary")


@dataclass(frozen=True, slots=True)
class WalkedNode:
    """The fields of a reached node that the graph candidate stage reads.

    The names match ``MemoryNode``. ``metadata`` is None for a node that is
    not an entity or a summary.
    """

    id: str
    node_type: NodeType
    user_id: str
    scope: Scope
    confidence: float
    created_at: datetime
    valid_from: datetime | None
    valid_to: datetime | None
    metadata: dict | None


def _allowed(dialect: Literal["duckdb", "postgres"]) -> str:
    """The SQL condition that an edge ``e`` is not an alias proposal no one accepted."""
    if dialect == "postgres":
        relation = "e.metadata->>'relation'"
        verified = "e.metadata->>'identity_verified'"
    else:
        relation = "json_extract_string(e.metadata, '$.relation')"
        verified = "json_extract_string(e.metadata, '$.identity_verified')"
    return (
        f"NOT (e.edge_type = 'relates_to' AND COALESCE({relation}, '') = 'alias' "
        f"AND COALESCE({verified}, '') = 'false')"
    )


def walk_links_sql(dialect: Literal["duckdb", "postgres"], touching: str) -> str:
    """The links a walk can step along that touch the nodes ``touching`` names (``walk_links``).

    ``touching`` is the SQL for the node ids: a literal list on DuckDB, a bound
    array on PostgreSQL. A link is the edge's two ends as text, never an edge
    from a node to itself and never an alias proposal no one accepted.
    """
    if dialect == "postgres":
        ends = "e.source_id::text, e.target_id::text"
        where = f"(e.source_id = ANY({touching}) OR e.target_id = ANY({touching}))"
    else:
        ends = "CAST(e.source_id AS VARCHAR), CAST(e.target_id AS VARCHAR)"
        where = f"(e.source_id IN ({touching}) OR e.target_id IN ({touching}))"
    return f"""
        SELECT {ends} FROM edges e
        WHERE {where} AND e.source_id <> e.target_id AND {_allowed(dialect)}
    """


def seed_walk_sql(dialect: Literal["duckdb", "postgres"], *, with_valid_at: bool) -> str:
    """The walk's SQL. Parameters: $1 user, $2 max hops, $3 seed ids, $4 valid-at time."""
    if dialect == "postgres":
        node_id, times = "n.id::text", "n.created_at, n.valid_from, n.valid_to"
    else:
        # DuckDB builds a Python datetime with a time zone slowly; the
        # microseconds since 1970 are the same instants.
        node_id = "CAST(n.id AS VARCHAR)"
        times = "epoch_us(n.created_at), epoch_us(n.valid_from), epoch_us(n.valid_to)"
    allowed = _allowed(dialect)
    valid_at = "AND n.valid_from <= $4 AND (n.valid_to IS NULL OR n.valid_to > $4)" if with_valid_at else ""
    metadata_types = ", ".join(f"'{value}'" for value in _METADATA_TYPES)
    return f"""
        WITH RECURSIVE
        steps AS NOT MATERIALIZED (
            SELECT e.source_id AS src, e.target_id AS dst FROM edges e WHERE {allowed}
            UNION ALL
            SELECT e.target_id AS src, e.source_id AS dst FROM edges e WHERE {allowed}
        ),
        seeds AS MATERIALIZED (
            SELECT s.id FROM (SELECT DISTINCT unnest($3::uuid[]) AS id) s
            LEFT JOIN nodes n ON n.id = s.id
            WHERE n.id IS NULL OR n.user_id = $1
        ),
        walk AS (
            SELECT id, 0 AS depth FROM seeds
            UNION
            SELECT st.dst, w.depth + 1
            FROM walk w
            JOIN steps st ON st.src = w.id
            LEFT JOIN nodes o ON o.id = st.dst
            WHERE w.depth < $2 AND (o.id IS NULL OR o.user_id = $1)
        ),
        hop1 AS MATERIALIZED (
            SELECT sd.id AS seed, st.dst AS id
            FROM seeds sd
            JOIN steps st ON st.src = sd.id
            LEFT JOIN nodes o ON o.id = st.dst
            WHERE o.id IS NULL OR o.user_id = $1
        ),
        seed_depth AS (
            SELECT a.id, 1 AS depth
            FROM hop1 a JOIN seeds sd ON sd.id = a.id
            WHERE a.id <> a.seed
            UNION ALL
            SELECT a.seed, 2
            FROM hop1 a JOIN hop1 b ON b.id = a.id AND b.seed <> a.seed
            UNION ALL
            SELECT a.seed, 3
            FROM hop1 a
            JOIN steps st ON st.src = a.id
            JOIN hop1 b ON b.id = st.dst AND b.seed <> a.seed
        ),
        reached AS (
            SELECT id, MIN(depth) AS depth FROM walk
            WHERE id NOT IN (SELECT id FROM seeds)
            GROUP BY id
            UNION ALL
            SELECT id, MIN(depth) AS depth FROM seed_depth
            WHERE depth <= $2
            GROUP BY id
        )
        SELECT {node_id}, r.depth, n.node_type, n.user_id, n.scope, n.confidence, {times},
               CASE WHEN n.node_type IN ({metadata_types}) THEN n.metadata END
        FROM reached r
        JOIN nodes n ON n.id = r.id
        WHERE n.user_id = $1
          AND n.lifecycle_state IN ('tentative', 'stable', 'contested')
          {valid_at}
    """


def walked_nodes(rows: list[Any], *, epoch: bool) -> list[tuple[WalkedNode, int]]:
    """Decode the walk's rows. With ``epoch`` the three times are microseconds since 1970."""

    def when(value: Any) -> datetime | None:
        if value is None:
            return None
        if epoch:
            return _EPOCH + timedelta(microseconds=value)
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    result = []
    for (node_id, depth, node_type, user_id, scope, confidence,
         created_at, valid_from, valid_to, metadata) in rows:
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        result.append((
            WalkedNode(
                id=node_id,
                node_type=NodeType(node_type),
                user_id=user_id,
                scope=Scope(scope),
                confidence=confidence,
                created_at=when(created_at),
                valid_from=when(valid_from),
                valid_to=when(valid_to),
                metadata=metadata,
            ),
            int(depth),
        ))
    return result
