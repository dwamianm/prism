# Phase 2: Ingestion Pipeline - Context

**Gathered:** 2026-02-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Conversations enter the system and produce structured memory. A developer submits conversation messages and the pipeline persists them as immutable events, extracts entities/facts/relationships into the graph store, embeds content into the vector index, and indexes content in the lexical index. The pipeline supports multiple LLM and embedding providers. Retrieval, self-organization, and API exposure are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Extraction scope
- Rich extraction: named entities plus locations, temporal references, quantities, and events mentioned
- Same-pass extraction: entities AND relationships extracted together in one LLM call
- Extract conversation summaries alongside structured entities/facts for richer search content
- Best-effort entity merge at ingestion: match existing entities by name/type and link to the same node rather than always creating new ones
- Detect supersedence at ingestion: when new facts contradict existing ones, create supersedence chains immediately rather than deferring to Phase 5
- Store all extractions regardless of confidence — everything starts as Tentative, organizer handles cleanup

### Provider preferences
- Extraction and embedding providers configured independently (mix and match)
- Three extraction providers: OpenAI, Anthropic/Claude, and one local option
- API-based embedding provider alongside existing FastEmbed local option
- Provider abstraction designed to be extensible for future providers

### Input model
- Primary input: single message (role + content) — pipeline processes incrementally
- Also support batch ingestion for importing conversation history
- Both sync and async modes: default async (event persisted immediately, extraction in background) with option to wait for extraction completion

### Confidence & failure handling
- No minimum confidence threshold — store all extractions as Tentative
- On LLM extraction failure: event is persisted immediately, extraction queued for retry with exponential backoff
- Validate extracted entities/facts against source text — discard ungrounded/hallucinated extractions

### Claude's Discretion
- Which node types to extract (full set vs core subset based on practical complexity)
- Extraction schema design (LLM output format)
- Single vs split LLM calls for extraction
- Specific local LLM option (Ollama, llama.cpp, etc.)
- Specific API embedding provider (OpenAI, Voyage, etc.)
- Input metadata schema (required vs optional fields beyond user_id/session_id/role/content)
- Retry count and backoff strategy for failed extractions

</decisions>

<specifics>
## Specific Ideas

- Extraction should be rich enough to capture temporal references ("yesterday", "last week") and resolve them to actual dates when possible
- Entity merging at ingestion should be conservative — better to create a duplicate than incorrectly merge two different entities
- Supersedence detection is high-value: "Sarah left Google" should immediately flag existing "Sarah works_at Google" facts
- Source text validation prevents the graph from being corrupted by LLM hallucinations — this is a quality gate

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-ingestion-pipeline*
*Context gathered: 2026-02-19*
