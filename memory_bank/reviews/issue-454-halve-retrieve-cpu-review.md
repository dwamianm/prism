# Issue #454 review: hosted request profile at 10,000 nodes

## Review Scope

- Base commit: 57ec400f5f83488439c30dd84a93fb0e1c822322 at review time. The branch was rebased onto
  origin/main c8b817ee before the PR, which brought the named person's profile change (#448) behind
  `enable_owner_profile_context`, a switch this profile leaves off. The reviewed source is identical after the
  rebase, and `tests/test_hosted_profile.py` and `tests/test_profile_graph_sources.py` were rerun on the rebased
  tree (22 passed, 9 skipped).
- Branch: `issue-454-halve-retrieve-cpu`.
- Final reviewed snapshot: 6f8b379649445253277433691e2209e375169507187679922fd46a32c93646c2.
- Earlier reviewed snapshots: d0ebceb58a453db14a777f507dd0f885390c61d3baf2052da897ce10a510617c (after the first fix round) and fb832f746349847d24d5e50d12df97ab6f9d1ddd34a78a7d95d176600380e918 (the first reports).
- Tier: M. Predicates from `review-plan`: NEW_SYMBOLS true; DATA, INFRA, PERSISTED_FIELD and SECURITY false.
- Roster: core (checklists 1, 3, 4, 5) and adversarial (checklist 8). Security, infrastructure and data
  reviewers were not applicable per the helper. The core reviewer screened basic security and wiring.
- The core and adversarial reports were folded into this file. The core reviewer's final delta check on snapshot
  6f8b3796 approved the change: N1 to N7 resolved, no new findings, and the derivation test failure confirmed as
  the local `.env` effect.

## Files Changed

- `benchmarks/diagnostics/hosted_profile.py` (new): the profile harness.
- `tests/test_hosted_profile.py` (new): unit tests and a PostgreSQL-gated end-to-end test.
- `benchmarks/results/research/2026-11-05/hosted-profile/hosted-profile.json` (new): the 0.17.0a2 baseline.
- `BENCHMARKS.md`: the section "Hosted request profile at 10,000 nodes (dwamianm/prism#454)".

## Approach Summary

The harness mirrors the hosted service's three request shapes in process against a PostgreSQL store of 10,000
raw conversation turns for one owner: `retrieve` under the settings pinned in
`apps/api/src/prism_api/app.py`, `store_with_receipt(extract=False)`, and `ingest_fast_many` followed by the
materialization route's `process_pending(budget_ms=1000)`. It reports wall time and process CPU time per request
and per engine stage. Stages are measured by temporarily wrapping the pipeline's stage functions, the candidate
backends, the embedding provider, the write paths and token counting; the engine is not changed. The store is
built from the graph oracle's deterministic conversation padding, with local fastembed at one ONNX thread, and
the benchmark tables are analyzed before measurement. The committed baseline is the 0.17.0a2 run.

## Must Fix

### M1. Tests required the gitignored benchmark data, which CI does not have

Resolution: Resolved. `test_profile_runs_end_to_end` monkeypatches `hosted_profile.corpus`, and
`test_corpus_uses_the_graph_oracle_pad` monkeypatches `benchmarks.graph_oracle.scale.conversation_pad`, so no
test reads `data/benchmarks`. The harness docstring and BENCHMARKS state that the data and the embedding model
must be downloaded for a real run. Verified: `uv run pytest -q tests/test_hosted_profile.py` passes without the
corpus being touched by a test; the end-to-end test passes with `PRME_TEST_DATABASE_URL` set.

## Should Fix

- S1 (shared write-shape corpus range left embeddings warm): Resolved. `forget_embeddings` drops each measured
  text's cached vector before every sample, so both write shapes are cold. The artifact's ingest embedding cost
  is now paid on all 10 items.
- S2 (partial wrapper restore on a missing later binding): Resolved. The binding loop is inside `try/finally`;
  covered by `test_instrumented_restores_earlier_bindings_when_a_later_one_is_missing`.
- S3 (ignored materialization result): Resolved. `drain_materialization` raises when `pending` or `failed` is
  nonzero, for the warm-up and every measured call.
- S4 (BENCHMARKS said every shape warms over every question): Resolved. The section says only the retrieve shape
  does; the write shapes warm one item each.

## Consider

- C1 (p95 equals max at 20 samples): Resolved. Defaults are 50 requests and 10-item batches, and the prose gives
  the p95 rank and names the maximum.
- C2 (store route uses `store_with_receipt`): Resolved. The store shape calls `store_with_receipt`; the prose
  says the route's post-store reads are included.
- C3 (`settle_maintenance` duplicated the runner's drain): Resolved. It calls `MaintenanceRunner.drain()`.
- C4 (curated digest omitted pinned switches): Resolved. `settings_digest` adds the extraction-tied and privacy
  switches and renames the key to `backend`.
- C5 (nested settings could read `.env`): Resolved. Every nested `BaseSettings` section is built with
  `_env_file=None`.
- C6 (dimension from the field default): Resolved. Uses `fastembed_model_dimension(model_name)`.
- C7 (missing provenance): Resolved. The artifact records PostgreSQL and pgvector versions, the embedding model
  identity, library versions and the processor; the unverifiable second-run claim was removed.
- C8 (`profile_top` wall time labeled CPU): Resolved. `cProfile` uses `time.process_time`, drops the query
  vector first, filters to `src/prme` frames, and the documentation says process CPU.
- C9 (silent stage loss and a fake provider test): Resolved. The end-to-end test asserts the full stage sets and
  `forget_embeddings` is tested against a real `CachedEmbeddingProvider`.
- C10 (unused parameters): Resolved. Removed.
- C11 (test gaps): Resolved. Tests cover the corpus bounds, the recorder warm-up exclusion, the partial restore
  and the digest.
- C12 (nesting sentence omitted the graph node write): Resolved in BENCHMARKS.
- C13 (first run downloads the model): Resolved as documentation in the module and BENCHMARKS.

## Security Audit Results

Screened by the core reviewer at tier M; no dedicated security reviewer was required.

| Area | Result | Details |
|---|---|---|
| Secrets in logs or the artifact | PASS | The database URL is not written; the report records only the backend. No API keys or credentials are handled. |
| Authorization or tenant isolation | N/A | A benchmark harness, not an endpoint. It writes under a random `hosted-profile-<hex>` owner and reads public benchmark text. |
| Input validation and injection | PASS | Table names passed to `ANALYZE` are a fixed tuple; no user-controlled SQL. |
| Unsafe deserialization | N/A | Only JSON output is written. |
| Network or paid providers | PASS | Local fastembed only; the first run may download the model. No paid API. |

## Pattern Consistency Assessment

The module follows the diagnostics conventions: `KIND` and `schema_version`, `--out` with a JSON artifact,
`benchmarks/results/research/<date>/` output, lazy corpus import, config isolation with an empty environment,
and reuse of the graph oracle's `percentiles`. The switch-by-switch comparison against
`prism_api/app.py` found no divergence that changes a measured shape. The extraction-tied switches are off
because the profile stores raw turns with `extract=False` and makes no extraction; the BENCHMARKS section states
the settings it mirrors.

## Redundancy Check

- `percentiles` reuses the graph oracle helper.
- `settle_maintenance` now reuses `MaintenanceRunner.drain()`.
- `corpus` is a thin seam over `conversation_pad`, kept so tests can replace it.
- `analyze_tables` repeats a small `ANALYZE` block from the graph oracle's latency and scale harnesses; keeping
  it local was preferred to coupling the benchmark to that module.
- `settings_digest` stays a curated digest rather than a full config dump, because the artifact is read by
  people comparing runs and the full dump would bury the pinned switches.

## Wiring Findings

- No product route, CLI registration, dependency or environment variable was added.
- The module is run as `python -m benchmarks.diagnostics.hosted_profile`; pytest discovers the new tests.
- No CI job invokes the 10,000-node profile, by design; CI runs the tests only.
- The profile requires downloaded benchmark data and a local model; both are documented and absent from CI tests.

## Data Safety and Read/Write Proof

No product schema, migration or persisted field changed. The harness writes raw events and nodes for a random
owner into the PostgreSQL database named on the command line, runs `ANALYZE` on `nodes`, `edges` and
`lexical_documents`, and leaves the rows in place. That is acceptable for the documented scratch database and is
the subject of the follow-up below. No existing tenant's rows are read or modified.

## Break Scenarios (adversarial)

| Scenario | Label | Trigger | Likelihood | Impact | Detection | Verdict |
|---|---|---|---|---|---|---|
| S1 tests need gitignored data, so CI fails | newly introduced | any CI run | certain before the fix | every test job red | loud in CI | Fix now, resolved |
| S2 command writes 10,400 rows into whatever database is given, with no guard or cleanup | newly introduced | a shared database URL, or a repeat run | low to moderate | fake-owner rows and an `ANALYZE` on a shared database | query for the owner id | Follow-up |
| S3 `profile_top` was wall time labeled CPU and used a cached query | newly introduced | reading the function ranking literally | certain before the fix | a misleading lead for the CPU cut | compare with the stage table | Fix now, resolved |
| S4 a materialization budget cut was silently charged to the next sample | newly introduced | a larger batch or a slower host | low at the defaults | per-request ingest numbers stop meaning one request | none before the fix | Fix now, resolved |

Pre-mortem headline before the fixes: a red CI run from the new tests reading a gitignored dataset, with a
second-order risk of writing 10,000 fake-owner rows into a shared database.

Re-review on snapshot d0ebceb5 confirmed the must and should fixes and raised one new Should Fix (N1) plus
considerations N2 to N7:

- N1, the custom `time.process_time` cProfile timer inflated the function-level figures and the prose mapped
  them onto stage cells: Fixed. `profile_top` uses cProfile's default timer, filters rows by the `prme` package
  root, and the text calls the result a hot-spot pointer, not CPU accounting, noting that offloaded functions
  have no row.
- N2, the module docstring named `engine.store` and said "exact": Fixed. It names `store_with_receipt` and
  process CPU.
- N3, the store receipt's processing status was ignored: Fixed. `checked_store` raises unless the status is
  complete, and the build drains and refuses leftover work before measurement.
- N4, a failing `setattr` could leave a `delattr` to run on an unset attribute: Fixed by saving the binding
  after `setattr`.
- N5, the query cache-key branch was untested: Fixed in the real-provider test.
- N6, the CPU brand named in BENCHMARKS was not recorded: Fixed with `cpu_brand()` (`sysctl` on macOS).
- N7, the drain refusal had no unit test and its message was inverted: Fixed, with a fake-engine test.
- Adversarial N1, the PG end-to-end test's fastembed fetch would newly fail CI: Dismissed. The PG CI job
  already runs `tests/test_pg_integration.py`, which creates the default engine and stores content, so it
  already downloads the model; the new test adds no new CI dependency. The DuckDB job never runs it.
- Adversarial S3 caveat (profiler overhead) and the delta note on the docstring: Fixed as N1 and N2 above.
- Adversarial S4 residual (pending and failed counts are owner-wide): Fixed for the measurement boundary by
  draining the build and checking every store receipt; the refusal message now says "for this owner".

## Follow-ups Raised

- #464: the profile command can write to any database it is pointed at. It refuses no non-scratch database,
  prints no owner or row count before writing, and leaves the rows in place. Raised from the adversarial S2
  verdict.

## Verification Evidence

- `uv run pytest -q tests/test_hosted_profile.py`: 14 passed, 1 skipped (the PostgreSQL-gated test without a URL).
- The PostgreSQL-gated end-to-end test with `PRME_TEST_DATABASE_URL`: 1 passed.
- `uv run ruff check src/ tests/`: All checks passed.
- `uv run pytest -q` on the final tree: 7084 passed, 1515 skipped, 2 xfailed, 1 failed. The failure is
  `tests/test_derivation_version.py`, which builds its config without isolating `.env`; this checkout's
  gitignored `.env` sets `PRME_EXTRACTION_PROVIDER` and `PRME_EXTRACTION_MODEL`, which move the derivation
  fingerprint. With `.env` moved aside, both tests in that file pass. The change touches no derivation code and
  CI has no `.env`, so this is a local environment artifact, not a regression.
- The 10,000-node profile finished exit 0 on the final tree. The artifact is reproducible from the committed
  corpus digest, and every BENCHMARKS table cell was checked against it.

## Resolution Status

| Item | Severity | Verdict | Status |
|---|---|---|---|
| M1 tests need gitignored data | Must Fix | Fix now | Fixed |
| Core S1 shared warm write corpus | Should Fix | Fix now | Fixed |
| Core S2 partial wrapper restore | Should Fix | Fix now | Fixed |
| Core S3 ignored materialization result | Should Fix | Fix now | Fixed |
| Core S4 warm-up prose | Should Fix | Fix now | Fixed |
| Adversarial S1 (same as M1) | Must Fix | Fix now | Fixed |
| Adversarial S2 database writes | Follow-up | Follow-up | #464 |
| Adversarial S3 profile timer | Consider | Fix now | Fixed |
| Adversarial S4 budget truncation | Consider | Fix now | Fixed |
| R1, R2, R3, R4 | Residual | Fix now / Accept | Fixed, except the soft-fail settle, accepted |
| C1 to C13 | Consider | Fix now | Fixed |
| Core re-review N1 | Should Fix | Fix now | Fixed |
| Core re-review N2 to N7 | Consider | Fix now | Fixed |
| Adversarial re-review N1 (fastembed in CI) | Consider | Dismissed | Pre-existing in the PG CI job |
