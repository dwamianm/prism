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
reliable evidence retrieval, complete aggregation, and correct temporal state.

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

## Why PRME?

Persistent memory helps an assistant carry context between conversations. It
also needs to preserve evidence, distinguish speculation from facts, and keep
earlier information available when circumstances change. PRME combines:

- **Durable source history** — immutable append-only events; rebuildable search indexes from the durable graph
- **Graph-based relational model** — 9 typed node kinds (entities, facts, preferences, decisions, tasks, instructions, summaries, events, notes) with edges capturing relationships, supersedence, and temporal validity
- **Epistemic state tracking** — memories have lifecycle states (tentative -> stable -> superseded -> archived), confidence scores, contradiction detection, and oscillation dampening
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

Deferred raw events survive restart. LLM `ingest()` also queues original-source
indexing atomically with its event, so extraction failure cannot make that source
unsearchable after restart. For these ingestion paths, processing status acknowledges raw NOTE indexing;
LLM extraction has its own durable work record and recovery API below.
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
Built-in providers must cite and classify relationships. Legacy/custom providers
that omit classification produce unverified model claims, excluded from default
retrieval at the standard confidence setting. A fact covering the same endpoints
and passage takes precedence over an additional relationship label; the saved
extraction still contains both. Existing committed graphs and saved plans retain
their original behavior; this change does not migrate historical edges.

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

For a custom Ollama extraction endpoint, use its OpenAI-compatible URL, for
example `ExtractionConfig(provider="ollama", model="qwen3.5:4b",
base_url="http://localhost:11434/v1")`. The `/v1` path is required by the
extraction adapter; omitting `base_url` uses the local default.

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
- **Retrieval Pipeline** — query analysis -> multi-source candidate generation -> deterministic scoring -> context packing. Supports bi-temporal queries with `knowledge_at` for point-in-time snapshots.
- **Epistemic State Model** — tracks confidence, lifecycle transitions (tentative -> stable -> superseded -> archived), contradiction detection, supersedence chains, oscillation dampening, and surprise-gated storage.
- **Organizer** — twelve registered jobs, including index compaction; `centrality_boost` is currently a stub. Explicit passes run through `prme organize`. Retrieve/ingest can schedule opportunistic in-process maintenance; there is no built-in cron or daemon scheduler.
- **Storage** — DuckDB (events + graph), usearch (HNSW vectors), Tantivy (full-text). Optional PostgreSQL backend with asyncpg + pgvector.

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

# Run simulations (19 scenarios)
python -m simulations --list           # List available scenarios
python -m simulations                  # Run all
python -m simulations changing_facts   # Run specific scenario

# Run benchmarks
python -m benchmarks                   # All benchmarks
python -m benchmarks epistemic         # Epistemic benchmark only

# Stress tests (opt-in)
PRME_STRESS_TESTS=1 pytest tests/test_stress.py
```

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
