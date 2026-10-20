"""Retrieval strategies over a gold graph: the oracle, a generic traversal, and rank fusion with the text list.

``oracle`` follows a question's gold query spec exactly, so it is the upper
bound of the graph's shape: if it does not rank the answers first, the shape
cannot answer the question. ``generic`` is a candidate for a real feature: it
finds seeds in the question text, walks claims to a hop limit with hop decay
and a weight per relation, and scores the memories that name or evidence what
it reached. ``fuse`` and ``graph_first`` combine either list with the ranking
``retrieve()`` already gave. Everything here is deterministic and calls no model.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math

from benchmarks.graph_oracle.gold import Gold, mentions, relation_cues, stems, target_cues
from benchmarks.graph_oracle.graph import CLAIM, DIRECT, Edge, GraphData

RRF_K = 60
HUBS = ("none", "owner_seed", "owner_block", "degree")


@dataclass(frozen=True)
class Params:
    """The generic traversal's settings. Defaults are the best row of the ablations (BENCHMARKS.md)."""

    hops: int = 2
    decay: float = 0.6
    hub: str = "owner_block"         # none | owner_seed | owner_block | degree
    hub_factor: float = 0.3
    owner_mentions: bool = False     # whether the owner's own score counts when a memory is scored
    mention: float = 1.0             # weight of the entities a memory names
    evidence: float = 1.0            # weight of the claims a memory evidences
    shape: str = DIRECT              # direct | claim (a claim node costs two hops)
    relations: frozenset | None = None   # None allows every relation
    inferred: bool = True
    target_boost: float = 2.0        # reached entities of the type or subtype the question names
    cue_weight: float = 0.5          # per relation cue or role word, up to two
    target_cues: bool = True
    relation_cues: bool = True
    cue_filter: bool = True          # with a relation cue, walk and score only the cued relations and containment
    superseded: float = 0.0          # factor on a claim a later claim replaced
    past: float = 0.7
    negative: float = 0.0            # factor on a claim with negative polarity
    weights: tuple = ()              # (relation, weight) pairs; others 1.0

    def with_(self, **changes) -> "Params":
        return replace(self, **changes)


@dataclass
class GraphResult:
    scores: dict[str, float] = field(default_factory=dict)     # memory id -> graph score
    entities: dict[str, float] = field(default_factory=dict)   # entity id -> score
    seeds: list[str] = field(default_factory=list)
    calls: int = 0
    edges_read: int = 0


def _edge_factor(edge: Edge, params: Params, cued: frozenset | None = None) -> float:
    if params.relations is not None and edge.r not in params.relations and not edge.literal:
        return 0.0
    if cued is not None and edge.r not in cued and edge.r not in ("located_in", "part_of", "has"):
        return 0.0
    if edge.inferred and not params.inferred:
        return 0.0
    factor = dict(params.weights).get(edge.r, 1.0)
    factor *= {"superseded": params.superseded, "past": params.past}.get(edge.status, 1.0)
    if edge.polarity == "negative":
        factor *= params.negative
    return factor


async def generic_scores(question: str, gold: Gold, source, params: Params) -> GraphResult:
    """Score the memories of a graph for one question, with no gold path."""
    seeds = mentions(gold, question)
    result = GraphResult(seeds=seeds)
    if not seeds:
        return result
    owner = gold.owner_entity
    cues = relation_cues(question) if params.relation_cues or params.cue_filter else set()
    cued = frozenset(cues) if params.cue_filter and cues else None
    damped = params.hub in ("owner_seed", "owner_block")
    best = {seed: {seed: params.hub_factor if seed == owner and damped else 1.0} for seed in seeds}
    cost = 2 if params.shape == CLAIM else 1
    steps = params.hops // cost
    claims_on: dict[str, dict[str, Edge]] = {}
    named: dict[str, set[str]] = {}
    frontier = {seed: {seed} for seed in seeds}
    for step in range(steps + 1):
        need = sorted(set().union(*frontier.values()) - claims_on.keys())
        if need:
            found, mentioned = await source.touching(need)
            for entity_id in need:
                claims_on[entity_id] = {}
            for edge in found:
                for end in {edge.s, edge.o} - {None}:
                    if end in claims_on:
                        claims_on[end][edge.id] = edge
            named.update(mentioned)
        if step == steps:
            break
        following: dict[str, set[str]] = {seed: set() for seed in seeds}
        for seed in seeds:
            for entity_id in frontier[seed]:
                if params.hub == "owner_block" and entity_id == owner and entity_id != seed:
                    continue
                damp = 1 / math.log2(2 + len(claims_on[entity_id])) if params.hub == "degree" else 1.0
                for edge in claims_on[entity_id].values():
                    factor = 0.0 if edge.literal else _edge_factor(edge, params, cued)
                    other = edge.o if edge.s == entity_id else edge.s
                    score = best[seed][entity_id] * factor * damp * params.decay ** cost
                    if score > best[seed].get(other, 0.0) + 1e-12:
                        best[seed][other] = score
                        following[seed].add(other)
        frontier = following
    entities: dict[str, float] = {}
    for per_seed in best.values():
        for entity_id, score in per_seed.items():
            entities[entity_id] = entities.get(entity_id, 0.0) + score
    result.entities = entities
    wanted = target_cues(gold, question) if params.target_cues else {"subtype": set(), "type": set()}
    boosted = {entity_id: score * (params.target_boost if (
        gold.entities[entity_id].subtype in wanted["subtype"] or gold.entities[entity_id].type in wanted["type"])
        else 1.0) for entity_id, score in entities.items()}
    asked = stems(question)

    def counts(entity_id: str | None) -> float:
        """An entity's score for scoring memories: the owner touches nearly everything, so it counts only on request."""
        return 0.0 if entity_id is None or (entity_id == owner and not params.owner_mentions) else boosted.get(entity_id, 0.0)

    scores: dict[str, float] = {}
    if params.mention:
        for entity_id, score in boosted.items():
            for memory_id in named.get(entity_id, ()):
                scores[memory_id] = scores.get(memory_id, 0.0) + params.mention * counts(entity_id)
    if params.evidence:
        counted: set[str] = set()
        for edges in claims_on.values():
            for edge in edges.values():
                if edge.id in counted:
                    continue
                counted.add(edge.id)
                factor = _edge_factor(edge, params, cued)
                if factor <= 0 or not edge.evidence:
                    continue
                reach = counts(edge.s) + counts(edge.o)
                if reach <= 0:
                    continue
                overlap = len((stems(edge.role) | stems(edge.attr)) & asked) + (edge.r in cues and params.relation_cues)
                bonus = 1 + params.cue_weight * min(overlap, 2) if params.relation_cues else 1
                for memory_id in edge.evidence:
                    scores[memory_id] = scores.get(memory_id, 0.0) + params.evidence * reach * factor * bonus
    result.scores = scores
    result.calls, result.edges_read = source.calls, source.edges_read
    return result


# --- The oracle -----------------------------------------------------------------------------------


def _step_ok(edge: Edge, step: dict) -> bool:
    if edge.polarity != step.get("polarity", "positive"):
        return False
    allowed = step.get("status")
    if allowed is None:
        if edge.status == "superseded":
            return False
    elif edge.status not in allowed:
        return False
    return (step.get("role", "").casefold() in edge.role.casefold()
            and step.get("time", "").casefold() in edge.time.casefold())


def _target_ok(gold: Gold, target: dict, entity_id: str) -> bool:
    entity = gold.entities[entity_id]
    return ((target.get("id") is None or target["id"] == entity_id)
            and (target.get("subtype") is None or target["subtype"] == entity.subtype)
            and (target.get("type") is None or target["type"] == entity.type))


@dataclass
class OracleResult:
    answered: list[str]                       # the memories the spec selects, best first
    ranked: list[str]                         # all memories: the selected ones, then the rest
    targets: list[str] = field(default_factory=list)
    claims: int = 0


def run_oracle(gold: Gold, data: GraphData, spec: dict, base: list[str]) -> OracleResult:
    """Follow the spec's seeds, relation path and target exactly; rank the memories it reaches ahead of the rest."""
    steps = spec["path"]
    target = spec.get("target", {})
    last_relation = max((index for index, step in enumerate(steps) if "r" in step), default=None)
    frontier = list(spec["seeds"])
    last: list[Edge] = []
    for index, step in enumerate(steps):
        found: dict[str, Edge] = {}
        reached: set[str] = set()
        for entity_id in frontier:
            for edge in data.touching.get(entity_id, ()):
                if edge.shape not in (DIRECT, "both"):
                    continue
                if "attr" in step:
                    wanted = stems(step["attr"])
                    if edge.literal and edge.s == entity_id and wanted <= stems(edge.attr) and _step_ok(edge, step):
                        found[edge.id] = edge
                    continue
                if edge.literal or edge.r != step["r"] or not _step_ok(edge, step):
                    continue
                direction = step.get("dir", "out")
                if (direction == "out" and edge.s != entity_id) or (direction == "in" and edge.o != entity_id):
                    continue
                other = edge.o if edge.s == entity_id else edge.s
                if index == last_relation and not _target_ok(gold, target, other):
                    continue
                found[edge.id] = edge
                reached.add(other)
        if "r" in step:
            frontier = sorted(reached)
        last = list(found.values())
    counts: dict[str, int] = {}
    for edge in last:
        for memory_id in edge.evidence:
            counts[memory_id] = counts.get(memory_id, 0) + 1
    named = {memory_id: len(entities & set(frontier)) for memory_id, entities in data.mentions.items()}
    position = {memory_id: place for place, memory_id in enumerate(base)}
    everyone = sorted({*base, *counts, *named}, key=lambda memory_id: (
        -counts.get(memory_id, 0), -named.get(memory_id, 0) if last_relation is not None else 0,
        position.get(memory_id, len(base)), memory_id))
    return OracleResult([memory_id for memory_id in everyone if counts.get(memory_id)], everyone, frontier, len(last))


# --- Combining a graph list with the text list ---------------------------------------------------


def graph_order(scores: dict[str, float], base: list[str]) -> list[str]:
    """Memories with a graph score, best first; equal scores keep the text order."""
    position = {memory_id: place for place, memory_id in enumerate(base)}
    return sorted((memory_id for memory_id, score in scores.items() if score > 0),
                  key=lambda memory_id: (-scores[memory_id], position.get(memory_id, len(base)), memory_id))


def graph_first(scores: dict[str, float], base: list[str]) -> list[str]:
    """The graph's memories first, then every other memory in text order."""
    first = graph_order(scores, base)
    return first + [memory_id for memory_id in base if memory_id not in set(first)]


def fuse(scores: dict[str, float], base: list[str], weight: float) -> list[str]:
    """Reciprocal rank fusion of the text list and the graph list, the graph list weighted."""
    graph = {memory_id: place for place, memory_id in enumerate(graph_order(scores, base), start=1)}
    text = {memory_id: place for place, memory_id in enumerate(base, start=1)}
    fused = {memory_id: (1 / (RRF_K + text[memory_id]) if memory_id in text else 0.0)
             + (weight / (RRF_K + graph[memory_id]) if memory_id in graph else 0.0)
             for memory_id in {*text, *graph}}
    return sorted(fused, key=lambda memory_id: (-fused[memory_id], text.get(memory_id, len(text) + 1), memory_id))
