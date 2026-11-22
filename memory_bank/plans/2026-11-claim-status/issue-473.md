# Issue 473: preserve status during claim merge

Base: `9129dc2b`. Approved scope: claim identity, compatibility, derivation version, regression checks, held PR.

## Acceptance criteria

- Planned and done claims with otherwise identical identity stay separate within one source and across sources.
- Equal statuses still deduplicate/merge. The shared key also protects owner-group matching and structured assertions.
- Missing/null status remain equivalent; exact strings remain distinct; malformed non-string status skips merging.
- Saved plans and existing records remain unchanged. Derivation version moves from 3 to 4 with its pinned fixture.
- Required default fast cycle passes its committed baseline using cache-only replay; final full suite passes in a
  clean plain clone at the final revision. No model calls, live reingestion, or merge is authorized.

## Investigation and approach

`claim_key` feeds within-source deduplication and both ordinary and owner-group prior-copy matching.
The typed extraction producer admits lowercase planned/current/done/past/superseded into node metadata and HAS_FACT
metadata. Caller-stated assertions use the same producer even with typed extraction disabled. Adding exact status
to the shared identity fixes all three consumers. Missing status must not be inferred as current. Non-string
legacy metadata is excluded instead of making an unhashable key or losing its meaning.

No schema or new setting is needed. Saved plans already contain fixed artifacts and replacements; replay stays
unchanged. Fresh planning uses the corrected key. The mandated global derivation-version bump makes every prior
source stale on the next explicit reingestion scan, including untyped sources; no scan or model request runs here.

## Finite validation plan

1. Run claim-merge, typed-extraction, owner-group, structured-assertion, reingestion, and derivation-version tests.
2. Run one default store+ingest fast cycle with `PRME_BENCH_CACHE_ONLY=1`, against baseline-main.json. This guard
   raises before any uncached extraction request; a miss or a regression is a failed gate, never a pass.
   Typed extraction changes the request/cache key, so deterministic typed integration tests prove the status fix
   separately. Default replay measures broad regression only, not typed-model extraction quality.
3. Self-review correctness, security/owner scope, compatibility, runtime wiring, and tests within the explicit
   one-implementer/no-extra-reviewers budget. Run the review-plan helper and preserve source snapshot evidence.
4. Commit final revision and run unmodified `uv run pytest -q` in a clean plain clone. PostgreSQL cases skip when
   PRME_TEST_DATABASE_URL is absent. Preserve logs outside the checkout, then push and open one held PR.

Stop the same repair strategy after two failed fix/validation cycles. Preserve failures. No paid evaluations,
broader benchmark cohorts, auto-merge, or merge. Malformed status handling prevents a new hashability regression;
other merge identity dimensions remain outside this ticket.
