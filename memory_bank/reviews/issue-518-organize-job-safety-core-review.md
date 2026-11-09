# Core review: issue #518 organizer job audit

Status: COMPLETE
Review snapshot: ee6ad54bba5b8e9f3aaf04a0322b072eca42acd505361d48e2560b86e3fa6db4
Re-review snapshot: 4000d88cf56e54acf934244f1a6767b3f5388568e8fbc5a21a64c3b4662747d7
Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
Branch: issue-518-organize-job-safety
Reviewer: Agent 1 (core: correctness, quality/tests, patterns/DRY, redundancy)

Checklists assigned:

1. Correctness and logic (edge cases, scoping, contracts of called utilities).
3. Code quality and test coverage (complexity, naming, dead code, error handling, tests, performance).
4. Pattern consistency and DRY (`_compact_postgres_indexes` vs the DuckDB path and vs
   `src/prme/storage/pg/graph_store.py`; test fixtures vs existing organizer tests).
5. Redundancy (existing helpers the new code duplicates).

`.env.bak` was excluded from review as a transport artifact.

---

## Must Fix

### M1. `tests/privacy_fakes.py:133` breaks every fake-based protected extraction test

`FakeSanitizer.admit()` now echoes `source_snapshot=request.snapshot`. Two production
guards key off a non-`None` snapshot and both reject the fake's output:

1. `src/prme/privacy/admission.py:491-493` (`load_source`) now calls
   `privacy.sanitizer.validate_source(source, source.source_snapshot)`; `validate_source`
   (`src/prme/privacy/sanitizer.py:99-107`) compares `private_checksum` against
   `sanitized_checksum(fields_json, role, event_time)` (`sanitizer.py:38-42`, sha256 over a
   canonical JSON unit). The fake still set
   `private_checksum=hashlib.sha256(fields.encode())` (`tests/privacy_fakes.py:127` as of
   the reviewed snapshot), which could not match. `load_source` therefore raised
   `PrivacyDenied`, so `resume()` failed.
2. `src/prme/privacy/admission.py:634-638` now runs `validate_private_grounding` whenever
   `prepared.source.source_snapshot is not None`. That gate requires
   `claim.evidence_quote` to be present and inside the source
   (`src/prme/ingestion/grounding.py:45-57`), and `FakeExtractor.result`
   (`tests/privacy_fakes.py:206-209`) has no `evidence_quote` at all. Even with a fixed
   checksum this path still raised `privacy_evidence_ambiguous`.

Concrete affected tests (all use `make_runtime()` with the fake sanitizer and the fake
extractor and then run protected processing through `privacy.resume`):

- `tests/test_privacy_ciphertext_storage.py:200-220` (`test_EM03_...`, asserts
  `result.processed == 1` at :213).
- `tests/test_privacy_admission.py:131-150` (AP04, `wait_for_extraction=True`, expects the
  extracted conditional fact).
- `tests/test_privacy_admission.py:162-183` (AP06, `process_extractions` expects
  `processed == 1`), and any other module that pairs `make_runtime` with `engine.ingest`
  (`tests/test_privacy_fences.py`, `tests/test_privacy_ephemeral.py`,
  `tests/test_privacy_direct_entity_lexical.py`, `tests/test_privacy_interfaces.py`).

Why the change was made: `reads.generated` passes `source.source_snapshot` as the snapshot
to `coordinator.sanitize` (`src/prme/privacy/reads.py:336-351`), and `sanitize`
dereferences `saved.policy` (`admission.py:337`), so the protected `summarize` and
`consolidate` jobs cannot run with a snapshot-less ingress source. That is a legitimate
need, but it cannot be met by making ingress sources carry a snapshot the fake cannot
validate.

Smallest fix: gate the echo behind an opt-in fake flag (default off) that only
`tests/test_organizer_privacy_scoping.py` turns on.

---

## Should Fix

### S1. `src/prme/organizer/jobs.py:918-924`: the orphan-lexical arm deletes content that is not a retired node

The unscoped arm was:

```
SELECT DISTINCT d.node_id::text FROM lexical_documents d
LEFT JOIN nodes n ON n.id::text = d.node_id WHERE n.id IS NULL
```

A row in `lexical_documents` with no node is not evidence of a retired node.
`PgLexicalIndex.index` inserts there **only** when the node does not exist yet
(`src/prme/storage/pg/lexical_index.py:81-98`), and both the class docstring
(`:43-47`) and `tests/test_pg_lexical_index.py:65,90-100` treat such rows as the
supported non-node / pre-publication representation. The DuckDB path anchors on
`vector_metadata` (`jobs.py:829-837`), a strictly narrower set; this arm was broader than
the path it claimed to mirror.

Smallest fix: drop the arm and document that orphan rows are left alone (matching the
DuckDB anchor).

### S2. `jobs.py:790-792`, `jobs.py:887-889`, `docs/ORGANIZER-JOBS.md:139-141`, `CHANGELOG.md:12`: the PostgreSQL lexical half is a no-op for a node that still exists

On PostgreSQL a node's lexical document is the generated `nodes.content_tsv`
(`src/prme/storage/pg/lexical_index.py:43-47, 166-170`), not a `lexical_documents` row.
`PgLexicalIndex.delete_by_node_id` only runs
`DELETE FROM lexical_documents WHERE node_id = $1` (`:401-406`), and `index()` never
inserts such a row for an existing node (`:81-98`). So for a retired node that still
exists, the compaction clears `nodes.embedding` and deletes nothing lexical, while the
node's text keeps matching `PgLexicalIndex.search` and keeps counting toward the
`term_weights` document frequencies (`:164-172`). Retrieval still drops the hit because
hydration filters to active states (`src/prme/storage/pg/graph_store.py:240-245`), so this
was not a leak, but the code comment, the docs table and the CHANGELOG sentence overstated
what the job does. The doc also described multi-model backwards.

Smallest fix: state what is actually cleared and track the node-`content_tsv` gap.

### S3. `tests/organizer_job_safety_support.py:126-128`: the snapshots silently cap at 100 nodes

`graph_snapshot` called `engine.query_nodes(...)` without `limit`, and `query_nodes`
defaults to `limit=100` on both backends (`src/prme/storage/pg/graph_store.py:363`,
`src/prme/storage/duckpgq_graph.py:328`), so any corpus or job that pushes past 100 nodes
silently truncates the before/after comparison.

Smallest fix: pass an explicit large `limit`.

### S4. `tests/organizer_job_safety_support.py:158-170`: the successor check proves evidence, not reachability

For a superseded node the test asserted only that the successor exists and that
`original.evidence_refs <= successor.evidence_refs`; a consolidate that superseded a
source whose fact text is absent from the summary would pass. Acceptance criterion 3 asks
for "every fact, evidence and provenance reachable".

Smallest fix: also assert the retired id is named in the SUMMARY successor's sources.

### S5. `tests/test_organizer_job_safety.py:41,59-74`: two job cases can pass by doing nothing

`EXPECTS_CHANGE` omitted `summarize` and `index_compaction`, so their no-loss cases passed
when the job was a no-op.

Smallest fix: add both to `EXPECTS_CHANGE`, seeding a real stale entry for
`index_compaction`, and check `snapshot_generation` by `nodes_processed` (its snapshots
are read-only).

### S6. `tests/test_organizer_job_safety.py:163-205`: the "both backends" compaction test never touches the lexical side

On PostgreSQL a node that exists never has a `lexical_documents` row
(`pg/lexical_index.py:81-98`), so the lexical count was 0 before and after and only the
vector half was proven; the unscoped branch was untested.

Smallest fix: insert a `lexical_documents` row for the archived node, and add an unscoped
case.

---

## Consider

- **C1 (DRY).** `jobs.py:932-949` duplicated the eviction loop of `jobs.py:847-864`.
- **C2 (pattern).** `jobs.py:893` reached `engine._graph_store._pool`; the engine exposes
  the same pool as `self._pool` (`src/prme/storage/engine.py:438`).
- **C3 (performance).** `jobs.py:923` joined with `n.id::text = d.node_id`, which cannot
  use the `nodes` primary key.
- **C4 (coverage drift).** `tests/test_organizer_job_safety.py:29-41` hardcoded
  `JOB_NAMES`.
- **C5 (shared-database hygiene).** `tests/test_organizer_job_safety.py:90,105` used fixed
  PostgreSQL owner names.
- **C6 (coverage).** The automatic pass was proven through `maybe_run` but not through
  `MaintenanceRunner.schedule()`, the request path.
- **C7 (test strength).** `tests/test_organizer_privacy_scoping.py:84-99` never asserted
  that the first owner's store actually changed.
- **C8 (harness).** `benchmarks/diagnostics/organizer_timing.py:41-91` makes one embedding
  call per seeded node against the default provider.
- **C9 (harness fidelity).** `organizer_timing.py:137-147` never clears the previous size's
  owner, so the "10,000-node" pass ran in a store holding 11,010 nodes across two owners.
- **C10 (docs).** `docs/ORGANIZER-JOBS.md:12` said "each job reads at most 500 nodes per
  pass"; `index_compaction` fetched every stale row.
- **C11 (minor).** `organizer_timing.py:196-206` writes only JSON to `--output` and prints
  the markdown to stdout; `markdown()` defines `cell()` inside the loop.

---

## Dimension results (first review)

1. **Correctness and logic.** Owner binding and placeholder numbering were correct
   (`$1-$3` active states, `$4` owner; `ACTIVE_LIFECYCLE_STATES`,
   `src/prme/types.py:284-288`), PostgreSQL's `lifecycle_state` is `NOT NULL DEFAULT
   'tentative'` so no NULL-not-in trap, and the budget/exception accounting matched the
   DuckDB branch. The issues were S1 and S2.
2. **Code quality and test coverage.** S3-S6 and C4-C7.
3. **Pattern consistency and DRY.** `$N` placeholders and details keys matched
   `pg/graph_store.py` and the DuckDB `JobResult`; the fixture reuse follows an existing
   pattern (`tests/test_retrieval_selection.py:20`). C1-C3.
4. **Redundancy.** Only the eviction loop duplicated an existing helper (C1).
5. **Documentation/harness cross-checks.** The timing table matched the JSON value for
   value, and the seed math is exact.
6. **Breaking changes.** None.

---

## Re-review (snapshot 4000d88c)

Every finding was re-checked against the current working tree. Evidence reported by the
worker: 90 passed in the two new modules on DuckDB and PostgreSQL; the fake-based privacy
subset passed except three restore tests whose only failure is a helper calling
`unquote(None)` on a password-less temporary URL (unrelated to this change). My own
inspection of the guard paths agrees that the default fake path is now the historical
snapshot-less one.

Which are closed:

- **M1 CLOSED.** `tests/privacy_fakes.py:111-141`: `echo_snapshot` defaults to `False`,
  `source_snapshot=request.snapshot if self.echo_snapshot else None`, and the checksum is
  now `sanitized_checksum(fields, request.source_role, request.source_time)` with
  `source_role`/`source_time` echoed and the source-derived `source_id`, matching
  `LocalSanitizer` (`src/prme/privacy/sanitizer.py:384-394`). With the echo off,
  `load_source` skips `validate_source` and `extraction_plan` skips
  `validate_private_grounding`, so the existing fake-based extraction tests keep working;
  the only consumer that needs the snapshot, `tests/test_organizer_privacy_scoping.py:56-59`,
  opts in. Because the checksum now matches the real validator, `echo_snapshot=True` would
  also survive `load_source`.
- **S1 CLOSED.** `src/prme/organizer/jobs.py:912-926`: the scoped and unscoped queries are
  both node-anchored (`n.embedding IS NOT NULL OR EXISTS (...)`, `NOT IN` active states)
  and the orphan `lexical_documents` arm is gone. The docstring (`jobs.py:786-797`) and
  `docs/ORGANIZER-JOBS.md:159-163` now say a node-less row is deliberately left alone.
- **S2 CLOSED.** `jobs.py:790-797` and `:876-884`, `docs/ORGANIZER-JOBS.md:30,165-170,184-192`
  and `CHANGELOG.md:12` now say the pass clears the stored vector plus any
  `lexical_documents` row, that a retired node's generated `content_tsv` is not cleared,
  that lexical search does not filter lifecycle state, and track both in
  dwamianm/prism#521. The multi-model paragraph is no longer inverted: it now states the
  pass selects on `nodes.embedding` while the multi-model index deletes
  `node_embeddings` rows only, so a stale node can be re-selected each pass (#520).
- **S3 CLOSED.** `tests/organizer_job_safety_support.py:192-196` passes `limit=100000`.
- **S4 CLOSED.** `tests/organizer_job_safety_support.py:247-254`: a SUMMARY successor must
  name the retired node in `source_node_ids` or `selected_source_node_ids` (written by
  `src/prme/organizer/consolidation.py:444-445`). The union of the two lists is slightly
  weaker than "selected", but a node is only SUPERSEDED when it was actually retired, so
  this satisfies the intent.
- **S5 CLOSED.** `tests/test_organizer_job_safety.py:32` adds `summarize` and
  `index_compaction` to `EXPECTS_CHANGE`; `support.py:116-143,154-155` archives a node with
  an injected eviction failure so `index_compaction` has a real entry to clear; and
  `test_organizer_job_safety.py:64-67` checks `snapshot_generation` by
  `nodes_processed > 0`.
- **S6 CLOSED.** `test_organizer_job_safety.py:190-198` inserts a `lexical_documents` row
  for the archived node on PostgreSQL, and `:209-223` adds an unscoped case, so both the
  vector and lexical halves are observed. See N1 for the isolation cost of the unscoped
  case.
- **C1 CLOSED.** `_evict_index_entries` (`jobs.py:938-965`) serves both branches.
- **C2 CLOSED.** `jobs.py:879` prefers `engine._pool` and keeps the graph-store pool only
  as a fallback.
- **C3 CLOSED for the practical case.** The index-defeating `LEFT JOIN nodes ON n.id::text
  = d.node_id` is gone; the remaining `d.node_id = n.id::text` is inside an `EXISTS` on
  the indexed `lexical_documents.node_id` side, so no per-row primary-key scan is forced.
- **C4 CLOSED.** `JOB_NAMES = tuple(DEFAULT_JOBS)` (`test_organizer_job_safety.py:31`).
- **C5 CLOSED.** `test_organizer_job_safety.py:88,103,120` append `uuid4().hex[:8]`.
- **C6 CLOSED.** `test_organizer_job_safety.py:116-129` runs `retrieve()` then
  `MaintenanceRunner.drain()`; `retrieve()` schedules the pass at
  `src/prme/storage/engine.py:2888-2889` and `drain()` awaits it
  (`src/prme/organizer/maintenance.py:106-118`).
- **C7 CLOSED.** `tests/test_organizer_privacy_scoping.py:95-100` asserts the scoped pass
  modified its own owner.
- **C9 CLOSED.** `docs/ORGANIZER-JOBS.md:42-52` now states that both sizes share one
  database, that every job is owner-scoped, and that the table reports per-owner work.
- **C10 CLOSED.** `docs/ORGANIZER-JOBS.md:10-14` excludes `index_compaction` from the
  500-node statement and gives it a 500-row bound, which matches the new
  `ORDER BY node_id LIMIT 500` (`jobs.py:925`).

Which are not fully closed (none blocking):

- **C8 PARTIAL.** `docs/ORGANIZER-JOBS.md:50-52` now names the configured embedding
  provider, but the harness still embeds every seeded node through the default local
  provider (`organizer_timing.py:41-91,126-132`). The reported job timings are unaffected
  because seeding happens outside the timed pass. A deterministic provider would still be
  cheaper and offline-safe.
- **C11 OPEN (nit).** `organizer_timing.py:200-206` still writes only JSON to `--output`
  and prints the markdown. No doc claims otherwise, so this is cosmetic only.

### New finding from the fixes

**N1 (Should Fix, test isolation; not blocking).**
`tests/test_organizer_job_safety.py:220` runs
`await engine.organize(jobs=["index_compaction"], budget_ms=5000)` unscoped. The `config`
fixture (`tests/test_durable_ingestion.py:264-285`) points at the shared
`PRME_TEST_DATABASE_URL` schema, so on PostgreSQL this unscoped pass clears
`nodes.embedding` and deletes `lexical_documents` rows for every non-active node in that
database, for every owner and every prior run. `MemoryEngine.organize` also drains any
pending materialization queue unscoped first (`src/prme/storage/engine.py:4507-4518`).
The test passes when one pytest process runs sequentially (as the 90-passed evidence
shows), but it makes the suite non-isolated: a second pytest process against the same
server, or a CI matrix sharing one PostgreSQL service, can see its own retired nodes
evicted mid-test and its pending materializations drained. Smallest fix: put the unscoped
case in its own schema (the `privacy_fakes.fresh_postgres_store()` pattern already exists
for exactly this), or create a second owner's stale node and assert it survives a scoped
run instead of running the whole store unscoped. This is the only new defect I found in
the fixes; nothing in the production code changed regressed.

Two smaller notes, no action required:

- **N2.** `_index_presence` (`tests/organizer_job_safety_support.py:162-189`) checks
  DuckDB `vector_metadata` only, so a DuckDB lexical-only eviction of an active node would
  not be caught by `assert_unchanged`. No current job does that.
- **N3.** `echo_snapshot=True` must not be combined with fake-based extraction:
  `admission.py:634-638` would then run `validate_private_grounding` and the canned
  `FakeExtractor.result` has no `evidence_quote`. The flag's comment
  (`tests/privacy_fakes.py:114-115`) scopes it to generated output; a sentence in the
  constructor docstring would keep a future test from enabling it globally.
