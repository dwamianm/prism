"""The typed walk as one SQL statement per question, on PostgreSQL and on DuckDB.

The Python walk reads the store one frontier at a time and builds an edge object
for every row, which costs about ten microseconds an edge and reads thousands of
edges on a hub. This walk sends the whole neighborhood of a question in a single
statement and gets back compact rows:

1. a three layer join over the typed claim edges: the claims on the seeds, on
   the entities those reach, and on the entities those reach (two hops, as the
   Python walk's last read), each layer capped per entity, newest first, and
   each frontier capped;
2. the ``mentions`` edges of every entity the layers reach, capped per entity.

The cue filter, the superseded and negative claim rules and the owner rule go
into the statement, so the rows are those the Python walk would score; the
scoring itself is unchanged (``generic_scores`` over a ``RowsSource``). The owner
entity is never a pv below the seed layer, and its ``mentions`` edges are not
read. The caps are in ``GATE.md``: 32 claims per entity per layer (128 for a seed,
which the question names), 32 mentions per reached entity, 64 entities per layer.
On a pack the caps do not cut, the scores equal the Python walk's (a test).
"""
from __future__ import annotations

import json
import re

from benchmarks.graph_oracle.gold import Gold
from benchmarks.graph_oracle.graph import DIRECT, Edge, Handles
from benchmarks.graph_oracle.strategies import GraphResult, Params, generic_scores, walk_plan
from prme.storage._threading import run_to_completion

CLAIM_CAP = 32
SEED_CAP = 128
MENTION_CAP = 32
FRONTIER_CAP = 64
SAFE = re.compile(r"^[0-9a-f-]{36}$")
CLAIM_TYPES = "'relates_to', 'part_of', 'has_fact'"
ALWAYS_ALLOWED = ("located_in", "part_of", "has")


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


class RowsSource:
    """A ``touching`` source over rows one statement already read."""

    def __init__(self, claims: list[Edge], mentions: dict[str, set[str]], rows: int):
        self.calls = 1
        self.edges_read = rows
        self._by_entity: dict[str, dict[str, Edge]] = {}
        for claim in claims:
            for end in {claim.s, claim.o} - {None}:
                self._by_entity.setdefault(end, {})[claim.id] = claim
        self._named = mentions

    async def touching(self, entity_ids: list[str]) -> tuple[list[Edge], dict[str, set[str]]]:
        claims = {claim_id: claim for entity_id in entity_ids for claim_id, claim in self._by_entity.get(entity_id, {}).items()}
        return list(claims.values()), {entity_id: set(self._named.get(entity_id, ())) for entity_id in entity_ids}


class SqlWalk:
    def __init__(self, engine, backend: str, owner: str, handles: Handles, gold: Gold, params: Params | None = None, *,
                 claim_cap: int = CLAIM_CAP, seed_cap: int = SEED_CAP, mention_cap: int = MENTION_CAP,
                 frontier_cap: int = FRONTIER_CAP):
        if backend not in ("duckdb", "postgres"):
            raise ValueError("backend is duckdb or postgres")
        self._store, self._backend, self._owner, self._handles, self._gold = engine._graph_store, backend, owner, handles, gold
        self.params = params or Params()
        self._caps = (claim_cap, seed_cap, mention_cap, frontier_cap)
        self.last_sql = ""

    def _json(self, key: str) -> str:
        return f"metadata->>'{key}'" if self._backend == "postgres" else f"json_extract_string(metadata, '$.{key}')"

    def _evidence(self) -> str:
        return "metadata->'evidence'" if self._backend == "postgres" else "json_extract(metadata, '$.evidence')"

    def sql(self, seeds: list[str], cued: frozenset | None) -> str:
        claim_cap, seed_cap, mention_cap, frontier_cap = self._caps
        ids = [self._handles.entity[entity_id] for entity_id in seeds]
        if not all(SAFE.match(node_id) for node_id in ids):
            raise ValueError("a node id is not a uuid")
        hubs = [self._handles.entity[entity_id] for entity_id in sorted(self._gold.owners) if entity_id in self._handles.entity]
        hub_list = ", ".join(f"CAST({_quote(node_id)} AS UUID)" for node_id in hubs) or "CAST('00000000-0000-0000-0000-000000000000' AS UUID)"
        user = _quote(self._owner)
        params = self.params
        where = [f"e.user_id = {user}", f"e.edge_type IN ({CLAIM_TYPES})", f"{self._json('shape')} IN ('direct', 'both')"]
        if params.superseded == 0:
            where.append(f"{self._json('status')} <> 'superseded'")
        if params.negative == 0:
            where.append(f"{self._json('polarity')} <> 'negative'")
        if not params.inferred:
            where.append(f"{self._json('inferred')} <> 'true'")
        allowed = None
        if cued is not None:
            allowed = set(cued) | set(ALWAYS_ALLOWED)
        if params.relations is not None:
            allowed = set(params.relations) | {"has"} if allowed is None else allowed & (set(params.relations) | {"has"})
        if allowed is not None:
            where.append(f"{self._json('relation')} IN ({', '.join(_quote(name) for name in sorted(allowed))})")
        # The metadata column is read through ``e.`` in the join, so the JSON helpers get that prefix.
        claim_where = " AND ".join(item.replace("metadata", "e.metadata") if "metadata" in item else item for item in where)

        def layer(name: str, frontier: str, cap: str, skip_hub: bool) -> str:
            pivot_filter = f" AND p.id NOT IN ({hub_list})" if skip_hub else ""
            return f"""{name} AS (
  SELECT id, source_id, target_id, edge_type, metadata, created_at, pv FROM (
    SELECT u.*, row_number() OVER (PARTITION BY u.pv ORDER BY u.created_at DESC) AS rn FROM (
      SELECT e.id, e.source_id, e.target_id, e.edge_type, e.metadata, e.created_at, p.id AS pv
      FROM {frontier} p JOIN edges e ON e.source_id = p.id WHERE {claim_where}{pivot_filter}
      UNION ALL
      SELECT e.id, e.source_id, e.target_id, e.edge_type, e.metadata, e.created_at, p.id AS pv
      FROM {frontier} p JOIN edges e ON e.target_id = p.id WHERE {claim_where}{pivot_filter} AND e.source_id <> e.target_id
    ) u
  ) w WHERE rn <= {cap}
)"""

        def next_frontier(name: str, claims: str, exclude: str) -> str:
            return f"""{name} AS (
  SELECT other AS id FROM (
    SELECT CASE WHEN pv = source_id THEN target_id ELSE source_id END AS other, created_at FROM {claims}
    WHERE edge_type IN ('relates_to', 'part_of') AND pv NOT IN ({hub_list})
  ) x WHERE other NOT IN ({exclude}) AND other NOT IN ({hub_list}) GROUP BY other ORDER BY max(created_at) DESC LIMIT {frontier_cap}
)"""

        seed_values = ", ".join(f"({_quote(node_id)})" for node_id in ids)
        # The hub is never expanded below the seed layer, but a seed that is the hub is expanded in the first.
        first = layer("c0", "l0", str(seed_cap), False)
        first_l1 = f"""l1 AS (
  SELECT other AS id FROM (
    SELECT CASE WHEN pv = source_id THEN target_id ELSE source_id END AS other, created_at FROM c0
    WHERE edge_type IN ('relates_to', 'part_of')
  ) x WHERE other NOT IN (SELECT id FROM l0) AND other NOT IN ({hub_list}) GROUP BY other ORDER BY max(created_at) DESC LIMIT {frontier_cap}
)"""
        mention_pivots = "SELECT id FROM l0 UNION SELECT id FROM l1 UNION SELECT id FROM l2"
        statement = f"""WITH
l0 AS (SELECT CAST(id AS UUID) AS id FROM (VALUES {seed_values}) AS v(id)),
{first},
{first_l1},
{layer("c1", "l1", str(claim_cap), True)},
{next_frontier("l2", "c1", "SELECT id FROM l0 UNION SELECT id FROM l1")},
{layer("c2", "l2", str(claim_cap), True)},
mentioned AS (
  SELECT source_id, target_id FROM (
    SELECT e.source_id, e.target_id, row_number() OVER (PARTITION BY e.target_id ORDER BY e.created_at DESC) AS rn
    FROM ({mention_pivots}) p JOIN edges e ON e.target_id = p.id
    WHERE e.user_id = {user} AND e.edge_type = 'mentions' AND {self._json('shape').replace('metadata', 'e.metadata')} = 'both'
      AND p.id NOT IN ({hub_list})
  ) m WHERE rn <= {mention_cap}
),
claims AS (
  SELECT id, source_id, target_id, edge_type, metadata FROM c0
  UNION SELECT id, source_id, target_id, edge_type, metadata FROM c1
  UNION SELECT id, source_id, target_id, edge_type, metadata FROM c2
)
SELECT 'c' AS kind, CAST(id AS VARCHAR) AS a, CAST(source_id AS VARCHAR) AS b, CAST(target_id AS VARCHAR) AS c,
  {self._json('relation')} AS relation, {self._json('claim')} AS claim, {self._json('polarity')} AS polarity,
  {self._json('status')} AS status, {self._json('role')} AS role, {self._json('time')} AS time,
  {self._json('inferred')} AS inferred, {self._json('attr')} AS attr, {self._json('value')} AS value,
  CAST({self._evidence()} AS VARCHAR) AS evidence, {self._json('shape')} AS shape
FROM claims
UNION ALL
SELECT 'm', NULL, CAST(source_id AS VARCHAR), CAST(target_id AS VARCHAR), NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM mentioned"""
        return statement

    async def _execute(self, statement: str) -> list:
        if self._backend == "postgres":
            async with self._store._pool.acquire() as connection:
                return [tuple(row) for row in await connection.fetch(statement)]
        async with self._store._conn_lock:
            return await run_to_completion(lambda: self._store._conn.execute(statement).fetchall())

    async def fetch(self, seeds: list[str], cued: frozenset | None) -> RowsSource:
        statement = self.sql(seeds, cued)
        self.last_sql = statement
        names = self._handles.names
        claims: dict[str, Edge] = {}
        mentions: dict[str, set[str]] = {}
        rows = await self._execute(statement)
        for kind, _id, source, target, relation, claim, polarity, status, role, time, inferred, attr, value, evidence, shape in rows:
            if kind == "m":
                if source in names and target in names:
                    mentions.setdefault(names[target][1], set()).add(names[source][1])
                continue
            literal = relation == "has"
            claims[claim] = Edge(
                claim, names[source][1], relation, None if literal else names[target][1], attr or "", value or "",
                tuple(names[node_id][1] for node_id in json.loads(evidence or "[]")), polarity, status, role or "", time or "",
                str(inferred).lower() == "true", DIRECT if shape == DIRECT else "both")
        return RowsSource(list(claims.values()), mentions, len(rows))

    async def scores(self, question: str) -> GraphResult:
        """The same result ``generic_scores`` gives over the store, from one statement."""
        seeds, _, cued = walk_plan(question, self._gold, self.params)
        if not seeds:
            return GraphResult(seeds=seeds)
        source = await self.fetch(seeds, cued)
        return await generic_scores(question, self._gold, source, self.params)
