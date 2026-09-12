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

Next: reproducible evidence retrieval measurement on explicit LongMemEval
variants with frozen splits, artifact provenance, and simple comparable baselines.
Full event/operation replay, persistent LLM extraction jobs, and public processing
status remain open; this change closes only the fast-ingestion recovery gap.

## Limits on claims

No current-release accuracy or superiority claim until reproducible comparative
runs support it. External-user feedback and production outcomes must be actual
observations, never simulated claims. Missing services/credentials are recorded
as validation gaps while independent work continues.
