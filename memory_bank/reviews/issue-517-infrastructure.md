# Issue 517 Infrastructure/Ops Review (checklist 6)

Status: COMPLETE
Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced
Snapshot: f6eb75eda452e53eec40538cd10278656f4fe0ef72e2125069d158918d75495d
(uncommitted work tree; no file edited except this report)

## Verdict

The change is wired end to end for the default (option off) path and for both
storage backends, and the new tests are collected and safely skipped where they
need PostgreSQL. One real gap: on a protected (privacy-enabled) store the new
`JobResult` fields are dropped by the privacy projection, so a hosted protected
deployment always reads `completed`/not-stopped. Everything else is docs and
robustness follow-up.

## Checklist

1. PostgreSQL `organizer_watermarks` + runtime fingerprint: mechanism is intended
   and matches the documented fail-closed contract, but the operator-facing
   upgrade step is not in the release notes. Finding 2.
2. DuckDB migration on open (directory, single-file, `prme migrate`,
   `compact pack`): correct. Clean.
3. `incremental_enabled` default false, env loading, `config_audit`, docs: correct
   and verified by running the config. Clean.
4. CLI / HTTP `JobResult` flow: additive and safe on the HTTP schema; the
   protected-store projection drops the new fields. Finding 1.
5. CI collection and PG skip: correct. Clean (one flake-risk note, Finding 6).
6. Other deployment steps: erasure, protected format, restore, packaging all
   covered. Remaining items are documentation accuracy. Findings 3, 4, 5.

## Findings

### Finding 1 (medium, should fix): protected stores lose `status` and `stopped_on_budget`

`organize()` and `end_session()` are both `@privacy_disclosure`
(`src/prme/storage/engine.py:4505`, `src/prme/storage/engine.py:5219`). On a store
with a privacy runtime selected, `project_public_result`
(`src/prme/privacy/reads.py:585-601`) routes the result to `project_organize`
(`src/prme/privacy/reads.py:682-685`), which rebuilds every per-job result with
only the old fields:

- `src/prme/privacy/diagnostics.py:178-188` constructs
  `JobResult(job=..., nodes_processed=..., nodes_modified=..., errors=...,
  duration_ms=...)`.

The new fields therefore fall back to their defaults
(`status="completed"`, `stopped_on_budget=False`;
`src/prme/organizer/models.py:29-30`), even when the job produced
`status="budget_exhausted"` / `status="error"` and a resume point in
`src/prme/organizer/jobs.py:324-334` (and the dedup/snapshot twins at
`jobs.py:790-804`, `jobs.py:981-992`).

Impact: the CLI prints the wrong status on such a store
(`src/prme/cli.py:619-625`) and the HTTP route returns the projected dict
(`src/prme/api/routes.py:588-593`). Stored data and the option-off path are not
affected, and no test currently asserts the projected values
(`tests/test_privacy_erasure_copies.py:33` checks only `jobs_skipped`).

Fix: carry the two typed scalars through `project_organize`, whitelisting
`status` to `{"completed", "budget_exhausted", "error"}`, and add a test that a
budget-stopped protected run still reports it. `details` should stay dropped
(it is heterogeneous and intentionally excluded by the comment at
`diagnostics.py:176-177`).

### Finding 2 (medium, docs/ops): PostgreSQL upgrade step missing from release notes

The mechanism is the intended one and works as designed:
`organizer_watermarks` is added to `_RUNTIME_TABLES`
(`src/prme/storage/pg/schema.py:29`), `initialize_pg_database` creates it inside
the advisory-locked transaction (`pg/schema.py:289-294`, `pg/schema.py:376-377`)
and records the new fingerprint (`pg/schema.py:395`, `62-67`), and
`validate_pg_runtime` rejects a runtime open when the stored table list or
catalog fingerprint differs (`pg/schema.py:82-85` -> `94`). An engine opened
with `initialize=False` (`src/prme/storage/engine.py:949-953`;
`src/prme/storage/pg/namespace.py:127`) therefore fails closed with
`postgres_schema_bootstrap_required` until the privileged bootstrap re-runs.
That contract is documented generically in `docs/WORKSPACES.md:175-181`, and the
default `MemoryEngine.create` path (initialize=True) upgrades automatically.

What is missing: the release notes name the new table but not the upgrade step.
`CHANGELOG.md:12` (Unreleased, Added) and the RFC section
(`docs/RFC-0015-Self-Organizing-Memory.md:469-496`) say nothing about a
bootstrap re-run, while the released sections already use an explicit
"Upgrade notes:" paragraph (`CHANGELOG.md:34`). Add a line for the next release:
a namespace or runtime-login deployment must re-run the privileged bootstrap
before a runtime open, and a runtime login that grants per-table DML
(`docs/WORKSPACES.md:184-186`) needs INSERT/UPDATE/DELETE on
`organizer_watermarks` once `incremental_enabled` is on. The second point is the
only new runtime write in this change.

### Finding 3 (low, docs accuracy): RFC says a failed run "writes neither"

`docs/RFC-0015-Self-Organizing-Memory.md:489` states "A failed run writes
neither, so the window re-runs." The code writes a resume point for a failed
run that processed a prefix: `src/prme/organizer/jobs.py:100-114` writes
`completed_through` (unchanged) plus `resume_updated_at`/`resume_id` when
`status == "error"` and a cursor exists, which is exactly what the
`_run_incremental` docstring describes at `jobs.py:70-73`. Only a failure with
no processed prefix writes nothing. Correct the RFC sentence to match.

### Finding 4 (low, docs precision): "writes nothing" in the CHANGELOG entry

`CHANGELOG.md:12` says "A second run over an unchanged scope writes nothing."
The memory records are unchanged (asserted in
`tests/test_organizer_incremental.py:176-188`), but every completed incremental
run still upserts its watermark row (`jobs.py:115-120` ->
`src/prme/storage/duckpgq_graph.py:544-555`,
`src/prme/storage/pg/graph_store.py:602-617`), which rewrites
`completed_through` and `updated_at`. Say "changes no memory" so an operator
does not read it as zero write amplification.

### Finding 5 (low, perf/ops risk): no index serves the incremental window; protected scans are whole-scope

The window and ordering use `COALESCE(updated_at, created_at)`
(`src/prme/storage/duckpgq_graph.py:467`, `497`;
`src/prme/storage/pg/graph_store.py:530`, `562`). No existing nodes index can
serve that expression (`src/prme/storage/schema.py:149-164`,
`src/prme/storage/pg/schema.py:185-193`), so each page sorts the scope's rows.
The 10,000-node test passes (`tests/test_organizer_incremental.py:393-426`,
`tests/test_organizer_incremental_pg.py:151-176`), so this is not a blocker.

On a protected store the cost is worse: `AdmittedGraphView.scan_changed_nodes`
(`src/prme/privacy/read_graph.py:153-183`) walks every admitted node through
`_all_nodes` (`read_graph.py:105-115`) on every page request, so the
"incremental" scan is O(whole scope) per page there, and no test measures a
protected incremental run. Record this as a known limitation in the RFC, and
consider an expression index on `(COALESCE(updated_at, created_at), id)` later.

### Finding 6 (low, CI reliability): wall-clock bound and shared-database rows

`tests/test_organizer_incremental.py:422` and
`tests/test_organizer_incremental_pg.py:172` assert a run finishes under 1.0 s.
CI runs the suite with `-n auto --dist worksteal` against one PostgreSQL service
(`.github/workflows/ci.yml`, `PYTEST_PARALLEL`), so the timing bound is a flake
risk on a loaded runner. The PG test also inserts 10,000 nodes that are never
removed (`test_organizer_incremental_pg.py:151-161`); I found no assertion this
breaks (other PG tests are user-scoped, or use a dedicated schema via
`tests/privacy_portability_support.py:172-192` and
`tests/test_pg_namespace_pool.py`), but it is the only new unmocked bulk insert
into the shared per-worker database.

## Verified clean

- DuckDB DDL: `create_schema` creates the table with
  `CREATE TABLE IF NOT EXISTS` (`src/prme/storage/schema.py:237-245`) and runs
  on every open through `initialize_database` (`schema.py:580`), which the
  DuckDB path calls for both layouts (`src/prme/storage/engine.py:638`, `734`),
  so existing directory and single-file packs gain it on open.
- Pack migration: `prme migrate` opens the source with an engine first
  (`src/prme/storage/pack_tools.py:405`), then copies with
  `COPY FROM DATABASE` (`pack_tools.py:228`); `compact_pack` uses the same copy
  (`pack_tools.py:236-260`). A source without the table produces a target
  without it, which the next engine open creates. Read-only pack opens
  (`src/prme/cli.py:1241`, `1337`) never read the table.
- PostgreSQL DDL placement inherits the #411 advisory serialization
  (`src/prme/storage/pg/schema.py:289-294`), and the table is created for the
  protected format too (only event/privacy DDL is gated by `protected`).
- Erasure: the new statements sit behind the existing catalog check
  (`src/prme/storage/erasure.py:682-697`) and run on both backends through
  `ExtractionWorkRepository` (`src/prme/storage/extraction_work.py:72-90`);
  passing `str` for the UUID `resume_id` matches the existing `nodes.id` pattern
  at `erasure.py:672` and is accepted by asyncpg for UUID columns
  (`asyncpg/pgproto/codecs/uuid.pyx:15-18`).
- Config: `incremental_enabled` defaults false
  (`src/prme/config.py:630-640`); `PRME_ORGANIZER__INCREMENTAL_ENABLED=true`
  resolves to `True` and off to `False` (checked by constructing `PRMEConfig`
  with the variable, `config.py:1817-1821`); documented at
  `documentation/configuration.md:225`; not `[HYPOTHESIS]`-tagged, so
  `config_audit` and `tests/test_config_audit.py` need no entry.
- Dispatch: `run_job` gates on the flag and only for
  `INCREMENTAL_JOBS` (`src/prme/organizer/jobs.py:175-180`,
  `src/prme/organizer/watermarks.py:30-32`); the engine passes
  `self._config.organizer` (`src/prme/storage/engine.py:4567-4569`).
- CLI: `cmd_organize` prints `status` and the budget-stop line
  (`src/prme/cli.py:619-625`); JSON mode stays serializable because
  `_run_incremental` stores ISO strings (`jobs.py:122-125`) and pops `_resume`
  (`jobs.py:98`).
- HTTP: `OrganizeResponse.per_job` is `dict[str, Any]`
  (`src/prme/api/models.py:469-474`) filled with
  `jr.model_dump(mode="json")` (`src/prme/api/routes.py:588-593`), so the new
  fields flow without a strict response break; MCP returns only `jobs_run` and
  `duration_ms` (`src/prme/mcp/server.py:1013-1016`).
- CI: `pytest tests/` collects `test_*.py` with no `python_files` override
  (`pyproject.toml:104-111`), so both new files run; the PG file skips without
  `PRME_TEST_DATABASE_URL` (`tests/test_organizer_incremental_pg.py:22-28`),
  the same pattern as `tests/test_pg_graph_store.py:18-24`, and conftest gives
  each xdist worker its own database (`tests/conftest.py:30-60`).
- Packaging: `uv_build` ships `src/prme`; the new module needs no
  `src/prme/organizer/__init__.py` export (`jobs.py:18-22` imports it directly).
- Option-off behavior: no watermark is read or written and every job stays
  full-scope (`jobs.py:175-180`), asserted at
  `tests/test_organizer_incremental.py:373-390`.
- Restore/export and protected format create the table through the same
  initializers (`src/prme/storage/privacy_export.py:584-596`), and neither
  `prme doctor` table check (`src/prme/cli.py:1245-1249`) nor the privacy
  format preflight (`src/prme/storage/privacy_format.py:46-104`) enumerates a
  closed table set, so no other code path needs the new name.
