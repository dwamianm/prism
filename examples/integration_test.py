"""PRME Integration Test — comprehensive exercise of all memory system features.

Simulates a realistic multi-session scenario where a user "Alex" is working on
a software project. Stores memories across sessions, queries them with synthetic
questions, tests lifecycle transitions, graph traversal, cross-scope retrieval,
epistemic filtering, and score trace analysis.

All operations are logged in full detail to a timestamped log directory.

Run:
    python examples/integration_test.py

Logs are saved to: examples/logs/<timestamp>/
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
import warnings
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

# Suppress noisy warnings
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore")

from prme import EdgeType, LifecycleState, MemoryEngine, NodeType, PRMEConfig, Scope
from prme.models.edges import MemoryEdge


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------

class PRMEEncoder(json.JSONEncoder):
    """JSON encoder that handles PRME types."""

    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "value"):  # Enum
            return obj.value
        if hasattr(obj, "model_dump"):  # Pydantic model
            return obj.model_dump(mode="json")
        return super().default(obj)


def to_json(obj) -> str:
    return json.dumps(obj, cls=PRMEEncoder, indent=2, default=str)


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class TestLogger:
    """Logs all operations to both console and structured JSON files."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []
        self.section_logs: dict[str, list[dict]] = {}
        self._current_section: str | None = None

        # Console output
        self._console_fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(message)s",
            datefmt="%H:%M:%S",
        )
        self._console = logging.StreamHandler(sys.stdout)
        self._console.setFormatter(self._console_fmt)

        # File output — full detail
        self._file_handler = logging.FileHandler(log_dir / "full.log")
        self._file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s — %(message)s"
        ))

        # Configure root logger to capture PRME internals
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.addHandler(self._console)
        root.addHandler(self._file_handler)

        # Also configure prme logger specifically
        prme_logger = logging.getLogger("prme")
        prme_logger.setLevel(logging.DEBUG)

        self.logger = logging.getLogger("integration_test")
        self.logger.setLevel(logging.DEBUG)

    def section(self, name: str):
        self._current_section = name
        if name not in self.section_logs:
            self.section_logs[name] = []
        self.logger.info("")
        self.logger.info("=" * 70)
        self.logger.info("  %s", name)
        self.logger.info("=" * 70)

    def log_op(self, operation: str, inputs: dict, outputs: dict | None = None,
               duration_ms: float = 0, error: str | None = None):
        """Log a single operation with full input/output."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "section": self._current_section,
            "operation": operation,
            "inputs": inputs,
            "outputs": outputs,
            "duration_ms": round(duration_ms, 2),
            "error": error,
        }
        self.events.append(entry)
        if self._current_section:
            self.section_logs[self._current_section].append(entry)

        status = "OK" if error is None else f"ERROR: {error}"
        self.logger.info(
            "  %-30s  %6.1f ms  %s",
            operation, duration_ms, status,
        )

    def log_detail(self, msg: str, data: object = None):
        """Log a detail line."""
        self.logger.info("    %s", msg)
        if data is not None:
            for line in to_json(data).split("\n"):
                self.logger.debug("      %s", line)

    def save(self):
        """Persist all logs to disk."""
        # Full structured log
        with open(self.log_dir / "operations.json", "w") as f:
            json.dump(self.events, f, cls=PRMEEncoder, indent=2, default=str)

        # Per-section logs
        for section_name, entries in self.section_logs.items():
            safe_name = section_name.lower().replace(" ", "_").replace("/", "_")
            safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
            with open(self.log_dir / f"section_{safe_name}.json", "w") as f:
                json.dump(entries, f, cls=PRMEEncoder, indent=2, default=str)

        # Summary
        summary = {
            "total_operations": len(self.events),
            "sections": {k: len(v) for k, v in self.section_logs.items()},
            "errors": [e for e in self.events if e.get("error")],
            "total_duration_ms": sum(e["duration_ms"] for e in self.events),
        }
        with open(self.log_dir / "summary.json", "w") as f:
            json.dump(summary, f, cls=PRMEEncoder, indent=2, default=str)

        self.logger.info("")
        self.logger.info("Logs saved to: %s", self.log_dir)
        self.logger.info("  operations.json  — %d operations", len(self.events))
        self.logger.info("  summary.json     — test summary")
        self.logger.info("  full.log         — complete console + debug log")
        for name in self.section_logs:
            safe = name.lower().replace(" ", "_").replace("/", "_")
            safe = "".join(c for c in safe if c.isalnum() or c == "_")
            self.logger.info("  section_%s.json", safe)


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------

class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.ms = (time.perf_counter() - self.start) * 1000


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

async def test_01_store_all_node_types(engine: MemoryEngine, log: TestLogger):
    """Store memories of every node type across multiple scopes."""
    log.section("01 — Store All Node Types")

    memories = [
        # Personal scope — user facts and preferences
        ("Alex Chen is a senior backend engineer at Nexus AI.", NodeType.ENTITY, Scope.PERSONAL,
         {"entity_type": "person", "role": "user_profile"}),
        ("Alex prefers Neovim with a catppuccin theme.", NodeType.PREFERENCE, Scope.PERSONAL, None),
        ("Alex prefers Python for backend, TypeScript for frontend.", NodeType.PREFERENCE, Scope.PERSONAL, None),
        ("Alex lives in San Francisco and commutes by bike.", NodeType.FACT, Scope.PERSONAL, None),
        ("Alex is learning Rust in his spare time.", NodeType.FACT, Scope.PERSONAL, None),
        ("Alex had a 1:1 with his manager today about promotion timeline.", NodeType.EVENT, Scope.PERSONAL, None),
        ("Alex needs to finish the performance review by Friday.", NodeType.TASK, Scope.PERSONAL,
         {"priority": "high", "due": "friday"}),
        ("Read the Rust book chapter on lifetimes.", NodeType.TASK, Scope.PERSONAL,
         {"priority": "low", "category": "learning"}),

        # Project scope — team decisions and architecture
        ("The team decided to use FastAPI for the new microservice.", NodeType.DECISION, Scope.PROJECT,
         {"team": "backend", "date": "2024-01-15"}),
        ("We chose DuckDB as the analytics database for its embedded nature.", NodeType.DECISION, Scope.PROJECT, None),
        ("The API gateway uses Kong for rate limiting and auth.", NodeType.FACT, Scope.PROJECT, None),
        ("Project Orion is a real-time recommendation engine.", NodeType.ENTITY, Scope.PROJECT,
         {"entity_type": "project"}),
        ("Sprint goal: complete the recommendation pipeline MVP.", NodeType.TASK, Scope.PROJECT, None),
        ("The ML model uses a two-tower architecture for retrieval.", NodeType.FACT, Scope.PROJECT,
         {"subsystem": "ml"}),
        ("We switched from Redis to DragonflyDB for better memory efficiency.", NodeType.DECISION, Scope.PROJECT, None),

        # Organisation scope — company-wide facts
        ("Nexus AI was founded in 2022 and has 150 employees.", NodeType.FACT, Scope.ORGANISATION, None),
        ("The company uses Slack for communication and Linear for project tracking.", NodeType.FACT,
         Scope.ORGANISATION, None),
        ("Engineering all-hands is every other Thursday at 2pm.", NodeType.EVENT, Scope.ORGANISATION, None),
        ("Company policy: all services must have 95% test coverage.", NodeType.DECISION, Scope.ORGANISATION, None),
    ]

    stored_ids: dict[str, str] = {}  # content_preview -> event_id
    for content, node_type, scope, metadata in memories:
        with Timer() as t:
            event_id = await engine.store(
                content,
                user_id="alex",
                node_type=node_type,
                scope=scope,
                metadata=metadata,
            )
        log.log_op(
            f"store/{node_type.value}/{scope.value}",
            {"content": content[:60], "node_type": node_type.value, "scope": scope.value},
            {"event_id": event_id},
            duration_ms=t.ms,
        )
        stored_ids[content[:40]] = event_id

    log.log_detail(f"Stored {len(memories)} memories across {len(set(s for _, _, s, _ in memories))} scopes")
    return stored_ids


async def test_02_retrieval_queries(engine: MemoryEngine, log: TestLogger):
    """Run synthetic queries that simulate real LLM-assistant interactions."""
    log.section("02 — Retrieval Queries (Simulated Usage)")

    queries = [
        # Factual lookups
        ("What does Alex do for a living?", Scope.PERSONAL, "Should find Alex's job info"),
        ("Where does Alex work?", Scope.PERSONAL, "Should find Nexus AI entity"),
        ("What editor does Alex use?", Scope.PERSONAL, "Should find Neovim preference"),

        # Project-specific
        ("What tech stack is the team using?", Scope.PROJECT, "Should find FastAPI, DuckDB, Kong decisions"),
        ("What database did we choose for analytics?", Scope.PROJECT, "Should find DuckDB decision"),
        ("What is Project Orion?", Scope.PROJECT, "Should find project entity"),
        ("What architecture does the ML model use?", Scope.PROJECT, "Should find two-tower fact"),

        # Cross-scope (no scope filter)
        ("Tell me everything about Alex and his work.", None, "Should pull from personal + project + org"),
        ("What decisions have been made recently?", None, "Should find decisions across all scopes"),

        # Organisation scope
        ("What are the company policies?", Scope.ORGANISATION, "Should find test coverage policy"),
        ("When is the all-hands meeting?", Scope.ORGANISATION, "Should find Thursday meeting"),

        # Task-oriented
        ("What tasks does Alex need to do?", Scope.PERSONAL, "Should find pending tasks"),
        ("What is the sprint goal?", Scope.PROJECT, "Should find MVP sprint goal"),

        # Semantic / fuzzy
        ("coding preferences and tools", Scope.PERSONAL, "Should find editor + language preferences"),
        ("infrastructure and deployment choices", Scope.PROJECT, "Should find Kong, DragonflyDB decisions"),
    ]

    results_summary = []
    for query, scope, expected in queries:
        with Timer() as t:
            response = await engine.retrieve(
                query,
                user_id="alex",
                scope=scope,
            )

        result_data = {
            "result_count": len(response.results),
            "bundle_sections": list(response.bundle.sections.keys()),
            "tokens_used": response.bundle.tokens_used,
            "token_budget": response.bundle.token_budget,
            "timing_ms": response.metadata.timing_ms,
            "backends_used": response.metadata.backends_used,
            "candidates_generated": response.metadata.candidates_generated,
            "top_3": [
                {
                    "content": r.node.content[:80],
                    "score": round(r.composite_score, 4),
                    "paths": r.paths,
                    "node_type": r.node.node_type.value,
                    "lifecycle": r.node.lifecycle_state.value,
                }
                for r in response.results[:3]
            ],
            "score_traces": [
                {
                    "semantic": round(st.semantic_similarity, 3),
                    "lexical": round(st.lexical_relevance, 3),
                    "graph": round(st.graph_proximity, 3),
                    "recency": round(st.recency_factor, 3),
                    "confidence": round(st.confidence, 3),
                    "epistemic": round(st.epistemic_weight, 3),
                    "composite": round(st.composite_score, 4),
                }
                for st in response.score_traces[:3]
            ],
            "cross_scope_count": len(response.cross_scope_hints),
        }

        log.log_op(
            f"retrieve/{scope.value if scope else 'all'}",
            {"query": query, "scope": scope.value if scope else None, "expected": expected},
            result_data,
            duration_ms=t.ms,
        )
        results_summary.append({
            "query": query, "scope": scope.value if scope else None,
            "count": len(response.results), "expected": expected,
        })

    log.log_detail(f"Ran {len(queries)} queries")
    return results_summary


async def test_03_lifecycle_transitions(engine: MemoryEngine, log: TestLogger):
    """Test the full lifecycle: tentative -> stable -> superseded -> archived."""
    log.section("03 — Lifecycle Transitions")

    # Store a fact that starts as TENTATIVE
    with Timer() as t:
        event_id = await engine.store(
            "The recommendation service runs on port 8080.",
            user_id="alex",
            node_type=NodeType.FACT,
            scope=Scope.PROJECT,
        )
    log.log_op("store/initial_fact", {"content": "port 8080 fact"}, {"event_id": event_id}, t.ms)

    # Find the node
    nodes = await engine.query_nodes(user_id="alex", node_type=NodeType.FACT)
    port_node = next((n for n in nodes if "port 8080" in n.content), None)
    assert port_node is not None, "Could not find port 8080 node"

    node_id = str(port_node.id)
    log.log_detail(f"Found node: {node_id}, state={port_node.lifecycle_state.value}")

    # TENTATIVE -> STABLE
    with Timer() as t:
        await engine.promote(node_id)
    promoted = await engine.get_node(node_id)
    log.log_op(
        "promote/tentative_to_stable",
        {"node_id": node_id},
        {"new_state": promoted.lifecycle_state.value},
        t.ms,
    )
    assert promoted.lifecycle_state == LifecycleState.STABLE

    # Store a corrected fact
    with Timer() as t:
        correction_id = await engine.store(
            "The recommendation service runs on port 9090, not 8080.",
            user_id="alex",
            node_type=NodeType.FACT,
            scope=Scope.PROJECT,
        )
    log.log_op("store/correction", {"content": "port 9090 correction"}, {"event_id": correction_id}, t.ms)

    new_nodes = await engine.query_nodes(user_id="alex", node_type=NodeType.FACT)
    new_node = next((n for n in new_nodes if "port 9090" in n.content), None)
    assert new_node is not None
    new_node_id = str(new_node.id)

    # STABLE -> SUPERSEDED
    with Timer() as t:
        await engine.supersede(node_id, new_node_id)
    old_node = await engine.get_node(node_id, include_superseded=True)
    log.log_op(
        "supersede/stable_to_superseded",
        {"old_node_id": node_id, "new_node_id": new_node_id},
        {
            "old_state": old_node.lifecycle_state.value,
            "superseded_by": str(old_node.superseded_by),
        },
        t.ms,
    )
    assert old_node.lifecycle_state == LifecycleState.SUPERSEDED

    # SUPERSEDED -> ARCHIVED
    with Timer() as t:
        await engine.archive(node_id)
    archived = await engine.get_node(node_id, include_superseded=True)
    log.log_op(
        "archive/superseded_to_archived",
        {"node_id": node_id},
        {"new_state": archived.lifecycle_state.value},
        t.ms,
    )
    assert archived.lifecycle_state == LifecycleState.ARCHIVED

    # Verify superseded node is hidden from default queries
    with Timer() as t:
        response = await engine.retrieve(
            "What port does the recommendation service run on?",
            user_id="alex",
            scope=Scope.PROJECT,
        )
    visible_contents = [r.node.content for r in response.results]
    log.log_op(
        "retrieve/after_supersedence",
        {"query": "port for recommendation service"},
        {
            "result_count": len(response.results),
            "results": [r.node.content[:60] for r in response.results[:5]],
            "old_fact_visible": any("port 8080" in c and "not" not in c for c in visible_contents),
            "new_fact_visible": any("port 9090" in c for c in visible_contents),
        },
        t.ms,
    )

    return {"node_id": node_id, "new_node_id": new_node_id}


async def test_04_graph_edges_and_traversal(engine: MemoryEngine, log: TestLogger):
    """Test explicit edge creation and graph neighborhood traversal."""
    log.section("04 — Graph Edges and Traversal")

    # Store related entities
    ids = {}
    entities = [
        ("FastAPI microservice", NodeType.ENTITY, {"entity_type": "service"}),
        ("PostgreSQL database", NodeType.ENTITY, {"entity_type": "database"}),
        ("Redis cache layer", NodeType.ENTITY, {"entity_type": "infrastructure"}),
        ("The FastAPI service connects to PostgreSQL for persistence.", NodeType.FACT, None),
        ("Redis is used as a caching layer in front of PostgreSQL.", NodeType.FACT, None),
        ("Alex is the tech lead for the FastAPI microservice.", NodeType.FACT, None),
    ]

    for content, node_type, metadata in entities:
        with Timer() as t:
            eid = await engine.store(
                content,
                user_id="alex",
                node_type=node_type,
                scope=Scope.PROJECT,
                metadata=metadata,
            )
        ids[content[:30]] = eid
        log.log_op(f"store/{node_type.value}", {"content": content[:50]}, {"event_id": eid}, t.ms)

    # Look up node IDs for edge creation
    all_nodes = await engine.query_nodes(user_id="alex", node_type=NodeType.ENTITY)
    fastapi_node = next((n for n in all_nodes if "FastAPI" in n.content), None)
    pg_node = next((n for n in all_nodes if "PostgreSQL" in n.content), None)
    redis_node = next((n for n in all_nodes if "Redis" in n.content), None)

    if not all([fastapi_node, pg_node, redis_node]):
        log.log_op("edges/skip", {}, {"reason": "Could not find all entity nodes"}, 0)
        return

    # Create typed edges
    edge_defs = [
        (fastapi_node.id, pg_node.id, EdgeType.RELATES_TO, "FastAPI -> PostgreSQL"),
        (redis_node.id, pg_node.id, EdgeType.RELATES_TO, "Redis -> PostgreSQL"),
        (fastapi_node.id, redis_node.id, EdgeType.RELATES_TO, "FastAPI -> Redis"),
    ]

    for source_id, target_id, edge_type, label in edge_defs:
        edge = MemoryEdge(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            user_id="alex",
            confidence=0.9,
        )
        with Timer() as t:
            edge_id = await engine._graph_store.create_edge(edge)
        log.log_op(
            f"create_edge/{edge_type.value}",
            {"source": str(source_id)[:8], "target": str(target_id)[:8], "label": label},
            {"edge_id": edge_id},
            t.ms,
        )

    # Graph neighborhood traversal
    with Timer() as t:
        neighborhood = await engine._graph_store.get_neighborhood(
            str(fastapi_node.id), max_hops=2
        )
    log.log_op(
        "get_neighborhood",
        {"node_id": str(fastapi_node.id)[:8], "max_hops": 2},
        {
            "nodes_found": len(neighborhood),
            "node_contents": [n.content[:50] for n in neighborhood[:5]],
            "node_types": [n.node_type.value for n in neighborhood[:5]],
        },
        t.ms,
    )

    # Query edges
    with Timer() as t:
        edges = await engine._graph_store.get_edges(source_id=str(fastapi_node.id))
    log.log_op(
        "get_edges",
        {"source_id": str(fastapi_node.id)[:8]},
        {
            "count": len(edges),
            "edge_types": [e.edge_type.value for e in edges],
            "targets": [str(e.target_id)[:8] for e in edges],
        },
        t.ms,
    )

    # Retrieval should benefit from graph connectivity
    with Timer() as t:
        response = await engine.retrieve(
            "What infrastructure does the FastAPI service depend on?",
            user_id="alex",
            scope=Scope.PROJECT,
        )
    log.log_op(
        "retrieve/graph_enhanced",
        {"query": "FastAPI dependencies"},
        {
            "count": len(response.results),
            "top_results": [
                {"content": r.node.content[:60], "paths": r.paths, "graph_prox": round(r.graph_proximity, 3)}
                for r in response.results[:5]
            ],
        },
        t.ms,
    )


async def test_05_event_store_queries(engine: MemoryEngine, log: TestLogger):
    """Test event store operations — the append-only source of truth."""
    log.section("05 — Event Store Queries")

    # Get all events for user
    with Timer() as t:
        events = await engine.get_events("alex")
    log.log_op(
        "get_events/all",
        {"user_id": "alex"},
        {
            "count": len(events),
            "roles": list(set(e.role for e in events)),
            "scopes": list(set(e.scope.value for e in events)),
            "date_range": {
                "earliest": events[-1].timestamp.isoformat() if events else None,
                "latest": events[0].timestamp.isoformat() if events else None,
            },
        },
        t.ms,
    )

    # Get a specific event
    if events:
        sample_event = events[0]
        with Timer() as t:
            fetched = await engine.get_event(str(sample_event.id))
        log.log_op(
            "get_event/by_id",
            {"event_id": str(sample_event.id)[:8]},
            {
                "found": fetched is not None,
                "content": fetched.content[:60] if fetched else None,
                "role": fetched.role if fetched else None,
                "content_hash": fetched.content_hash[:16] if fetched else None,
            },
            t.ms,
        )


async def test_06_node_queries_with_filters(engine: MemoryEngine, log: TestLogger):
    """Test node querying with various filters."""
    log.section("06 — Node Queries with Filters")

    # Query by node type
    for node_type in [NodeType.FACT, NodeType.DECISION, NodeType.PREFERENCE, NodeType.ENTITY, NodeType.TASK]:
        with Timer() as t:
            nodes = await engine.query_nodes(user_id="alex", node_type=node_type)
        log.log_op(
            f"query_nodes/{node_type.value}",
            {"node_type": node_type.value},
            {
                "count": len(nodes),
                "contents": [n.content[:50] for n in nodes[:5]],
                "states": [n.lifecycle_state.value for n in nodes],
            },
            t.ms,
        )

    # Query by scope
    for scope in [Scope.PERSONAL, Scope.PROJECT, Scope.ORGANISATION]:
        with Timer() as t:
            nodes = await engine.query_nodes(user_id="alex", scope=scope)
        log.log_op(
            f"query_nodes/scope_{scope.value}",
            {"scope": scope.value},
            {
                "count": len(nodes),
                "node_types": list(set(n.node_type.value for n in nodes)),
            },
            t.ms,
        )

    # Query by lifecycle state
    with Timer() as t:
        stable_nodes = await engine.query_nodes(
            user_id="alex",
            lifecycle_states=[LifecycleState.STABLE],
        )
    log.log_op(
        "query_nodes/stable_only",
        {"lifecycle_state": "stable"},
        {
            "count": len(stable_nodes),
            "contents": [n.content[:50] for n in stable_nodes[:5]],
        },
        t.ms,
    )


async def test_07_cross_scope_retrieval(engine: MemoryEngine, log: TestLogger):
    """Test cross-scope hint generation and scope isolation."""
    log.section("07 — Cross-Scope Retrieval")

    # Query personal scope — should get cross-scope hints from project
    queries = [
        ("What does Alex work on?", Scope.PERSONAL,
         "Personal query should hint at project entities"),
        ("engineering decisions and policies", Scope.PROJECT,
         "Project query should hint at org policies"),
        ("Tell me about the team and company", Scope.ORGANISATION,
         "Org query should hint at project + personal"),
    ]

    for query, scope, expectation in queries:
        with Timer() as t:
            response = await engine.retrieve(
                query,
                user_id="alex",
                scope=scope,
                include_cross_scope=True,
            )
        log.log_op(
            f"retrieve/cross_scope/{scope.value}",
            {"query": query, "scope": scope.value, "expectation": expectation},
            {
                "primary_count": len(response.results),
                "cross_scope_count": len(response.cross_scope_hints),
                "cross_scope_contents": [
                    h.node.content[:50] for h in response.cross_scope_hints[:3]
                ],
                "filter_metadata": {
                    "scope_filter": response.filter_metadata.scope_filter if response.filter_metadata else None,
                    "cross_scope_enabled": response.filter_metadata.cross_scope_enabled if response.filter_metadata else None,
                },
            },
            t.ms,
        )

    # Without cross-scope
    with Timer() as t:
        response = await engine.retrieve(
            "What does Alex work on?",
            user_id="alex",
            scope=Scope.PERSONAL,
            include_cross_scope=False,
        )
    log.log_op(
        "retrieve/no_cross_scope",
        {"query": "What does Alex work on?", "cross_scope": False},
        {
            "primary_count": len(response.results),
            "cross_scope_count": len(response.cross_scope_hints),
        },
        t.ms,
    )


async def test_08_token_budget_and_packing(engine: MemoryEngine, log: TestLogger):
    """Test context packing with different token budgets."""
    log.section("08 — Token Budget and Context Packing")

    query = "Tell me everything relevant about the project architecture and team."

    for budget in [500, 2000, 8000]:
        with Timer() as t:
            response = await engine.retrieve(
                query,
                user_id="alex",
                token_budget=budget,
            )
        log.log_op(
            f"retrieve/budget_{budget}",
            {"query": query[:40], "token_budget": budget},
            {
                "results_scored": len(response.results),
                "bundle_included": response.bundle.included_count,
                "tokens_used": response.bundle.tokens_used,
                "token_budget": response.bundle.token_budget,
                "budget_remaining": response.bundle.budget_remaining,
                "excluded_count": len(response.bundle.excluded_ids),
                "sections": {k: len(v) for k, v in response.bundle.sections.items()},
                "min_fidelity": response.bundle.min_fidelity.value,
            },
            t.ms,
        )


async def test_09_epistemic_types_and_confidence(engine: MemoryEngine, log: TestLogger):
    """Test epistemic type inference and confidence scoring."""
    log.section("09 — Epistemic Types and Confidence")

    from prme.types import EpistemicType, SourceType

    # Store memories with different epistemic profiles
    epistemic_test_data = [
        ("Alex definitely said he prefers dark mode.", NodeType.PREFERENCE,
         EpistemicType.OBSERVED, SourceType.USER_STATED),
        ("Alex probably uses a Mac based on his editor config.", NodeType.FACT,
         EpistemicType.INFERRED, SourceType.SYSTEM_INFERRED),
        ("Alex might be interested in the Zig programming language.", NodeType.FACT,
         EpistemicType.HYPOTHETICAL, SourceType.SYSTEM_INFERRED),
        ("If the team grows, they may need to split the monorepo.", NodeType.FACT,
         EpistemicType.CONDITIONAL, SourceType.SYSTEM_INFERRED),
    ]

    for content, node_type, epistemic_type, source_type in epistemic_test_data:
        with Timer() as t:
            event_id = await engine.store(
                content,
                user_id="alex",
                node_type=node_type,
                scope=Scope.PERSONAL,
                epistemic_type=epistemic_type,
                source_type=source_type,
            )
        # Look up the created node to check confidence
        nodes = await engine.query_nodes(user_id="alex")
        node = next((n for n in nodes if content in n.content), None)
        log.log_op(
            f"store/epistemic_{epistemic_type.value}",
            {"content": content[:50], "epistemic": epistemic_type.value, "source": source_type.value},
            {
                "event_id": event_id,
                "confidence": node.confidence if node else None,
                "epistemic_type": node.epistemic_type.value if node else None,
                "source_type": node.source_type.value if node else None,
            },
            t.ms,
        )

    # DEFAULT retrieval should filter out HYPOTHETICAL
    with Timer() as t:
        response = await engine.retrieve(
            "What programming languages might Alex be interested in?",
            user_id="alex",
            scope=Scope.PERSONAL,
        )
    hypothetical_results = [
        r for r in response.results
        if r.node.epistemic_type == EpistemicType.HYPOTHETICAL
    ]
    log.log_op(
        "retrieve/default_filters_hypothetical",
        {"query": "languages Alex interested in", "mode": "DEFAULT"},
        {
            "total_results": len(response.results),
            "hypothetical_in_results": len(hypothetical_results),
            "should_be_filtered": True,
        },
        t.ms,
    )


async def test_10_multi_user_isolation(engine: MemoryEngine, log: TestLogger):
    """Verify that different users cannot see each other's memories."""
    log.section("10 — Multi-User Isolation")

    # Store something for a different user
    with Timer() as t:
        await engine.store(
            "Bob prefers light mode and uses VS Code.",
            user_id="bob",
            node_type=NodeType.PREFERENCE,
            scope=Scope.PERSONAL,
        )
    log.log_op("store/bob", {"content": "Bob preference", "user_id": "bob"}, {}, t.ms)

    # Alex should NOT see Bob's memories
    with Timer() as t:
        alex_response = await engine.retrieve(
            "What editor preferences exist?",
            user_id="alex",
            scope=Scope.PERSONAL,
        )
    bob_leak = any("Bob" in r.node.content or "VS Code" in r.node.content for r in alex_response.results)

    with Timer() as t:
        bob_response = await engine.retrieve(
            "What editor preferences exist?",
            user_id="bob",
            scope=Scope.PERSONAL,
        )

    log.log_op(
        "retrieve/isolation_check",
        {"query": "editor preferences"},
        {
            "alex_results": len(alex_response.results),
            "bob_results": len(bob_response.results),
            "bob_data_leaked_to_alex": bob_leak,
            "isolation_passed": not bob_leak,
            "alex_contents": [r.node.content[:40] for r in alex_response.results[:3]],
            "bob_contents": [r.node.content[:40] for r in bob_response.results[:3]],
        },
        t.ms,
    )


async def test_11_supersedence_chain(engine: MemoryEngine, log: TestLogger):
    """Test a multi-step supersedence chain (fact evolves over time)."""
    log.section("11 — Supersedence Chain")

    # Simulate a fact evolving: v1 -> v2 -> v3
    versions = [
        "The deployment target is AWS EC2.",
        "The deployment target is AWS ECS (migrated from EC2).",
        "The deployment target is AWS EKS with Kubernetes (migrated from ECS).",
    ]

    node_ids = []
    for i, content in enumerate(versions):
        with Timer() as t:
            await engine.store(
                content, user_id="alex",
                node_type=NodeType.FACT, scope=Scope.PROJECT,
            )
        nodes = await engine.query_nodes(user_id="alex", node_type=NodeType.FACT)
        node = next((n for n in nodes if content in n.content), None)
        if node:
            node_ids.append(str(node.id))
            if i > 0:
                await engine.supersede(node_ids[i - 1], node_ids[i])
        log.log_op(
            f"store/chain_v{i + 1}",
            {"content": content[:50], "version": i + 1},
            {"node_id": node_ids[-1] if node_ids else None},
            t.ms,
        )

    # Check the supersedence chain
    if len(node_ids) >= 1:
        with Timer() as t:
            chain = await engine._graph_store.get_supersedence_chain(node_ids[0])
        log.log_op(
            "get_supersedence_chain",
            {"start_node": node_ids[0][:8]},
            {
                "chain_length": len(chain),
                "chain": [
                    {"content": n.content[:50], "state": n.lifecycle_state.value}
                    for n in chain
                ],
            },
            t.ms,
        )

    # Only the latest version should appear in retrieval
    with Timer() as t:
        response = await engine.retrieve(
            "What is the deployment target?",
            user_id="alex",
            scope=Scope.PROJECT,
        )
    log.log_op(
        "retrieve/latest_fact_only",
        {"query": "deployment target"},
        {
            "count": len(response.results),
            "results": [
                {"content": r.node.content[:60], "state": r.node.lifecycle_state.value}
                for r in response.results[:5]
            ],
        },
        t.ms,
    )


async def test_12_session_scoped_queries(engine: MemoryEngine, log: TestLogger):
    """Store memories in named sessions and query by session."""
    log.section("12 — Session-Scoped Storage")

    sessions = {
        "standup-2024-01-15": [
            "Discussed the API refactor progress. Alex is 80% done.",
            "Need to resolve the rate limiting bug before release.",
        ],
        "design-review-2024-01-16": [
            "Reviewed the new search architecture. Team approved the hybrid approach.",
            "Alex will implement the vector index integration.",
        ],
        "retro-2024-01-17": [
            "Sprint retrospective: deployment pipeline was the biggest pain point.",
            "Action item: Alex to investigate GitOps tooling.",
        ],
    }

    for session_id, messages in sessions.items():
        for msg in messages:
            with Timer() as t:
                await engine.store(
                    msg,
                    user_id="alex",
                    session_id=session_id,
                    node_type=NodeType.EVENT,
                    scope=Scope.PROJECT,
                )
            log.log_op(
                f"store/session/{session_id[:15]}",
                {"content": msg[:50], "session_id": session_id},
                {},
                t.ms,
            )

    # Query events by session
    with Timer() as t:
        events = await engine.get_events("alex", session_id="design-review-2024-01-16")
    log.log_op(
        "get_events/by_session",
        {"session_id": "design-review-2024-01-16"},
        {
            "count": len(events),
            "contents": [e.content[:50] for e in events],
        },
        t.ms,
    )

    # Semantic retrieval across sessions
    with Timer() as t:
        response = await engine.retrieve(
            "What action items does Alex have from recent meetings?",
            user_id="alex",
            scope=Scope.PROJECT,
        )
    log.log_op(
        "retrieve/cross_session",
        {"query": "Alex action items from meetings"},
        {
            "count": len(response.results),
            "results": [
                {"content": r.node.content[:60], "score": round(r.composite_score, 4)}
                for r in response.results[:5]
            ],
        },
        t.ms,
    )


async def test_13_summary_generation(engine: MemoryEngine, log: TestLogger):
    """Store summaries and verify they participate in retrieval."""
    log.section("13 — Summary Nodes")

    summaries = [
        "Weekly summary: Alex worked on the FastAPI microservice, resolved the rate limiting "
        "bug, and started the vector index integration. Team approved the hybrid search approach.",
        "Project status summary: Orion recommendation engine MVP is 60% complete. Remaining: "
        "vector retrieval integration, A/B testing framework, and load testing.",
    ]

    for summary in summaries:
        with Timer() as t:
            await engine.store(
                summary,
                user_id="alex",
                node_type=NodeType.SUMMARY,
                scope=Scope.PROJECT,
            )
        log.log_op("store/summary", {"content": summary[:60]}, {}, t.ms)

    with Timer() as t:
        response = await engine.retrieve(
            "What is the overall project status?",
            user_id="alex",
            scope=Scope.PROJECT,
        )
    log.log_op(
        "retrieve/with_summaries",
        {"query": "overall project status"},
        {
            "count": len(response.results),
            "results": [
                {
                    "content": r.node.content[:60],
                    "type": r.node.node_type.value,
                    "score": round(r.composite_score, 4),
                }
                for r in response.results[:5]
            ],
        },
        t.ms,
    )


async def test_14_stress_retrieval(engine: MemoryEngine, log: TestLogger):
    """Run a batch of diverse queries to stress test the retrieval pipeline."""
    log.section("14 — Stress Retrieval (Batch Queries)")

    stress_queries = [
        "Who is Alex?",
        "What language does Alex prefer?",
        "What databases does the project use?",
        "Tell me about the ML architecture.",
        "What happened in the design review?",
        "Is there anything about Rust?",
        "What are Alex's pending tasks?",
        "Company information and policies",
        "Sprint progress and deadlines",
        "Infrastructure and deployment",
        "How many employees at Nexus?",
        "communication tools used by the team",
        "recent meetings and decisions",
        "Alex's learning goals",
        "technology migration history",
    ]

    total_ms = 0
    for query in stress_queries:
        with Timer() as t:
            response = await engine.retrieve(query, user_id="alex")
        total_ms += t.ms
        log.log_op(
            "retrieve/stress",
            {"query": query},
            {
                "count": len(response.results),
                "top_score": round(response.results[0].composite_score, 4) if response.results else 0,
                "backends": response.metadata.backends_used,
                "timing_ms": round(response.metadata.timing_ms, 1),
            },
            t.ms,
        )

    log.log_detail(f"Batch: {len(stress_queries)} queries in {total_ms:.0f} ms "
                   f"(avg {total_ms / len(stress_queries):.1f} ms)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    # Setup log directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent / "logs" / timestamp
    log = TestLogger(log_dir)

    # Create temp directory for data
    tmpdir = tempfile.mkdtemp(prefix="prme_integration_")
    log.logger.info("Data directory: %s", tmpdir)
    log.logger.info("Log directory:  %s", log_dir)

    lexical_dir = Path(tmpdir) / "lexical_index"
    lexical_dir.mkdir(parents=True, exist_ok=True)

    config = PRMEConfig(
        db_path=str(Path(tmpdir) / "memory.duckdb"),
        vector_path=str(Path(tmpdir) / "vectors.usearch"),
        lexical_path=str(lexical_dir),
    )

    engine = await MemoryEngine.create(config)

    tests = [
        ("01_store_all_node_types", test_01_store_all_node_types),
        ("02_retrieval_queries", test_02_retrieval_queries),
        ("03_lifecycle_transitions", test_03_lifecycle_transitions),
        ("04_graph_edges_and_traversal", test_04_graph_edges_and_traversal),
        ("05_event_store_queries", test_05_event_store_queries),
        ("06_node_queries_with_filters", test_06_node_queries_with_filters),
        ("07_cross_scope_retrieval", test_07_cross_scope_retrieval),
        ("08_token_budget_and_packing", test_08_token_budget_and_packing),
        ("09_epistemic_types_and_confidence", test_09_epistemic_types_and_confidence),
        ("10_multi_user_isolation", test_10_multi_user_isolation),
        ("11_supersedence_chain", test_11_supersedence_chain),
        ("12_session_scoped_queries", test_12_session_scoped_queries),
        ("13_summary_generation", test_13_summary_generation),
        ("14_stress_retrieval", test_14_stress_retrieval),
    ]

    passed = 0
    failed = 0
    errors: list[str] = []

    try:
        for name, test_fn in tests:
            try:
                await test_fn(engine, log)
                passed += 1
            except Exception as e:
                failed += 1
                tb = traceback.format_exc()
                errors.append(f"{name}: {e}")
                log.log_op(
                    f"TEST_FAILED/{name}",
                    {},
                    {"error": str(e), "traceback": tb},
                    0,
                    error=str(e),
                )
                log.logger.error("TEST FAILED: %s — %s", name, e)
                log.logger.debug(tb)

        # Final stats
        log.section("RESULTS")
        log.logger.info("  Passed: %d / %d", passed, passed + failed)
        log.logger.info("  Failed: %d / %d", failed, passed + failed)
        if errors:
            log.logger.info("  Errors:")
            for err in errors:
                log.logger.info("    - %s", err)

        # Disk usage
        total_size = sum(p.stat().st_size for p in Path(tmpdir).rglob("*") if p.is_file())
        file_count = sum(1 for p in Path(tmpdir).rglob("*") if p.is_file())
        log.logger.info("")
        log.logger.info("  Data files: %d files, %s bytes", file_count, f"{total_size:,}")

    finally:
        await engine.close()
        log.save()
        shutil.rmtree(tmpdir, ignore_errors=True)
        log.logger.info("Cleaned up data directory: %s", tmpdir)


if __name__ == "__main__":
    asyncio.run(main())
