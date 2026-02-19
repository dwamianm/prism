---
phase: 02-ingestion-pipeline
plan: 01
subsystem: database
tags: [instructor, openai, anthropic, ollama, dateparser, asyncio-queue, write-queue, embedding-provider, pydantic-settings]

# Dependency graph
requires:
  - phase: 01-01
    provides: "prme Python package with core dependencies, PRMEConfig with EmbeddingConfig"
  - phase: 01-03
    provides: "EmbeddingProvider Protocol, FastEmbedProvider, VectorIndex"
provides:
  - "Phase 2 dependencies: instructor, openai, anthropic, ollama, dateparser"
  - "ExtractionConfig with provider/model/max_retries/timeout for LLM extraction"
  - "EmbeddingConfig extended with optional api_key for API-based providers"
  - "PRMEConfig.write_queue_size for configurable queue depth"
  - "WriteQueue: asyncio.Queue-based single consumer for DuckDB write serialization"
  - "OpenAIEmbeddingProvider satisfying EmbeddingProvider Protocol with lazy AsyncOpenAI client"
  - "create_embedding_provider() factory dispatching fastembed/openai based on config"
affects: [02-02, 02-03, 02-04]

# Tech tracking
tech-stack:
  added: [instructor, openai, anthropic, ollama, dateparser]
  patterns: [asyncio-queue-consumer-pattern, future-based-response, lazy-async-client-init, factory-function-for-provider-selection]

key-files:
  created:
    - src/prme/storage/write_queue.py
  modified:
    - pyproject.toml
    - uv.lock
    - src/prme/config.py
    - src/prme/storage/embedding.py

key-decisions:
  - "WriteQueue uses asyncio.Queue with None sentinel for clean shutdown (matching research Pattern 3)"
  - "OpenAIEmbeddingProvider.embed() uses asyncio.run() for sync wrapper since it runs from asyncio.to_thread() in VectorIndex"
  - "create_embedding_provider factory uses simple if/elif dispatch rather than registry pattern (only 2 providers)"

patterns-established:
  - "Future-based async write queue: submit coroutine factory, await Future for result"
  - "Factory function for config-driven provider selection: create_embedding_provider(config)"
  - "Lazy API client init: _ensure_client() pattern for API-based providers to defer key validation"
  - "ExtractionConfig/EmbeddingConfig as independent nested BaseSettings with separate env_prefix"

requirements-completed: [INGE-03, INGE-05]

# Metrics
duration: 3min
completed: 2026-02-19
---

# Phase 2 Plan 1: Dependencies, Config, WriteQueue, and OpenAI Embedding Summary

**Phase 2 foundation: instructor/openai/anthropic/ollama deps, ExtractionConfig, async WriteQueue for DuckDB write serialization, and OpenAI embedding provider with factory dispatch**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-19T21:07:23Z
- **Completed:** 2026-02-19T21:10:19Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Added 5 new dependencies (instructor, openai, anthropic, ollama, dateparser) with all resolved via uv sync
- Extended PRMEConfig with ExtractionConfig (provider/model/max_retries/timeout), optional api_key on EmbeddingConfig, and configurable write_queue_size
- Built WriteQueue with asyncio.Queue-based single consumer, Future-based responses, clean start/stop lifecycle, and structured logging
- Added OpenAIEmbeddingProvider with lazy AsyncOpenAI client init satisfying EmbeddingProvider Protocol
- Created create_embedding_provider() factory dispatching fastembed/openai based on config

## Task Commits

Each task was committed atomically:

1. **Task 1: Add Phase 2 dependencies and extend PRMEConfig** - `d9815aa` (feat)
2. **Task 2: Implement WriteQueue and OpenAIEmbeddingProvider** - `3447a5a` (feat)

## Files Created/Modified
- `pyproject.toml` - Added instructor>=1.14, openai>=2.20, anthropic>=0.80, ollama>=0.6, dateparser>=1.2
- `uv.lock` - Resolved dependency tree with 26 new packages
- `src/prme/config.py` - Added ExtractionConfig, api_key on EmbeddingConfig, extraction/write_queue_size on PRMEConfig
- `src/prme/storage/write_queue.py` - WriteQueue class with WriteJob dataclass, asyncio.Queue consumer pattern
- `src/prme/storage/embedding.py` - OpenAIEmbeddingProvider class and create_embedding_provider() factory

## Decisions Made
- WriteQueue uses `asyncio.Queue[WriteJob | None]` with None sentinel for clean shutdown, matching the research-recommended Pattern 3
- OpenAIEmbeddingProvider.embed() wraps async API via `asyncio.run()` since VectorIndex calls embed() from `asyncio.to_thread()` (cannot reuse caller's event loop)
- Factory function uses simple if/elif dispatch -- only 2 providers, no need for registry pattern overhead
- WriteJob includes a `label` field for structured logging of failed jobs

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. API keys (OPENAI_API_KEY) only needed when OpenAI embedding provider is actually selected and embed() is called.

## Next Phase Readiness
- WriteQueue ready for integration into MemoryEngine's write path (Plan 02-02 ingestion pipeline)
- ExtractionConfig ready for ExtractionProvider implementations (Plan 02-02)
- OpenAIEmbeddingProvider available alongside FastEmbedProvider for config-driven selection
- All 5 Phase 2 dependencies installed and importable

## Self-Check: PASSED

All 5 key files verified on disk. Both task commits (d9815aa, 3447a5a) verified in git log.

---
*Phase: 02-ingestion-pipeline*
*Completed: 2026-02-19*
