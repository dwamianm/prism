# PRME Integration Reference

> **Audience:** AI coding assistants and developers integrating PRME into applications.
> **Original reference:** 2026-03-02, with subsequent sections updated incrementally.
> **API coverage is partial.** See the [README](../README.md) for newer capabilities and focused guides.

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
    retrieval_content: str | None = None,
    value_bindings: list[MemoryValueBinding | dict] | None = None,
    session_id: str | None = None,
    role: str = "user",
    node_type: NodeType = NodeType.NOTE,
    scope: Scope = Scope.PERSONAL,
    metadata: dict | None = None,
    confidence: float | None = None,
    epistemic_type: EpistemicType | None = None,
    source_type: SourceType | None = None,
    event_time: datetime | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    ttl_days: int | None = ...,
) -> str
```

Store content across all four backends in one call. No LLM needed. By default,
the exact source text is also the searchable and model-facing memory. For large
agent traces, logs, or structured documents, pass compact
`retrieval_content`; `get_event()` still returns the exact source while the graph,
vector index, lexical index and context packer use the compact representation.
Use `value_bindings` when an exact source-backed presentation value differs from
the complete string accepted by a tool. The [value-binding guide](VALUE-BINDINGS.md)
defines visibility, resolution and audit behavior.

The event, complete initial node snapshot and repair job are saved atomically,
then the graph node and both indexes are written. Index failures leave the job
pending; `processing_status()` and `process_pending()` expose scoped inspection
and repair after restart. A graph creation failure raises `MaterializationError`
with the accepted `event_id`. Recovery preserves the original node values without
an LLM. Optional reinforcement, supersedence and QA pairing run afterward and
are outside this job's completion boundary.

Question/answer pairing is disabled by default. `enable_qa_pairing=True`
enables an experimental in-process heuristic that creates an extra merged node
for consecutive, differently-typed roles in one exact session and scope. It is
not recovered after restart, its graph/index writes are not one atomic durable
publication, and registered quality evaluations have not enabled it. Prefer
normal session-aware retrieval unless you are explicitly evaluating this
hypothesis.

When `enable_store_supersedence=True`, a newly formed flip-flop chain can apply
the existing bounded oscillation confidence penalty. The engine revalidates the
exact owner, scope, node snapshots, and `SUPERSEDES` edges under the backend
transaction, then commits the confidence update with one deterministic,
checksummed `PENALTY` record. Concurrent and restarted attempts reuse that
identity. This is a lexical heuristic behind the supersedence opt-in, not a
truth judgment or calibrated confidence model.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `content` | `str` | required | Exact source text retained in the immutable event log |
| `retrieval_content` | `str \| None` | `None` | Optional compact text to index, rank and place in model context |
| `value_bindings` | `list[MemoryValueBinding \| dict] \| None` | `None` | Exact presentation values paired with caller-supplied complete lookup forms |
| `user_id` | `str` | required | Owner user ID (all queries scoped to this) |
| `session_id` | `str \| None` | `None` | Optional session identifier |
| `role` | `str` | `"user"` | `"user"`, `"assistant"`, or `"system"` |
| `node_type` | `NodeType` | `NodeType.NOTE` | Type of memory node |
| `scope` | `Scope` | `Scope.PERSONAL` | Memory scope |
| `metadata` | `dict \| None` | `None` | Optional structured metadata |
| `confidence` | `float \| None` | `None` | Confidence 0.0-1.0. If None, derived from confidence matrix |
| `epistemic_type` | `EpistemicType \| None` | `None` | If None, inferred from node_type |
| `source_type` | `SourceType \| None` | `None` | If None, inferred from node_type + role; `role="tool"` selects `TOOL_OUTPUT` |
| `event_time` | `datetime \| None` | `None` | Timezone-aware source event time; remains separate from admission and validity |
| `valid_from` | `datetime \| None` | `None` | Timezone-aware inclusive validity start; omission uses admission time |
| `valid_to` | `datetime \| None` | `None` | Exclusive validity end; requires an explicit earlier `valid_from` |
| `ttl_days` | `int \| None` | `...` | Omit for configured default, pass `None` to disable, or set a nonnegative override |

**Returns:** `str` — UUID of the created event (source of truth ID).

Use `store_with_receipt()` when the next operation needs the created node ID:

```python
receipt = await engine.store_with_receipt(
    "The project uses PostgreSQL.",
    user_id="alice",
    node_type=NodeType.FACT,
    scope=Scope.PROJECT,
)
await engine.promote(str(receipt.node_id), user_id="alice")
```

The immutable source ID is `receipt.event_id`; lifecycle methods accept
`receipt.node_id`. `receipt.node` is the exact created node and
`receipt.processing_status` reports its durable materialization state. Resolution
follows the source event, so a concurrent write cannot be mistaken for this
node. Existing `store()` callers retain the event-ID return for compatibility.

For example, retain a complete tool trajectory without spending the retrieval
budget on raw SDK payloads:

```python
receipt = await engine.store_with_receipt(
    raw_trace_json,
    retrieval_content="Traveler: Alice\nFinal plan:\n...",
    user_id="alice",
    metadata={"record_kind": "agent_trace"},
)
source = await engine.get_event(str(receipt.event_id), user_id="alice")
assert source.content == raw_trace_json
assert receipt.node.content.startswith("Traveler: Alice")
```

The projection is caller-supplied data, not a generated summary or a claim that
the source supports it. It is durably journaled with the initial node and reused
exactly during restart recovery.

For raw imports that do not need typed-node overrides or model extraction, use
`ingest_fast_many(items, user_id=...)`. Each `FastIngestItem` carries `content`,
`role`, `session_id`, `scope`, `metadata`, and an optional timezone-aware
`event_time`. PRME validates and snapshots the complete list before I/O, then
admits every immutable event and materialization job in one transaction. It
returns event IDs in input order; an empty Python batch is a no-op. Run
`process_pending()` until `pending == 0` to build the deterministic raw NOTE and
both indexes. Pass a persisted UUID as `request_id` to make a lost-response retry
return the original event IDs. The identity is owner scoped and survives
restart; reusing it with different items raises an input conflict. HTTP `POST
/v1/ingest/fast` uses the UUID `Idempotency-Key` header, while MCP
`memory_ingest_fast_many` accepts `request_id`. Both expose the same
owner-scoped admission contract; their
empty request lists are rejected. MCP `memory_process_materializations` mirrors
the HTTP processing endpoint.

An explicit `EpistemicType.CONDITIONAL` requires
`metadata={"condition": "..."}`. New conditional memories always begin with
`condition_state="unknown"`; setting a resolved state during creation is
rejected so an evaluation cannot bypass its audit record.

Validity intervals use `[valid_from, valid_to)`. Python, HTTP `/v1/store`, and
MCP `memory_store` preserve the same fields. Timezone-free values, an end without
an explicit start, and empty or inverted intervals fail before the immutable
source is admitted. New extracted facts use their resolved source-effective time
as `valid_from`; source-grounded replacements close the previous interval in the
same derivation transaction when the stored interval can be closed safely.

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
- **Phase 1 (immediate):** Atomically persist the source event and its raw indexing job.
- **Phase 2 (background):** Extract and ground model output, save it in the operation log, then materialize the graph and indexes. Indexing retries reuse the saved output.

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
| `min_fidelity` | `RepresentationLevel \| None` | `None` | Override minimum representation level. `STRUCTURED`, `PROSE` or `FULL` keeps text-free `KEY_VALUE` and `REFERENCE` fallbacks, and blank records, out of the context |
| `include_cross_scope` | `bool` | `True` | Include cross-scope hints when scope is filtered |

**Returns:** `RetrievalResponse` with bundle, scored results, metadata, score traces.

`retrieval_mode` accepts `DEFAULT` or `EXPLICIT` across the async engine, sync
client, HTTP API, and MCP tool. `DEFAULT` excludes hypothetical and deprecated
claims; `EXPLICIT` includes them within the generated candidate pool.

---

#### `engine.record_answer_citations()`

```python
async def record_answer_citations(
    self,
    submission: AnswerCitationSubmission,
    *,
    user_id: str,
) -> AnswerCitationRecord
```

Append the memory citations for an answer created from a saved retrieval. The
submission includes the retrieval `request_id`, a caller answer reference,
zero or more cited node IDs, an optional answer SHA-256 digest, a collection
method, and a caller-generated citation UUID used for safe retries. Every cited
node must be content-bearing and included in the receipt's rendered context.

An empty citation tuple explicitly records that the answer reported no memory
citations. The record remains valid after graph changes or archival. It is
telemetry and does not modify memory state or ranking. Use
`get_answer_citations()` and `list_answer_citations()` to read owned records.

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

Query nodes with flexible filters. Defaults to active lifecycle states
(tentative, stable and contested). Filters include `user_id`, `node_type`, `scope`
or `scopes`, `session_ids`, `lifecycle_states`, `valid_at`, `min_confidence`,
`min_salience`, `content_contains_any`, `created_before`, `oldest_first` and
`limit`. Use the plural `lifecycle_states`; there is no `offset` argument.
Pass the owner explicitly in application queries.

---

#### `engine.get_event()` / `engine.get_events()`

```python
async def get_event(self, event_id: str, *, user_id: str | None = None) -> Event | None
async def get_events(self, user_id: str, **kwargs) -> list[Event]
async def get_extraction(self, event_id: str, *, user_id: str) -> ExtractionRecord | None
```

Retrieve events by ID or by user. `get_events()` accepts `session_id`, `limit`, `offset`.
`get_extraction()` reads saved grounded model output without inference or pending
processing. `None` means no owned record exists. A record includes the source
hash, provider/model, schema and grounding versions, and structured output; it
does not acknowledge graph completion or prove its claims. The synchronous
`MemoryClient` has the same method. HTTP and MCP expose the scoped source record
through `/v1/events/{event_id}/extraction` and `memory_get_extraction`.

---

#### `engine.consolidate_knowledge()`

```python
created = await engine.consolidate_knowledge(
    user_id="alice", scope=Scope.PROJECT,
    entity_names=["Aurora"], max_profile_tokens=1000,
)
```

Build inferred entity profiles from complete same-owner, same-scope source
excerpts. Each replacement commits atomically after index preparation. Source
changes or competing rebuilds raise `StaleProfileError`; embedding/storage
errors also propagate. `MemoryClient` provides the equivalent synchronous API.
See the [entity profile guide](ENTITY-PROFILES.md) for requirements and recovery.

---

#### Lifecycle Transitions

```python
async def promote(
    self, node_id: str, *, user_id: str | None = None,
    request_id: str | UUID | None = None,
) -> None                                           # TENTATIVE → STABLE
async def supersede(
    self,
    old_node_id: str,
    new_node_id: str,
    *,
    evidence_id: str | None = None,
    user_id: str | None = None,
    actor_id: str | None = None,
) -> None                                              # → SUPERSEDED
async def archive(
    self, node_id: str, *, user_id: str | None = None,
    request_id: str | UUID | None = None,
) -> None                                           # → ARCHIVED (terminal)
```

All raise `ValueError` if the transition is invalid per the lifecycle state
machine. Supersedence commits a deterministic edge and checksummed before/after
record with the state change. Repeating the same ordered nodes, evidence, and
actor is safe across restarts; changing an input after publication is rejected.
HTTP exposes `POST /v1/supersedences`, and MCP exposes `memory_supersede`.
Promotion and archival accept an optional UUID `request_id`; reuse it with the
same node, action, and actor after an ambiguous response. HTTP accepts that UUID
as `Idempotency-Key`, and the MCP lifecycle tools accept `request_id`. Reusing a
key with different inputs raises a conflict. Calls without a request ID retain
strict lifecycle errors and reject a repeated transition.

#### `engine.evaluate_condition()`

```python
updated = await engine.evaluate_condition(
    node_id,
    ConditionState.TRUE,
    user_id="alice",
    evidence_id=approval_event_id,
    request_id="9ee0440b-4ea4-48c5-87ed-c1f43546475b",
    evaluation_method=ConditionEvaluationMethod.TOOL,
    reason="Approval service confirmed",
)
```

This is an explicit recorded evaluation, not an automatic truth inference.
Mutation and a checksummed `EPISTEMIC_TRANSITION` record with the complete
before/after claim commit atomically. Evidence must belong to the same owner and
scope. Reusing `request_id` safely retries the same evaluation; changing its
inputs raises a conflict. The conditional epistemic type remains intact so a
changing condition can be evaluated again. `MemoryClient.evaluate_condition()`
provides the synchronous equivalent. HTTP uses
`PUT /v1/nodes/{node_id}/condition` with an optional UUID `Idempotency-Key`
header; MCP exposes `memory_evaluate_condition`.

#### `engine.get_provenance()`

```python
history = await engine.get_provenance(
    node_id, user_id="alice", operation_limit=100,
)
next_page = await engine.get_provenance(
    node_id, user_id="alice",
    operation_cursor=history.next_operation_cursor,
)
```

Returns the current node, owned source events, references whose source event is
missing or outside the node scope, same-owner/same-scope contradiction edges,
and a chronological page of raw operation records. The page limit is 1–1000;
the opaque cursor is stable for append-only operation history. Missing and
foreign nodes return `None`. `MemoryClient.get_provenance()` is the synchronous
equivalent. HTTP exposes `GET /v1/nodes/{node_id}/provenance`; MCP exposes
`memory_get_provenance`.

#### `engine.contradict()` and `engine.resolve_contradiction()`

```python
first, second = await engine.contradict(
    first_claim_id,
    second_claim_id,
    user_id="alice",
    actor_id="reviewer",
    evidence_id=review_event_id,
)
winner, loser = await engine.resolve_contradiction(
    second_claim_id,
    first_claim_id,
    user_id="alice",
    resolver_actor_id="reviewer",
    evidence_id=review_event_id,
)
```

Each operation commits claim states, the contradiction edge, and its audit
record atomically. Claims and optional evidence must share one owner and scope.
An exact retry with the same ordered claims, actor, and evidence is a no-op;
changing those inputs after the operation raises `ValueError` instead of
silently accepting a different decision. Resolution returns the stable winner
followed by the deprecated loser and removes the loser from derived search
indexes after the durable transaction. `MemoryClient` provides synchronous
methods with the same names. HTTP uses `POST /v1/contradictions` and
`POST /v1/contradictions/resolve`; MCP uses `memory_mark_contradiction` and
`memory_resolve_contradiction`.

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

Ordered by fidelity (lowest → highest):

| Value | Description |
|---|---|
| `"reference"` | Type and ID only, no memory text |
| `"key_value"` | ID, type and confidence, no memory text |
| `"structured"` | Type label plus the complete text |
| `"prose"` | The complete text (no separate prose rendering exists) |
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
    dimension=384,                       # PRME_EMBEDDING_DIMENSION; optional for registered models
    api_key=None,                        # PRME_EMBEDDING_API_KEY
)
```

Supported providers: `"fastembed"` (local, default), `"openai"` (requires API key).
When `dimension` is omitted, PRME reads registered FastEmbed model metadata or
uses the known OpenAI model dimension without downloading weights. Selecting
`provider="openai"` alone chooses `text-embedding-3-small` and 1,536 dimensions.
Unknown or newly released model names require an explicit positive dimension,
and fail during configuration instead of after an index has been opened.

### ExtractionConfig

```python
from prme.config import ExtractionConfig

ExtractionConfig(
    provider="openai",                   # PRME_EXTRACTION_PROVIDER
    model="gpt-4o-mini",                 # PRME_EXTRACTION_MODEL
    max_retries=3,                       # PRME_EXTRACTION_MAX_RETRIES
    timeout=30.0,                        # PRME_EXTRACTION_TIMEOUT
    temperature=0.0,                     # PRME_EXTRACTION_TEMPERATURE
    reasoning_effort=None,               # PRME_EXTRACTION_REASONING_EFFORT
)
```

Supported providers: `"openai"`, `"anthropic"`, `"ollama"`. Temperature zero
favors repeatable schema-constrained extraction. Ollama resolves an omitted
reasoning effort to `"none"`, preventing thinking traces from exhausting the
structured response window. Ollama also uses its constrained JSON output mode
instead of requiring a tool-call envelope. Other providers retain their native default.
Benchmark before increasing either setting.

### ScoringWeights

Scoring fields are frozen. The six additive weights must sum to 1.0. Epistemic weight is multiplicative; paths weight is a tiebreaker.

Scoring and packing configuration reject `NaN` and positive/negative infinity
at construction or environment loading, including nested node-type boosts and
scoped scoring overrides. Validation errors identify the offending field before
retrieval starts. Finite defaults and their version identifiers are unchanged.

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

`multipath_ordering="score"` selects composite-score ordering within the multi-path
priority tier. The default is `"balanced"`: it reserves the highest-scored ordinary
multi-path candidate, then uses a quarter-length penalty. Pins, instructions,
active tasks, other tiers and measured whole-output budgets keep their existing
rules. The default change follows a complete 119-question answer trial at 4K:
balanced scored 83 versus density at 67, with 26 wins and 10 losses. A separately
registered 381-question answer confirmation scored 250 versus 185, with 88 wins
and 23 losses. Both source partitions had already been inspected, so these results
do not establish superior behavior for every workload.

```python
from prme import MemoryClient, config_from_directory
from prme.retrieval.config import PackingConfig

config = config_from_directory("./my_memories")
config.packing = PackingConfig(multipath_ordering="density")

with MemoryClient(config=config) as client:
    client.store("Aurora requires deployment approval.", user_id="alice")
    result = client.retrieve("Aurora deployment policy", user_id="alice")
    print(result.bundle.render())
```

`config_from_directory()` creates the directory and resolves the database, vector
and lexical paths together. Set typed options before opening the client. Passing
a `config` to `MemoryClient` uses its paths as-is; its separate `directory` argument
is ignored. The equivalent environment setting is
`PRME_PACKING__MULTIPATH_ORDERING=density`.
Temporal context guidance is enabled by default; disable it with
`PRME_PACKING__CONTEXT_GUIDANCE_MODE=off`. `all` additionally enables
experimental current-state and personalization prompts. Ordinary retrieval
receipts use schema version 12 and retains ordering, guidance, context format,
episode and evidence-projection settings in `receipt.packing`, and the
current-update multiplier in `receipt.scoring`. Set
`PRME_PACKING__CONTEXT_FORMAT=compact` to use
schema-declared JSON arrays and bundle-local references; `auditable` remains the
default. Set `PRME_PACKING__CONTEXT_FORMAT=reader` for one plain line per record
(date, state tags, text) with the audit envelope kept in the bundle and receipt,
and `PRME_PACKING__CONTEXT_CITATIONS=true` to add `[m3]` references; reader
receipts use schema version 14. Set `PRME_SCORING__FUSION=rrf` to rank by
reciprocal rank fusion of semantic and lexical ranks instead of the weighted sum
(RFC-0005 Section 7.2); those receipts use schema version 16. Rank-fused scores
are rank-based, so `min_score` then compares against each result's
`semantic_relevance`, its semantic cosine similarity, and a floor tuned on weighted
scores does not carry over. Fused scores are compressed, so session neighbors
at the default decay of 0.85 can crowd primary evidence out of the context; set
`PRME_PACKING__SESSION_CONTEXT_RANK_FUSION_SCORE_DECAY` (0.6 on the offline
evidence gate) to give rank-fused triggers their own decay, which version 17
receipts record. Versions 1–7 mean episode routing was disabled. Versions 1–6 retain
their original canonical JSON and feedback checksums and always mean auditable
rendering. Versions 1–5 also mean context guidance was off. For source blocks or
bounded dialogue episodes stored under meaningful session IDs, set
`PRME_PACKING__EPISODE_CONTEXT_TOP_K=2` to trial deterministic episode routing;
the default `0` disables it.
Set `PRME_PACKING__EVIDENCE_PROJECTION_TOP_K=50` to trial source projection for
the top exact evidence groups; its default `0` also disables it.
Set `PRME_PACKING__EVIDENCE_AUGMENTATION_TOP_K=10` to retain the derived claims
and add bounded direct sources beside them. Projection and augmentation are
mutually exclusive and disabled by default.
`PRME_PACKING__EVIDENCE_AUGMENTATION_ANCHOR_POLICY=non_entity` prevents an
entity-name match from routing its whole source passage; the default `all`
preserves version 11 behavior.

Current-state retrieval gives the newest record that explicitly presents itself
as an update a bounded, relevance-capped multiplier. Configure
`PRME_SCORING__CURRENT_UPDATE_MULTIPLIER` between `1.0` and `2.0`; the default is
the provisional `1.30`, and `1.0` disables it. Receipts record the exact applied
coefficient. This ranking signal does not supersede or validate either claim.

This example chooses smaller candidate limits explicitly; it is not a list of defaults.

```python
from prme.retrieval.config import PackingConfig
from prme.types import RepresentationLevel

PackingConfig(
    token_budget=4096,               # Context budget in tokens
    min_fidelity=RepresentationLevel.REFERENCE,  # Minimum fidelity
    overhead_tokens=100,             # Additional caller reserve beyond measured context
    context_format="auditable",      # Or "compact" for schema-declared arrays, "reader" for plain lines
    context_citations=False,         # Reader only: add [m3] references and context_references
    episode_context_top_k=0,         # Opt-in session-scoped episode routing
    episode_context_local_k=8,       # Records reserved per selected episode
    episode_context_score_decay=0.95,# Inherited episode-evidence score
    graph_max_candidates=50,         # Max from graph traversal
    vector_k=50,                     # Max from vector search
    lexical_k=50,                    # Max from lexical search
    graph_max_hops=3,                # Max graph hops (1-3)
    cross_scope_top_n=5,             # Top-N cross-scope hints
)
```

Context packing counts the complete rendered output with the configured
`tokenizer`. The legacy `chars_per_token` and `cross_scope_token_budget` names
remain accepted for configuration and receipt compatibility, but non-default
values are ignored with a warning. Cross-scope hints live outside the packed
context and are bounded by `cross_scope_top_n`.

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

For extracted stores, set `max_per_source=1` on `retrieve()` when the consuming
surface presents `node.content` as a list of passages. This limits only results
with the same exact nonempty evidence set and byte-identical content, so sibling
claims cannot consume the result budget with repeated source text. The default
is disabled while answer-quality trials establish when to promote it.

Use `max_per_evidence=1` when that surface needs one ranked representative per
exact cited evidence set even if extracted sibling nodes have different text.
This broader option fills the requested result limit from later evidence groups.
Nodes without evidence remain independent, and identical text citing distinct
events remains distinct. The result metadata and retrieval receipt record the
applied value; exclusions use `evidence_limit`.

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
1. Count each serialized candidate with the configured tokenizer
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
    rendered_context: str
    coverage_notice: str | None       # Counted system boundary when applicable
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
    backend_failures: dict[str, str]
    aggregation_coverage: AggregationCoverage | None
```

For detected natural-language counts and lists, `aggregation_coverage` reports
`exhaustive=False`, candidate/selection/context counts, stable limitation codes,
and candidate paths observed at their configured caps. The rendered bundle also
contains a token-counted non-exhaustive warning. Use `scan_nodes()` or
`iter_nodes()` for complete stored-record traversal; semantic retrieval cannot
prove that every real-world item matching a natural-language criterion was
found or deduplicated.

The same stored-record page is available to remote clients through
`GET /v1/nodes/scan` and the MCP tool `memory_scan_nodes`. Both require an
explicit or credential-bound owner, accept scope, node type, lifecycle, UUID
cursor, and page-size filters, and return `has_more` plus `next_cursor`. Follow
the cursor until `has_more` is false. The response reports `order="id"` and
`consistency="page"`: traversal is complete for an unchanged store but is not a
transaction snapshot across requests. `GET /v1/nodes` remains a bounded query
convenience and must not be used as an export or counting contract.

Structured assertion counts use the same complete scan without exposing page
bookkeeping to the caller:

```python
from datetime import datetime, timezone

from prme import AssertionQuery

result = memory.aggregate_assertions(
    AssertionQuery(
        subjects=["I"],
        predicates=["tried"],
        group_by=["object"],
        event_time_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
    ),
    user_id="alice",
)
```

`matched_records` counts assertion occurrences and `distinct_count` counts the
normalized `group_by` keys. Each group carries occurrence/evidence counts,
bounded provenance samples, and its earliest/latest event time. Selectors use
exact NFKC/case/whitespace-normalized matching, with predicate spaces and
hyphens normalized to underscores. Use `retrieval_mode="explicit"` to include
all epistemic and lifecycle states, or pass explicit lifecycle states to narrow
the exact state set independently.

HTTP exposes this at `POST /v1/assertions/aggregate` with
`{"user_id": "alice", "query": {...}}`; MCP exposes
`memory_aggregate_assertions`. Both bind the result to the authenticated owner.
`stored_set_exhaustive=true` covers matching structured records for an unchanged
store. Separate fields report unknown source-extraction and real-world coverage,
with semantic equivalence limited to normalized exact values. Finish pending
ingestion and prevent concurrent mutation for audited counts. This API
counts and groups text values; it does not parse or sum numeric quantities.

For facts carrying `grounding="object_decimal_v1"`, use the dedicated exact
quantity operation:

```python
from prme import QuantityAggregationQuery

totals = memory.aggregate_quantities(
    QuantityAggregationQuery(
        predicate_prefixes=["raised"],
        units=["$"],
        group_by=["unit"],
    ),
    user_id="alice",
)
```

Each group returns an exact `Decimal` total, minimum, maximum, value/evidence
counts, temporal bounds, and bounded per-node source samples. JSON serializes
decimals as strings. `group_by` must contain `unit`; normalization is limited to
Unicode, case, and whitespace, and no conversion or currency inference occurs.
`predicate_prefixes` is opt-in and token-bounded after predicate normalization:
`raised` includes `raised` and `raised_*`, but not `fundraised`. A response using
it reports `semantic_equivalence="normalized_exact_and_predicate_prefix"` rather
than `normalized_exact_only`; no synonym or embedding inference is performed.
The read path revalidates the stored decimal against its claim object and source
evidence. HTTP exposes `POST /v1/quantities/aggregate`; MCP exposes
`memory_aggregate_quantities`. These operations use the same unchanged-store and
extraction/real-world coverage boundaries as assertion aggregation.

For supported simple questions, the convenience path returns its exact plan and
execution together:

```python
planned = memory.aggregate_quantities_from_text(
    "How many kilometers did I run?",
    user_id="alice",
)
```

The planner recognizes only complete, qualifier-free amount/count shapes in a
fixed action and unit table. It preserves the exact `I` or `we` subject from the
question and selects positive default epistemic state, explicit predicate-prefix
families, and unit-separated groups. Unsupported qualifiers, negation, future
wording, named subjects,
actions, or units return `plan.status="unsupported"` with no scan. Inspect
`plan.query` and `plan.assumptions`; the convenience result does not hide a
semantic model call. HTTP exposes `POST /v1/quantities/aggregate-text`; MCP
exposes `memory_aggregate_quantities_from_text`.

Use the exact temporal state operation when the application already knows an
assertion's subject and predicate:

```python
from datetime import datetime, timezone
from prme import AssertionStateQuery, Scope

state = memory.get_assertion_state(
    AssertionStateQuery(
        subject="Alice",
        predicate="lives_in",
        scope=Scope.PERSONAL,
        valid_at=datetime.now(timezone.utc),
    ),
    user_id="alice",
)
```

This scans all matching FACT, DECISION, and PREFERENCE records for an unchanged
store. It returns eligible current candidates separately from the bounded
timeline, along with event time, ingestion time, validity windows, lifecycle,
supersedence pointers, contradiction edges, and evidence IDs. Status is
`unknown`, `single`, `consistent`, `multiple`, or `contested`. `multiple` does
not imply a contradiction, and the operation never treats the latest record as
truth. Scope and `valid_at` are required to prevent cross-scope state mixing and
implicit wall-clock results. An optional `knowledge_at` remains an ingestion
cutoff over current lifecycle state and returns `exact_snapshot=false`.

HTTP exposes `POST /v1/assertions/state`; MCP exposes
`memory_get_assertion_state`. Subject and predicate matching use the same exact
normalization contract as assertion aggregation. Source extraction and
real-world coverage remain explicitly unknown.

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

When a fact object contains one exact numeric amount, built-in extraction may
also populate `node.metadata["quantity"]` with decimal-string `value`, verbatim
`unit`, verbatim `source_text`, and `grounding="object_decimal_v1"`. Grounding
requires the quantified phrase in both the object and source evidence and checks
the parsed decimal. When JSON transport emits a float, built-in extraction can
recover the decimal only by reparsing one exact supported token from the grounded
source phrase; it never converts the float. Approximation or range cues in the
surrounding evidence reject clipped exact-looking output. If model-authored
quantity fields are absent or invalid, built-in extraction can recognize one
verbatim currency or unit from its bounded physical, data and count-unit
lexicon in an otherwise grounded fact object. It does not normalize that unit
or accept an unlisted noun as a measure. Invalid optional quantity output is removed while the
otherwise grounded fact remains. Ranges, approximations, scientific notation,
locale decimal commas, and phrases with multiple numbers are not typed. No
currency inference or unit conversion occurs.

Fresh `speech_act_v11` extraction can also recover one leading exact measure
from a user-authored first-person completed action in a bounded verb lexicon,
such as `I just ran 5 kilometers`. It retains the source phrase as the object and
uses the same validators; modals, negations, examples, questions, conditions,
approximations and ranges do not use this recovery path.

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


### Experimental retrieval composition policies

Two explicit policies are available for controlled trials through `PRMEConfig`:
`reranker_policy="anchored_score_envelope"` with `enable_reranker=True`, and
`query_reformulation_merge_policy="max_signals"` with
`enable_query_reformulation=True`. The unanchored `"score_envelope"` reranker is
also available. Default flags and the existing `"legacy"` / `"new_only"` policies
are unchanged. Both local and PostgreSQL engines use these settings.

See [experimental retrieval policies](EXPERIMENTAL-RETRIEVAL-POLICIES.md) for
failure semantics, receipt versions, quality evidence and configuration examples.
These options have not passed an untouched answer confirmation and are not
recommended production defaults. Product alignment/Jev remains a separate
explicit pair-selection and review workflow.
