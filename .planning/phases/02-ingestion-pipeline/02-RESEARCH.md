# Phase 2: Ingestion Pipeline - Research

**Researched:** 2026-02-19
**Domain:** LLM-powered structured extraction, multi-provider abstraction, async write queue, embedding provider abstraction for knowledge graph construction from conversation text
**Confidence:** MEDIUM-HIGH

## Summary

Phase 2 transforms PRME from a passive storage engine into an active memory system by building the ingestion pipeline: conversation messages go in, structured memory (events, entities, facts, relationships) comes out across all four storage backends. The core technical challenge is LLM-powered structured extraction -- converting freeform conversation text into typed graph nodes and edges with confidence scores, temporal references, and provenance tracking. The secondary challenge is the async write queue pattern required to handle DuckDB's single-writer constraint under concurrent HTTP load (INGE-05).

The research strongly recommends the **instructor** library (v1.14.5) as the extraction layer. Instructor provides a unified `from_provider()` API across OpenAI, Anthropic, and Ollama with Pydantic response models, automatic retry/validation, and async support -- exactly matching the user's decision for three extraction providers (OpenAI, Anthropic, one local). For embedding, the existing FastEmbedProvider from Phase 1 covers the local option; adding an OpenAI embedding provider (text-embedding-3-small, 1536 dims) satisfies the API-based requirement. The write queue pattern uses a dedicated `asyncio.Queue` with a single consumer coroutine that serializes all DuckDB writes, replacing the per-store `asyncio.Lock()` pattern from Phase 1.

**Primary recommendation:** Use instructor for structured extraction with Pydantic schema definitions for all extractable types. Build an ExtractionProvider Protocol mirroring the existing EmbeddingProvider Protocol. Implement the write queue as a background asyncio task consuming from an asyncio.Queue. Keep the extraction schema focused on a core subset of node types (Entity, Fact, Decision, Preference) rather than all eight, with the LLM extracting entities and relationships in a single call per the user's decision.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Extraction scope
- Rich extraction: named entities plus locations, temporal references, quantities, and events mentioned
- Same-pass extraction: entities AND relationships extracted together in one LLM call
- Extract conversation summaries alongside structured entities/facts for richer search content
- Best-effort entity merge at ingestion: match existing entities by name/type and link to the same node rather than always creating new ones
- Detect supersedence at ingestion: when new facts contradict existing ones, create supersedence chains immediately rather than deferring to Phase 5
- Store all extractions regardless of confidence -- everything starts as Tentative, organizer handles cleanup

#### Provider preferences
- Extraction and embedding providers configured independently (mix and match)
- Three extraction providers: OpenAI, Anthropic/Claude, and one local option
- API-based embedding provider alongside existing FastEmbed local option
- Provider abstraction designed to be extensible for future providers

#### Input model
- Primary input: single message (role + content) -- pipeline processes incrementally
- Also support batch ingestion for importing conversation history
- Both sync and async modes: default async (event persisted immediately, extraction in background) with option to wait for extraction completion

#### Confidence & failure handling
- No minimum confidence threshold -- store all extractions as Tentative
- On LLM extraction failure: event is persisted immediately, extraction queued for retry with exponential backoff
- Validate extracted entities/facts against source text -- discard ungrounded/hallucinated extractions

### Claude's Discretion
- Which node types to extract (full set vs core subset based on practical complexity)
- Extraction schema design (LLM output format)
- Single vs split LLM calls for extraction
- Specific local LLM option (Ollama, llama.cpp, etc.)
- Specific API embedding provider (OpenAI, Voyage, etc.)
- Input metadata schema (required vs optional fields beyond user_id/session_id/role/content)
- Retry count and backoff strategy for failed extractions

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INGE-01 | System extracts entities, facts, and relationships from conversation events using pluggable LLM providers | **instructor** library (v1.14.5) provides unified `from_provider()` API for OpenAI, Anthropic, Ollama. Pydantic response models define extraction schema. ExtractionProvider Protocol mirrors Phase 1's EmbeddingProvider pattern for pluggability. |
| INGE-02 | System supports at least OpenAI and one local option for LLM-powered extraction | instructor supports `"openai/gpt-4o-mini"`, `"anthropic/claude-3-5-sonnet"`, and `"ollama/llama3.2"` through the same `from_provider()` API. Ollama recommended as local option -- widely adopted, supports structured outputs natively, runs on macOS/Linux. |
| INGE-03 | System supports pluggable embedding providers (API-based and local model) | Phase 1's EmbeddingProvider Protocol + FastEmbedProvider covers local. New OpenAIEmbeddingProvider wraps `openai.AsyncOpenAI().embeddings.create()` for text-embedding-3-small (1536 dims, $0.00002/1k tokens). Dimension configurable via API parameter. |
| INGE-04 | System persists full conversation history as searchable events | Phase 1's EventStore.append() persists events. Phase 2 adds: event also indexed in LexicalIndex for content search, plus extraction results create graph nodes with evidence_refs pointing back to the source event. |
| INGE-05 | System uses a write queue pattern to handle DuckDB single-writer concurrency under HTTP API load | asyncio.Queue-based write queue with single consumer coroutine replaces per-store asyncio.Lock(). All write operations (event append, node/edge create, vector index, lexical index) flow through the queue. Producers await a Future that the consumer resolves on completion. |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| instructor | 1.14.5 | Structured LLM extraction with Pydantic models | 3M+ monthly downloads, 11k stars. Unified `from_provider()` API for OpenAI/Anthropic/Ollama. Automatic retry/validation on schema failures. Async support. Eliminates hand-rolled JSON parsing and provider-specific code. |
| openai | 2.21.0 | OpenAI API client for extraction + embedding | Official SDK. Async via AsyncOpenAI. Structured output via `response_format`. Embedding via `embeddings.create()`. Python 3.9-3.14. |
| anthropic | 0.83.0 | Anthropic Claude API client for extraction | Official SDK. Async via AsyncAnthropic. Structured output via tool_use. Python 3.9-3.14. Required by `instructor[anthropic]`. |
| ollama | 0.6.1 | Local LLM client for extraction | Official Python SDK. AsyncClient for async operations. Structured output via format parameter. Supports llama3.2, mistral-nemo, qwen2.5, etc. |
| dateparser | 1.3.0 | Temporal reference resolution | Parses "yesterday", "last week", "3 days ago" to actual datetimes. 200+ language locales. Used to resolve temporal references in extracted content per user requirement. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | (dep of openai/anthropic) | HTTP client | Already pulled in as transitive dependency. Used internally by OpenAI and Anthropic SDKs. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| instructor | Direct SDK calls (openai/anthropic) | More control but requires hand-rolling JSON schema conversion, retry logic, validation, provider-specific code. instructor abstracts all of this. |
| instructor | LiteLLM | LiteLLM is heavier (proxy server, cost tracking, 100+ providers). instructor is focused on structured extraction which is exactly what PRME needs. |
| instructor | Pydantic AI | Newer, less battle-tested. instructor has larger community and more examples for knowledge graph extraction specifically. |
| Ollama (local) | llama.cpp Python bindings | More direct but requires compiling from source, manual model management. Ollama provides a managed experience with one-command model downloads and an HTTP API. |
| Ollama (local) | vLLM | Production inference server, but heavyweight for a local development option. Ollama is simpler for single-user local extraction. |
| OpenAI embedding | Voyage AI embedding | Voyage has higher benchmark scores but OpenAI is more widely deployed, has simpler pricing, and the openai SDK is already a dependency. |
| dateparser | LLM-based date resolution | LLM resolution is slower, costs tokens, and less reliable for standard patterns. dateparser handles the common cases perfectly. LLM handles the ambiguous cases during extraction. |

**Installation:**
```bash
# Extraction providers
uv add instructor>=1.14
uv add openai>=2.20
uv add anthropic>=0.80
uv add ollama>=0.6

# Temporal resolution
uv add dateparser>=1.2

# Note: fastembed already in pyproject.toml from Phase 1
```

## Architecture Patterns

### Recommended Project Structure
```
src/
└── prme/
    ├── __init__.py                  # (existing)
    ├── config.py                    # Extended with extraction/embedding provider config
    ├── models/                      # (existing)
    │   ├── __init__.py
    │   ├── base.py
    │   ├── events.py
    │   ├── nodes.py
    │   └── edges.py
    ├── storage/                     # (existing)
    │   ├── engine.py                # Extended with write queue
    │   ├── event_store.py
    │   ├── graph_store.py
    │   ├── vector_index.py
    │   ├── lexical_index.py
    │   ├── embedding.py             # Extended with OpenAIEmbeddingProvider
    │   └── write_queue.py           # NEW: async write queue
    ├── ingestion/                   # NEW: ingestion pipeline package
    │   ├── __init__.py
    │   ├── pipeline.py              # IngestionPipeline orchestrator
    │   ├── extraction.py            # ExtractionProvider Protocol + implementations
    │   ├── schema.py                # Pydantic models for extraction output
    │   ├── entity_merge.py          # Best-effort entity deduplication
    │   ├── supersedence.py          # Contradiction detection + supersedence chain creation
    │   └── grounding.py             # Source text validation for hallucination filtering
    └── types.py                     # (existing)
```

### Pattern 1: ExtractionProvider Protocol with instructor Implementations

**What:** Define an ExtractionProvider Protocol (structural typing) for LLM-powered extraction. Implement it using instructor's `from_provider()` for each supported backend (OpenAI, Anthropic, Ollama). The protocol receives message text and returns a structured ExtractionResult (Pydantic model) containing entities, facts, relationships, and a conversation summary.

**When to use:** Every extraction call. This mirrors Phase 1's EmbeddingProvider pattern.

**Why Protocol:** Same reasoning as Phase 1 -- structural subtyping allows swapping providers without inheritance. Easy mocking for tests. Future providers (Gemini, Mistral) just implement the same methods.

**Example:**
```python
from typing import Protocol, runtime_checkable
from pydantic import BaseModel, Field

# Extraction output schema
class ExtractedEntity(BaseModel):
    name: str = Field(description="Entity name")
    entity_type: str = Field(description="Type: person, organization, location, product, concept")
    description: str | None = Field(default=None, description="Brief description from context")

class ExtractedFact(BaseModel):
    subject: str = Field(description="Entity this fact is about")
    predicate: str = Field(description="Relationship or attribute")
    object: str = Field(description="Value or target entity")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    temporal_ref: str | None = Field(default=None, description="Temporal reference if mentioned")

class ExtractedRelationship(BaseModel):
    source_entity: str = Field(description="Source entity name")
    target_entity: str = Field(description="Target entity name")
    relationship_type: str = Field(description="Type of relationship")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    summary: str | None = Field(default=None, description="Conversation summary")

@runtime_checkable
class ExtractionProvider(Protocol):
    """Protocol for LLM-powered structured extraction."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    async def extract(self, content: str, *, role: str = "user") -> ExtractionResult: ...
```

### Pattern 2: instructor-based ExtractionProvider Implementation

**What:** Concrete implementation using instructor's `from_provider()`. Each provider (OpenAI, Anthropic, Ollama) uses the same instructor API with different provider strings.

**Example:**
```python
import instructor

class InstructorExtractionProvider:
    """ExtractionProvider using instructor for any supported LLM."""

    def __init__(self, provider_string: str, *, model: str | None = None):
        # e.g., "openai/gpt-4o-mini", "anthropic/claude-3-5-sonnet", "ollama/llama3.2"
        self._provider_string = provider_string
        self._model = model
        self._client = instructor.from_provider(provider_string, async_client=True)

    @property
    def provider_name(self) -> str:
        return self._provider_string.split("/")[0]

    @property
    def model_name(self) -> str:
        return self._provider_string

    async def extract(self, content: str, *, role: str = "user") -> ExtractionResult:
        return await self._client.create(
            response_model=ExtractionResult,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": role, "content": content},
            ],
            max_retries=3,
        )

EXTRACTION_SYSTEM_PROMPT = """You are a knowledge extraction system. Extract structured information from the conversation message.

Extract:
1. Named entities (people, organizations, locations, products, concepts)
2. Facts (subject-predicate-object triples with confidence)
3. Relationships between entities
4. A brief summary of the message content

For temporal references like "yesterday", "last week", "in March", include the raw temporal reference text.
Only extract information that is explicitly stated or strongly implied by the text.
Do NOT infer facts that are not grounded in the source text."""
```

### Pattern 3: Async Write Queue with Future-based Response

**What:** Replace per-store `asyncio.Lock()` with a centralized write queue. All write operations are submitted as work items to an `asyncio.Queue`. A single consumer coroutine processes items sequentially, ensuring DuckDB single-writer safety. Each submission returns a `Future` that the caller can await for the result.

**When to use:** All write operations in the ingestion pipeline. This is the INGE-05 requirement.

**Why:** The Phase 1 pattern of per-store `asyncio.Lock()` works for single-caller scenarios but becomes a bottleneck under concurrent HTTP load. Multiple callers waiting on separate locks (EventStore lock, GraphStore lock, VectorIndex lock) can deadlock if the ordering varies. A single queue ensures total ordering of all writes.

**Example:**
```python
import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

@dataclass
class WriteJob:
    """A unit of work for the write queue."""
    coro_factory: Callable[[], Coroutine[Any, Any, Any]]
    future: asyncio.Future

class WriteQueue:
    """Async write queue serializing all storage backend writes."""

    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue[WriteJob | None] = asyncio.Queue(maxsize=maxsize)
        self._consumer_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the consumer coroutine."""
        self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        """Signal the consumer to stop and wait for completion."""
        await self._queue.put(None)  # Sentinel
        if self._consumer_task:
            await self._consumer_task

    async def submit(self, coro_factory: Callable[[], Coroutine]) -> Any:
        """Submit a write operation and await its result."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self._queue.put(WriteJob(coro_factory=coro_factory, future=future))
        return await future

    async def _consume(self) -> None:
        """Process write jobs sequentially."""
        while True:
            job = await self._queue.get()
            if job is None:
                break  # Shutdown sentinel
            try:
                result = await job.coro_factory()
                job.future.set_result(result)
            except Exception as exc:
                job.future.set_exception(exc)
            finally:
                self._queue.task_done()
```

### Pattern 4: Two-Phase Ingestion (Persist-then-Extract)

**What:** The ingestion pipeline has two phases: (1) persist the event immediately (sync path), (2) extract and store derived structures (async background or awaitable). This matches the user's decision for "default async with option to wait."

**When to use:** Every message ingestion call.

**Example:**
```python
class IngestionPipeline:
    """Orchestrates message ingestion: persist event, then extract."""

    async def ingest(
        self,
        content: str,
        *,
        user_id: str,
        role: str = "user",
        session_id: str | None = None,
        wait_for_extraction: bool = False,
    ) -> str:
        """Ingest a message. Returns event_id immediately (default) or after extraction."""

        # Phase 1: Persist event (MUST succeed)
        event = Event(content=content, user_id=user_id, role=role, session_id=session_id)
        event_id = await self._write_queue.submit(
            lambda: self._event_store.append(event)
        )

        # Phase 2: Extract and store derived structures
        extraction_task = asyncio.create_task(
            self._extract_and_store(event, event_id)
        )

        if wait_for_extraction:
            await extraction_task

        return event_id

    async def _extract_and_store(self, event: Event, event_id: str) -> None:
        """Extract entities/facts/relationships and store in graph/vector/lexical."""
        try:
            result = await self._extraction_provider.extract(event.content, role=event.role)
            # Validate extractions against source text
            result = await self._grounding_validator.validate(result, event.content)
            # Merge entities, detect supersedence, create nodes/edges
            await self._materialize(result, event, event_id)
        except Exception:
            # Queue for retry with exponential backoff
            await self._retry_queue.enqueue(event_id)
```

### Pattern 5: Best-Effort Entity Merge at Ingestion

**What:** When the extraction produces an entity like "Sarah", check if an entity node with the same name and type already exists for this user. If found, reuse the existing node ID rather than creating a duplicate. The merge is conservative -- only match on exact name + type.

**When to use:** During materialization of extraction results into graph nodes.

**Why conservative:** The user explicitly stated "better to create a duplicate than incorrectly merge two different entities." Name + type match is the safest heuristic. More sophisticated merging (alias resolution, fuzzy matching) is deferred to Phase 5's organizer.

**Example:**
```python
async def find_existing_entity(
    self,
    name: str,
    entity_type: str,
    user_id: str,
) -> MemoryNode | None:
    """Find an existing entity by name and type for merge."""
    nodes = await self._graph_store.query_nodes(
        node_type=NodeType.ENTITY,
        user_id=user_id,
    )
    # Conservative match: exact name (case-insensitive) + same type
    for node in nodes:
        if node.metadata and node.metadata.get("entity_type") == entity_type:
            if node.content.strip().lower() == name.strip().lower():
                return node
    return None
```

### Pattern 6: Supersedence Detection at Ingestion

**What:** When a new fact is extracted that contradicts an existing fact about the same entity, immediately create a supersedence chain. For example, if "Sarah left Google" is extracted and "Sarah works_at Google" exists, the old fact is superseded.

**When to use:** During materialization, after entity merge has identified the relevant entity node.

**Key insight from user decisions:** "Sarah left Google" should immediately flag existing "Sarah works_at Google" facts. This is high-value but must be conservative -- only supersede when the contradiction is clear.

**Example:**
```python
async def detect_supersedence(
    self,
    new_fact: ExtractedFact,
    entity_node_id: str,
    user_id: str,
    event_id: str,
) -> list[str]:
    """Check if new fact contradicts existing facts about the same entity."""
    superseded_ids = []
    existing_edges = await self._graph_store.get_edges(source_id=entity_node_id)
    for edge in existing_edges:
        existing_node = await self._graph_store.get_node(str(edge.target_id))
        if existing_node and existing_node.node_type == NodeType.FACT:
            # Check for semantic contradiction
            if self._facts_contradict(new_fact, existing_node):
                superseded_ids.append(str(existing_node.id))
    return superseded_ids
```

### Anti-Patterns to Avoid

- **Making extraction synchronous in the write path:** Event persistence must be fast. Extraction can take 1-5 seconds (LLM API latency). Always persist the event first, then extract asynchronously.
- **Using separate asyncio.Locks per store under concurrent load:** Per-store locks can cause ordering issues and potential deadlocks. Use a single write queue for all backend writes.
- **Trusting LLM extraction output without validation:** LLMs hallucinate entities not present in source text. Always validate extracted names/facts against the source content before persisting.
- **Creating new entity nodes for every extraction:** Without entity merge, the graph quickly fills with duplicates ("Sarah", "Sarah Smith", "sarah" as three separate entities). Conservative merge at ingestion reduces this significantly.
- **Deferring all supersedence to the organizer:** Obvious contradictions ("Sarah left Google" vs "Sarah works_at Google") should be caught immediately. Waiting for the Phase 5 organizer means the graph temporarily contains conflicting active facts.
- **Using instructor's sync client in async code:** instructor supports async via `async_client=True` in `from_provider()`. Using the sync client with `asyncio.to_thread()` is wasteful since the underlying HTTP calls are natively async.
- **Making the extraction schema too complex:** Asking the LLM to extract all 8 node types in one call reduces quality. Focus on the types that actually appear in conversations: Entity, Fact, Decision, Preference. Summary is extracted separately.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LLM structured extraction | Custom JSON parsing + prompt engineering per provider | instructor library | Handles schema conversion, retry, validation, multi-provider support. 3M+ downloads. Battle-tested. |
| Provider abstraction | Custom API client wrappers per LLM provider | instructor `from_provider()` | Unified interface across 15+ providers. Handles provider-specific differences (tool_use vs response_format vs format param). |
| Temporal reference parsing | Regex-based date extraction | dateparser library | Handles "yesterday", "last week", "3 days ago", "in March", 200+ locales. Robust edge case handling. |
| Retry with backoff | Custom retry loop | instructor built-in `max_retries` + tenacity for queue retries | instructor handles extraction retries natively. tenacity handles queue-level retry with configurable backoff. |
| Entity name matching | Custom string similarity | Case-insensitive exact match (conservative) | Fuzzy matching introduces false merges. Conservative exact match is safe. Phase 5 organizer handles alias resolution. |
| Embedding provider switching | Environment-variable-driven if/else chains | EmbeddingProvider Protocol + factory function | Protocol pattern from Phase 1 already works. Add a factory that reads config and returns the right provider. |

**Key insight:** instructor is the critical "don't hand-roll" item. Building custom structured extraction with raw OpenAI/Anthropic/Ollama SDKs requires: JSON schema conversion per provider, response parsing per provider, retry logic, validation, error handling. instructor handles all of this through a single `create()` call.

## Common Pitfalls

### Pitfall 1: LLM Extraction Hallucinations Corrupting the Graph

**What goes wrong:** The LLM extracts entities or facts not present in the source text. These phantom entities enter the graph store as Tentative nodes, polluting search results and entity merge.

**Why it happens:** LLMs generate plausible completions based on training data, not just the input text. Asked to "extract entities from X", they may include entities they associate with the topic but that aren't actually mentioned.

**How to avoid:** Implement a grounding validation step between extraction and materialization. For each extracted entity name, verify it appears (case-insensitive substring match) in the source content. For extracted facts, verify both subject and object are grounded. Discard ungrounded extractions and log them for monitoring.

**Warning signs:** Entity nodes in the graph with names that don't appear in any event content. Facts about entities that were never mentioned in conversation.

### Pitfall 2: Entity Merge Incorrectly Conflating Different Entities

**What goes wrong:** Two different entities with the same name (e.g., "Jordan" the person and "Jordan" the country) are merged into a single node, contaminating the entity's fact graph.

**Why it happens:** Name-only matching without type discrimination. Or type classification is too coarse (both classified as "entity" rather than "person" vs "location").

**How to avoid:** Always match on both name AND entity_type. Use specific entity types (person, organization, location, product, concept) not just "entity". When in doubt, create a new node -- duplicates are cleaned up by the Phase 5 organizer; incorrect merges are much harder to fix.

**Warning signs:** Entity nodes with contradictory facts (e.g., a "Jordan" node with both "population: 10 million" and "works at Google" facts).

### Pitfall 3: Write Queue Backpressure Under Burst Load

**What goes wrong:** Batch ingestion of conversation history floods the write queue faster than it can process, causing memory growth and eventually OOM or extreme latency.

**Why it happens:** The queue has no backpressure mechanism. Producers submit work items without waiting for queue capacity.

**How to avoid:** Set `maxsize` on the `asyncio.Queue`. When the queue is full, `queue.put()` blocks the producer, providing natural backpressure. For batch ingestion, use a bounded semaphore to limit concurrent submissions. Monitor queue depth as a health metric.

**Warning signs:** Queue size growing monotonically during batch operations. Memory usage increasing linearly with batch size.

### Pitfall 4: instructor Provider Initialization Requires API Keys at Import Time

**What goes wrong:** `instructor.from_provider("openai/gpt-4o-mini")` requires OPENAI_API_KEY to be set. If the extraction provider is initialized at module import or MemoryEngine creation, missing API keys crash the entire system even if extraction is not needed.

**Why it happens:** The underlying SDKs (openai, anthropic) validate API keys during client construction.

**How to avoid:** Lazy initialization of the extraction provider. Don't create the instructor client until the first `extract()` call. Store the provider string and create the client on demand. This also enables graceful fallback -- if OpenAI is unavailable, the system still persists events.

**Warning signs:** ImportError or AuthenticationError during MemoryEngine initialization when API keys are not configured.

### Pitfall 5: Supersedence Detection False Positives

**What goes wrong:** A new fact that is merely additional information (not contradictory) incorrectly supersedes an existing fact. For example, "Sarah is a senior engineer" supersedes "Sarah works at Google" because both are about Sarah.

**Why it happens:** Overly broad contradiction detection. Any two facts about the same entity are treated as contradictory.

**How to avoid:** Supersedence should only trigger when the predicate matches and the object differs. "Sarah works_at Google" vs "Sarah works_at Meta" is a contradiction. "Sarah works_at Google" vs "Sarah is a senior engineer" is not -- they have different predicates. Use predicate matching, not just entity matching.

**Warning signs:** Superseded fact chains that don't actually represent corrections. High supersedence rate relative to new fact rate.

### Pitfall 6: Ollama Model Not Downloaded Before First Extraction

**What goes wrong:** The first extraction call to Ollama fails because the model hasn't been pulled yet. Unlike OpenAI/Anthropic where models are remote, Ollama requires local model files.

**Why it happens:** Ollama models must be downloaded via `ollama pull model_name` before use. The Python SDK's `chat()` call fails if the model isn't available locally.

**How to avoid:** Add a model health check during provider initialization. Call `ollama.show(model_name)` to verify the model is available. If not, either auto-pull or raise a clear error message with instructions.

**Warning signs:** ConnectionError or 404 responses from Ollama during extraction.

## Code Examples

### instructor Extraction with Pydantic Schema

```python
# Source: instructor docs (python.useinstructor.com)
import instructor
from pydantic import BaseModel, Field

class ExtractedEntity(BaseModel):
    """An entity extracted from conversation text."""
    name: str = Field(description="Entity name as it appears in text")
    entity_type: str = Field(description="One of: person, organization, location, product, concept, event")
    description: str | None = Field(default=None, description="Brief contextual description")

class ExtractedFact(BaseModel):
    """A fact (subject-predicate-object triple) extracted from text."""
    subject: str = Field(description="Entity this fact is about")
    predicate: str = Field(description="Relationship or attribute type")
    object: str = Field(description="Value or target entity")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Extraction confidence")
    temporal_ref: str | None = Field(default=None, description="Raw temporal reference if any")

class ExtractedRelationship(BaseModel):
    """A relationship between two entities."""
    source_entity: str = Field(description="Source entity name")
    target_entity: str = Field(description="Target entity name")
    relationship_type: str = Field(description="Edge type: relates_to, part_of, caused_by, supports, mentions")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

class ExtractionResult(BaseModel):
    """Complete extraction result from a single message."""
    entities: list[ExtractedEntity] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    summary: str | None = Field(default=None, description="Brief summary of the message")

# Usage with OpenAI
client = instructor.from_provider("openai/gpt-4o-mini", async_client=True)
result = await client.create(
    response_model=ExtractionResult,
    messages=[
        {"role": "system", "content": "Extract structured information from this message."},
        {"role": "user", "content": "Sarah told me she left Google last month to join Meta as a senior engineer."},
    ],
    max_retries=3,
)
# result.entities: [ExtractedEntity(name="Sarah", entity_type="person", ...),
#                   ExtractedEntity(name="Google", entity_type="organization", ...),
#                   ExtractedEntity(name="Meta", entity_type="organization", ...)]
# result.facts: [ExtractedFact(subject="Sarah", predicate="left", object="Google", temporal_ref="last month"),
#                ExtractedFact(subject="Sarah", predicate="joined", object="Meta", ...),
#                ExtractedFact(subject="Sarah", predicate="role", object="senior engineer", ...)]
```

### OpenAI Embedding Provider

```python
# Source: OpenAI Python SDK docs, embeddings API reference
from openai import AsyncOpenAI

class OpenAIEmbeddingProvider:
    """EmbeddingProvider using OpenAI's text-embedding-3-small model."""

    _KNOWN_DIMENSIONS: dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        *,
        api_key: str | None = None,
        dimension: int | None = None,
    ):
        self._model_name = model_name
        self._dimension = dimension or self._KNOWN_DIMENSIONS.get(model_name, 1536)
        self._client: AsyncOpenAI | None = None
        self._api_key = api_key

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_version(self) -> str:
        return f"openai-{self._model_name}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Synchronous embed (required by EmbeddingProvider Protocol)."""
        import asyncio
        return asyncio.run(self._embed_async(texts))

    async def _embed_async(self, texts: list[str]) -> list[list[float]]:
        client = self._ensure_client()
        response = await client.embeddings.create(
            model=self._model_name,
            input=texts,
            dimensions=self._dimension,
        )
        return [item.embedding for item in response.data]
```

### Async Write Queue

```python
# Source: Python asyncio.Queue documentation + producer-consumer pattern
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

@dataclass
class WriteJob:
    coro_factory: Callable[[], Coroutine[Any, Any, Any]]
    future: asyncio.Future
    label: str = ""  # For logging/debugging

class WriteQueue:
    """Serializes all write operations through a single async consumer."""

    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue[WriteJob | None] = asyncio.Queue(maxsize=maxsize)
        self._consumer_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self._queue.put(None)
        if self._consumer_task:
            await self._consumer_task

    async def submit(self, coro_factory: Callable[[], Coroutine], label: str = "") -> Any:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self._queue.put(WriteJob(coro_factory=coro_factory, future=future, label=label))
        return await future

    async def _consume(self) -> None:
        while True:
            job = await self._queue.get()
            if job is None:
                break
            try:
                result = await job.coro_factory()
                if not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:
                logger.error("Write queue job failed: %s", job.label, exc_info=True)
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()
```

### Grounding Validation

```python
# Source: Entity-grounding validation pattern for LLM extraction
def validate_grounding(result: ExtractionResult, source_text: str) -> ExtractionResult:
    """Filter out extracted items not grounded in source text."""
    source_lower = source_text.lower()

    grounded_entities = []
    for entity in result.entities:
        if entity.name.lower() in source_lower:
            grounded_entities.append(entity)
        else:
            logger.warning("Discarding ungrounded entity: %s", entity.name)

    grounded_facts = []
    for fact in result.facts:
        # Subject must be grounded (it's an entity name)
        if fact.subject.lower() in source_lower:
            grounded_facts.append(fact)
        else:
            logger.warning("Discarding ungrounded fact: subject=%s", fact.subject)

    grounded_relationships = []
    for rel in result.relationships:
        if (rel.source_entity.lower() in source_lower and
            rel.target_entity.lower() in source_lower):
            grounded_relationships.append(rel)
        else:
            logger.warning(
                "Discarding ungrounded relationship: %s -> %s",
                rel.source_entity, rel.target_entity,
            )

    return ExtractionResult(
        entities=grounded_entities,
        facts=grounded_facts,
        relationships=grounded_relationships,
        summary=result.summary,  # Summary is always kept
    )
```

## Discretion Decisions

### Node Types to Extract: Core Subset (Entity, Fact, Decision, Preference)

**Decision:** Extract four node types from conversations: Entity, Fact, Decision, Preference. Summary is generated per-message as a text field but not as a separate node type. Event, Task, and Note are created by other codepaths.

**Rationale:**
- **Entity:** The foundation of the knowledge graph. Every person, organization, location, product, concept mentioned in conversation.
- **Fact:** Subject-predicate-object triples. The primary structured knowledge type. "Sarah works_at Google", "React uses virtual DOM", etc.
- **Decision:** Decisions made or communicated in conversation. "We decided to use PostgreSQL." High value for agent memory.
- **Preference:** User preferences expressed in conversation. "I prefer dark mode." Direct input for personalization.
- **NOT Event:** Event nodes represent happenings mentioned in text ("the meeting happened"). These overlap with Facts and add extraction complexity without clear value in Phase 2.
- **NOT Task:** Tasks require action-tracking semantics (status, assignee, deadline) that extraction can't reliably produce.
- **NOT Note/Summary as separate nodes:** Summaries are extracted as text and stored as event metadata / searchable content, not as separate graph nodes (that's Phase 5 summarization).

### Extraction Schema Design: Single LLM Call with Combined Schema

**Decision:** One LLM call per message extracts all four types plus a summary. The ExtractionResult Pydantic model contains lists of entities, facts, relationships, and an optional summary string.

**Rationale:**
- User decision: "Same-pass extraction: entities AND relationships extracted together in one LLM call."
- Single call is cheaper and faster than multiple calls per message.
- Modern LLMs (GPT-4o-mini, Claude Sonnet) handle combined extraction well with structured output.
- instructor's Pydantic integration makes the combined schema trivial to define.
- Trade-off: single call may extract less precisely than specialized per-type calls. But for Tentative assertions that the organizer will refine, this is acceptable.

### Local LLM Option: Ollama

**Decision:** Use Ollama as the local LLM option.

**Rationale:**
- Most widely adopted local LLM runner. One-command installation (`brew install ollama` on macOS).
- Native structured output support (JSON schema via format parameter).
- instructor supports Ollama natively via `from_provider("ollama/model")`.
- Supports function-calling-capable models: llama3.2, mistral-nemo, qwen2.5.
- HTTP API means no compiled dependencies -- just install the ollama Python SDK.
- llama.cpp is more performant but requires compilation and manual model management.
- vLLM is overkill for a local development option.

### API Embedding Provider: OpenAI text-embedding-3-small

**Decision:** Use OpenAI's text-embedding-3-small (1536 dimensions) as the API-based embedding provider.

**Rationale:**
- OpenAI SDK is already a dependency (for extraction).
- text-embedding-3-small costs $0.02/million tokens -- extremely cheap for embedding.
- 1536 dimensions is a good balance of quality and storage. Supports dimension reduction via API parameter.
- 8191 token context length is sufficient for message-level embedding.
- Alternative (Voyage AI) scores higher on some benchmarks but adds another dependency and API key.
- The EmbeddingProvider Protocol makes switching trivial in the future.

**Dimension consideration:** FastEmbed (Phase 1) uses 384-dim vectors. OpenAI uses 1536-dim. The VectorIndex stores dimension metadata per vector, so switching providers triggers re-embedding (as designed in Phase 1). The vector index must be initialized with the correct dimension for the active provider.

### Input Metadata Schema

**Decision:** Required fields: user_id, role, content. Optional: session_id, metadata dict. The metadata dict can contain arbitrary key-value pairs.

**Rationale:**
- Matches the existing Event model from Phase 1 (user_id, role, content, session_id, metadata).
- No new required fields to avoid breaking the simple API.
- Developers can pass additional context (e.g., timestamp, source system) via metadata dict.
- The pipeline always sets scope to PERSONAL by default; overridable via metadata.

### Retry Strategy: 3 Retries with Exponential Backoff

**Decision:** instructor handles per-extraction retries (max_retries=3). For queue-level failures (extraction completely fails after retries), use exponential backoff: 5s, 30s, 180s, then give up and log.

**Rationale:**
- instructor's built-in retry handles transient validation failures (LLM returns malformed JSON).
- Queue-level retry handles provider outages and rate limiting.
- 3 queue retries over ~3.5 minutes total is sufficient for transient issues.
- After all retries fail, the event is persisted (it was stored in Phase 1 of ingestion) but extraction is marked as failed. The event can be re-processed manually or by the organizer.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Rule-based NER (spaCy, NLTK) for entity extraction | LLM-based structured extraction via instructor + Pydantic | 2024-2025 | Dramatically higher quality extraction. No training data needed. Handles context and nuance. |
| Custom JSON parsing per LLM provider | instructor unified `from_provider()` API | 2025-2026 | Single interface for 15+ providers. Automatic retry/validation. |
| Separate LLM calls per extraction type | Single-call combined extraction with structured output | 2024-2025 | Lower cost, lower latency. Modern models handle combined extraction well. |
| OpenAI text-embedding-ada-002 (1536 fixed) | OpenAI text-embedding-3-small (configurable dimensions) | Jan 2024 | Better multilingual performance. Dimension reduction via API parameter. Lower cost. |
| Synchronous write serialization with threading.Lock | asyncio.Queue-based write queue | Standard pattern | Better integration with async frameworks (FastAPI in Phase 4). No GIL contention. |

**Deprecated/outdated:**
- **instructor `from_openai()` / `from_anthropic()`**: Superseded by unified `from_provider()` in recent versions. Legacy methods still work but `from_provider()` is the recommended path.
- **OpenAI text-embedding-ada-002**: Replaced by text-embedding-3-small (better, cheaper). Still works but not recommended for new projects.
- **Rule-based NER for knowledge graph construction**: Still useful for high-volume/low-latency scenarios, but LLM extraction is superior for conversational text with context-dependent entities.

## Open Questions

1. **instructor async behavior with Ollama under load**
   - What we know: instructor supports `async_client=True` for Ollama. Ollama itself processes one request at a time on the GPU.
   - What's unclear: Whether concurrent async extraction calls to Ollama queue properly or timeout. What happens when Ollama's inference queue is full.
   - Recommendation: Set a reasonable timeout (30s for local models). Implement a semaphore to limit concurrent Ollama extraction calls to 1-2.

2. **Supersedence detection accuracy**
   - What we know: Predicate-matching is the correct approach. "works_at Google" contradicted by "left Google" or "works_at Meta."
   - What's unclear: How to handle predicate normalization. "works at" vs "employed by" vs "joined" -- are these the same predicate?
   - Recommendation: Start with exact predicate match. If hit rate is too low, add a small set of predicate equivalence classes (works_at/employed_by/joined, lives_in/resides_in, etc.). Full semantic matching is Phase 5 territory.

3. **OpenAI embedding dimension mismatch with existing FastEmbed vectors**
   - What we know: Phase 1 uses FastEmbed (384 dims). OpenAI uses 1536 dims. The vector index must have a single dimension at any time.
   - What's unclear: Whether switching embedding providers requires re-indexing all existing vectors, or whether the system should support multiple dimension indices.
   - Recommendation: Single active embedding provider, single dimension. Switching providers is a configuration change that requires re-embedding. The vector_metadata table already tracks model info per vector; a mismatch check on startup warns the operator. Re-indexing from the event log is a Phase 6 rebuild capability.

4. **Grounding validation strictness**
   - What we know: Substring match for entity names works for exact mentions. User decided to "discard ungrounded/hallucinated extractions."
   - What's unclear: How to handle paraphrased references. "The company" referring to Google, or "she" referring to Sarah. These are grounded in context but not as exact substrings.
   - Recommendation: Start with substring match for entity names. Accept that some valid context-dependent references will be filtered out. Better to discard a valid extraction than to accept a hallucinated one. Phase 5 can re-process events with more sophisticated coreference resolution.

## Sources

### Primary (HIGH confidence)
- [instructor PyPI](https://pypi.org/project/instructor/) -- v1.14.5, Jan 29 2026, Python 3.9+, 3M+ monthly downloads
- [instructor docs](https://python.useinstructor.com/) -- from_provider API, response_model, retry, async support
- [instructor Ollama integration](https://python.useinstructor.com/integrations/ollama/) -- from_provider("ollama/model"), TOOLS/JSON modes, async support
- [instructor Anthropic integration](https://python.useinstructor.com/integrations/anthropic/) -- pip install instructor[anthropic], Mode.TOOLS
- [instructor knowledge graph example](https://python.useinstructor.com/examples/building_knowledge_graphs/) -- Node/Edge/KnowledgeGraph Pydantic models
- [OpenAI Python SDK PyPI](https://pypi.org/project/openai/) -- v2.21.0, Feb 14 2026, AsyncOpenAI, Python 3.9-3.14
- [Anthropic Python SDK PyPI](https://pypi.org/project/anthropic/) -- v0.83.0, Feb 19 2026, AsyncAnthropic, Python 3.9-3.14
- [Ollama Python SDK PyPI](https://pypi.org/project/ollama/) -- v0.6.1, Nov 13 2025, AsyncClient, Python 3.8+
- [OpenAI Embeddings API](https://platform.openai.com/docs/api-reference/embeddings) -- text-embedding-3-small, dimensions parameter, 8191 token limit
- [DuckDB Concurrency](https://duckdb.org/docs/stable/connect/concurrency) -- single-writer model, optimistic concurrency control
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) -- JSON schema format parameter

### Secondary (MEDIUM confidence)
- [Anthropic structured outputs announcement](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) -- beta since Nov 2025, Sonnet 4.5 + Opus 4.1
- [dateparser docs](https://dateparser.readthedocs.io/en/latest/) -- v1.3.0, relative date parsing, 200+ locales
- [asyncio.Queue producer-consumer](https://gist.github.com/showa-yojyo/4ed200d4c41f496a45a7af2612912df3) -- pattern reference for write queue implementation

### Tertiary (LOW confidence)
- [KGGen: Knowledge Graph Extraction](https://arxiv.org/html/2502.09956v1) -- Academic paper on KG extraction from text, Feb 2025. Validates LLM-based approach but specific tooling may not apply.
- [HalluGraph grounding validation](https://arxiv.org/pdf/2512.01659) -- Entity grounding score concept. Academic approach; simplified substring matching is sufficient for Phase 2.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- instructor, openai, anthropic, ollama SDKs all verified via PyPI with recent releases. instructor's from_provider API confirmed across all three providers.
- Architecture patterns: HIGH -- Write queue pattern is well-established asyncio pattern. ExtractionProvider Protocol mirrors Phase 1's proven EmbeddingProvider pattern. Two-phase ingestion (persist-then-extract) is standard in event-sourced systems.
- Extraction schema: MEDIUM -- The Pydantic schema for ExtractionResult is straightforward, but the extraction prompt quality and LLM accuracy for knowledge graph construction varies by provider and model. Testing with real conversation data is needed.
- Pitfalls: HIGH -- LLM hallucination in extraction is well-documented. Entity merge challenges are well-understood in the knowledge graph community. DuckDB single-writer constraint is verified via official docs.
- Supersedence detection: MEDIUM -- Predicate-matching approach is sound but predicate normalization is an open problem. Starting with exact match is safe but may miss valid contradictions.

**Research date:** 2026-02-19
**Valid until:** 2026-03-19 (30 days -- instructor and LLM SDKs release frequently; check for breaking changes)
