# Review: issue-493-concurrent-consolidation-stale-error

## Review Scope

- Base commit: dc8282e23837cd4151123836d94eea397213ca18 (`main`).
- Reviewed snapshots:
  - b60ad626146f8d6bac8611c276dd10f630b3874e3459d296ebfb44f4f3368437 (first pass, two changed files).
  - ab07b16251443ac22499779541e74e28fdc3e262a300f5ba8b6fae6403de80ac (re-review after the first round of fixes, four changed files).
  - 56f9cef6298d8d45b9633c26bb362552a8c9816387b8fbf45c6cc1a39d6178f2 (final tree). The delta from the re-reviewed snapshot is only the three Consider items the re-review recommended: the hierarchical `created` flag in `src/prme/organizer/summarization.py`, two extra assertions in the guard unit test, and the narrowed cleanup clause in the CHANGELOG. The consolidation publication code and the cluster organizer are byte-identical to the re-reviewed snapshot, so no further review round was run.
  - 95f60adae8e4eab13ec70d87e48706ba45f5204ecaf569078d934329e902ebf5 (rebased onto 327d246d11e0ac20bfd4f215c7379ee4f185836d after the review). The five changed files and their content are unchanged; only the base moved, which added an unrelated CHANGELOG bullet. The full suite was rerun on this tree.
- Tier: M (`review-plan --base main`), predicates DATA/INFRA/SECURITY/PERSISTED_FIELD false, NEW_SYMBOLS true.
- Roster: core, Sonnet, checklists 1, 3, 4, 5; adversarial, Opus, checklist 8. Merged checklists: 3, 4, 5 into the core report. Not applicable: 2 (security), 6 (infrastructure), 7 (data safety); the core reviewer screened basic security and wiring.
- Reports:
  - `memory_bank/reviews/issue-493-concurrent-consolidation-stale-error-core.md`
  - `memory_bank/reviews/issue-493-concurrent-consolidation-stale-error-adversarial.md`

## Files Changed

- `src/prme/storage/consolidation_publication.py`
- `src/prme/organizer/consolidation.py`
- `src/prme/organizer/summarization.py`
- `tests/test_consolidation_publication.py`
- `CHANGELOG.md`

## Approach Summary

The consolidation pipeline reads the active lineage and then the publication generation as two separate reads. A sibling publisher can commit between them, so the later plan carries the same key and request hash at the next generation while its `previous` snapshot predates the sibling. `_validate_dependencies` then compares `prior_ids=[sibling]` with `previous=()` and raises `StaleConsolidationError("Current consolidation summary changed; rebuild")`, which is the CI failure in issue #493.

The fix adds `_converged_publication_id` and calls it from `_commit_duckdb` and `commit_postgres` before `_validate_dependencies`. It returns the sibling's summary id only when exactly one active prior summary for the plan's key has the same request hash, its recorded generation is the plan's generation minus one, its id is the deterministic identity for that request hash and generation, and it is not already declared in `plan.previous`. The convergence path still validates the plan's sources against their stored checksums. DuckDB rolls back the transaction, which also rolls back the head increment; PostgreSQL returns before any write. The cluster organizer and the hierarchical summarizer now report `created=False` when the returned id is not the plan's own node, and the PostgreSQL additional-model write is skipped for a converged publication.

## Must Fix

None open. Core Must Fix 1 (convergence firing when the prior summary was already a declared dependency, which broke the privacy migration generation-advance path) was applied and verified in the re-review.

## Should Fix

None open. Core Should Fix 2 (additional-model vector written for a node that was never created) was applied and verified in the re-review.

## Consider

1. Summarization `created` flag (core Consider 1, adversarial re-review). Applied in `src/prme/organizer/summarization.py`: the hierarchical publish path now returns `published.id == plan.node.id`.
2. Guard-boundary coverage (core Consider 2). Applied in `tests/test_consolidation_publication.py`: the unit test now also pins convergence with an unrelated non-empty `previous` and the non-numeric generation decline.
3. CHANGELOG cleanup claim (core Consider 3, adversarial re-review). Applied: the entry now says only an offline rebuild collects the retained preparation.
4. Prior-row reshaping duplication (core Consider 4). Recorded, not changed; the two forms are correct and a normalizer would add an abstraction for two call sites.

## Security Audit Results

Not applicable as a dedicated checklist (SECURITY false). The core reviewer screened basic security: no secrets or PII in logs, no new input surface, no authorization change, and the sibling rows are selected under the same owner and scope filters as before. The convergence path returns an id only after the deterministic identity check, so it cannot cross owners or scopes.

## Pattern Consistency Assessment

The new SQL columns mirror the existing `json_extract_string` / `->>` forms and are applied to both backends and both the plain and hierarchical prior-row branches. `_validate_sources` is extracted from `_validate_dependencies` without changing the checks. The profile publication analogue is a different design (its request hash includes the generation and its plan carries a random node id), so no change was made there.

## Redundancy Check

Net reduction: the `_validate_sources` extraction removes the duplicated source loop. No new dependency, type or constant. The only duplication is the four-element tuple reshaping in the two commit paths, recorded as Consider 4.

## Wiring Findings

No new environment or configuration reads. Both storage backends and both publication callers (cluster consolidation and hierarchical summaries) share the helper. `GraphStore.publish_consolidation` still returns a node id; its only caller uses it to fetch the node, so no public contract changes.

## Data Safety and Read/Write Proof

Not applicable as a dedicated checklist (DATA and PERSISTED_FIELD false). No schema, index or durable field changed. The fix reuses existing summary metadata (`consolidation_request_hash`, `consolidation_key`, `consolidation_generation`) that every publication already writes, and older records without it decline to the existing stale path. The one accepted data observation is the retained preparation of a converged plan, which is the documented fate of an interrupted preparation and is collected by an offline rebuild.

## Break Scenarios (adversarial) and adjudication

| # | Scenario | Label | Likelihood | Impact | Verdict |
| --- | --- | --- | --- | --- | --- |
| 1 | Protected (schema_version 2) publications never converge because stored metadata omits `consolidation_request_hash`, so the stale error persists for protected stores. | Pre-existing (fix does not apply) | Low | Same symptom for an opt-in protected store; the organizer logs and skips the cluster | Follow-up |
| 2 | Phantom additional-model vector written for the never-created plan node on PostgreSQL. | Newly introduced | Low | Index and coverage drift for an orphan id; default off | Fix now, applied |
| 3 | Convergence reported as a created summary and logged as nothing. | Newly introduced | Certain when convergence fires | `summaries_created` over-counts and the collision has no signal | Fix now, applied for the cluster and hierarchical callers |
| 4 | The converged plan's preparation (prepared operation, staging rows, staged vector and lexical document) is left behind. | Pre-existing, accepted by design | Always with convergence | Retained like any interrupted preparation; an offline rebuild collects it | Accept |

Adjudication reasoning:

- Scenario 1 is a pre-existing gap in a different mode. Closing it needs a request hash read from the sibling's prepared plan, plus a protected concurrency test, and the privacy projection adds a separate correctness question. It is not a regression from this change and is recorded as a follow-up. The PR states that the reported unprotected path is fixed.
- Scenario 2 is a durable wrong-node write introduced by the convergence path. It is contained (a gate on the returned id) and was applied.
- Scenario 3 is reporting drift introduced by the convergence path. The same one-line computation was applied to both callers.
- Scenario 4 matches the documented interrupted-preparation behavior (`docs/RFC-0015-Self-Organizing-Memory.md`), so the cost of cleanup in this issue outweighs the benefit.

## Follow-ups Raised

- #510: protected-store concurrent consolidation publication can still raise `StaleConsolidationError` because summary metadata under a privacy manifest does not carry `consolidation_request_hash`.
- #511: concurrent identical profile rebuilds can raise `StaleProfileError` because the profile generation is read before the previous profile snapshot and the profile plan has no convergence path.

## Resolution Status

| Item | Status |
| --- | --- |
| Core Must Fix 1 | Fixed and re-review verified |
| Core Should Fix 2 | Fixed and re-review verified |
| Core Consider 1 (summarization flag) | Fixed |
| Core Consider 2 (guard coverage) | Fixed |
| Core Consider 3 (CHANGELOG clause) | Fixed |
| Core Consider 4 (duplication) | Recorded, no action |
| Adversarial 1 | Follow-up |
| Adversarial 2 | Fixed |
| Adversarial 3 | Fixed |
| Adversarial 4 | Accepted |
| Ruff on changed files | Passed |
| Targeted suites on PostgreSQL | 45 passed, 1 skipped, 3 xfailed |
| Full suite on the rebased final revision | 8637 passed, 157 skipped, 3 xfailed with `-n 4 --dist worksteal`; the serial lexical test 10 passed |
