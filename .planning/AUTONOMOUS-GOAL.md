# PRME memory leadership goal

Authorized 2026-09-11: work autonomously to close the assessment gaps and
deliver an excellent developer experience. Leadership requires comparative
evidence; completed features alone do not satisfy this goal.

Baseline: a26338e, v0.11.0; 1,187 local tests passed, 25 skipped.

User clarification: historical GSD goals are not authoritative. Test existing
behavior, supersede outdated approaches where evidence supports it, and select
techniques against the current goal. This delivery order is provisional and
must adapt to measured failures and comparative experiments.

## Delivery order and gates

1. Durable ingestion: atomic event/work persistence, original-event provenance,
   restart recovery, idempotent retries, scoped processing, visible failures.
2. Measurement: explicit dataset variants/splits, provenance, evidence metrics,
   actual context budgets, raw-ingestion evaluation, comparable baselines.
3. Knowledge quality: contextual extraction, grounded assertions, temporal
   updates with cardinality/conditions, complete scoped enumeration.
4. Context and maintenance: faithful rendering, bounded budgets, evidence-safe
   consolidation/forgetting, access to supporting episodes.
5. Recovery and trust: complete versioned derivation log/replay, identity-bound
   shared APIs, deliberate retention/deletion semantics, backend parity.
6. Learning and developer experience: persistent scoped outcome feedback,
   stable simple APIs, actionable errors, documented local workflows.
7. Comparative validation: held-out long-history evaluations, cost/latency and
   quality comparisons, longitudinal agent tasks and real developer feedback.

Each delivery needs targeted regression tests, appropriate backend checks,
updated documentation, and a reviewable commit. Keep historical planning
artifacts as history; the root ROADMAP.md and this file describe current work.

## Current work

Durable fast ingestion implemented: event/work transaction, original-event node
identity, persistent retries, scoped drains, bounded batches without dropping
work, index persistence before acknowledgement. Raw ingestion no longer embeds
before acknowledging the event. Removed the obsolete in-memory queue.

Verified 2026-09-12: local suite 1,172 passed / 36 skipped (optional dependencies
and PostgreSQL unavailable in that invocation); 32 targeted tests passed against
live PostgreSQL and local storage, including two independent PG consumers;
13 durability cases passed / 3 backend-specific cases skipped after final schema
index changes. Local process-exit recovery and partial-index retry are covered.
Ruff and diff whitespace checks pass. Two pre-existing coroutine warnings remain
in the sync client after-close test; address in developer-experience work.

Evidence evaluator delivered: explicit LongMemEval variants, neutral source
identifiers, stable dev/test splits, provenance, category coverage, recall/MRR/
nDCG, actual whole-turn token budgets, and BM25/vector/RRF/recency/empty baselines.
The product packer and LLM extraction need separate measurement. Full S runs
are in progress on 119 development questions from frozen source checkouts.
No default ranking replacement based on the initial five-question smoke test.

Correctness fixes since the baseline: explicit replayable retrieval clock;
query dates no longer silently filter assertion validity; explicit validity
filters apply to all candidate paths; relative recency compares episode times
consistently; update-language scoring preserves relevance limits and caller
configuration. Targeted tests passed against local and live PostgreSQL storage.
The sync client no longer leaks unawaited coroutines after close.

Public deferred-processing status and bounded scoped processing are implemented
for both async and sync APIs, with durable error/attempt reporting. These track
only ingest_fast(), not LLM extraction. Processing/client tests: 36 passed,
3 backend-specific skips. A full-suite run was invalidated by concurrent source
edits (mixed imports); rerun from a frozen checkout before accepting it.

Full event/operation replay, persistent LLM extraction jobs, contextual/grounded
extraction, faithful product context packing, complete scoped enumeration,
persistent feedback, identity-bound APIs, and comparative agent outcomes remain
open. The revised delivery order must follow the evidence from these runs.

## Limits on claims

No current-release accuracy or superiority claim until reproducible comparative
runs support it. External-user feedback and production outcomes must be actual
observations, never simulated claims. Missing services/credentials are recorded
as validation gaps while independent work continues.
