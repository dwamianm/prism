# Issue #400 typed extraction relations: consolidated review

## Review scope

- Base commit: `48bb094196d356579600e8f8709afdab405a050f` (`origin/main`).
- Reviewed snapshots: `5eec1b079707c54b4241ab12163d2b7368645f1c85fb38a1c3521530ea625e63` (first pass) and
  `f699b811fff6d610ba2603fd4096baf9705cc03fdcbd92ee5c7f6c2cf7f70ddf` (fix verification). HEAD
  `ad81421535fce2df3e3160930d602dcd33f99eb0` plus the working tree.
- Tier: L. Predicates: DATA, PERSISTED_FIELD, NEW_SYMBOLS. INFRA not applicable.
- Reviewers and coverage: the ticket's recorded light review budget is two helpers per issue. The core reviewer covers
  checklists 1 (correctness), 2 (security), 3 (quality and test coverage), 4 (patterns and full-config comparison),
  5 (redundancy) and 7 (data safety and read/write proof). The adversarial reviewer covers checklist 8. Checklist 6
  (infrastructure/ops) is not applicable. The merged coverage is recorded here, not treated as a skipped review.
- Reports: `issue-400-typed-extraction-relation-core.md` and `issue-400-typed-extraction-relation-adversarial.md`.
  Both reviewers completed a fix verification pass on the second snapshot. The core reviewer resolved F1, F2 and F3
  and recorded F4 and F5 as partly resolved; the adversarial reviewer confirmed S1, S3, S4, S6 and S8 fixed and kept
  S2, S5 and S7 as follow-ups. The privacy projection was narrowed to `HAS_FACT` edges after the verification pass,
  which keeps the alias barrier strict for every other edge type.

## Files changed

`BENCHMARKS.md`, `CHANGELOG.md`, `benchmarks/diagnostics/extracted_packs.py`,
`benchmarks/fast_cycle/store_extract.py`, `benchmarks/graph_oracle/extraction.py` (new),
`benchmarks/graph_oracle/run.py`, `docs/RELATIONS.md`, `documentation/configuration.md`, `src/prme/config.py`,
`src/prme/config_audit.py`, `src/prme/ingestion/admitted_statements.py`, `src/prme/ingestion/extraction.py`,
`src/prme/ingestion/pipeline.py`, `src/prme/ingestion/reingest.py`, `src/prme/ingestion/schema.py`,
`src/prme/retrieval/relation_walk.py`, `src/prme/storage/engine.py`,
`tests/fixtures/extraction/typed_batch_v1.json` (new), `tests/test_config_audit.py`, `tests/test_fast_cycle.py`,
`tests/test_graph_oracle.py`, `tests/test_typed_extraction.py` (new).

## Approach summary

`enable_typed_extraction` (off by default) adds a typed block to the extraction prompt and optional
`relation`/`role`/`status` fields to the fact schema. `admit_relation` keeps a relation only from the closed
vocabulary and drops the role and status with it; a bad role or status is dropped alone. The admitted fields are
stored on the claim node and its `HAS_FACT` link. Relation routing reads the node relation first, then the link for
the graph oracle, and falls back to the predicate normalizer. The option is part of the re-derivation settings only
while it is on and the provider accepts the `typed` keyword. The fidelity harness stores each fixture, re-derives it,
reads the graph back and scores it against the gold graph.

## Must fix

None. The core reviewer found no blocking defect. The adversarial reviewer's Fix now items are S1, S3 and S4 below,
all applied and re-verified.

## Should fix

- F1: the privacy path ignores the option, and the edge projection would reject a typed `HAS_FACT` edge. Fixed: a
  closed-vocabulary `relation` is left out of the projection instead of raising, so export and migration proceed, and
  the limitation is documented in the config description and `documentation/configuration.md`.
- F2: `config_audit` reported the off flag as effective. Fixed: the activation gate is added and the expected counts
  return to 31 and 37.

## Consider

- F3: the single-turn typed path, the typed correcting batch, the cache keys and the on-transition re-derivation are
  now tested. Fixed.
- F4: the status vocabulary and field description now render from `RELATION_STATUSES`. The duplicated
  `drop_malformed_items` remains; recorded as a small follow-up opportunity, not worth a change here.
- F5: the per-fact INFO drop log remains. Acceptable for an experimental option that is off; role and status drops
  are now logged individually.

## Security audit results

| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs | PASS | The new logs carry a predicate, a stated relation, a role or a status only, never a source span or quote. A protected scope replaces them with its privacy operation event. |
| Prompt and injection surface | PASS | The typed block is built from the static `RELATIONS` table; no source text reaches the format string. |
| SQL, command and path injection | PASS | The walk statement is unchanged and binds parsed UUIDs and the owner id. The harness runs a fixed `git rev-parse` argv and reads operator CLI paths. |
| Tenant scoping and privacy | PASS with F1 | The walk keeps its `user_id` and scope filters. The guarded extraction path keeps the untyped schema and is documented as out of scope for this option. |
| External calls | PASS | Product code adds none. The harness reaches the local Ollama endpoint only. |

## Pattern consistency and full-config comparison

The option is read in both `IngestionPipeline` constructions, stored once, used by the single-turn path, the batch
path, the fact metadata and the link, and named in `extraction_settings` only while it acts. It follows
`enable_named_claim_text` and `enable_extraction_corrections`: config field, config audit, documentation row, engine
wiring, conditional derivation settings, CHANGELOG and tests. Request and schema selection use the same
`_accepts_keyword` test in the pipeline, the cache and the settings digest. The off path is unchanged: the prompt,
the batch messages, the cache keys and the derivation settings are byte-identical with the option off.

## Redundancy check

The new symbols are the feature's minimum: two model selectors, one admission function, one prompt block, one
pipeline flag set, one cache flag and one harness module. F4's remaining validator duplication is recorded as a
cleanup, not a blocker.

## Wiring findings

- Both engine construction sites pass `config.enable_typed_extraction`.
- The cache wrappers (`ExtractionCache`, `StoredTurnCache`, `RequestMeter`) accept and key on `typed` end to end.
- The harness exposes `extraction-fidelity` with `--untyped` and reads both the oracle's link metadata and typed
  extraction's node and link metadata.
- The privacy projection now tolerates a typed edge's closed-vocabulary relation.

## Data safety and read/write proof

| Field | Writer | Reader | Old records |
|---|---|---|---|
| Claim node `metadata.relation/role/status` | `src/prme/ingestion/pipeline.py` (per-fact block before node creation) | `src/prme/retrieval/relation_walk.py` `_typed`; `benchmarks/graph_oracle/extraction.py` | Missing keys: `_typed` returns `None` and `normalize_predicate` runs; no migration or backfill |
| `HAS_FACT` edge `metadata.relation/role/status` | `src/prme/ingestion/pipeline.py` (only when the subject entity resolved) | `src/prme/retrieval/relation_walk.py`; harness | Same fallback; `metadata=None` when untyped, preserving the old edge |
| Claim metadata across merge | `merged_claim` keeps the new node's metadata, so the typed fields survive | as above | unchanged |

- The fields are optional and additive. DuckDB is covered end to end by `tests/test_typed_extraction.py`; PostgreSQL
  uses the existing `MemoryEdge.metadata` JSON column and `get_edges` decode. The local PostgreSQL suite skips without
  `PRME_TEST_DATABASE_URL`, so that path is a residual risk only.
- A typed claim's status is stored but never retires it from the walk; the claim lifecycle is the authority. The
  graph oracle's own `superseded` status on a `shape`-tagged link still retires that claim.
- Turning the option on makes stored sources stale and re-derives them; turning it off makes typed-derived sources
  stale again, as documented. `DERIVATION_VERSION` is not bumped because the option is a setting and the default
  derivation is unchanged. `tests/fixtures/derivation/trip_derived.json` is unchanged and
  `tests/test_derivation_version.py` passes.

## Break scenarios (adversarial) and adjudication

| # | Scenario | Label | Verdict | Reasoning |
|---|---|---|---|---|
| S1 | A typed role is always read at the object end, so role-gated routes reject `is_child_of`-style claims | Newly introduced | Fix now, applied | `_typed` now takes the normalizer's `role_end` when it names the same relation and role, and the link no longer overrides a node-typed relation. Test added. |
| S2 | A stated relation overrides the deterministic predicate mapping with no agreement check or receipt source | Newly introduced | Follow-up | The issue explicitly prefers the stored relation; an agreement check or a `relation_source` receipt field needs a routing decision and a fast-gate run. Issue opened. |
| S3 | The option is inert with a provider that does not take `typed`, while the derivation marker says it was on | Newly introduced | Fix now, applied | The pipeline records provider support, warns once when it is absent, gates storage and the drop log on it, and leaves the flag out of `extraction_settings`. Test added. |
| S4 | A typed `HAS_FACT` edge cannot pass the privacy projection, so migration or export aborts | Newly introduced | Fix now, applied | The projection leaves a closed-vocabulary relation out on `HAS_FACT` edges only and still rejects free-form relation text on every edge. Test added. |
| S5 | The fidelity harness has four measurement defects (literal `attr`, default status, object selection, inferred credit) | Newly introduced | Follow-up | The harness is new and the table already says the option is not ready. Fixing the measurement is required before a later flip reads it as evidence. Issue opened. |
| S6 | Turning the option off does not restore the old routing or un-type stored claims | Newly introduced by design | Accept, documented | The stored relation is the point of the option. The config description and documentation now say turning it off does not un-type stored claims. A typed-claim count in the reingest result is folded into the S2 follow-up. |
| S7 | Claim merge cannot tell a planned from a done action, so the status is lost on a repeated statement | Newly introduced | Follow-up | Adding `status` to `claim_key` changes merge behavior for every store and needs the fast cycle. Issue opened. |
| S8 | A dropped role or status is not logged, and the status vocabulary has two copies | Newly introduced | Fix now, applied | Role and status drops are logged individually, and the prompt and field description render from one `RELATION_STATUSES` source. |

Pre-mortem headline from the adversarial reviewer: a gated option flips on for its stated purpose and the routing it
was built to improve gets worse, silently; the model's role label is read at the wrong end of the claim and its
relation label overrides the deterministic mapping that was right, while a store that later tries to become a
privacy pack cannot migrate because a routing hint on an edge is read as a forbidden alias field. S1, S2 and S4
address that headline; S2 remains open by design.

## Follow-ups raised

1. Typed relation authority: trust a stored relation only when it agrees with the normalizer or the normalizer is
   unmapped, and record `relation_source` in the receipt; report the typed claim count in the reingest result.
2. Fidelity harness accuracy: store or compare the literal `attr`, score an absent status as absent, exclude every
   non-object `basis` and pick the object deterministically, and do not credit inferred gold claims to relation
   precision.
3. Claim merge: include the typed status in `claim_key` so a planned and a done statement stay separate records.

## Resolution status

| Finding | Checklist | Severity | Status |
|---|---|---|---|
| F1 privacy limitation and projection | 2, 7 | Should fix | Fixed and documented |
| F2 config audit activation gate | 4 | Should fix | Fixed |
| F3 test coverage gaps | 3 | Consider | Fixed |
| F4 status vocabulary and validator duplication | 5 | Consider | Status vocabulary fixed; validator duplication recorded |
| F5 per-fact drop log | 1 | Consider | Accepted for an off option; role and status drops now logged |
| S1 role end | 8 | Fix now | Fixed |
| S2 relation authority | 8 | Follow-up | Open |
| S3 unsupported provider | 8 | Fix now | Fixed |
| S4 privacy projection | 8 | Fix now | Fixed |
| S5 fidelity measurement | 8 | Follow-up | Open |
| S6 rollback semantics | 8 | Accept | Documented |
| S7 claim merge status | 8 | Follow-up | Open |
| S8 role/status drop signal | 8 | Fix now | Fixed |
| Acceptance criterion 2 (fast gate) | - | Criterion not met | Recorded in `BENCHMARKS.md`; option stays off |
| Acceptance criterion 4 (full pytest) | - | Passes | Final full run without `.env`: 7133 passed, 1534 skipped, 2 xfailed in 37:52. The first run's two failures in `tests/test_fast_cycle.py` were fake cache keys that did not accept the new `typed` keyword; fixed as a test-only signature update and both tests pass. |
