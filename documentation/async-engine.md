# Async Engine API Reference

`MemoryEngine` is the low-level async API. Use this when you need full control over the event loop, or when integrating into an existing async application. For most use cases, prefer [MemoryClient](memory-client.md).

## Creating an Engine

```python
from prme.storage.engine import MemoryEngine
from prme.config import PRMEConfig

# Factory method
config = PRMEConfig(
    db_path="./memory.duckdb",
    vector_path="./vectors.usearch",
    lexical_path="./lexical_index",
)
engine = await MemoryEngine.create(config)

# Context manager
async with MemoryEngine.open(config) as engine:
    await engine.store("hello", user_id="alice")
    # engine.close() called automatically
```

Always call `await engine.close()` when done, or use the context manager.

## store()

```python
async def store(
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
    event_time: datetime | None = None,
    ttl_days: int | None = ...,
) -> str
```

The async API exposes additional parameters not available on `MemoryClient`:

- `epistemic_type` — override auto-inference (OBSERVED, ASSERTED, INFERRED, etc.)
- `source_type` — override auto-inference (USER_STATED, SYSTEM_INFERRED, etc.)
- `ttl_days` — explicit TTL. Use `None` for no TTL. Default (`...`) uses the config's per-type default.

## ingest()

Two-phase ingestion: persists event immediately, then runs LLM extraction.

```python
async def ingest(
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

Set `wait_for_extraction=True` to block until the LLM has finished extracting entities and relationships.

## ingest_batch()

```python
async def ingest_batch(
    messages: list[dict],
    *,
    user_id: str,
    session_id: str | None = None,
    wait_for_extraction: bool = False,
    scope: Scope = Scope.PERSONAL,
) -> list[str]
```

## ingest_fast()

The fast path commits the event and its pending work atomically, without embedding or LLM calls. Retrieval or organization materializes a raw NOTE with the original event ID, provenance, and timestamps. Pending work survives restart; the configured queue size bounds each batch rather than dropping events. Use `ingest()` when you need LLM extraction. Latency depends on the database commit.

```python
async def ingest_fast(
    content: str,
    *,
    user_id: str,
    role: str = "user",
    session_id: str | None = None,
    metadata: dict | None = None,
    scope: Scope = Scope.PERSONAL,
    event_time: datetime | None = None,
) -> str
```

Use this for real-time conversational ingestion where latency matters.

Completion is acknowledged only after the vector and lexical indexes are
persisted. Failed work remains pending for a later pass; one failure does not
block the other items in that batch. A drain budget is cooperative: an indexing
operation already in progress finishes before the budget is checked again.

## Processing deferred events

```python
event_id = await engine.ingest_fast("Alice prefers dark mode", user_id="alice")
status = await engine.processing_status(event_id, user_id="alice")
result = await engine.process_pending(user_id="alice", budget_ms=1000)
print(result.processed, result.pending, result.failed)
status = await engine.processing_status(event_id, user_id="alice")
print(status.status, status.attempts, status.last_error)
```

`process_pending()` processes one batch without running organizer jobs. Repeat
as needed; failed items remain in `pending`, and persistent errors should be
inspected before retrying. `budget_ms=0` reads current counts without processing.
Status and counts are read from durable storage and restricted to the supplied
user. Errors contain the exception type, without provider response text.

`processing_status()` returns `None` for an unknown event, another user's event,
or an event written through `store()`/`ingest()`. This API tracks only deferred
raw ingestion, not LLM extraction. A `complete` status records successful
materialization; a later lifecycle operation may still retire that memory.

## retrieve()

```python
async def retrieve(
    query: str,
    *,
    user_id: str,
    scope: Scope | list[Scope] | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    reference_time: datetime | None = None,
    knowledge_at: datetime | None = None,
    event_time_from: datetime | None = None,
    event_time_to: datetime | None = None,
    token_budget: int | None = None,
    weights: ScoringWeights | None = None,
    min_fidelity: RepresentationLevel | None = None,
    include_cross_scope: bool = True,
) -> RetrievalResponse
```

Additional parameters vs MemoryClient:

- `weights` — override scoring weights for this query
- `min_fidelity` — minimum representation level for context packing
- `include_cross_scope` — include hints from other scopes (default: True)
- `event_time_from`/`event_time_to` — filter by when events actually occurred (vs when they were stored)
- `time_from`/`time_to` — filter assertion validity windows across all retrieval
  paths (ENTITY and PREFERENCE nodes retain their existing exemption). Dates
  inferred from query text guide ranking, without imposing a validity cutoff.
- `reference_time` — timezone-aware clock for relative query dates and scoring decay.
  Defaults to UTC request time and is returned in `response.metadata.reference_time`.
  Reuse it to replay a query against unchanged memory/configuration. It does not
  apply a knowledge cutoff; set `knowledge_at` separately when needed.

## Node Operations

```python
# Get single node
node = await engine.get_node(node_id, include_superseded=False)

# Query with filters
nodes = await engine.query_nodes(
    user_id="alice",
    node_type=NodeType.FACT,
    lifecycle_state=LifecycleState.STABLE,
    limit=50,
)

# Get single event
event = await engine.get_event(event_id)

# Get events for a user
events = await engine.get_events("alice", session_id="s1", limit=100)
```

## Lifecycle Transitions

```python
# Promote: TENTATIVE → STABLE
await engine.promote(node_id)

# Supersede: marks old node as SUPERSEDED, links to new
await engine.supersede(old_node_id, new_node_id, evidence_id="evt-123")

# Archive: any state → ARCHIVED (terminal)
await engine.archive(node_id)

# Reinforce: boost confidence (+0.05) and reinforcement_boost (+0.15)
await engine.reinforce(node_id, evidence_id="evt-456")
```

## Entity Snapshots

Generate a point-in-time snapshot of an entity and its neighborhood:

```python
snapshot = await engine.snapshot("entity-id", at_time=datetime(2024, 6, 1))
```

## Organize

```python
result = await engine.organize(
    user_id="alice",
    jobs=["promote", "deduplicate"],
    budget_ms=3000,
)

# End-of-session lightweight organize
result = await engine.end_session(user_id="alice", session_id="s1")
```

## Quality & Feedback

```python
from prme.quality.feedback import FeedbackSignal

# Record retrieval feedback
await engine.feedback(FeedbackSignal(
    query="preferences?",
    user_id="alice",
    selected_node_id="...",
    was_helpful=True,
))

# Get quality metrics
metrics = engine.quality_metrics

# Check materialization debt
debt = engine.materialization_debt
```

## close()

```python
await engine.close()
```

Encrypts the memory pack if encryption is enabled, then closes all backends.
