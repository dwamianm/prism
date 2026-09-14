# PRME — Portable Relational Memory Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/dwamianm/prism/actions/workflows/ci.yml/badge.svg)](https://github.com/dwamianm/prism/actions/workflows/ci.yml)

**Local-first, embeddable memory substrate for LLM-powered systems.**

PRME gives AI agents and chatbots stable long-term memory by combining an append-only event log, a graph-based relational model, hybrid retrieval (graph + vector + lexical), and epistemic state tracking — all in a portable, single-directory bundle.

## Retrieval quality and evaluation

PRME includes synthetic regression scenarios and LoCoMo/LongMemEval adapters.
There is **no validated current-release accuracy headline yet**. Historical runs
in `benchmarks/results/` used different configurations, including dataset-provided
observations and benchmark-only retrieval expansion. They are research artifacts,
not a reproducible measurement of the current product.

See [BENCHMARKS.md](BENCHMARKS.md) for the measurement contract, commands, and
remaining baseline work. The [roadmap](ROADMAP.md) prioritizes retrieval quality:
reliable evidence retrieval, explicit aggregation coverage, and correct temporal state.

For applications that need fewer memories, `retrieve(..., limit=5, min_score=0.5)`
applies a count cap and an inclusive score floor **before context packing**.
`limit=0` returns no primary results; rejected nodes are listed in
`response.excluded`. A score is a ranking signal, not a calibrated probability:
choose a floor using labeled queries from your application. No acceptance floor
is enabled by default, because a threshold that suppresses noise in a small
fact corpus can discard useful evidence in long conversations. Cross-scope
hints remain separate; disable them with `include_cross_scope=False` when needed.

The HTTP API accepts `limit`, `min_score`, `token_budget`, and `mode`, plus typed
`filters` (`scope`, `time_from`, `time_to`, `knowledge_at`, `event_time_from`,
`event_time_to`, `include_cross_scope`). Unknown keys are rejected. `mode="explicit"`
relaxes epistemic filtering within the generated candidate pool; it is not an
exhaustive historical scan. The MCP `memory_retrieve` tool also accepts score,
count, and token bounds.

`knowledge_at` is a timezone-aware ingestion-time cutoff over candidates from
the current graph and search indexes. It does not reconstruct prior lifecycle,
correction, organizer, or evicted-index state. Requests that use it return
`response.metadata.historical_coverage` with `exact_snapshot=False`, and the
token-counted model context carries the same warning. Use retained events and
operation records for an audit; PRME does not currently offer exact historical
state replay.

Natural-language count and list queries return
`response.metadata.aggregation_coverage`. Semantic retrieval always reports
`exhaustive=False`; it includes candidate, selected, and packed-context counts,
stable limitation codes, and the backend paths that reached a candidate cap.
The packed context contains the same non-exhaustive boundary within its measured
token budget, so a downstream model cannot silently treat retrieved candidates
as a complete corpus. HTTP and MCP expose the structure under
`metrics.aggregation_coverage`. Use `iter_nodes()` or `scan_nodes()` when the
task is complete stored-record enumeration.

## Why PRME?

Persistent memory helps an assistant carry context between conversations. It
also needs to preserve evidence, distinguish speculation from facts, and keep
earlier information available when circumstances change. PRME combines:

- **Durable source history** — immutable append-only events; rebuildable search indexes from the durable graph
- **Graph-based relational model** — 9 typed node kinds (entities, facts, preferences, decisions, tasks, instructions, summaries, events, notes) with edges capturing relationships, supersedence, and temporal validity
- **Epistemic state tracking** — memories have lifecycle states (tentative -> stable -> superseded -> archived), confidence scores, contradiction detection, and oscillation dampening
- **Auditable conditions** — conditional claims begin unresolved, stay out of factual retrieval, and can be evaluated through an atomic, retry-safe transition with evidence
- **Hybrid retrieval** — semantic similarity + lexical search + graph proximity, scored and packed into a token-efficient context bundle
- **Self-organizing memory** — organizer jobs handle promotion, decay, deduplication, summarization, consolidation, and archival
- **Dual-stream ingestion** — durable fast path with deferred graph materialization and indexing
- **Local-first** — everything lives in a single directory (DuckDB + usearch + Tantivy). No cloud dependency. Optional PostgreSQL backend for production.

## Installation

```bash
pip install prme
```

With optional extras:

```bash
pip install prme[postgres]   # PostgreSQL backend
pip install prme[api]        # HTTP API (FastAPI)
```

### From source

```bash
git clone https://github.com/dwamianm/prism.git
cd prism
uv sync --dev
```

## Quickstart

```python
from prme import MemoryClient

with MemoryClient("./my_memories") as client:
    # Store memories (no LLM needed)
    client.store("Alice prefers dark mode in all her editors.", user_id="alice")
    client.store("The team decided to use PostgreSQL.", user_id="alice")

    # Retrieve with hybrid scoring
    response = client.retrieve("What are Alice's preferences?", user_id="alice")
    for result in response.results:
        print(f"[{result.composite_score:.3f}] {result.node.content}")
```

`MemoryClient` is a synchronous wrapper — no `async`/`await` needed. It works everywhere: scripts, notebooks, FastAPI apps.

Conditional memories require explicit condition text and always start unresolved.
Record the result when a user, tool, rule, or model evaluates that condition:

```python
from prme import ConditionState, EpistemicType, MemoryClient

with MemoryClient("./my_memories") as client:
    event_id = client.store(
        "If the release is approved, deploy Atlas.",
        user_id="alice",
        epistemic_type=EpistemicType.CONDITIONAL,
        metadata={"condition": "the release is approved"},
    )
    claim = client.get_event_nodes(event_id, user_id="alice")[0]
    client.evaluate_condition(
        str(claim.id), ConditionState.TRUE, user_id="alice",
        request_id="9ee0440b-4ea4-48c5-87ed-c1f43546475b",
        evaluation_method="tool", reason="Approval service confirmed",
    )
```

The state change and its complete before/after `EPISTEMIC_TRANSITION` record
commit together. Reuse `request_id` after a timeout. A true condition is scored
as asserted; false, unknown, and expired states remain outside default retrieval.
Use `client.get_provenance(claim_id, user_id="alice")` to inspect its owned
source events, missing evidence, contradiction links, and paged transition
history. The async engine exposes the same method; HTTP uses
`GET /v1/nodes/{node_id}/provenance`, and MCP uses `memory_get_provenance`.

Corrections and conflicts use the same public lifecycle instead of reaching
into a storage backend:

```python
client.supersede(
    outdated_claim_id, corrected_claim_id,
    evidence_id=correction_event_id, user_id="alice", actor_id="alice",
)
contested = client.contradict(
    first_claim_id, second_claim_id,
    user_id="alice", actor_id="reviewer",
)
winner, loser = client.resolve_contradiction(
    second_claim_id, first_claim_id,
    user_id="alice", resolver_actor_id="reviewer",
)
```

These operations update claims, graph edges, and audit records atomically.
Repeating an exact call is safe after an ambiguous timeout. HTTP exposes
`POST /v1/supersedences`, `POST /v1/contradictions`, and
`POST /v1/contradictions/resolve`; the corresponding MCP tools are
`memory_supersede`, `memory_mark_contradiction`, and
`memory_resolve_contradiction`.

`promote()` and `archive()` also accept a caller-generated UUID `request_id`.
Reuse it for an exact retry; HTTP uses the `Idempotency-Key` header and the MCP
lifecycle tools expose the same `request_id` field.

The [packing guide](docs/PACKING.md) explains exact context budgets and the
default `balanced` policy. It improved 4K source retention from 74.85% to 95.91%
and answer accuracy from 67/119 to 83/119 on the complete development cohort.
On a separately registered 381-question answer confirmation, it scored 250/381
versus density at 185/381. That partition had already been inspected for source
retention, so this confirms the product default without constituting an independent
competitive benchmark. Use explicit `density` or `score` ordering when a
workload-specific evaluation supports it.

Temporal questions also receive compact question-time and date-arithmetic
guidance when it fits without displacing a memory. A registered confirmation
improved temporal answers from 31/50 to 34/50. Set
`packing=PackingConfig(context_guidance_mode="off")` to disable it. The `"all"`
mode includes experimental personalization and current-state prompts and is not
the default because personalization produced a real negative-preference failure.

For exports and counts, enumerate the stored records instead of counting search
results. Iteration reads every matching node in bounded pages:

```python
from prme import MemoryClient
from prme.types import NodeType, Scope

with MemoryClient("./my_memories") as client:
    facts = client.iter_nodes(
        user_id="alice", scope=Scope.PERSONAL,
        node_type=NodeType.FACT, batch_size=100,
    )
    for fact in facts:
        print(fact.id, fact.content, fact.evidence_refs)
```

`MemoryEngine.iter_nodes()` supports `async for`. Both clients also expose
`scan_nodes(..., after_id=last_id, limit=100)` for explicit pagination. Defaults
include active lifecycle states. This counts stored records, which can contain
multiple assertions about the same real-world item. Pages are complete for an
unchanged store; concurrent writes can change matches between pages. Finish
pending ingestion first when an export needs to include those events.

<details>
<summary>Async API (advanced)</summary>

```python
import asyncio
from prme import MemoryEngine, PRMEConfig

async def main():
    config = PRMEConfig(
        db_path="./memory.duckdb",
        vector_path="./vectors.usearch",
        lexical_path="./lexical_index",
    )
    async with MemoryEngine.open(config) as engine:
        await engine.store("Alice prefers dark mode.", user_id="alice")
        response = await engine.retrieve("preferences?", user_id="alice")
        for result in response.results:
            print(f"[{result.composite_score:.3f}] {result.node.content}")

asyncio.run(main())
```

Both clients create missing local storage directories, including parent directories.

</details>

### LLM-Powered Ingestion

With an API key set, PRME can automatically extract entities, facts, and relationships from conversation text:

```python
# Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or configure Ollama
events = await engine.ingest_batch(
    [
        {"role": "user", "content": "I just switched to Neovim and love it."},
        {"role": "assistant", "content": "Great choice! The plugin ecosystem is excellent."},
    ],
    user_id="alice",
    session_id="session-1",
    scope=Scope.PERSONAL,
)
```

For real-time use, the fast path skips graph extraction:

```python
# Durably accept the event; defer embedding and indexing
event_id = await engine.ingest_fast(content, user_id="alice", scope=Scope.PERSONAL)
# Optional explicit processing; retrieval also processes pending events
result = await engine.process_pending(user_id="alice")
status = await engine.processing_status(event_id, user_id="alice")
```

For a raw conversation import, accept the events first and then process them
together. This lets the local index share a durable commit:

```python
with MemoryClient("./memories") as memory:
    event_ids = [
        memory.ingest_fast(
            message["content"], user_id="alice", role=message["role"],
            session_id="imported-conversation",
        )
        for message in messages
    ]
    result = memory.process_pending(user_id="alice", budget_ms=30_000)
    print(result.processed, result.pending, result.failed)
```

Each event is durably accepted separately; the whole import is not one
transaction. `pending` means more processing remains, and `failed` reports
failed attempts in that pass. Fix any underlying failure and process the same
owner's pending work again, rather than resubmitting accepted source events.
Use `ingest()` when the import needs LLM extraction, or `store()` for immediate
typed storage.

For imported conversations, `ingest()` accepts a timezone-aware `event_time`:

```python
from datetime import datetime
from prme import MemoryClient

with MemoryClient("./memories") as memory:
    event_id = memory.ingest(
        "Alice started using Rust yesterday.",
        user_id="alice",
        event_time=datetime.fromisoformat("2024-03-10T01:30:00-06:00"),
        session_id="imported-conversation",
        metadata={"source": "chat-export"},
    )
```

Relative dates in extracted facts use the source clock, while the immutable
receipt retains the actual ingestion timestamp. `MemoryEngine.ingest`, HTTP
`/v1/ingest` and MCP `memory_ingest` accept the same source time. Python batch
ingestion accepts an `event_time` datetime on each message; messages without it
use ingestion time. Batch ingestion admits messages sequentially and is not an
all-or-nothing transaction. Dates without a timezone are rejected before that
message is admitted. This does not rewrite already journaled extraction plans.

Raw `store()` and `ingest_fast()` writes accept the same timezone-aware clock,
including through `MemoryClient`; omitted source times remain unknown. MCP
`memory_store` also accepts `event_time`, and node responses expose source time
and validity dates separately. None of these APIs infer a timezone for a naive
datetime.

Deferred raw events survive restart. LLM `ingest()` also queues original-source
indexing atomically with its event, so extraction failure cannot make that source
unsearchable after restart. For these ingestion paths, processing status acknowledges raw NOTE indexing;
LLM extraction has its own durable work record and recovery API below.
Local pending-work passes batch Tantivy replacements after preparing their
sources and durable vectors. They acknowledge each source only after the lexical
commit succeeds; a failed batch retries documents separately so one bad item
does not block healthy sources. Time budgets are checked between source items;
the final commit and fallback repair can extend a pass. Direct `store()` retains
immediate per-source indexing. PostgreSQL uses individual database writes.
Processing reports remaining work and retry failures per user; the same methods
are available on `MemoryClient`. Model summaries do not overwrite original-source
indexes. Relative dates in extracted facts use the source timestamp.

Raw-source indexing attempts full-text and vector indexes independently. If one
backend fails, the healthy search path remains available and deferred processing
stays pending until both indexes succeed. Direct `store()` also attempts both
indexes and now saves a repair job with the event and complete initial node
values. `processing_status()` and `process_pending()` also track and repair new
`store()` writes after restart, preserving their type, identity, timestamps and
TTL without calling an LLM. If graph creation fails after acceptance, catch
`MaterializationError` and use its `event_id` to inspect/retry the saved request.
Automatic instruction reinforcement requires an exact repetition of an explicit
user instruction in the same scope. Similar facts, contradictions, assistant
echoes and speculative rules do not confirm an instruction. Opt-in semantic
re-mention reinforcement also stays within scope and excludes instructions.
This heuristic is not calibrated evidence of truth; prior boosts are retained.

Explicit `reinforce(node_id, evidence_id=..., user_id=...)` requires an existing
evidence event in the node's owner and scope. Invalid references change nothing;
omitting evidence remains a caller confirmation. Reinforcement preserves values
already above its increment caps. It does not verify semantic support or make
repeated confirmations independent evidence.

Completion covers the node and indexes; optional reinforcement, supersedence and
QA pairing run afterward and are not replayed by this job. Retired nodes remain
retired. Historical direct stores without repair jobs still need `prme rebuild`
for index repair.

Retrieval reports failures of its primary candidate paths in
`response.metadata.backend_failures`, using `backend_error` or
`embedding_mismatch` reason codes without provider error details. No vector
matches alone is not an error. Stored model/version metadata is checked before
returning vector hits; incompatible embeddings fall back to other search paths.
Rebuild after changing embedding models. Legacy PostgreSQL embeddings without
model metadata also require a rebuild; their model is never guessed from current
configuration. Dimension changes may additionally require storage migration.
HTTP and MCP retrieval responses expose the same diagnostics under `metrics`.

Built-in extraction providers validate that every fact subject and relationship
endpoint names a listed entity. Invalid or ambiguous references trigger the
provider's bounded schema retries. When the same name has different types, use
`subject_entity_type`, `object_entity_type`, `source_entity_type`, or
`target_entity_type` to select the
intended entity. PRME does not guess aliases from substring similarity.

A malformed individual fact or relationship cannot invalidate valid siblings or
the immutable source event; PRME drops that item and continues. Malformed list
envelopes and unresolved named references still use bounded provider retries.
Stored evidence remains paragraph-complete, while condition and uncertainty
checks use only the claim's cited sentence and an immediately following
qualification. This keeps unrelated questions and hypotheticals in the same
message from contaminating an otherwise factual claim; indirect questions such
as “see if” do not become logical conditions, and polite request modals such as
“could you help” do not turn adjacent asserted memory into a hypothetical.

Custom-provider and historical facts with unresolved subjects remain searchable.
Their node metadata reports `subject_link_status` as `missing` or `ambiguous`,
and no guessed graph link is created. Resolved subjects report `resolved`.
These checks establish structural references; they do not prove model claims or
distinguish different people with the same name and type across conversations.

Extracted relationships are source-cited FACT nodes with the same epistemic
filtering as other claims. `HAS_FACT` connects the subject to its claim;
`MENTIONS` connects a claim to its resolved object entity. Model predicates stay
in metadata: ingestion does not turn a model's `part_of` or `caused_by` label into
a structural edge. These links aid retrieval; graph paths do not prove entailment.
Built-in providers must cite, classify, and identify the semantic polarity of
facts and relationships. Explicit if/unless conditions must be copied from the
cited source. They are stored with an unknown condition state and remain outside
DEFAULT retrieval until a caller records that the condition is true with
`evaluate_condition()`; EXPLICIT
retrieval keeps every state available for audit. Legacy/custom providers that
omit polarity retain `unknown`; relationships that omit classification become
unverified model claims, excluded from default retrieval at the standard
confidence setting. A fact covering the same endpoints and passage takes
precedence over an additional relationship label; the saved extraction still
contains both. Existing committed graphs and saved plans retain their original
behavior; this change does not migrate historical edges.

Successful grounded extraction output is saved before graph materialization.
An indexing retry reuses that output without another LLM call. Inspect it with
`engine.get_extraction(event_id, user_id="alice")` (also on `MemoryClient`).
The record includes the provider/model, source hash, grounding policy, and
structured output. It survives restart, but its presence does not prove graph
completion or semantic correctness. Complete prepared plans preserve graph identities
and embeddings; recovery publishes their graph changes atomically.

LLM `ingest()` persists an extraction job with the source event. After an outage
or restart, inspect and explicitly process that user's work:

```python
status = await engine.extraction_status(event_id, user_id="alice")
# Make a terminal failure eligible again; this does not call a provider
await engine.retry_extraction(event_id, user_id="alice")
result = await engine.process_extractions(user_id="alice", limit=100, budget_ms=5000)
print(result.processed, result.pending, result.failed)
```

These methods also exist on `MemoryClient`. Status reports `pending`, `running`,
`failed`, or `complete`, together with the current phase, attempts, lease expiry,
and a sanitized error code. Completion is recorded with the graph transaction,
including for an empty extraction. `processed` counts completions in this pass;
`pending` includes active workers; `failed` counts terminal failures needing retry.
The time budget is checked between jobs; a provider call may exceed it.

New failures in `status.last_error` preserve meaningful categories: `TimeoutError` for
a provider deadline, `AuthenticationError` or `RateLimitError` for provider
access/limits, and `ValidationError` for rejected structured output. Caller
cancellation remains `Cancelled`. Exception messages and provider response bodies
are not persisted. Correct the provider configuration or model issue before
explicitly retrying a terminal failure. These categories do not change retry limits.

For blocking ingestion, catch `ExtractionError` (available from `prme`):
`error.event_id` identifies the persisted source and `error.reason_code` provides
the sanitized failure category when available (it may be `None` for pending work). Inspect that event's scoped extraction status to
see whether recovery is pending or requires an explicit retry.

Active workers renew leases (`ExtractionConfig.lease_seconds`, default 300).
After an abrupt exit, work becomes claimable when its lease expires. Claims
serialize pending work within an owner and scope in append order. Bounded retry
attempts survive restart; manual retry preserves their history. Retrieval never
runs LLM recovery. Use repeated explicit passes or an external timer; there is
no background daemon. Existing sources predating durable extraction jobs are
not automatically enrolled.

If publication fails with `last_error="StaleDerivationPlanError"`, a referenced
memory changed after the plan was saved. Queue a new immutable plan revision:

```python
await engine.retry_extraction(event_id, user_id="alice", replan=True)
await engine.process_extractions(user_id="alice")
```

This preserves the original plan and saved extraction, invalidates its old worker
generation, and reports `plan_revision` in status. Processing reuses grounded
extraction but may compute new embeddings. Replanning leaves live or completed
work untouched; it requires an existing prepared plan. Repeated requests before
that new plan is prepared leave the same revision queued. This revises graph
planning, not the original model output or grounding policy.

For local packs, maintenance can reclaim external index entries belonging to
replaced revisions:

```python
await engine.organize(user_id="alice", jobs=["index_compaction"], budget_ms=5000)
```

Cleanup retains the source and plan journals, current retryable work, committed
graph nodes and ambiguous legacy identities. It considers up to 500 retired
identities per pass. Failed deletions remain discoverable for retry. Job details
report `retired_staging_found` and `stage_cleanup_reason` when incomplete or invalid
registration prevents reclamation. PostgreSQL writes prepared indexes inside
its graph transaction and has no separate native staging to collect.


HTTP exposes `GET /v1/events/{event_id}/extraction-status`,
`POST /v1/events/{event_id}/retry-extraction`, and `POST /v1/extractions/process`.
MCP exposes `memory_extraction_status`, `memory_retry_extraction`, and
`memory_process_extractions`, with the same authenticated owner boundaries.
Pass `?replan=true` on the HTTP retry endpoint, `replan: true` to the MCP retry
tool, or `--replan` to the CLI retry command for a new plan revision.
CLI equivalents are:

```bash
prme extraction-status memory.duckdb EVENT_ID --user-id alice --format json
prme retry-extraction memory.duckdb EVENT_ID --user-id alice
prme process-extractions memory.duckdb --user-id alice --budget-ms 5000
```

For local FastEmbed inference, PRME defaults `ORT_DISABLE_TELEMETRY=1` before
loading ONNX Runtime. This avoids an observed macOS shutdown failure in its
optional telemetry uploader. The setting is process-wide and an explicit value
is preserved. If your application imports ONNX Runtime first, set this variable
before that import to apply the same startup behavior.

For a custom Ollama extraction endpoint, use its OpenAI-compatible URL. On a
48 GB Apple Silicon development machine, the bounded `qwen3.5:35b-a3b` profile
in [`examples/ollama/qwen35b-a3b-8k.Modelfile`](examples/ollama/qwen35b-a3b-8k.Modelfile)
passed PRME's 12-case qualifier diagnostic twice and was faster than the tested
9B profile:

```bash
ollama pull qwen3.5:35b-a3b
ollama create prme-qwen3.5:35b-a3b-8k \
  -f examples/ollama/qwen35b-a3b-8k.Modelfile
```

```python
from prme.config import ExtractionConfig

extraction = ExtractionConfig(
    provider="ollama",
    model="prme-qwen3.5:35b-a3b-8k",
    base_url="http://localhost:11434/v1",
)
```

Structured extraction uses temperature zero by default to reduce output
variance. Set `temperature` directly or with `PRME_EXTRACTION_TEMPERATURE` only
after benchmarking the selected provider. Ollama extraction disables reasoning
by default so thinking tokens cannot consume the structured response window;
set `reasoning_effort` or `PRME_EXTRACTION_REASONING_EFFORT` to opt into it.
The adapter uses Ollama's constrained JSON output mode. The `/v1` path is
required by the extraction adapter; omitting `base_url` uses
the local default. The 35B-A3B
profile requires about 23 GB on disk and was observed at about 22 GB loaded;
use the same 8K profile with `qwen3.5:9b` on lower-memory systems. See the
[`raw diagnostic evidence`](benchmarks/results/extraction/2026-09-13/README.md)
for timings, hashes, limitations, and reproduction commands.

See [`examples/quickstart.py`](examples/quickstart.py) for a full walkthrough and [`examples/chat.py`](examples/chat.py) for a terminal chat app with persistent memory.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    PRME Engine                        │
├──────────────┬──────────────┬────────────┬───────────┤
│  Ingestion   │  Retrieval   │ Epistemic  │ Organizer │
│  Pipeline    │  Pipeline    │ State      │ Jobs      │
├──────────────┴──────────────┴────────────┴───────────┤
│                   Storage Layer                       │
│  ┌──────────┬───────────┬──────────────────────────┐ │
│  │ DuckDB   │ usearch   │ Tantivy                  │ │
│  │ Events + │ HNSW      │ Full-text                │ │
│  │ Graph    │ Vectors   │ Search                   │ │
│  └──────────┴───────────┴──────────────────────────┘ │
│           Optional: PostgreSQL backend                │
└──────────────────────────────────────────────────────┘
```

- **Ingestion Pipeline** — stores raw events, optionally extracts entities/facts/relationships via LLM (OpenAI, Anthropic, Ollama). Dual-stream mode atomically records events and deferred work; indexing resumes on retrieve/organize after a restart.
- **Retrieval Pipeline** — query analysis -> multi-source candidate generation -> deterministic scoring -> context packing. Supports event-time ranges and a disclosed `knowledge_at` ingestion cutoff over current indexes.
- **Epistemic State Model** — tracks confidence, lifecycle transitions (tentative -> stable -> superseded -> archived), contradiction detection, supersedence chains, oscillation dampening, and surprise-gated storage.
- **Organizer** — eleven implemented jobs, including consolidation, snapshot generation, and index compaction. Explicit passes run through `prme organize`. Retrieve/ingest can schedule opportunistic in-process maintenance; there is no built-in cron or daemon scheduler. Proposed jobs are not advertised as runnable work.
- **Storage** — DuckDB (events + graph), usearch (HNSW vectors), Tantivy (full-text). Optional PostgreSQL backend with asyncpg + pgvector.

Extractive consolidation is idempotent for an unchanged source set. The organizer
journals the complete prepared summary and embedding, stages local indexes under a
database fence, and atomically publishes the summary, provenance edges, predecessor
archival, and generation receipt. A retry after interruption reuses the saved
embedding; independent engine instances converge on one active summary. Adding or
removing a cluster member starts a separate lineage rather than guessing that the
new similarity cluster represents the same concept.

## CLI

PRME includes a command-line tool for setup and memory inspection:

```bash
prme init ./my_memories            # Initialize a new memory directory
prme doctor ./my_memories          # Check memory pack health
prme info ./memory.duckdb          # Memory pack statistics
prme nodes ./memory.duckdb         # List nodes (--type, --state, --limit)
prme search ./memory.duckdb "query" # Run hybrid retrieval
prme chain ./memory.duckdb <id>    # Show supersedence chain
prme organize ./memory.duckdb     # Run organizer jobs
prme stats ./memory.duckdb        # Detailed statistics
prme export ./memory.duckdb       # Export as JSON
```

Local file commands target the named pack even if `PRME_DATABASE_URL` is set.
With `--format json`, stdout contains the result and diagnostics go to stderr.
Interrupted entity profiles can be inspected and resumed without new model calls:

```bash
prme profile-jobs ./memory.duckdb --user-id alice --scope project --format json
prme process-profiles ./memory.duckdb --user-id alice --scope project --format json
```

See the [profile recovery guide](docs/ENTITY-PROFILES.md) for single-job retries,
abandonment, staging collection, bounded processing and exit codes.

For another embedding service or model, pass `embedding_provider=` to either
Python client. Providers can supply separate document and query encoders; see
the [custom embedding guide](docs/CUSTOM-EMBEDDINGS.md) for the contract, caching,
versioning and a runnable local example.

Applications opening several local packs can set `duckdb_threads` explicitly
and share a caller-owned embedding provider. See [local resource control](docs/LOCAL-RESOURCES.md)
for configuration, same-file constraints and the current named-project boundary.

For named projects, `MemoryWorkspace` manages identity-checked local packs or
PostgreSQL schemas with a bounded engine cache. `open_postgres()` shares one
connection pool across project engines. See [workspace usage and ownership](docs/WORKSPACES.md)
for concurrent leases, recovery, backup/restore and the hosted-access boundary.

Both storage backends default to exact vector search. PostgreSQL applies
eligibility before top-k ordering; see [search modes and costs](docs/POSTGRES-VECTOR-SEARCH.md)
before enabling approximate search on a large corpus.

## Following source evidence

`store()` returns its durable event ID. Resolve the associated nodes directly:

```python
event_id = client.store("Use the staging key only for staging.", user_id="alice")
nodes = client.get_event_nodes(event_id, user_id="alice")
source = client.get_event(event_id, user_id="alice")
```

`get_event_nodes` returns all nodes citing the owned event, including retired
nodes, in stable ID order. An empty result means there are no visible derivations;
it does not mean pending extraction finished. `get_event` returns the original
content and timestamps. Both async and sync clients support these methods.
For a retrieved node, follow its `evidence_refs` with `get_event` to inspect
qualifications and surrounding actions before drawing conclusions.

For an event submitted through `ingest()`, `get_extraction` returns its saved
model output or `None` when no owned record exists. HTTP exposes
`GET /v1/events/{event_id}/extraction`; MCP exposes `memory_get_extraction`.
Both bind access to the source owner's identity and perform no model calls.

## Build entity profiles

Use `consolidate_knowledge(user_id=..., scope=..., entity_names=[...])` to build
searchable profiles from complete source excerpts under an exact token budget.
Each replacement preserves the previous profile until its replacement is ready.
The [entity profile guide](docs/ENTITY-PROFILES.md) covers sync/async use, scope
isolation, explicit retries and current limits.

## Record relevance feedback

Every successful receipt log preserves returned candidates, score traces, content
hashes and context membership. `response.metadata.receipt_persisted` reports
whether that log succeeded; retrieval still works during a logging outage.
New receipts also preserve applied weights and neural/session score adjustments.
`receipt.replay_ranking()` reproduces the returned candidate order after restart,
without a model or the current graph. This covers the saved candidates; it cannot
recover candidates omitted during retrieval or prove improved answer quality.
Older version 1 receipts remain readable and usable for labels but cannot replay
their ranking because they lack that score provenance.

```python
from prme import MemoryClient, RelevanceSubmission

# Call this handler only after an explicit user judgment.
def save_user_judgment(memory, response, node_id, relevant):
    if not response.metadata.receipt_persisted:
        raise ValueError("This retrieval has no saved feedback receipt")
    submission = RelevanceSubmission(
        request_id=response.metadata.request_id,
        labels={node_id: relevant},
    )
    # Keep submission.feedback_id for retries after a lost acknowledgement.
    return memory.record_relevance(submission, user_id="alice")

with MemoryClient("./memories") as memory:
    response = memory.retrieve("Which database does Aster use?", user_id="alice")
    # Present results to your user, then call save_user_judgment with their label.
```

`get_retrieval_receipt`, `get_relevance` and `list_relevance` expose owned saved
records after restart. The synchronous client also exposes `promote(node_id,
user_id=...)` and `archive(node_id, user_id=...)`; archiving removes a node from
retrieval while preserving its source and prior receipts. HTTP provides `/v1/retrievals/{request_id}` and
`/v1/relevance`; MCP provides `memory_get_retrieval_receipt`,
`memory_record_relevance`, `memory_get_relevance` and `memory_list_relevance`.
Receipts add per-candidate metadata to the existing retrieval operation log;
they do not duplicate candidate text or prove an application used the context.
Labels preserve the original exposure when graph state changes. They do not
change facts or weights, and the legacy global feedback tuner does not consume
them. Default `organize()` and `end_session()` calls preserve ranking weights.
The legacy anonymous-feedback tuner requires an explicit unscoped operator call,
`organize(jobs=["feedback_apply"])`; passing `user_id` with that job raises
`ValueError` before any work. It affects every user of the engine and does not
activate an evaluated learning profile.

Record the memories an answer actually cites separately from relevance labels:

```python
from hashlib import sha256
from prme import AnswerCitationSubmission

citations = AnswerCitationSubmission(
    request_id=response.metadata.request_id,
    answer_id="assistant-message-42",
    cited_node_ids=(node_id,),
    answer_sha256=sha256(answer_text.encode()).hexdigest(),  # optional
    method="application_verified",
)
saved = memory.record_answer_citations(citations, user_id="alice")
```

Every cited node must have appeared as content in the saved context, rather than
only in results or as a reference. Pass an empty tuple to record that an answer
reported no memory citations. Keep `citations.citation_id` for retry safety.
These records survive graph changes and restart, and remain readable with
`get_answer_citations` and `list_answer_citations`. HTTP provides
`/v1/answer-citations`; MCP provides `memory_record_answer_citations`,
`memory_get_answer_citations`, and `memory_list_answer_citations`. A model report
is usage telemetry. It is not verified answer correctness or causal evidence
that uncited memories were unnecessary.

For a controlled re-answer test, `ablate_context(response.bundle, [node_id])`
removes one cited entry without mutating the pack, repacking, or changing any
other context byte. `assess_context_presence(...)` verifies the citation and
baseline context hash, then records whether the entry was load-bearing,
redundant for that answer, misleading, or noncuring under a named fixed
evaluation protocol. It returns an inspectable model and never changes ranking
or retention. See the [memory credit guide](docs/MEMORY-CREDIT.md) for the full
workflow and its limits.

In a registered 37-question development diagnostic, removing the sole annotated
source from an otherwise byte-identical balanced context reduced the fixed
reader from 29 correct answers to 7; 23 answers changed from correct to wrong.
The [complete ablation report](benchmarks/results/research/2026-09-13/CONTEXT-ABLATION-ANSWER.md)
retains every transition, the failed first run, and the important limits: this is
source-anchored development evidence, not model-citation calibration, bank-level
deletion, or a competitive benchmark holdout.

Evaluate a proposed adjustment after collecting explicit positive and negative
judgments across enough distinct queries:

```python
with MemoryClient("./memories") as memory:
    report = memory.evaluate_learning(user_id="alice")
    print(report.decision, report.coverage)
    # Save report.model_dump_json(indent=2) with your evaluation artifacts.
```

Pass `scopes=[Scope.PROJECT]` when judging retrievals made with that same scope
filter. The evaluator separates query groups, reports conflicting labels, fits
on training queries and checks the proposal on validation queries. It reads a
bounded snapshot; `max_records` overflow fails instead of truncating silently.
Insufficient evidence yields `insufficient_data`, and a failed validation gate
yields `no_improvement`. It leaves active weights unchanged. An offline success
covers the observed candidates, not full retrieval or generated answers.
[Scoped profile activation and rollback](docs/RFC-0017-Scoped-Retrieval-Learning.md)
remain pending.

Retrieval accepts `scope=Scope.PROJECT`, `scope="project"`, or a nonempty sequence
of scope enums/names. Only `scope=None` is unfiltered. Empty lists and invalid
names fail before pending-memory work is processed; caller mutation of a scope
list during retrieval cannot change that request's filter.

For a full-pipeline trial, pass `ranking_multipliers=report.multipliers` to
`retrieve()` on the async engine or sync client, or send a
`ranking_multipliers` object to HTTP `POST /v1/retrieve` or the MCP
`memory_retrieve` tool. The adjustment runs after
query-specific weight redistribution and before neural reranking, session
expansion, selection and packing. Each call keeps its own adjustment; defaults
and other owners' requests remain unchanged. Compare on a fixed memory pack
without concurrent writes or maintenance, using the same `reference_time` and
request filters. A trial may select different session neighbors and context than
offline replay predicts. An explicit trial is not automatic profile activation.

For example, an HTTP trial can use:

```json
{
  "query": "What telescope do I use?",
  "reference_time": "2026-09-12T18:00:00Z",
  "ranking_multipliers": {"semantic": 0.5, "lexical": 2},
  "filters": {"scope": "project", "include_cross_scope": false},
  "min_fidelity": "full",
  "token_budget": 2048
}
```

Multipliers range from 0.25 to 4; omitted features use 1. MCP takes scope and
temporal filters as top-level arguments, including `reference_time`,
`event_time_from`, `event_time_to` and `include_cross_scope`. Set
`include_context: true` to receive the actual rendered packed `context` alongside
results and metrics; the token budget applies to that context, not the complete
JSON response. Use `metrics.request_id` to read the owned receipt and label the
observed trial. HTTP exposes the packed bundle in its existing `bundle` field.

New version 3 receipts save the requested adjustment, temporal filters and
reported feature environment in `receipt.execution`. Versions 1 and 2 keep their
canonical JSON/checksums and continue to accept labels. Source-file hashes and
reported model names/versions describe the environment; they do not establish
that remote model weights are pinned. Response `metadata.timing_ms` includes
receipt logging, which is also reported as `receipt_logging_ms`; engine startup
and pre-retrieval queue draining are outside that pipeline timer.

## MCP server

Install `prme[mcp]`. For a local stdio assistant, set `PRME_MCP_USER_ID=alice`
and run `prme-mcp --db-path ./my_memories`. Tools inherit that owner when
`user_id` is omitted and reject another user; node resources and statistics
respect the same boundary. Without a fixed owner, stdio retains trusted local
operator access to the pack.

For multiple users over HTTP, configure separate credentials:

```dotenv
PRME_MCP_USER_KEYS={"alice":"replace-alice-secret","bob":"replace-bob-secret"}
```

Run `prme-mcp --transport streamable-http --db-path ./my_memories` and connect
to `http://127.0.0.1:8000/mcp` with `Authorization: Bearer <credential>`.
The `memory_get_event` tool reads original evidence by event ID.
Each stateless request authenticates independently. The server shares one engine
for its lifetime; tools and resources use the current request's identity.
HTTP requires per-user credentials. Fixed stdio owners and HTTP keys cannot be
combined. Keys must be provisioned to clients explicitly; this mode does not
issue OAuth tokens or offer OAuth discovery. Use TLS for network transport.

Migration: the former unauthenticated `--transport sse` option is rejected.
Use authenticated Streamable HTTP instead. HTTP API and MCP credentials are
separate settings. Tenant maintenance excludes the global feedback job.

## HTTP API

Install with `pip install prme[api]` and run:

```bash
uvicorn prme.api:app
```

For a shared HTTP server, configure a distinct bearer credential for each user
in `.env` (replace these example values with generated secrets):

```dotenv
PRME_API_USER_KEYS={"alice":"replace-alice-secret","bob":"replace-bob-secret"}
```

Send `Authorization: Bearer <credential>`. The server derives the owner from that
credential: store/ingest/retrieve can omit `user_id`, lists and statistics are
scoped automatically, and a different requested user returns 403. Foreign node
IDs return 404 for reads and mutations. Maintenance is scoped to the caller and
excludes the engine-global `feedback_apply` job, which remains an operator task.
Credentials are redacted from configuration output; restart the server to rotate
them. Use TLS when carrying bearer credentials over a network.

See the [HTTP source and recovery reference](docs/HTTP-API.md) for explicit
provenance, historical event times, TTL semantics, blocking extraction and
accepted-work recovery. Write requests reject unsupported fields.

Node and event path IDs are UUIDs. Malformed IDs return HTTP 422 with a path
validation error before accessing storage. Valid IDs that do not exist or belong
to another user return 404. The OpenAPI schema documents the UUID format.

The legacy `PRME_API_API_KEY` retains unrestricted operator access and cannot be
combined with per-user keys. With neither configured, the API is unrestricted
for local single-user use. This is application-level user isolation; PostgreSQL
row-level security, project membership grants, and OAuth federation are separate
work. These HTTP credentials do not configure the MCP server.

Endpoints under `/v1`:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/store` | Store a memory node |
| `POST` | `/v1/ingest` | LLM-powered ingestion |
| `POST` | `/v1/retrieve` | Hybrid retrieval |
| `GET` | `/v1/events/{id}` | Original source evidence |
| `GET` | `/v1/events/{id}/nodes` | Nodes citing that source |
| `GET` | `/v1/events/{id}/processing-status` | Saved source/index work status |
| `POST` | `/v1/materializations/process` | Repair the owner's pending source/index work |
| `POST` | `/v1/organize` | Run organizer jobs |
| `GET` | `/v1/nodes` | Query nodes with filters |
| `GET` | `/v1/nodes/{id}` | Get single node |
| `PUT` | `/v1/nodes/{id}/promote` | Promote lifecycle state |
| `PUT` | `/v1/nodes/{id}/archive` | Archive node |
| `PUT` | `/v1/nodes/{id}/reinforce` | Increase confidence |
| `GET` | `/v1/nodes/{id}/neighborhood` | Graph neighborhood |
| `GET` | `/v1/nodes/{id}/chain` | Supersedence chain |
| `GET` | `/v1/health` | Health check |
| `GET` | `/v1/stats` | Memory statistics |

## Configuration

PRME uses pydantic-settings. Configure via constructor arguments, environment variables (`PRME_` prefix), or `.env` files:

Settings load `.env` from the current working directory. Constructor values take
priority over process environment variables, which take priority over the file.
Nested settings support their documented prefixes and `__` paths (for example,
`PRME_PACKING__TOKEN_BUDGET=4096`). Unrelated application settings are ignored.
Extraction reads the selected provider's `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
and optional `*_BASE_URL` from the environment or `.env`, without exporting them
into process globals. `ExtractionConfig(api_key=..., base_url=...)` or
`PRME_EXTRACTION_API_KEY` / `PRME_EXTRACTION_BASE_URL` explicitly override those
provider settings. Recreate the client after changing credentials.

If updating `.env` does not change authentication behavior, run `prme doctor .`
from the project directory. It warns when a process environment variable
overrides a different file value, without displaying either value or contacting
the provider. Update or unset the named shell variable, then recreate the client.
For an IDE or service, update its launch environment and restart the process.

```bash
# Extraction provider
PRME_EXTRACTION_PROVIDER=openai        # openai | anthropic | ollama
PRME_EXTRACTION_MODEL=gpt-4o-mini

# Embedding
PRME_EMBEDDING_PROVIDER=fastembed      # fastembed (local, default) or openai
PRME_EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5

# Context packing
PRME_PACKING__CONTEXT_GUIDANCE_MODE=temporal  # temporal (default) | off | all

# Encryption at rest
PRME_ENCRYPTION_ENABLED=true           # Master toggle (default false)
PRME_ENCRYPTION_KEY=your-secret-key    # Passphrase (PBKDF2 -> Fernet AES-128-CBC + HMAC)
# Optional explicit key type (avoids passphrase/raw-key ambiguity):
#   PRME_ENCRYPTION_KEY=passphrase:your-secret-key   # always derive via PBKDF2
#   PRME_ENCRYPTION_KEY=raw_key:<44-char-fernet-key> # use a raw Fernet key
```

> **Plaintext window.** When the engine is open, pack files are decrypted to
> plaintext on disk and are re-encrypted on a clean `close()` (an `atexit`
> handler also re-encrypts on normal process exit as a best-effort safety
> net). A hard crash (`SIGKILL`, power loss) can leave the pack plaintext on
> disk until the next clean `close()`. Encryption secrets are held as
> `SecretStr` so they are never rendered by config dumps or tracebacks.

## Testing

```bash
# Run all tests
pytest tests/ -q

# Run simulations
python -m simulations --list           # List available scenarios
python -m simulations                  # Run all
python -m simulations changing_facts   # Run specific scenario

# CI checkpoint gate: retains failures and exits nonzero if any checkpoint fails
python -m scripts.run_simulations --output simulation-results.json

# Run benchmarks
python -m benchmarks                   # All benchmarks
python -m benchmarks epistemic         # Epistemic benchmark only

# Stress tests (opt-in)
PRME_STRESS_TESTS=1 pytest tests/test_stress.py
```

Simulations advance source arrival and memory-maintenance clocks together;
execution budgets still use real elapsed time. Fresh runs generate fresh object
IDs, and organizer excerpt selection can differ when source confidence ties.
They are workload checks, not replay of one identical event log. Preserve failed
checkpoints when comparing runs; passing unit tests alone does not establish
retrieval quality.

## Documentation

Detailed technical documentation lives in [`docs/`](docs/):

- [RFC Suite Overview](docs/RFC-0000-Suite-Overview.md)
- [Core Data Model](docs/RFC-0001-Core-Data-Model.md)
- [Event Store](docs/RFC-0002-Event-Store.md)
- [Epistemic State Model](docs/RFC-0003-Epistemic-State-Model.md)
- [Namespace & Scope Isolation](docs/RFC-0004-Namespace-and-Scope-Isolation.md)
- [Hybrid Retrieval Pipeline](docs/RFC-0005-Hybrid-Retrieval-Pipeline.md)
- [Decay and Forgetting](docs/RFC-0007-Decay-and-Forgetting.md)
- [Confidence Evolution](docs/RFC-0008-Confidence-Evolution.md)
- [Integration Guide](docs/INTEGRATION.md)
- [Full RFC Index](docs/INDEX.md) (15 RFCs)

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full development plan.

**Current (v0.11.0)** — hybrid retrieval, synchronous and async clients, MCP/REST, framework adapters, deterministic vector search, and index rebuilds.

**Next** — a trustworthy retrieval baseline, complete aggregation results, temporal state, and measured improvements to context packing. See the roadmap for acceptance criteria and GitHub issue links.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
