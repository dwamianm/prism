"""Latency of the typed walk on synthetic packs of 2,000 and 10,000 memories, in DuckDB and PostgreSQL.

A pack is built straight in the graph store (no embedding, no model): memory
nodes, entity nodes with a zipf shaped popularity, ``mentions`` edges from each
memory to the one to three entities it names, and typed claim edges between
entities with evidence memories. The owner entity is named by 30 percent of the
memories and touches a quarter of the claims, as the owner of a real pack does.
The walk is the generic strategy over a ``StoreSource``, so each hop is one
``get_edges`` call over the frontier, as in the oracle report. Questions name one
to three entities, half of them the owner.

Three read modes are timed on the same questions: ``naive`` (every edge touching the
frontier, as the oracle report reads), ``owner_aware`` (the owner's ``mentions`` edges
are skipped, since a walk never scores them) and ``claims_only`` (claim edges only, by
edge type, and memories score by evidence alone).

    uv run python -m benchmarks.graph_oracle latency --backend duckdb --turns 2000 10000
    uv run python -m benchmarks.graph_oracle latency --backend postgres --database-url <url> --turns 2000 10000
"""
from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import time
import uuid
from uuid import UUID

from pydantic import SecretStr

from benchmarks.diagnostics.product_packing import gate_config
from benchmarks.graph_oracle.gold import RELATIONS, Entity, Gold, _alias_patterns
from benchmarks.graph_oracle.graph import DIRECT, Handles, StoreSource, _decode_edge
from benchmarks.graph_oracle.run import percentiles
from benchmarks.graph_oracle.sqlwalk import SqlWalk
from benchmarks.graph_oracle.strategies import Params, generic_scores
from prme import MemoryEngine
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, NodeType, Scope

KIND = "prme-graph-oracle-latency"
QUESTIONS = 100
RELATION_NAMES = [name for name in RELATIONS if name != "has"]
KINDS = [("person", "person"), ("place", "city"), ("org", "company"), ("thing", "tool"), ("event", "project")]
BUDGET_MS = 50.0  # proposed: the walk's reads at the 95th percentile at 10,000 memories (BLIND.md)


class OwnerAwareSource(StoreSource):
    """A StoreSource that never reads the owner's ``mentions`` edges: the owner's claims are read by edge type."""

    def __init__(self, engine, owner: str, handles: Handles, owner_entity: str):
        super().__init__(engine, owner, handles)
        self._hub = handles.entity[owner_entity]

    async def touching(self, entity_ids: list[str]):
        hub = self._handles.names[self._hub][1]
        others = [entity_id for entity_id in entity_ids if entity_id != hub]
        claims, named = await super().touching(others) if others else ([], {})
        if hub in entity_ids:
            seen = {claim.id for claim in claims}
            for edge_type in (EdgeType.RELATES_TO, EdgeType.PART_OF, EdgeType.HAS_FACT):
                edges = await self._store.get_edges(node_ids=[self._hub], edge_type=edge_type)
                self.calls += 1
                self.edges_read += len(edges)
                for edge in edges:
                    meta = edge.metadata or {}
                    if edge.user_id == self._owner and meta.get("shape") in (DIRECT, "both") and meta.get("claim") not in seen:
                        claims.append(_decode_edge(edge, self._handles, DIRECT if meta["shape"] == DIRECT else "both"))
        return claims, named


class ClaimsOnlySource(StoreSource):
    """Reads only the claim edges, one call per edge type, and never a ``mentions`` edge: memories score by evidence."""

    async def touching(self, entity_ids: list[str]):
        nodes = [self._handles.entity[entity_id] for entity_id in entity_ids]
        claims: dict[str, object] = {}
        for edge_type in (EdgeType.RELATES_TO, EdgeType.PART_OF, EdgeType.HAS_FACT):
            edges = await self._store.get_edges(node_ids=nodes, edge_type=edge_type)
            self.calls += 1
            self.edges_read += len(edges)
            for edge in edges:
                meta = edge.metadata or {}
                if edge.user_id == self._owner and meta.get("shape") in (DIRECT, "both"):
                    claim = _decode_edge(edge, self._handles, DIRECT if meta["shape"] == DIRECT else "both")
                    claims[claim.id] = claim
        return list(claims.values()), {}


def _zipf_pick(rng: random.Random, count: int) -> int:
    """An entity index, low indexes much more often than high ones."""
    return min(count - 1, int(count ** rng.random()) - 1)


async def build_pack(engine, owner: str, turns: int, rng: random.Random) -> tuple[Gold, Handles, dict]:
    store = engine._graph_store
    entity_count = max(50, turns // 8)
    handles = Handles()
    entities: dict[str, Entity] = {}

    async def node(node_type: NodeType, content: str, metadata: dict) -> str:
        return await store.create_node(MemoryNode(user_id=owner, node_type=node_type, content=content, scope=Scope.PERSONAL,
                                                  metadata=metadata, confidence=0.9))

    async def link(source: str, target: str, edge_type: EdgeType, meta: dict) -> None:
        await store.create_edge(MemoryEdge(source_id=UUID(source), target_id=UUID(target), edge_type=edge_type, user_id=owner,
                                           confidence=0.9, metadata=meta))

    for number in range(entity_count):
        kind, subtype = KINDS[number % len(KINDS)]
        entity_id = "owner" if number == 0 else f"e{number}"
        name = "Ownerx" if number == 0 else f"Entity{number}x"
        entities[entity_id] = Entity(entity_id, kind, subtype, (name,))
        handles.add("entity", entity_id, await node(NodeType.ENTITY, name, {"gold_id": entity_id}))
    ids = list(entities)
    mentioned_by: dict[str, list[str]] = {entity_id: [] for entity_id in ids}
    for number in range(turns):
        memory_id = f"t{number}"
        handles.add("memory", memory_id, await node(NodeType.NOTE, f"synthetic memory {number}", {}))
        chosen = {ids[_zipf_pick(rng, entity_count)] for _ in range(rng.randint(1, 3))}
        if rng.random() < 0.3:
            chosen.add("owner")
        for entity_id in chosen:
            mentioned_by[entity_id].append(memory_id)
            await link(handles.memory[memory_id], handles.entity[entity_id], EdgeType.MENTIONS, {"shape": "both"})
    claims = 0
    for number in range(turns // 3):
        subject = "owner" if rng.random() < 0.25 else ids[_zipf_pick(rng, entity_count)]
        target = ids[_zipf_pick(rng, entity_count)]
        if subject == target:
            continue
        relation = rng.choice(RELATION_NAMES)
        pool = mentioned_by[subject] or mentioned_by[target]
        evidence = [handles.memory[memory_id] for memory_id in rng.sample(pool, min(len(pool), rng.randint(1, 2)))] if pool else []
        meta = {"shape": DIRECT, "relation": relation, "claim": f"c{number}", "polarity": "positive", "status": "current",
                "role": "", "time": "", "inferred": False, "evidence": evidence}
        edge_type = EdgeType.PART_OF if relation in ("located_in", "part_of") else EdgeType.RELATES_TO
        await link(handles.entity[subject], handles.entity[target], edge_type, meta)
        claims += 1
    gold = Gold("synthetic", Path("."), owner, "owner", entities, [], [], [])
    gold.patterns = _alias_patterns(entities)
    return gold, handles, {"entities": entity_count, "claims": claims,
                           "owner_mentions": len(mentioned_by["owner"])}


async def measure(engine, owner: str, gold: Gold, handles: Handles, rng: random.Random, mode: str, backend: str = "duckdb") -> dict:
    params = Params(hops=2, mention=0.0) if mode == "claims_only" else Params(hops=2)
    names = [entity.names[0] for entity in gold.entities.values() if entity.id != "owner"]
    timings: dict[str, list[float]] = {"owner_is_a_seed": [], "owner_is_not_a_seed": []}
    reads: list[int] = []
    edges: list[int] = []
    walk = SqlWalk(engine, backend, owner, handles, gold, params) if mode == "sql" else None
    for number in range(QUESTIONS):
        picks = [names[_zipf_pick(rng, len(names))] for _ in range(rng.randint(1, 3))]
        with_owner = number % 2 == 0
        text = "what links " + " and ".join(([("Ownerx")] if with_owner else []) + picks) + "?"
        began = time.perf_counter()
        if walk is not None:
            result = await walk.scores(text)
            calls, read = result.calls, result.edges_read
        else:
            source = {"naive": lambda: StoreSource(engine, owner, handles),
                      "owner_aware": lambda: OwnerAwareSource(engine, owner, handles, "owner"),
                      "claims_only": lambda: ClaimsOnlySource(engine, owner, handles)}[mode]()
            await generic_scores(text, gold, source, params)
            calls, read = source.calls, source.edges_read
        timings["owner_is_a_seed" if with_owner else "owner_is_not_a_seed"].append((time.perf_counter() - began) * 1000)
        reads.append(calls)
        edges.append(read)
    everything = timings["owner_is_a_seed"] + timings["owner_is_not_a_seed"]
    return {"all": percentiles(everything), "owner_is_a_seed": percentiles(timings["owner_is_a_seed"]),
            "owner_is_not_a_seed": percentiles(timings["owner_is_not_a_seed"]),
            "store_reads": percentiles([float(count) for count in reads]),
            "edges_read": percentiles([float(count) for count in edges])}


async def run(out: Path, *, backend: str, turns: list[int], database_url: str | None = None, seed: int = 20261020) -> dict:
    results = []
    for count in turns:
        rng = random.Random(seed + count)
        with tempfile.TemporaryDirectory(prefix="graph-oracle-latency-") as work:
            pack = Path(work) / "pack"
            (pack / "lexical_index").mkdir(parents=True)
            config = gate_config(pack)
            if backend == "postgres":
                config = config.model_copy(update={"database_url": SecretStr(database_url)})
            owner = f"latency-{count}-{uuid.uuid4().hex[:8]}" if backend == "postgres" else "latency"
            async with MemoryEngine.open(config) as engine:
                began = time.perf_counter()
                gold, handles, shape = await build_pack(engine, owner, count, rng)
                built = time.perf_counter() - began
                if backend == "postgres":
                    # A deployment's autovacuum has analyzed a table this size; a fresh synthetic one has not.
                    async with engine._graph_store._pool.acquire() as connection:
                        await connection.execute("ANALYZE edges")
                        await connection.execute("ANALYZE nodes")
                modes = {f"{mode}_reads": await measure(engine, owner, gold, handles, random.Random(seed), mode, backend)
                         for mode in ("sql", "naive", "owner_aware", "claims_only")}  # the SQL walk first, before the others fill memory
        results.append({"turns": count, "built_seconds": round(built, 1), **shape, **modes})
    report = {"kind": KIND, "backend": backend, "budget_ms_p95_at_10000": BUDGET_MS, "questions": QUESTIONS,
              "results": results}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"latency-{backend}.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def markdown(reports: list[dict]) -> str:
    lines = ["| Backend | Memories | Reads | Owner a seed | Median ms | p95 ms | Edges read (median) | Store reads |",
             "|---|---:|---|---|---:|---:|---:|---:|"]
    for report in reports:
        for result in report["results"]:
            for reads in ("sql_reads", "naive_reads", "owner_aware_reads", "claims_only_reads"):
                for label in ("owner_is_not_a_seed", "owner_is_a_seed", "all"):
                    row = result[reads]
                    lines.append(f"| {report['backend']} | {result['turns']} | {reads.removesuffix('_reads').replace('_', '-')} | "
                                 f"{label.replace('owner_is_', '').replace('_', ' ')} | {row[label]['p50']} | {row[label]['p95']} | "
                                 f"{row['edges_read']['p50']:.0f} | {row['store_reads']['p50']:.0f} |")
    return "\n".join(lines) + "\n"
