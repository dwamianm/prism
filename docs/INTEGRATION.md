# PRME Integration Reference

> **Audience:** AI coding assistants and developers integrating PRME into applications.
> **Version:** Based on source as of 2026-03-02.
> **Single-file reference** — copy this into your assistant's context for complete API coverage.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Architecture Overview](#2-architecture-overview)
3. [Core API Reference](#3-core-api-reference)
4. [Data Models](#4-data-models)
5. [Type Reference](#5-type-reference)
6. [Configuration](#6-configuration)
7. [Retrieval Pipeline Deep Dive](#7-retrieval-pipeline-deep-dive)
8. [Multi-User / Multi-Tenant](#8-multi-user--multi-tenant)
9. [Working Examples](#9-working-examples)
10. [PostgreSQL Backend](#10-postgresql-backend)
11. [RFC Summary Table](#11-rfc-summary-table)

---

## 1. Quick Start

Minimal working example — store memories and retrieve them with hybrid search.

```python
import asyncio
import tempfile
from pathlib import Path

from prme import MemoryEngine, PRMEConfig, NodeType, Scope


async def main():
    # 1. Configure — paths default to cwd; use a temp dir for demos
    tmp = tempfile.mkdtemp(prefix="prme_")
    config = PRMEConfig(
        db_path=str(Path(tmp) / "memory.duckdb"),
        vector_path=str(Path(tmp) / "vectors.usearch"),
        lexical_path=str(Path(tmp) / "lexical_index"),
    )

    # 2. Create engine (async factory — initializes all 4 backends)
    engine = await MemoryEngine.create(config)

    try:
        # 3. Store memories
        await engine.store(
            "Alice prefers dark mode in all her editors.",
            user_id="alice",
            node_type=NodeType.PREFERENCE,
            scope=Scope.PERSONAL,
        )
        await engine.store(
            "The team decided to use PostgreSQL for the backend.",
            user_id="alice",
            node_type=NodeType.DECISION,
            scope=Scope.PROJECT,
        )

        # 4. Retrieve with hybrid search
        response = await engine.retrieve(
            "What does Alice prefer?",
            user_id="alice",
        )

        # 5. Use the results
        for r in response.results:
            print(f"[{r.composite_score:.3f}] {r.node.content}")

    finally:
        await engine.close()


asyncio.run(main())
```

**Requirements:** Python 3.11+, `pip install prme`. No LLM API key needed for `store()` + `retrieve()`. The `ingest()` method requires an OpenAI/Anthropic/Ollama API key for LLM extraction.

> **PostgreSQL backend:** Pass `database_url` instead of file paths to use PostgreSQL for all storage. See [Section 10](#10-postgresql-backend) for details and a quick start example.

---

## 2. Architecture Overview

PRME coordinates four storage backends behind a single `MemoryEngine` API:

```
                        MemoryEngine
                             │
          ┌──────────┬───────┴───────┬──────────┐
          ▼          ▼               ▼          ▼
     EventStore   GraphStore    VectorIndex  LexicalIndex
```

Two backend modes (selected automatically via `config.backend`):

| Layer | DuckDB mode (default) | PostgreSQL mode (`database_url` set) |
|---|---|---|
| EventStore | DuckDB table | PostgreSQL table (asyncpg) |
| GraphStore | DuckDB + recursive CTEs | PostgreSQL + recursive CTEs |
| VectorIndex | USearch HNSW + DuckDB metadata | pgvector HNSW on `nodes.embedding` |
| LexicalIndex | Tantivy (BM25) | `tsvector` / `tsquery` (GIN index) |
| Write Queue | Serialized `WriteQueue` (single-writer) | `NoOpWriteQueue` (passthrough) |

### Data Flow

**Write path** (`store()` / `ingest()`):
```
Content → Event (DuckDB) → MemoryNode (Graph) → Vector embedding → Lexical index
```

**Read path** (`retrieve()`):
```
Query → Analysis → Candidate Generation → Epistemic Filtering → Scoring → Context Packing → MemoryBundle
         (stage 1)    (stages 2-3)           (stage 4)          (stage 5)    (stage 6)
```

### Memory Pack (portable artifact — DuckDB mode only)

In DuckDB mode, all state lives in these files, copyable as a unit:

| File | Backend | Purpose |
|---|---|---|
| `memory.duckdb` | EventStore + GraphStore | Event log, graph nodes/edges, operations |
| `vectors.usearch` | VectorIndex | HNSW vector index |
| `lexical_index/` | LexicalIndex | Tantivy full-text index directory |

In PostgreSQL mode, all data lives in a single database. The memory pack portability story (RFC-0014) applies to DuckDB mode.

---

## 3. Core API Reference

### MemoryEngine

The single entry point for all operations. Always use the async `create()` factory.

#### `MemoryEngine.create()`

```python
@classmethod
async def create(cls, config: PRMEConfig | None = None) -> MemoryEngine
```

Factory method. Dispatches to DuckDB or PostgreSQL based on `config.backend`:
- **DuckDB** (default): Opens DuckDB file, initializes schema, USearch index, Tantivy index, serialized write queue.
- **PostgreSQL** (`database_url` set): Creates asyncpg pool, initializes PostgreSQL schema (pgvector + tsvector), uses `NoOpWriteQueue` passthrough.

- `config` — Optional `PRMEConfig`. Defaults to `PRMEConfig()` (loads from env vars / `.env`).
- **Returns:** Initialized `MemoryEngine`.

---

#### `engine.store()`

```python
async def store(
    self,
    content: str,
    *,
    user_id: str,
    session_id: str | None = None,
    role: str = "user",
    node_type: NodeType = NodeType.NOTE,
    scope: Scope = Scope.PERSONAL,
    metadata: dict | None = None,
    confidence: float | None = None,
    epistemic_type: EpistemicType | None = None,
    source_type: SourceType | None = None,
) -> str
```

Store content across all four backends in one call. No LLM needed.

**Auto-propagation:** Event → GraphNode → VectorIndex → LexicalIndex.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `content` | `str` | required | Text content to store |
| `user_id` | `str` | required | Owner user ID (all queries scoped to this) |
| `session_id` | `str \| None` | `None` | Optional session identifier |
| `role` | `str` | `"user"` | `"user"`, `"assistant"`, or `"system"` |
| `node_type` | `NodeType` | `NodeType.NOTE` | Type of memory node |
| `scope` | `Scope` | `Scope.PERSONAL` | Memory scope |
| `metadata` | `dict \| None` | `None` | Optional structured metadata |
| `confidence` | `float \| None` | `None` | Confidence 0.0-1.0. If None, derived from confidence matrix |
| `epistemic_type` | `EpistemicType \| None` | `None` | If None, inferred from node_type |
| `source_type` | `SourceType \| None` | `None` | If None, inferred from node_type + role |

**Returns:** `str` — UUID of the created event (source of truth ID).

---

#### `engine.ingest()`

```python
async def ingest(
    self,
    content: str,
    *,
    user_id: str,
    role: str = "user",
    session_id: str | None = None,
    metadata: dict | None = None,
    wait_for_extraction: bool = False,
    scope: Scope = Scope.PERSONAL,
) -> str
```

Ingest with LLM-powered extraction. Two-phase pipeline:
- **Phase 1 (immediate):** Persist event + index in lexical store.
- **Phase 2 (background):** LLM extracts entities, facts, relationships → materialized into graph/vector/lexical.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `content` | `str` | required | Message text to ingest |
| `user_id` | `str` | required | Owner user ID |
| `role` | `str` | `"user"` | Message role |
| `session_id` | `str \| None` | `None` | Optional session ID |
| `metadata` | `dict \| None` | `None` | Optional metadata |
| `wait_for_extraction` | `bool` | `False` | If True, block until extraction completes |
| `scope` | `Scope` | `Scope.PERSONAL` | Memory scope |

**Returns:** `str` — UUID of the persisted event. Falls back to `store()` if no pipeline configured.

---

#### `engine.ingest_batch()`

```python
async def ingest_batch(
    self,
    messages: list[dict],
    *,
    user_id: str,
    session_id: str | None = None,
    wait_for_extraction: bool = False,
    scope: Scope = Scope.PERSONAL,
) -> list[str]
```

Ingest multiple messages sequentially (preserves conversation order). Each dict must have `"content"` and `"role"` keys, with optional `"metadata"`.

**Returns:** `list[str]` — Event IDs, one per message.

---

#### `engine.retrieve()`

```python
async def retrieve(
    self,
    query: str,
    *,
    user_id: str,
    scope: Scope | list[Scope] | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    token_budget: int | None = None,
    weights: ScoringWeights | None = None,
    min_fidelity: RepresentationLevel | None = None,
    include_cross_scope: bool = True,
) -> RetrievalResponse
```

Hybrid retrieval through the 6-stage pipeline.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | required | Natural language query |
| `user_id` | `str` | required | User ID for scoping |
| `scope` | `Scope \| list[Scope] \| None` | `None` | Scope filter. None = all scopes |
| `time_from` | `datetime \| None` | `None` | Start of temporal window |
| `time_to` | `datetime \| None` | `None` | End of temporal window |
| `token_budget` | `int \| None` | `None` | Override default token budget (default: 4096) |
| `weights` | `ScoringWeights \| None` | `None` | Override scoring weights |
| `min_fidelity` | `RepresentationLevel \| None` | `None` | Override minimum representation level |
| `include_cross_scope` | `bool` | `True` | Include cross-scope hints when scope is filtered |

**Returns:** `RetrievalResponse` with bundle, scored results, metadata, score traces.

> **Note:** The underlying `RetrievalPipeline` supports a `retrieval_mode` parameter (`DEFAULT` or `EXPLICIT`) that controls epistemic filtering. This is not currently exposed through `MemoryEngine.retrieve()` — it always uses `DEFAULT` mode (excludes HYPOTHETICAL and DEPRECATED). Access the pipeline directly if you need `EXPLICIT` mode.

---

#### `engine.get_node()`

```python
async def get_node(
    self,
    node_id: str,
    *,
    include_superseded: bool = False,
) -> MemoryNode | None
```

Retrieve a node by ID. Returns `None` if not found or not visible (superseded/archived, unless `include_superseded=True`).

---

#### `engine.query_nodes()`

```python
async def query_nodes(self, **kwargs) -> list[MemoryNode]
```

Query nodes with flexible filters. Defaults to active lifecycle states (tentative + stable). Accepts keyword arguments: `user_id`, `node_type`, `scope`, `lifecycle_state`, `limit`, `offset`.

---

#### `engine.get_event()` / `engine.get_events()`

```python
async def get_event(self, event_id: str) -> Event | None
async def get_events(self, user_id: str, **kwargs) -> list[Event]
```

Retrieve events by ID or by user. `get_events()` accepts `session_id`, `limit`, `offset`.

---

#### Lifecycle Transitions

```python
async def promote(self, node_id: str) -> None        # TENTATIVE → STABLE
async def supersede(
    self,
    old_node_id: str,
    new_node_id: str,
    *,
    evidence_id: str | None = None,
) -> None                                              # → SUPERSEDED
async def archive(self, node_id: str) -> None          # → ARCHIVED (terminal)
```

All raise `ValueError` if the transition is invalid per the lifecycle state machine.

---

#### `engine.close()`

```python
async def close(self) -> None
```

Shuts down ingestion pipeline, drains write queue, saves vector index, closes lexical index, and closes the DuckDB connection or PostgreSQL pool. **Always call this** (use `try/finally`).

---

## 4. Data Models

All models are Pydantic `BaseModel` subclasses. Import from `prme.models`.

### MemoryObject (base class)

```python
from prme.models import MemoryObject

class MemoryObject(BaseModel):
    id: UUID                    # default: uuid4()
    user_id: str                # required — owner user ID
    session_id: str | None      # default: None
    scope: Scope                # default: Scope.PERSONAL
    created_at: datetime        # default: now(UTC)
    updated_at: datetime        # default: now(UTC)
```

### Event

Immutable event in the append-only log. **Frozen** (all fields read-only after creation).

```python
from prme.models import Event

class Event(MemoryObject):
    # Frozen (immutable)
    timestamp: datetime         # default: now(UTC)
    role: str                   # required — "user", "assistant", or "system"
    content: str                # required — event content text
    content_hash: str           # auto-computed SHA-256 of content
    metadata: dict | None       # default: None
```

### MemoryNode

A typed node in the memory graph.

```python
from prme.models import MemoryNode

class MemoryNode(MemoryObject):
    node_type: NodeType              # required
    content: str                     # required
    metadata: dict | None            # default: None
    confidence: float                # default: 0.5, range [0.0, 1.0]
    salience: float                  # default: 0.5, range [0.0, 1.0]
    epistemic_type: EpistemicType    # default: EpistemicType.ASSERTED
    source_type: SourceType          # default: SourceType.USER_STATED
    lifecycle_state: LifecycleState  # default: LifecycleState.TENTATIVE
    valid_from: datetime             # default: now(UTC)
    valid_to: datetime | None        # default: None (still valid)
    superseded_by: UUID | None       # default: None
    evidence_refs: list[UUID]        # default: [] — event IDs as evidence
```

### MemoryEdge

A typed edge connecting two nodes.

```python
from prme.models import MemoryEdge

class MemoryEdge(BaseModel):
    id: UUID                         # default: uuid4()
    source_id: UUID                  # required
    target_id: UUID                  # required
    edge_type: EdgeType              # required
    user_id: str                     # required
    confidence: float                # default: 0.5, range [0.0, 1.0]
    valid_from: datetime             # default: now(UTC)
    valid_to: datetime | None        # default: None
    provenance_event_id: UUID | None # default: None
    metadata: dict | None            # default: None
    created_at: datetime             # default: now(UTC)
```

---

## 5. Type Reference

All enums are `str, Enum` subclasses. Import from `prme.types`.

### NodeType

Eight node types for the memory graph:

| Value | Description |
|---|---|
| `"entity"` | Named entity (person, org, tool) |
| `"event"` | Conversational event |
| `"fact"` | Factual assertion |
| `"decision"` | Decision record |
| `"preference"` | User preference |
| `"task"` | Active task |
| `"summary"` | Summarization output |
| `"note"` | Generic catch-all (default for `store()`) |

### EdgeType

Nine relationship types:

| Value | Description |
|---|---|
| `"relates_to"` | General relationship |
| `"supersedes"` | New fact replaces old |
| `"derived_from"` | Derivation provenance |
| `"mentions"` | Entity mention |
| `"part_of"` | Part-whole relationship |
| `"caused_by"` | Causal link |
| `"supports"` | Supporting evidence |
| `"contradicts"` | Contradiction (triggers CONTESTED state) |
| `"has_fact"` | Entity → Fact link |

### Scope

Six namespace isolation levels:

| Value | Description |
|---|---|
| `"personal"` | Single user. Highest trust. |
| `"project"` | Shared across actors on a common goal |
| `"organisation"` | Cross-project org-wide facts |
| `"agent"` | Private to a specific AI agent |
| `"system"` | System-generated (summaries, organizer) |
| `"sandbox"` | Temporary isolated scope for testing |

### LifecycleState

Six states with forward-only transitions:

| Value | Description |
|---|---|
| `"tentative"` | Initial state for all new nodes |
| `"stable"` | Promoted after verification |
| `"contested"` | Unresolved contradiction detected |
| `"superseded"` | Replaced by a newer node |
| `"deprecated"` | Confirmed incorrect |
| `"archived"` | Terminal state |

**State machine transitions:**

```
TENTATIVE ──► STABLE ──► SUPERSEDED ──► ARCHIVED
    │            │                          ▲
    │            └──► CONTESTED             │
    │                    │ └──► STABLE      │
    │                    └──► DEPRECATED ───┘
    └──► SUPERSEDED / CONTESTED / ARCHIVED
```

**Valid transitions:**
- TENTATIVE → STABLE, SUPERSEDED, CONTESTED, ARCHIVED
- STABLE → SUPERSEDED, CONTESTED, ARCHIVED
- CONTESTED → STABLE, DEPRECATED, ARCHIVED
- SUPERSEDED → ARCHIVED
- DEPRECATED → ARCHIVED
- ARCHIVED → (terminal, no transitions)

### EpistemicType

Seven epistemic classifications:

| Value | Weight | Description |
|---|---|---|
| `"observed"` | 1.0 | Directly observed |
| `"asserted"` | 0.9 | User stated |
| `"inferred"` | 0.7 | System inferred |
| `"hypothetical"` | 0.3 | Speculative (excluded in DEFAULT retrieval) |
| `"conditional"` | 0.5 | Conditionally true |
| `"deprecated"` | 0.1 | Confirmed wrong (excluded in DEFAULT retrieval). **Not assignable at creation** — lifecycle transition only. |
| `"unverified"` | 0.5 | Not yet verified |

### SourceType

Five source provenance types:

| Value | Description |
|---|---|
| `"user_stated"` | User explicitly stated |
| `"user_demonstrated"` | Inferred from user behavior |
| `"system_inferred"` | System/LLM inferred |
| `"external_document"` | From external documents |
| `"tool_output"` | From tool/API output |

### QueryIntent

| Value | Description |
|---|---|
| `"semantic"` | Meaning-based similarity search |
| `"factual"` | Looking for specific facts |
| `"entity_lookup"` | Looking up a named entity |
| `"temporal"` | Time-scoped query |
| `"relational"` | Relationship-based query |

### RetrievalMode

| Value | Description |
|---|---|
| `"default"` | Excludes HYPOTHETICAL and DEPRECATED |
| `"explicit"` | Includes everything |

### RepresentationLevel

Ordered by token cost (lowest → highest):

| Value | Description |
|---|---|
| `"reference"` | ID + type only |
| `"key_value"` | Structured key-value pairs |
| `"structured"` | Full structured representation |
| `"prose"` | Natural language |
| `"full"` | Complete original content |

---

## 6. Configuration

All configuration uses pydantic-settings. Env vars use `PRME_` prefix with `__` delimiter for nesting.

### PRMEConfig

```python
from prme.config import PRMEConfig

config = PRMEConfig(
    # PostgreSQL backend (when set, all storage uses PostgreSQL)
    database_url=None,                      # PRME_DATABASE_URL

    # Storage paths (DuckDB mode only, ignored when database_url is set)
    db_path="./memory.duckdb",              # PRME_DB_PATH
    vector_path="./vectors.usearch",        # PRME_VECTOR_PATH
    lexical_path="./lexical_index",         # PRME_LEXICAL_PATH

    # Nested configs (see below)
    embedding=EmbeddingConfig(...),
    extraction=ExtractionConfig(...),

    # Write queue
    write_queue_size=1000,                  # PRME_WRITE_QUEUE_SIZE

    # Retrieval scoring and packing
    scoring=ScoringWeights(...),
    packing=PackingConfig(...),

    # Epistemic parameters
    epistemic_weights={                     # PRME_EPISTEMIC_WEIGHTS
        "observed": 1.0,
        "asserted": 0.9,
        "inferred": 0.7,
        "hypothetical": 0.3,
        "conditional": 0.5,
        "deprecated": 0.1,
        "unverified": 0.5,
    },
    unverified_confidence_threshold=0.30,   # PRME_UNVERIFIED_CONFIDENCE_THRESHOLD

    # Confidence matrix overrides
    confidence_overrides={},                # Keys: "epistemic:source" e.g. "observed:user_stated"
)
```

**Environment variable prefix:** `PRME_`
**Nested delimiter:** `__` (e.g., `PRME_EMBEDDING__DIMENSION=384`)

### EmbeddingConfig

```python
from prme.config import EmbeddingConfig

EmbeddingConfig(
    provider="fastembed",               # PRME_EMBEDDING_PROVIDER
    model_name="BAAI/bge-small-en-v1.5", # PRME_EMBEDDING_MODEL_NAME
    dimension=384,                       # PRME_EMBEDDING_DIMENSION
    api_key=None,                        # PRME_EMBEDDING_API_KEY
)
```

Supported providers: `"fastembed"` (local, default), `"openai"` (requires API key).

### ExtractionConfig

```python
from prme.config import ExtractionConfig

ExtractionConfig(
    provider="openai",                   # PRME_EXTRACTION_PROVIDER
    model="gpt-4o-mini",                 # PRME_EXTRACTION_MODEL
    max_retries=3,                       # PRME_EXTRACTION_MAX_RETRIES
    timeout=30.0,                        # PRME_EXTRACTION_TIMEOUT
)
```

Supported providers: `"openai"`, `"anthropic"`, `"ollama"`.

### ScoringWeights

Immutable (frozen). The six additive weights must sum to 1.0. Epistemic weight is multiplicative; paths weight is a tiebreaker.

```python
from prme.retrieval.config import ScoringWeights

ScoringWeights(
    w_semantic=0.30,       # Semantic similarity
    w_lexical=0.15,        # Lexical relevance
    w_graph=0.20,          # Graph proximity
    w_recency=0.10,        # Recency factor
    w_salience=0.10,       # Salience
    w_confidence=0.15,     # Confidence
    w_epistemic=0.05,      # Epistemic (multiplicative)
    w_paths=0.00,          # Multi-path corroboration (tiebreaker)
    recency_lambda=0.02,   # Decay rate: exp(-lambda * days)
)
# .version_id → deterministic SHA-256 hash (12 chars) for traceability
```

### PackingConfig

```python
from prme.retrieval.config import PackingConfig

PackingConfig(
    token_budget=4096,               # Context budget in tokens
    min_fidelity=RepresentationLevel.REFERENCE,  # Minimum fidelity
    overhead_tokens=100,             # Reserved for JSON envelope
    chars_per_token=4.2,             # Token estimation ratio
    graph_max_candidates=50,         # Max from graph traversal
    vector_k=50,                     # Max from vector search
    lexical_k=50,                    # Max from lexical search
    graph_max_hops=3,                # Max graph hops (1-3)
    cross_scope_top_n=5,             # Top-N cross-scope hints
    cross_scope_token_budget=512,    # Separate budget for hints
)
```

---

## 7. Retrieval Pipeline Deep Dive

The 6-stage pipeline transforms a natural language query into a token-budgeted `MemoryBundle`.

### Stage 1: Query Analysis

**Input:** Raw query string, optional temporal overrides.
**Output:** `QueryAnalysis` with intent, entities, temporal signals, retrieval mode.

```python
class QueryAnalysis(BaseModel):
    query: str
    intent: QueryIntent                   # semantic, factual, entity_lookup, temporal, relational
    entities: list[str]                   # extracted entity names
    temporal_signals: list[dict]          # [{type, value, resolved}, ...]
    time_from: datetime | None
    time_to: datetime | None
    retrieval_mode: RetrievalMode         # default or explicit
    request_id: UUID                      # unique per retrieval
```

### Stages 2-3: Candidate Generation + Merging

**Input:** `QueryAnalysis`, user_id, scope, temporal window, packing config.
**Output:** Deduplicated `list[RetrievalCandidate]`, per-backend candidate counts.

Runs **three backends in parallel:**
1. **Graph neighborhood** — entities from query analysis → graph traversal up to `graph_max_hops` hops
2. **Vector similarity** — ANN search over embeddings, top-`vector_k`
3. **Lexical search** — BM25-style full-text search, top-`lexical_k`

Candidates are deduplicated by `node_id`. Each candidate tracks which backends produced it (`paths` field) and multi-path count.

### Stage 4: Epistemic Filtering

**Input:** Merged candidates, retrieval mode.
**Output:** Filtered candidates, excluded candidates list.

In **DEFAULT** mode:
- Excludes nodes with `epistemic_type` of `HYPOTHETICAL` or `DEPRECATED`
- Filters `UNVERIFIED` nodes below `unverified_confidence_threshold` (default: 0.30)

In **EXPLICIT** mode: no filtering.

### Stage 5: Scoring + Ranking

**Input:** Filtered candidates, scoring weights, epistemic weights.
**Output:** Scored + sorted candidates, score traces.

**Composite score formula:**

```
additive = (
    w_semantic   * semantic_score   +
    w_lexical    * lexical_score    +
    w_graph      * graph_proximity  +
    w_recency    * recency_factor   +
    w_salience   * salience         +
    w_confidence * confidence
)

composite = additive * epistemic_weight
```

The epistemic weight is a **direct multiplier** on the additive sum (not `1 + w_epistemic * ...`). The `w_paths` / `path_score` value is used only as a **sort tiebreaker**, not in the composite score itself.

Where:
- `recency_factor = exp(-recency_lambda * days_since_update)` — uses `updated_at` (falls back to `created_at`)
- `graph_proximity`: 1-hop = 1.0, 2-hop = 0.7, 3-hop = 0.4
- `epistemic_weight`: looked up from `epistemic_weights` dict by `EpistemicType`
- `path_score`: number of backends that independently found this candidate (tiebreaker only)

Candidates sorted by `(-composite_score, -path_score)` for deterministic ordering.

Every candidate gets a **ScoreTrace** for full explainability:

```python
class ScoreTrace(BaseModel):
    semantic_similarity: float
    lexical_relevance: float
    graph_proximity: float
    recency_factor: float
    salience: float
    confidence: float
    epistemic_weight: float
    path_score: float
    composite_score: float
```

### Stage 5.5: Conflict Metadata

CONTESTED candidates are annotated with `conflict_flag=True` and `contradicts_id` pointing to the counterpart node. Counterparts are **not** auto-injected — only included if independently relevant.

### Cross-Scope Hints

When `scope` is filtered and `include_cross_scope=True`, a secondary vector+lexical pass runs **without** scope restriction. Results from outside the primary scope are scored and the top-N (default: 5) appear as `cross_scope_hints` in the response — never mixed into primary results.

### Stage 6: Context Packing

**Input:** Scored candidates, packing config.
**Output:** `MemoryBundle`.

Greedy bin-packing within the token budget:
1. Estimate token cost per candidate using `chars_per_token` ratio
2. Reserve `overhead_tokens` for JSON envelope
3. Pack candidates in score order, assigning representation levels:
   - High-budget: `FULL` or `PROSE`
   - Mid-budget: `STRUCTURED` or `KEY_VALUE`
   - Low-budget: `REFERENCE`
4. Group packed candidates into sections: `entity_snapshots`, `stable_facts`, `recent_decisions`, `active_tasks`, `provenance_refs`, `contested_claims`

```python
class MemoryBundle(BaseModel):
    sections: dict[str, list[RetrievalCandidate]]
    included_count: int
    excluded_ids: list[UUID]
    tokens_used: int
    token_budget: int
    budget_remaining: int
    min_fidelity: RepresentationLevel
```

### RetrievalResponse (full return type)

```python
class RetrievalResponse(BaseModel):
    bundle: MemoryBundle                          # Context-packed output
    results: list[RetrievalCandidate]             # Scored results (pre-packing)
    metadata: RetrievalMetadata                   # Timing, counts, config version
    score_traces: list[ScoreTrace]                # Always-on, one per result
    filter_metadata: FilterMetadata | None        # Active filters for debugging
    cross_scope_hints: list[RetrievalCandidate]   # Results from outside scope
```

```python
class RetrievalMetadata(BaseModel):
    request_id: UUID
    candidates_generated: dict[str, int]     # Per-backend counts
    candidates_filtered: int
    candidates_included: int
    scoring_config_version: str              # ScoringWeights.version_id
    timing_ms: float
    backends_used: list[str]
    embedding_mismatch: bool
```

---

## 8. Multi-User / Multi-Tenant

### user_id Isolation

Every `store()`, `ingest()`, and `retrieve()` call requires a `user_id`. All backend queries are scoped to this user — a user's data is never returned for another user's queries.

```python
# User A stores memories
await engine.store("I like Python", user_id="user-a")

# User B cannot see User A's memories
response = await engine.retrieve("What languages?", user_id="user-b")
# response.results → empty
```

### Scope Layering

Scopes provide cross-cutting isolation within a user's data:

```python
# Store at different scopes
await engine.store("My preference", user_id="alice", scope=Scope.PERSONAL)
await engine.store("Team decision", user_id="alice", scope=Scope.PROJECT)

# Retrieve only personal memories
response = await engine.retrieve("...", user_id="alice", scope=Scope.PERSONAL)

# Retrieve from multiple scopes
response = await engine.retrieve("...", user_id="alice", scope=[Scope.PERSONAL, Scope.PROJECT])

# Retrieve all scopes (default)
response = await engine.retrieve("...", user_id="alice")  # scope=None
```

Cross-scope hints automatically surface relevant results from other scopes when filtering by scope, appearing in `response.cross_scope_hints`.

---

## 9. Working Examples

### Basic Store and Retrieve

```python
engine = await MemoryEngine.create()

# Store different types
await engine.store("Alice is a senior engineer", user_id="u1", node_type=NodeType.FACT)
await engine.store("Use Python for backends", user_id="u1", node_type=NodeType.DECISION)
await engine.store("Prefers dark mode", user_id="u1", node_type=NodeType.PREFERENCE)

# Retrieve
response = await engine.retrieve("What do we know about Alice?", user_id="u1")
for r in response.results:
    print(f"[{r.node.node_type.value}] {r.node.content} (score: {r.composite_score:.3f})")

await engine.close()
```

### Chat App Integration Pattern

Core pattern: store every message, retrieve before each LLM call, inject memories into system prompt.

```python
from openai import AsyncOpenAI
from prme import MemoryEngine, PRMEConfig, NodeType, Scope

client = AsyncOpenAI()
engine = await MemoryEngine.create(PRMEConfig(
    db_path="./chat_memory.duckdb",
    vector_path="./chat_vectors.usearch",
    lexical_path="./chat_lexical",
))

async def handle_message(user_input: str, user_id: str, session_id: str) -> str:
    # 1. Store the user message
    await engine.store(
        user_input,
        user_id=user_id,
        session_id=session_id,
        role="user",
        node_type=NodeType.EVENT,
    )

    # 2. Retrieve relevant memories
    response = await engine.retrieve(user_input, user_id=user_id)

    # 3. Format memories for the system prompt
    memory_lines = []
    for r in response.results[:10]:
        memory_lines.append(f"- [{r.node.node_type.value}] {r.node.content}")
    memory_block = "\n".join(memory_lines)

    # 4. Call LLM with memories injected
    system_prompt = f"""You are a helpful assistant with persistent memory.

## Relevant memories
{memory_block}
"""
    completion = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
    )
    reply = completion.choices[0].message.content

    # 5. Store the assistant response
    await engine.store(
        reply,
        user_id=user_id,
        session_id=session_id,
        role="assistant",
        node_type=NodeType.EVENT,
    )

    return reply
```

### LLM Extraction with ingest()

```python
# Requires OPENAI_API_KEY (or another extraction provider)
engine = await MemoryEngine.create(PRMEConfig(
    extraction=ExtractionConfig(provider="openai", model="gpt-4o-mini"),
))

# Ingest a conversation — LLM extracts entities, facts, relationships
event_ids = await engine.ingest_batch(
    [
        {"role": "user", "content": "I just started using Neovim and I love it."},
        {"role": "assistant", "content": "Neovim has excellent plugin support!"},
        {"role": "user", "content": "Sarah recommended it. She has used it for years."},
    ],
    user_id="alice",
    session_id="session-1",
    wait_for_extraction=True,  # block until extraction done
    scope=Scope.PROJECT,
)

# Now retrieve — extraction created entities (Alice, Sarah, Neovim) and facts
response = await engine.retrieve("What tools does Alice use?", user_id="alice")
```

### Custom Scoring Weights

```python
from prme.retrieval.config import ScoringWeights

# Emphasize semantic similarity and recency over graph proximity
custom_weights = ScoringWeights(
    w_semantic=0.40,
    w_lexical=0.10,
    w_graph=0.10,
    w_recency=0.20,
    w_salience=0.05,
    w_confidence=0.15,
)

response = await engine.retrieve(
    "What happened recently?",
    user_id="alice",
    weights=custom_weights,
    token_budget=2048,  # smaller context window
)

# Check the scoring config version used
print(response.metadata.scoring_config_version)
```

### Lifecycle Management

```python
# Store a fact (starts TENTATIVE)
eid = await engine.store("Project uses MySQL", user_id="u1", node_type=NodeType.FACT)

# Find the node
nodes = await engine.query_nodes(user_id="u1", node_type=NodeType.FACT)
old = next(n for n in nodes if "MySQL" in n.content)

# Promote to STABLE after verification
await engine.promote(str(old.id))

# Store corrected fact and supersede
await engine.store("Project uses PostgreSQL, not MySQL", user_id="u1", node_type=NodeType.FACT)
nodes = await engine.query_nodes(user_id="u1", node_type=NodeType.FACT)
new = next(n for n in nodes if "PostgreSQL" in n.content)

await engine.supersede(str(old.id), str(new.id))

# Old node is now SUPERSEDED — excluded from default retrieval
# New node is TENTATIVE — included in retrieval, can be promoted
```

---

## 10. PostgreSQL Backend

PRME supports PostgreSQL as an alternative storage backend. When `database_url` is set, all four storage layers (events, graph, vector, lexical) use a single PostgreSQL instance instead of file-based DuckDB/USearch/Tantivy.

### Quick Start (PostgreSQL)

```python
import asyncio
from prme import MemoryEngine, PRMEConfig, NodeType, Scope


async def main():
    config = PRMEConfig(
        database_url="postgresql://user:pass@localhost:5432/myapp",
    )

    engine = await MemoryEngine.create(config)

    try:
        await engine.store(
            "Alice prefers dark mode",
            user_id="alice",
            node_type=NodeType.PREFERENCE,
            scope=Scope.PERSONAL,
        )

        response = await engine.retrieve("What does Alice prefer?", user_id="alice")
        for r in response.results:
            print(f"[{r.composite_score:.3f}] {r.node.content}")
    finally:
        await engine.close()


asyncio.run(main())
```

### Installation

PostgreSQL support requires optional dependencies:

```bash
pip install prme[postgres]
```

This installs `asyncpg` (async PostgreSQL driver) and `pgvector` (vector similarity extension support).

**PostgreSQL requirements:**
- PostgreSQL 14+ with the [`pgvector`](https://github.com/pgvector/pgvector) extension installed
- The `pgcrypto` extension (ships with PostgreSQL by default)

### Configuration

Set `database_url` via config or environment variable:

```python
# Via config
config = PRMEConfig(database_url="postgresql://user:pass@host:5432/dbname")

# Via environment variable
# export PRME_DATABASE_URL=postgresql://user:pass@host:5432/dbname
config = PRMEConfig()  # auto-reads PRME_DATABASE_URL
```

When `database_url` is set, `config.backend` returns `"postgres"` and all file-path settings (`db_path`, `vector_path`, `lexical_path`) are ignored.

### Schema

`MemoryEngine.create()` automatically initializes the PostgreSQL schema on first use (all DDL is idempotent with `IF NOT EXISTS`). Tables created:

| Table | Purpose |
|---|---|
| `events` | Append-only event log (mirrors DuckDB `events` table) |
| `nodes` | Memory graph nodes with `embedding vector(N)` column and `content_tsv tsvector GENERATED` column |
| `edges` | Graph edges with foreign key constraints and `ON DELETE CASCADE` |
| `operations` | Operation log for retrieval/ingestion tracking |
| `lexical_documents` | Non-node content indexed for full-text search (events, summaries) |

**Indexes:**
- HNSW index on `nodes.embedding` using `vector_cosine_ops` (pgvector)
- GIN indexes on `nodes.content_tsv` and `lexical_documents.content_tsv`
- B-tree indexes on `user_id`, `node_type`, `scope`, `lifecycle_state`, timestamps

### How It Differs from DuckDB Mode

| Aspect | DuckDB mode | PostgreSQL mode |
|---|---|---|
| **Concurrency** | Single-writer via `WriteQueue` | Multi-writer via connection pool (`NoOpWriteQueue` passthrough) |
| **Vector search** | USearch HNSW + separate metadata table | pgvector HNSW on `nodes.embedding` column directly |
| **Lexical search** | Tantivy (BM25) | `tsvector` / `tsquery` with `ts_rank_cd()` scoring |
| **Text indexing** | Explicit `index()` call for all content | `GENERATED ALWAYS AS` column auto-maintains tsvector for nodes; explicit for non-node content |
| **Graph traversal** | DuckDB recursive CTEs | PostgreSQL recursive CTEs with native `CYCLE` detection |
| **Persistence** | Manual `save()` calls for vector index | Automatic (PostgreSQL WAL) |
| **Portability** | Memory pack (copyable files) | Single database (use `pg_dump` for export) |
| **Async model** | `asyncio.to_thread()` wrappers around sync DuckDB | Natively async end-to-end (asyncpg) |

### API Compatibility

The `MemoryEngine` API is identical in both modes. All methods — `store()`, `ingest()`, `retrieve()`, `promote()`, `supersede()`, `archive()`, `close()`, etc. — work the same way. No code changes needed when switching backends.

### SST / Cloud Integration

For SST (or similar IaC) apps that provision a PostgreSQL database:

```python
import os
from prme import MemoryEngine, PRMEConfig

# SST injects the database URL at runtime
config = PRMEConfig(
    database_url=os.environ["DATABASE_URL"],
)

engine = await MemoryEngine.create(config)
```

### Connection Pool

PostgreSQL mode uses an `asyncpg` connection pool (default: `min_size=2`, `max_size=10`). The pool is created by `MemoryEngine.create()` and closed by `engine.close()`.

### Testing Against PostgreSQL

PostgreSQL tests require the `PRME_TEST_DATABASE_URL` environment variable:

```bash
export PRME_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/prme_test
pytest tests/test_pg_*.py -v
```

Tests are automatically skipped when `PRME_TEST_DATABASE_URL` is not set. All existing DuckDB tests continue to pass without any PostgreSQL instance.

---

## 11. RFC Summary Table

| RFC | Title | Tier | One-line Summary |
|---|---|---|---|
| RFC-0000 | Suite Overview and Design Philosophy | 0 | Design principles, tier system, `[HYPOTHESIS]` tagging convention |
| RFC-0001 | Core Data Model and Terminology | 0 | Node types, edge types, memory objects, temporal validity, evidence refs |
| RFC-0002 | Event Store and Append-Only Log | 1 | Immutable event log (DuckDB), content hashing, replay capability |
| RFC-0003 | Epistemic State Model | 1 | Epistemic types, confidence matrix, source provenance, filtering rules |
| RFC-0004 | Namespace and Scope Isolation | 1 | Six scope levels, cross-scope hints, sandbox hard-delete |
| RFC-0005 | Hybrid Retrieval Pipeline | 2 | 6-stage pipeline, 8-input composite scoring, deterministic ranking |
| RFC-0006 | Retrieval Cost and Context Efficiency | 2 | Token budgeting, representation levels, greedy bin-packing |
| RFC-0007 | Decay and Forgetting Model | 3 | Salience decay, TTL enforcement, policy-based archival |
| RFC-0008 | Confidence Evolution and Reinforcement | 3 | Confidence updates from retrieval feedback and corroboration |
| RFC-0009 | Memory Usage Feedback Loop | 3 | Track memory usage in retrieval to influence future ranking |
| RFC-0010 | Temporal Pattern Awareness | 4 | Detect recurring patterns and temporal clusters in memory |
| RFC-0011 | Multi-Agent Memory Semantics | 4 | Domain-scoped agent trust, shared memory coordination |
| RFC-0012 | Memory Branching and Simulation | 4 | Branch memory state for hypothetical exploration, merge back |
| RFC-0013 | Intent and Goal Memory | 4 | Track user goals and active intents (replaced emotional tracking) |
| RFC-0014 | Portability, Sync, and Federation | 4 | Memory pack export, sync protocol, multi-device federation |

### Conformance Tiers

| Tier | Required RFCs | Description |
|---|---|---|
| Tier 0 | 0000, 0001 | Core data model only |
| Tier 1 | + 0002, 0003, 0004 | Persistent, epistemic, namespace-isolated storage |
| Tier 2 | + 0005, 0006 | Hybrid retrieval with context-efficient bundling |
| Tier 3 | + 0007, 0008, 0009 | Adaptive lifecycle: decay, confidence, feedback |
| Tier 4 | + any of 0010-0014 | Advanced capabilities (each independently optional) |

PRME's current implementation covers **Tiers 0-2** fully, with partial Tier 1 epistemic support (confidence matrix, epistemic filtering, supersedence detection).
