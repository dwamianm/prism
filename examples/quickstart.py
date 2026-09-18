"""PRME Quickstart — practical demo of the memory engine.

This script demonstrates the three main workflows:

1. store()    — Direct storage (no LLM needed)
2. ingest()   — LLM-powered extraction (needs an API key)
3. retrieve() — Hybrid retrieval with scoring and context packing

Run:
    python examples/quickstart.py

By default, this runs in "store-only" mode (no LLM API key required).
Set OPENAI_API_KEY (or configure another provider) to enable LLM extraction.
"""

import asyncio
import logging
import os
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

# Suppress noisy warnings (pandas version, tokenizers, etc.)
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore")

# Keep logging at WARNING to avoid suppressing error handling in backends
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
)

sys.stdout.reconfigure(line_buffering=True)

from prme import MemoryEngine, NodeType, PRMEConfig, Scope  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def show_results(response) -> None:
    """Pretty-print a RetrievalResponse."""
    print(f"  Candidates scored : {len(response.results)}")
    print(f"  Bundle sections   : {list(response.bundle.sections.keys())}")
    print(f"  Tokens used       : {response.bundle.tokens_used}/{response.bundle.token_budget}")
    print(f"  Retrieval time    : {response.metadata.timing_ms:.1f} ms")
    print()
    for i, r in enumerate(response.results[:10], 1):
        content_preview = r.node.content[:70].replace("\n", " ")
        print(f"  {i:>2}. [{r.composite_score:.3f}] {content_preview}")
        if r.score_trace:
            t = r.score_trace
            print(f"      semantic={t.semantic_similarity:.2f}  lexical={t.lexical_relevance:.2f}  "
                  f"graph={t.graph_proximity:.2f}  recency={t.recency_factor:.2f}  "
                  f"confidence={t.confidence:.2f}  epistemic={t.epistemic_weight:.2f}")
    if not response.results:
        print("  (no results)")


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

async def demo_store_and_retrieve(engine: MemoryEngine) -> None:
    """Store facts directly (no LLM) and retrieve them."""
    section("1. Direct Storage (no LLM required)")

    memories = [
        ("Alice prefers dark mode in all her editors.", NodeType.PREFERENCE, Scope.PERSONAL),
        ("The team decided to use PostgreSQL for the backend.", NodeType.DECISION, Scope.PROJECT),
        ("Alice works at Acme Corp as a senior engineer.", NodeType.FACT, Scope.PERSONAL),
        ("The project deadline is March 15th.", NodeType.FACT, Scope.PROJECT),
        ("Alice prefers Python over JavaScript for backend work.", NodeType.PREFERENCE, Scope.PERSONAL),
        ("We chose React for the frontend framework.", NodeType.DECISION, Scope.PROJECT),
        ("Alice likes Blue Bottle coffee.", NodeType.FACT, Scope.PERSONAL),
        ("The API uses REST, not GraphQL.", NodeType.DECISION, Scope.PROJECT),
    ]

    for content, node_type, scope in memories:
        receipt = await engine.store_with_receipt(
            content,
            user_id="alice",
            node_type=node_type,
            scope=scope,
        )
        print(
            f"  Stored: {content[:50]:50s} -> "
            f"event={str(receipt.event_id)[:8]}... node={str(receipt.node_id)[:8]}..."
        )

    # --- Queries ---
    section("2. Retrieval - Personal Preferences")
    response = await engine.retrieve(
        "What are Alice preferences?",
        user_id="alice",
        scope=Scope.PERSONAL,
    )
    show_results(response)

    section("3. Retrieval - Project Decisions")
    response = await engine.retrieve(
        "What technology decisions has the team made?",
        user_id="alice",
        scope=Scope.PROJECT,
    )
    show_results(response)

    section("4. Retrieval - Cross-Scope (all memories)")
    response = await engine.retrieve(
        "Tell me everything about Alice and the project.",
        user_id="alice",
    )
    show_results(response)


async def demo_lifecycle(engine: MemoryEngine) -> None:
    """Demonstrate memory lifecycle transitions."""
    section("5. Lifecycle Transitions")

    # Store a fact (starts as TENTATIVE)
    mysql_receipt = await engine.store_with_receipt(
        "The project uses MySQL for the database.",
        user_id="alice",
        node_type=NodeType.FACT,
        scope=Scope.PROJECT,
    )
    mysql_node = mysql_receipt.node
    print(f"  Created:    '{mysql_node.content}'")
    print(f"  State:      {mysql_node.lifecycle_state.value}")

    # Promote to STABLE using the exact node returned by the store receipt.
    await engine.promote(str(mysql_receipt.node_id), user_id="alice")
    node = await engine.get_node(str(mysql_receipt.node_id), user_id="alice")
    print(f"  Promoted:   {node.lifecycle_state.value}")

    # Store a corrected fact and supersede the old one.
    pg_receipt = await engine.store_with_receipt(
        "The project uses PostgreSQL, not MySQL.",
        user_id="alice",
        node_type=NodeType.FACT,
        scope=Scope.PROJECT,
    )
    await engine.supersede(
        str(mysql_receipt.node_id),
        str(pg_receipt.node_id),
        evidence_id=str(pg_receipt.event_id),
        user_id="alice",
        actor_id="alice",
    )
    old = await engine.get_node(
        str(mysql_receipt.node_id),
        user_id="alice",
        include_superseded=True,
    )
    print(f"  Superseded: '{old.content}' -> {old.lifecycle_state.value}")
    print(f"  New fact:   '{pg_receipt.node.content}' -> {pg_receipt.node.lifecycle_state.value}")


async def demo_ingest_with_llm(engine: MemoryEngine) -> None:
    """Ingest conversation with LLM extraction (needs API key)."""
    section("6. LLM-Powered Ingestion")

    conversation = [
        {"role": "user", "content": "I just started using Neovim and I love it. "
         "Also, we had a team meeting and decided to switch from REST to GraphQL."},
        {"role": "assistant", "content": "Great choices! Neovim has excellent plugin support. "
         "GraphQL should simplify your frontend data fetching."},
        {"role": "user", "content": "Yeah, Sarah suggested it. She has been using GraphQL "
         "at her previous company for two years."},
    ]

    print("  Ingesting conversation (with LLM extraction)...")
    event_ids = await engine.ingest_batch(
        conversation,
        user_id="alice",
        session_id="demo-session",
        wait_for_extraction=True,
        scope=Scope.PROJECT,
    )
    for eid in event_ids:
        print(f"  Event persisted: {eid[:8]}...")

    print("\n  Querying extracted memories...")
    response = await engine.retrieve(
        "What tools and technologies were discussed?",
        user_id="alice",
    )
    show_results(response)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    # Create a temp directory so we don't pollute the project
    tmpdir = tempfile.mkdtemp(prefix="prme_demo_")
    print(f"Using temp directory: {tmpdir}")

    lexical_dir = Path(tmpdir) / "lexical_index"
    lexical_dir.mkdir(parents=True, exist_ok=True)

    config = PRMEConfig(
        db_path=str(Path(tmpdir) / "memory.duckdb"),
        vector_path=str(Path(tmpdir) / "vectors.usearch"),
        lexical_path=str(lexical_dir),
    )
    engine = await MemoryEngine.create(config)

    try:
        # Always runs - no LLM needed
        await demo_store_and_retrieve(engine)
        await demo_lifecycle(engine)

        # Only runs if an LLM API key is available
        has_llm = any(os.environ.get(k) for k in [
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        ])
        if has_llm:
            await demo_ingest_with_llm(engine)
        else:
            section("6. LLM-Powered Ingestion (SKIPPED)")
            print("  Set OPENAI_API_KEY or ANTHROPIC_API_KEY to enable.")
            print("  Example: OPENAI_API_KEY=sk-... python examples/quickstart.py")

        # Show what's on disk
        section("7. Memory Pack Contents")
        total_size = 0
        file_count = 0
        for p in sorted(Path(tmpdir).rglob("*")):
            if p.is_file():
                total_size += p.stat().st_size
                file_count += 1
        print("  memory.duckdb       (event store + graph)")
        print("  vectors.usearch     (HNSW vector index)")
        print("  lexical_index/      (tantivy full-text index)")
        print("  ---")
        print(f"  {file_count} files, {total_size:,} bytes total")

    finally:
        await engine.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"\nCleaned up {tmpdir}")


if __name__ == "__main__":
    asyncio.run(main())
