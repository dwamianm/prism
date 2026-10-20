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
    speaker: str | None = None,
    node_type: NodeType = NodeType.NOTE,
    scope: Scope = Scope.PERSONAL,
    metadata: dict | None = None,
    confidence: float | None = None,
    epistemic_type: EpistemicType | None = None,
    source_type: SourceType | None = None,
    event_time: datetime | None = None,
    ttl_days: int | None = ...,
    extract: bool | None = None,
) -> str
```

Both the async API and `MemoryClient.store()` accept these explicit controls:

- `epistemic_type` — override auto-inference (OBSERVED, ASSERTED, INFERRED, etc.)
- `source_type` — override auto-inference (USER_STATED, SYSTEM_INFERRED, etc.)
- `ttl_days` — explicit TTL. Use `None` for no TTL. Default (`...`) uses the config's per-type default.
- `extract` — `True` also queues the background LLM extraction `ingest()` runs once the node is stored, so the text gets entities and claims too; the call does not wait and the node is kept as it is. `False` never extracts. Default (`None`) follows `enable_store_extraction`, which is off.
- `role` and `speaker` — `role="participant"` marks a human in the conversation other than the memory's owner (`USER_STATED`, like `"user"`), and `speaker` names who said it. The reader context shows the name before the text. See [conversation participants](../docs/INTEGRATION.md).

## ingest()

Two-phase ingestion: persists event immediately, then runs LLM extraction.

```python
async def ingest(
    content: str,
    *,
    user_id: str,
    role: str = "user",
    speaker: str | None = None,
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

## Extraction status and recovery

The LLM `ingest()` path atomically saves extraction work alongside its source.
Retry attempts and scheduling survive restart. In-process retry tasks stop when
the engine closes; after reopening, explicitly process due work with:

```python
status = await engine.extraction_status(event_id, user_id="alice")
# After correcting the cause of a terminal failure, make it eligible again.
if status is not None and status.status == "failed":
    await engine.retry_extraction(event_id, user_id="alice")
result = await engine.process_extractions(user_id="alice", limit=100, budget_ms=5000)
```

Status distinguishes `pending`, `running`, `failed` and `complete`, and reports
phase, attempts, lease expiry and a bounded failure code. An abruptly terminated
worker's live lease must expire before another worker can claim it. Processing
budgets apply between jobs; provider calls retain their own timeout. Retrieval
does not run LLM recovery, and there is no background daemon. The same recovery
methods are available on `MemoryClient`.

Saved extraction is reused after a materialization failure, and a saved complete
plan preserves graph identities and numerical embeddings. If a referenced memory
changed (`StaleDerivationPlanError`), call `retry_extraction(..., replan=True)`
before processing: it creates a new plan revision from the same extraction and
may compute new embeddings. It does not preempt live or completed work, or repeat
the model's extraction. Legacy sources without work records are not automatically
enrolled; [`reingest()`](#re-deriving-stored-memories) derives them.

With `PRME_EXTRACTION_BATCH_TARGET_TOKENS` above zero, extraction runs in
batches of a session's turns (see
[batched extraction](../docs/INTEGRATION.md#batched-extraction)):
`ingest(wait_for_extraction=False)` queues the turn, a waiting `ingest()`
extracts the queue through its own turn, and `ingest_batch()` saves every
message before extracting them together. `close()` extracts the turns still
queued, waiting the usual 10 seconds plus the extraction timeout for each
batched request they need, five minutes at most; a turn still queued after
that stays pending until `process_extractions()` or the owner's next flush.

With `wait_for_extraction=True`, failure raises `ExtractionError`; its `event_id`
identifies the persisted source and `reason_code` gives the sanitized failure
category when available. Import it from `prme`. A successful empty extraction
remains distinct from a provider error. The synchronous client waits for
extraction by default.

`ingest(scope=...)` keeps every extracted node in the caller's scope. Model
scope classifications do not grant write access elsewhere; fact classifications
are retained as `metadata.suggested_scope`. Entity matching also stays within
the same user and scope.

## Re-deriving stored memories

A memory keeps the entities, claims and links the engine derived from it when
it was stored, or none when `store()` saved it plainly. When an engine change
improves how memories are derived, `reingest()` brings one owner's stored
memories up to date. It is off unless `enable_reingest` is set
(`PRME_ENABLE_REINGEST=true`); an engine that did not turn it on raises
`ReingestDisabledError`.

```python
config = PRMEConfig(enable_reingest=True)  # also needs extraction_batch_target_tokens above 0 (the default)
async with MemoryEngine.open(config) as engine:
    preview = await engine.reingest("alice", dry_run=True)   # no model call, no change
    print(preview.selected, preview.model_calls, preview.source_tokens)
    result = await engine.reingest("alice", limit=500)
    while result.cursor:                                     # more sources are left after the cap
        result = await engine.reingest("alice", limit=500, cursor=result.cursor)
```

It reads the owner's sources (every memory saved through `store()`, `ingest()`
and the like) in event order and runs the same batched extraction `ingest()`
runs: memories of one session share a request up to
`extraction_batch_target_tokens`, and one without a `session_id` goes alone.
Then it publishes what the present engine derives from each. The source events
and their raw notes are never changed or deleted, and only the named owner's
sources are touched.

- **Replacing old records.** An earlier claim the new derivation states again is
  superseded by it (`superseded_by` and a `SUPERSEDES` edge). One it no longer
  states is archived, with `metadata.reingest.replaced_by` naming the claims that
  replaced it. Both leave retrieval and the search indexes, and both stay
  readable with `get_node(..., include_superseded=True)`. Entities are kept and
  reused, because every memory that names an entity shares it.
- **Idempotent and resumable.** Each source is saved with a marker that names the
  settings it was derived under: the engine version, the extraction model and
  prompt, and the options that decide what is derived. A second run skips the
  sources that have it and creates nothing. The model's output is saved before
  anything is published, so a run that stops after a request does not ask the
  model again, and the next call continues with the sources that are not current.
  An option that only changes what is derived makes every source stale again but
  needs no new model calls while the saved output is current.
- **Counts.** `ReingestResult` has `total` sources, how many are `current`,
  how many are `unfinished` (their own extraction job has not finished: run
  `process_extractions()` first), the `selected` sources of this call, how many
  were `processed` and `failed`, how many are `remaining`, a `cursor` to
  continue after the last selected source, the `model_calls` made (or, for a dry
  run, expected) and the `source_tokens` those requests carry, for a cost
  estimate. A failed source stays not current and is tried by the next call
  without a cursor. Three failed requests in a row end the call.
- **Limits.** `limit` caps the sources one call re-derives; `scope` restricts the
  call to one scope. A second call for the same owner in the same engine raises
  `ReingestBusyError`. Between two calls of a long run, a claim an older memory
  states may be current beside the later memory that corrected it until that
  memory is reached, so run to the end. Retrieval and other writes continue
  during a run.

The same call is on `MemoryClient.reingest()`, `POST /v1/reingest`, the
`memory_reingest` MCP tool and `prme reingest`.

## ingest_fast()

The fast path commits the event and its pending work atomically, without embedding or LLM calls. Retrieval or organization materializes a raw NOTE with the original event ID, provenance, and timestamps. Pending work survives restart; the configured queue size bounds each batch rather than dropping events. Use `ingest()` when you need LLM extraction. Latency depends on the database commit.

```python
async def ingest_fast(
    content: str,
    *,
    user_id: str,
    role: str = "user",
    speaker: str | None = None,
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
or a legacy event without a work record. New `store()` writes retain a complete
initial node snapshot and repair job alongside the event. The same methods repair
those writes after an indexing failure or restart, preserving type, ID, timestamps
and TTL without an LLM. If graph creation fails after acceptance, the exported
`MaterializationError` carries an `event_id` for inspection and recovery.

For `ingest_fast()` and `ingest()`, this status covers raw NOTE materialization;
LLM derivations use `extraction_status()`. Completion does not include optional
post-store reinforcement, supersedence or QA pairing. A later lifecycle operation
may still retire the memory, and processing does not reactivate retired nodes.

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
- `max_per_source` — optional positive cap for results sharing one exact
  nonempty evidence set and byte-identical passage; use `1` when extracted
  sibling claims would otherwise repeat the same source text
- `include_cross_scope` — include hints from other scopes (default: True)
- `event_time_from`/`event_time_to` — filter by when events actually occurred (vs when they were stored)
- `time_from`/`time_to` — filter assertion validity windows across all retrieval
  paths (ENTITY and PREFERENCE nodes retain their existing exemption). Dates
  inferred from query text guide ranking, without imposing a validity cutoff.
- `reference_time` — timezone-aware clock for relative query dates and scoring decay.
  Defaults to UTC request time and is returned in `response.metadata.reference_time`.
  Reuse it to replay a query against unchanged memory/configuration. It does not
  apply a knowledge cutoff; set `knowledge_at` separately when needed.

## Rendered context and token budgets

```python
response = await engine.retrieve("What should I remember?", user_id="alice", token_budget=2048)
context = response.bundle.render()
print(response.bundle.tokens_used, response.bundle.tokenizer)
```

Pass this rendered string as the memory context. `tokens_used` counts the whole
string, including section labels, source IDs, dates, and epistemic metadata.
`PackingConfig.tokenizer` defaults to `cl100k_base`; select the consuming model's
encoding (for example `o200k_base`) in the engine configuration. Token counts are
specific to that encoding. `overhead_tokens` reserves additional space for the
caller outside the measured context.

Entries retain complete source text or become explicit metadata/references;
text is never sliced to fit. A tiny budget can produce an empty bundle even for
pinned memories. Set `min_fidelity=RepresentationLevel.FULL` (or `PROSE` or
`STRUCTURED`, which pack the same records) when references without memory text
are unsuitable: records that fit only as a `KEY_VALUE` or `REFERENCE` fallback,
or whose text is blank, are then excluded and listed in `bundle.excluded_ids`.
Full node objects remain in `results` for inspection; concatenating their
content bypasses the bundle's budget.

The separate `format_for_llm(..., token_budget=...)` formatter also counts its
entire output. It accepts `token_counter=your_model_counter` for tokenizers
outside tiktoken. Profiles, conflict annotations, and headers consume that same
budget, so a candidate is included only if the complete rendering fits.

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

Closes all backends, then, for a directory pack with encryption enabled, encrypts its files again. A single-file pack (`pack_path`) is encrypted by DuckDB the whole time it is open, so `close()` has nothing to encrypt; `lock()` and `unlock()` raise `RuntimeError` for such a pack.
