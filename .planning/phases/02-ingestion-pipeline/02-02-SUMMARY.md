---
phase: 02-ingestion-pipeline
plan: 02
subsystem: ingestion
tags: [instructor, pydantic, extraction, grounding, llm, openai, anthropic, ollama, structured-extraction]

# Dependency graph
requires:
  - phase: 02-01
    provides: "Phase 2 dependencies (instructor, openai, anthropic, ollama), ExtractionConfig, PRMEConfig"
provides:
  - "ExtractionResult Pydantic schema with entities, facts, relationships, summary"
  - "ExtractedFact.fact_type for single-pass extraction of facts/decisions/preferences"
  - "ExtractionProvider Protocol (runtime_checkable) for pluggable LLM extraction"
  - "InstructorExtractionProvider with lazy client init supporting OpenAI/Anthropic/Ollama"
  - "create_extraction_provider() factory from ExtractionConfig"
  - "EXTRACTION_SYSTEM_PROMPT covering entities, facts, decisions, preferences, relationships, temporal refs"
  - "validate_grounding() for substring-based hallucination filtering"
affects: [02-03, 02-04]

# Tech tracking
tech-stack:
  added: []
  patterns: [extraction-provider-protocol, lazy-instructor-client-init, grounding-validation-substring-match, fail-open-extraction]

key-files:
  created:
    - src/prme/ingestion/schema.py
    - src/prme/ingestion/extraction.py
    - src/prme/ingestion/grounding.py
  modified:
    - src/prme/ingestion/__init__.py

key-decisions:
  - "Single ExtractionResult schema with fact_type field for facts/decisions/preferences rather than separate models per type"
  - "InstructorExtractionProvider fails open (returns empty ExtractionResult) on extraction errors -- pipeline handles retry"
  - "Grounding validation uses conservative substring matching -- better to discard valid context-dependent refs than accept hallucinations"
  - "Facts filtered by subject grounding only; object not checked since it may be paraphrased (e.g., 'senior engineer')"

patterns-established:
  - "ExtractionProvider Protocol: runtime_checkable Protocol with provider_name, model_name properties and async extract() method"
  - "Lazy instructor client init: _ensure_client() defers instructor.from_provider() to first extract() call, avoiding API key validation at construction"
  - "Fail-open extraction: return empty ExtractionResult on error, log with structlog, let pipeline handle retry"
  - "Grounding validation: substring match of entity names against source text to filter LLM hallucinations"

requirements-completed: [INGE-01, INGE-02]

# Metrics
duration: 4min
completed: 2026-02-19
---

# Phase 2 Plan 2: Extraction Schema, Provider, and Grounding Validation Summary

**Pydantic extraction schema with instructor-based multi-provider LLM extraction and substring grounding validation for hallucination filtering**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-19T21:12:22Z
- **Completed:** 2026-02-19T21:17:17Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Built ExtractionResult Pydantic schema with ExtractedEntity, ExtractedFact (supporting fact/decision/preference via fact_type), ExtractedRelationship, and optional summary
- Implemented ExtractionProvider Protocol (runtime_checkable) and InstructorExtractionProvider supporting OpenAI, Anthropic, and Ollama via instructor's unified from_provider() API with lazy client initialization
- Created validate_grounding() that filters hallucinated entities/facts/relationships via case-insensitive substring matching against source text
- Comprehensive EXTRACTION_SYSTEM_PROMPT covering entities, facts, decisions, preferences, relationships, summaries, temporal references, and grounding instructions

## Task Commits

Each task was committed atomically:

1. **Task 1: Extraction schema, provider protocol, and instructor implementations** - `6e8e0da` (feat)
2. **Task 2: Grounding validation module** - `634c07b` (feat)

## Files Created/Modified
- `src/prme/ingestion/schema.py` - Pydantic models: ExtractedEntity, ExtractedFact, ExtractedRelationship, ExtractionResult
- `src/prme/ingestion/extraction.py` - ExtractionProvider Protocol, InstructorExtractionProvider, create_extraction_provider factory, EXTRACTION_SYSTEM_PROMPT
- `src/prme/ingestion/grounding.py` - validate_grounding() with substring matching and structlog warning logging
- `src/prme/ingestion/__init__.py` - Added re-exports for all extraction and grounding symbols

## Decisions Made
- Used fact_type field on ExtractedFact ("fact", "decision", "preference") for single-pass extraction rather than separate Pydantic models per type -- simpler schema, maps cleanly to NodeType during materialization
- InstructorExtractionProvider fails open on extraction errors -- returns empty ExtractionResult and logs the error. The pipeline is responsible for retry logic, keeping extraction concerns separated from error recovery
- Grounding validation checks subject grounding only for facts (not object), because objects may be paraphrased attribute values like "senior engineer" that don't appear verbatim in source text
- Summary field is always preserved through grounding validation since it's a paraphrase, not a verbatim extraction

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Pre-existing entity_merge.py and supersedence.py files from a previous plan execution (02-03) were already in the ingestion package. The __init__.py had been committed with imports for all modules including the ones created in this plan. No conflicts arose; the linter simply maintained those existing imports alongside our new validate_grounding re-export.

## User Setup Required
None - no external service configuration required. API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY) only needed when extraction is actually called.

## Next Phase Readiness
- ExtractionResult schema ready for use as instructor response_model in pipeline
- ExtractionProvider Protocol ready for dependency injection in IngestionPipeline
- validate_grounding() ready to filter extraction results before materialization
- EntityMerger (02-03, already present) can consume ExtractionResult entities
- SupersedenceDetector (02-03, already present) can consume ExtractionResult facts

## Self-Check: PASSED

All 4 key files verified on disk. Both task commits (6e8e0da, 634c07b) verified in git log.

---
*Phase: 02-ingestion-pipeline*
*Completed: 2026-02-19*
