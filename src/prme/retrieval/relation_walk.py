"""The typed walk of a routed question (``enable_relation_routing``, dwamianm/prism#399).

A route (:func:`prme.retrieval.relations.route_question`) names the relations a
question asks for. The walk starts at the question's seed entities, reads the
claims on them in one SQL statement and keeps those whose relation, read from
the claim's predicate by :func:`prme.retrieval.relations.normalize_predicate`
(or from a typed ``relation`` on its ``HAS_FACT`` link), is one the route
accepts. A claim is a fact node: ``HAS_FACT`` from its subject entity and
``MENTIONS`` to its object entity, so a seed can be either end. The statement:

1. the claims on the seeds, newest first, at most ``SEED_CAP`` per seed;
2. for a two-step route, the entities those claims reach (at most
   ``FRONTIER_CAP``, newest first) and their claims, at most ``CLAIM_CAP`` each;
3. the links of every claim read: its subjects, objects and the memories it is
   derived from.

Superseded and archived claims are never read, and a claim with negative
polarity is kept only for a step that asks for one (the graph oracle's rules).
The statement is the same on DuckDB and PostgreSQL; its only input is the
owner's id, bound as ``$1``, and node ids that were parsed as UUIDs.

The walk's candidates are the claims of the route's last step, the claims of
its first step when it has two, and the memories a claim is derived from by a
``DERIVED_FROM`` link (typed graphs write one; ``ingest()`` cites a claim's
source in ``evidence_refs`` instead, so its candidates are claims). Each
gets a route score: its step, then how many of the entities the last step
reached its text names, so a memory that names every answer ("My husband is
Theo and my child is Nico") comes before one that names one.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from prme.retrieval.relations import (
    RELATIONS,
    UNROUTED_RELATIONS,
    ClaimRelation,
    RelationStep,
    Route,
    normalize_predicate,
)
from prme.types import LifecycleState, NodeType, Scope

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore

# The graph oracle's caps (benchmarks/graph_oracle/GATE.md): claims read per
# seed, claims per entity of the second layer, and entities in that layer.
SEED_CAP = 128
CLAIM_CAP = 32
FRONTIER_CAP = 64
# Most candidates one walk adds to the route list.
ROUTE_MAX_CANDIDATES = 32
# Seeds read at most; the caller orders them by priority (the pipeline puts the
# owner's entities first, then the entities the question names).
MAX_SEEDS = 16

# A claim or memory in any other state (superseded, deprecated, archived) is never read.
_RETIRED = (LifecycleState.SUPERSEDED.value, LifecycleState.DEPRECATED.value, LifecycleState.ARCHIVED.value)


@dataclass
class RouteWalk:
    """What a walk found: the route score of each candidate, the nodes, and a record for the receipt."""

    # Candidate node id to its route score: (step, targets its text names).
    scores: dict[str, tuple[int, int]] = field(default_factory=dict)
    nodes: dict[str, MemoryNode] = field(default_factory=dict)
    claims: list[dict] = field(default_factory=list)
    rows: int = 0


def _uuid_list(ids: list[str]) -> str:
    # Each id is parsed, so the statement holds only canonical UUID text.
    return ", ".join(f"(CAST('{UUID(node_id)}' AS UUID))" for node_id in ids)


def _layer(name: str, frontier: str, cap: int, scope_filter: str) -> str:
    """The claims on a frontier's entities: subject by HAS_FACT, object by MENTIONS, newest first per entity."""
    retired = ", ".join(f"'{state}'" for state in _RETIRED)
    return f"""{name} AS (
  SELECT claim_id, anchor, created_at FROM (
    SELECT u.claim_id, u.anchor, u.created_at,
      row_number() OVER (PARTITION BY u.anchor ORDER BY u.created_at DESC, CAST(u.claim_id AS VARCHAR)) AS rn
    FROM (
      SELECT e.target_id AS claim_id, e.source_id AS anchor, e.created_at
      FROM {frontier} p JOIN edges e ON e.source_id = p.id
      WHERE e.user_id = $1 AND e.edge_type = 'has_fact'
      UNION ALL
      SELECT e.source_id AS claim_id, e.target_id AS anchor, e.created_at
      FROM {frontier} p JOIN edges e ON e.target_id = p.id
      WHERE e.user_id = $1 AND e.edge_type = 'mentions'
    ) u JOIN nodes n ON n.id = u.claim_id
    WHERE n.user_id = $1 AND n.node_type = 'fact' AND n.lifecycle_state NOT IN ({retired}){scope_filter}
  ) w WHERE rn <= {cap}
)"""


def walk_statement(seed_ids: list[str], *, steps: int, scopes: list[Scope] | None = None) -> str:
    """The walk's one SQL statement for DuckDB and PostgreSQL; ``$1`` is the owner's id.

    Rows are ``(kind, layer, claim, other, edge_type, metadata, node_type,
    content, lifecycle)``, every value text or NULL: ``c`` a claim read on
    ``other`` in ``layer`` (0 or 1), ``n`` a claim's own metadata and
    lifecycle state, ``e`` a link of a claim (``has_fact`` from a subject,
    ``mentions`` to an object, ``derived_from`` to a memory) with the linked
    node's type and text.
    """
    if not seed_ids:
        raise ValueError("A walk needs a seed")
    if steps not in (1, 2):
        raise ValueError("A route has one or two steps")
    scope_filter = (
        " AND n.scope IN (" + ", ".join(f"'{Scope(scope).value}'" for scope in scopes) + ")" if scopes else ""
    )
    parts = [f"l0 AS (SELECT id FROM (VALUES {_uuid_list(seed_ids)}) AS v(id))",
             _layer("c0", "l0", SEED_CAP, scope_filter)]
    claims = "SELECT claim_id, '0' AS layer, anchor FROM c0"
    if steps == 2:
        parts.append(f"""l1 AS (
  SELECT id FROM (
    SELECT x.id, max(x.created_at) AS newest FROM (
      SELECT e.source_id AS id, c.created_at FROM c0 c JOIN edges e ON e.target_id = c.claim_id
      WHERE e.user_id = $1 AND e.edge_type = 'has_fact'
      UNION ALL
      SELECT e.target_id AS id, c.created_at FROM c0 c JOIN edges e ON e.source_id = c.claim_id
      WHERE e.user_id = $1 AND e.edge_type = 'mentions'
    ) x WHERE x.id NOT IN (SELECT id FROM l0) GROUP BY x.id
  ) y ORDER BY newest DESC, CAST(id AS VARCHAR) LIMIT {FRONTIER_CAP}
)""")
        parts.append(_layer("c1", "l1", CLAIM_CAP, scope_filter))
        claims += " UNION ALL SELECT claim_id, '1' AS layer, anchor FROM c1"
    parts.append(f"claims AS ({claims})")
    parts.append("read AS (SELECT DISTINCT claim_id FROM claims)")
    with_clause = ",\n".join(parts)
    return f"""WITH
{with_clause}
SELECT 'c', layer, CAST(claim_id AS VARCHAR), CAST(anchor AS VARCHAR), NULL, NULL, NULL, NULL, NULL FROM claims
UNION ALL
SELECT 'n', NULL, CAST(n.id AS VARCHAR), NULL, NULL, CAST(n.metadata AS VARCHAR), NULL, NULL, n.lifecycle_state
FROM read r JOIN nodes n ON n.id = r.claim_id
UNION ALL
SELECT 'e', NULL, CAST(e.target_id AS VARCHAR), CAST(e.source_id AS VARCHAR), e.edge_type, CAST(e.metadata AS VARCHAR),
  o.node_type, o.content, o.lifecycle_state
FROM read r JOIN edges e ON e.target_id = r.claim_id JOIN nodes o ON o.id = e.source_id
WHERE e.user_id = $1 AND e.edge_type = 'has_fact'
UNION ALL
SELECT 'e', NULL, CAST(e.source_id AS VARCHAR), CAST(e.target_id AS VARCHAR), e.edge_type, CAST(e.metadata AS VARCHAR),
  o.node_type, o.content, o.lifecycle_state
FROM read r JOIN edges e ON e.source_id = r.claim_id JOIN nodes o ON o.id = e.target_id
WHERE e.user_id = $1 AND e.edge_type IN ('mentions', 'derived_from')"""


def owner_statement(*, json_key: str, backend: str, scopes: list[Scope] | None = None) -> str:
    """Entities of the owner (``$1``) whose metadata has ``json_key``: rows ``(id, value, content)``."""
    if not re.fullmatch(r"[a-z_]+", json_key):
        raise ValueError("A metadata key is lower case letters and underscores")
    value = (f"metadata->>'{json_key}'" if backend == "postgres"
             else f"json_extract_string(metadata, '$.{json_key}')")
    scope_filter = (
        " AND scope IN (" + ", ".join(f"'{Scope(scope).value}'" for scope in scopes) + ")" if scopes else ""
    )
    retired = ", ".join(f"'{state}'" for state in _RETIRED)
    return (f"SELECT CAST(id AS VARCHAR), {value}, content FROM nodes WHERE user_id = $1 AND node_type = 'entity' "
            f"AND lifecycle_state NOT IN ({retired}) AND {value} IS NOT NULL{scope_filter} "
            "ORDER BY CAST(id AS VARCHAR)")


def _backend(graph_store: Any) -> str:
    if getattr(graph_store, "_pool", None) is not None:
        return "postgres"
    if getattr(graph_store, "_conn", None) is not None:
        return "duckdb"
    raise TypeError("The relation walk needs a DuckDB or PostgreSQL graph store")


async def _fetch(graph_store: Any, statement: str, user_id: str) -> list[tuple]:
    if _backend(graph_store) == "postgres":
        async with graph_store._pool.acquire() as connection:
            return [tuple(row) for row in await connection.fetch(statement, user_id)]
    from prme.storage._threading import run_to_completion

    async with graph_store._conn_lock:
        return await run_to_completion(lambda: graph_store._conn.execute(statement, [user_id]).fetchall())


async def owner_entities(
    graph_store: GraphStore, *, user_id: str, scopes: list[Scope] | None, speaker: str | None = None,
) -> list[str]:
    """The owner's entities for a first-person question: the owner reference, and the named owner.

    The named owner is the caller's ``speaker``: the entities with that name,
    ignoring case. Without one, it is the entity a user turn marked with the
    owner's name (``enable_owner_name_carry``), only when user turns gave the
    owner one name across the request's scopes: several names mean several
    people write under this owner, and the asker need not be any one of them.
    ``enable_owner_profile_context`` (#398) applies that rule scope by scope
    instead, so a request over several scopes with a different name in each
    names an owner there and none here.
    """
    from prme.ingestion.owner_name import OWNER_NAME_KEY
    from prme.models.owner_reference import OWNER_REFERENCE_STATUS

    backend = _backend(graph_store)
    found: list[str] = []
    rows = await _fetch(graph_store, owner_statement(json_key="identity_status", backend=backend, scopes=scopes),
                        user_id)
    found.extend(node_id for node_id, value, _content in rows if value == OWNER_REFERENCE_STATUS)
    if speaker is not None and speaker.strip():
        wanted = speaker.strip().casefold()
        found.extend(sorted(
            str(node.id) for node in await graph_store.query_nodes(
                node_type=NodeType.ENTITY, user_id=user_id, scopes=scopes,
                content_contains_any=[speaker.strip()], limit=200,
            )
            if node.content.strip().casefold() == wanted
        ))
        return list(dict.fromkeys(found))
    rows = await _fetch(graph_store, owner_statement(json_key=OWNER_NAME_KEY, backend=backend, scopes=scopes),
                        user_id)
    named = [(node_id, (content or "").strip()) for node_id, _value, content in rows if (content or "").strip()]
    if len({content.casefold() for _node_id, content in named}) == 1:
        found.extend(node_id for node_id, _content in named)
    return list(dict.fromkeys(found))


def _metadata(text: str | None) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


@dataclass
class _Claim:
    relation: ClaimRelation | None = None
    polarity: str = "positive"
    retired: bool = False
    subjects: set[str] = field(default_factory=set)
    objects: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    pivots: dict[int, set[str]] = field(default_factory=lambda: defaultdict(set))


def _typed(edge_metadata: dict) -> ClaimRelation | None:
    """A claim typed on its HAS_FACT link (``metadata.relation`` in the vocabulary), as the graph oracle writes it."""
    relation = edge_metadata.get("relation")
    if not isinstance(relation, str) or relation not in RELATIONS or relation in UNROUTED_RELATIONS:
        return None
    role = edge_metadata.get("role")
    role = role.strip().casefold() if isinstance(role, str) and role.strip() else None
    return ClaimRelation(relation, role, "object" if role else None)


def _parse(rows: list[tuple]) -> tuple[dict[str, _Claim], dict[str, str]]:
    # Imported here: prme.ingestion imports storage, which imports retrieval.
    from prme.ingestion.mention_links import TEXT_LINK_METADATA

    claims: dict[str, _Claim] = defaultdict(_Claim)
    names: dict[str, str] = {}
    typed: dict[str, ClaimRelation] = {}
    for kind, layer, claim_id, other, edge_type, metadata, node_type, content, lifecycle in rows:
        claim = claims[claim_id]
        if kind == "c":
            claim.pivots[int(layer)].add(other)
        elif kind == "n":
            meta = _metadata(metadata)
            if claim_id not in typed:
                claim.relation = normalize_predicate(meta.get("predicate") if isinstance(meta.get("predicate"), str)
                                                     else None)
            if meta.get("polarity") == "negative":
                claim.polarity = "negative"
            claim.retired = claim.retired or lifecycle in _RETIRED
        elif edge_type == "derived_from":
            if lifecycle not in _RETIRED:
                claim.sources.add(other)
        elif node_type == NodeType.ENTITY.value:
            names[other] = (content or "").strip()
            edge_meta = _metadata(metadata)
            if edge_type == "has_fact":
                claim.subjects.add(other)
                found = _typed(edge_meta)
                if found is not None:
                    typed[claim_id] = found
                    claim.relation = found
                    if edge_meta.get("polarity") == "negative":
                        claim.polarity = "negative"
                    if edge_meta.get("status") == "superseded":
                        claim.retired = True
            elif edge_meta.get("basis") == TEXT_LINK_METADATA["basis"]:
                # A text mention (enable_mention_links) records only that the claim's text names the entity;
                # the entity is neither end of the claim.
                continue
            elif edge_meta.get("basis") == "group_member":
                # A claim about we, linked to a person it speaks for: that person is one of its subjects.
                claim.subjects.add(other)
            else:
                claim.objects.add(other)
    return claims, names


def _far(claim: _Claim, pivot: str) -> tuple[str, set[str]] | None:
    """The far end of a claim read on ``pivot``: which end it is, and its entities."""
    if pivot in claim.subjects and pivot not in claim.objects:
        return "object", claim.objects - {pivot}
    if pivot in claim.objects and pivot not in claim.subjects:
        return "subject", claim.subjects - {pivot}
    return None


def _accepts(step: RelationStep, claim: _Claim, far_end: str) -> bool:
    relation = claim.relation
    if relation is None or relation.relation != step.relation:
        return False
    polarity = "negative" if (claim.polarity == "negative") != relation.negative else "positive"
    if polarity != step.polarity:
        return False
    if step.roles:
        return relation.role in step.roles and relation.role_end == far_end
    return True


def _named(text: str, names: list[str]) -> int:
    folded = text.casefold()
    return sum(1 for name in names if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", folded))


async def walk_route(
    graph_store: GraphStore,
    route: Route,
    *,
    user_id: str,
    seed_ids: list[str],
    scopes: list[Scope] | None = None,
) -> RouteWalk:
    """Walk ``route`` from ``seed_ids`` and score the claims and memories it reaches."""
    result = RouteWalk()
    seeds = list(dict.fromkeys(seed_ids))[:MAX_SEEDS]
    if not seeds or not route:
        return result
    rows = await _fetch(graph_store, walk_statement(seeds, steps=len(route), scopes=scopes), user_id)
    result.rows = len(rows)
    claims, names = _parse(rows)
    levels: dict[str, int] = {}
    matched: dict[str, tuple[int, str, str]] = {}
    frontier = set(seeds)
    reached: set[str] = set()
    for index, steps in enumerate(route):
        reached = set()
        for claim_id, claim in sorted(claims.items()):
            if claim.retired:
                continue
            for pivot in sorted(claim.pivots.get(index, set()) & frontier):
                far = _far(claim, pivot)
                if far is None:
                    continue
                accepted = next((step for step in steps if _accepts(step, claim, far[0])), None)
                if accepted is None:
                    continue
                reached |= far[1]
                # 2 for a claim of the last step, which answers the question; 1 for one on the way.
                level = 2 if index == len(route) - 1 else 1
                if level > levels.get(claim_id, 0):
                    levels[claim_id] = level
                    matched[claim_id] = (index, accepted.relation, pivot)
        frontier = reached
        if not frontier:
            break
    if 2 not in levels.values():
        # The last step matched nothing, so nothing answers the question.
        return result
    targets = sorted({names[entity_id].casefold() for entity_id in reached
                      if len(names.get(entity_id, "")) >= 2})
    wanted: dict[str, int] = {}
    for claim_id, level in levels.items():
        wanted[claim_id] = max(wanted.get(claim_id, 0), level)
        for source in claims[claim_id].sources:
            wanted[source] = max(wanted.get(source, 0), level)
    nodes = await graph_store.get_nodes(sorted(wanted))
    admitted = {
        str(node.id): node for node in nodes
        if node.user_id == user_id and node.lifecycle_state.value not in _RETIRED
        and (not scopes or node.scope in scopes) and node.node_type != NodeType.ENTITY
    }
    scored = sorted(
        ((node_id, (wanted[node_id], _named(node.content or "", targets))) for node_id, node in admitted.items()),
        key=lambda item: (-item[1][0], -item[1][1], item[0]),
    )[:ROUTE_MAX_CANDIDATES]  # past the cap, equal route scores are cut by node id
    result.scores = dict(scored)
    result.nodes = {node_id: admitted[node_id] for node_id, _score in scored}
    result.claims = [
        {"node_id": claim_id, "step": matched[claim_id][0], "relation": matched[claim_id][1],
         **({"role": claims[claim_id].relation.role} if claims[claim_id].relation.role else {})}
        for claim_id in sorted(levels) if claim_id in result.scores
    ]
    return result


ROUTE_PATH = "ROUTE"


def add_route_candidates(candidates: list, walk: RouteWalk) -> int:
    """Give the candidates the walk reached their route score, and add the ones not yet in the pool.

    A candidate already in the pool also gains the ``ROUTE`` path, as session
    context does; a new one has that path alone. Returns how many were added.
    """
    from prme.retrieval.models import RetrievalCandidate

    by_id = {str(candidate.node.id): index for index, candidate in enumerate(candidates)}
    added = 0
    for node_id, score in walk.scores.items():
        index = by_id.get(node_id)
        if index is None:
            candidates.append(RetrievalCandidate(node=walk.nodes[node_id], paths=[ROUTE_PATH], path_count=1,
                                                 route_score=score))
            added += 1
            continue
        current = candidates[index]
        updates: dict[str, Any] = {"route_score": score}
        if ROUTE_PATH not in current.paths:
            updates["paths"] = [*current.paths, ROUTE_PATH]
            updates["path_count"] = current.path_count + 1
        candidates[index] = current.model_copy(update=updates)
    return added
