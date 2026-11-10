# Issue #517 Security Review (checklist 2)

Status: COMPLETE

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- Reviewed snapshot: f6eb75eda452e53eec40538cd10278656f4fe0ef72e2125069d158918d75495d (uncommitted work tree; nothing edited)
- Scope: incremental organizer watermarks (#517): new `organizer_watermarks` table on DuckDB and PostgreSQL, watermark reads/writes, the new `scan_changed_nodes` read path (including the protected `AdmittedGraphView` route), and the new erasure purge.

## Result table

| Area | Result | Details |
|---|---|---|
| Tenant isolation of the per-scope watermark keys | FAIL | `scope_key()` maps both "unscoped operator run" and `user_id=""` to the same key, so an empty-user request writes the operator's whole-store watermark (F1). Watermark API also bypasses the owner facade (F4). |
| SQL injection safety of the new queries | PASS | Every new statement binds user data: `?`/`$n` placeholders only, and the only interpolation is placeholder counts and fixed identifiers (pg/graph_store.py:514-559, duckpgq_graph.py:451-492, duckpgq_graph.py:510-566, erasure.py:686-697). `after_id`/`resume_id` are bound and cast, never concatenated. |
| Erasure purge correctness and scope | PASS | Per-node cursor clear and owner-scoped delete are correct and idempotent; residual coverage risk in F6. |
| AdmittedGraphView projection and disclosure of `scan_changed_nodes` | PASS | `_owner` check, per-node `project_node`, and `context.disclose` guard are applied; owner filter is hardcoded to `context.user_id` (read_graph.py:153-183, 68-103). |
| Protected-path behavioral equivalence (window semantics) | FAIL | First incremental run on a protected store returns an empty window (F3). |
| Cross-owner rows in the new read path | FAIL | PostgreSQL `scan_changed_nodes` omits the #443 foreign-successor hide that both `scan_nodes` and the DuckDB implementation apply (F2). |
| New log / CLI / result output content | PASS | Only status words, counts and timestamps are added; the resume node UUID is popped before the result leaves `_run_incremental` (jobs.py:98, 122-125). No content or secrets. |
| New config surface defaulting off | PASS | `run_job` gates incremental mode on `config.incremental_enabled` (jobs.py:175-179), the only reader/writer of watermarks is `_run_incremental`, and the table stays empty when off. |

## Findings

### F1 (Low-Medium) Tenant-isolation flaw: the `""` sentinel collides with an empty user id
`src/prme/organizer/watermarks.py:35-37` maps `user_id is None` (unscoped operator run) to `""`, and passes any other string through unchanged. `organize()` does not reject an empty `user_id` (src/prme/storage/engine.py:4506-4540 only reserves `feedback_apply`), and the HTTP route forwards an empty owner verbatim when no identity is bound (src/prme/api/routes.py:569-570, reachable in the legacy-global-key mode where `request.state.user_id` stays `None`, routes.py:132-145).

`_run_incremental` would then write `completed_through` under `scope_key = ""` (src/prme/organizer/jobs.py:75-77, 116-121), which is the same row a real unscoped operator run reads. A later operator run then treats the whole store as already processed and silently skips its backlog. No memory content is read or written; impact is maintenance suppression (and rows keyed by arbitrary caller-supplied ids: `scope_key="<anything>"` grows the table). The codebase treats an empty/non-canonical user id as invalid elsewhere (duckpgq_graph.py:729-734, pg/graph_store.py:636-641, privacy/reads.py:68-73), so the sentinel should be one that cannot collide, or `organize()` should reject `""`.

### F2 (Low) PostgreSQL `scan_changed_nodes` returns a foreign `superseded_by`
`src/prme/storage/pg/graph_store.py:568` returns `[record_to_node(row) for row in rows]` with no `_owned_view` call, so a node whose `superseded_by` names another owner's node is returned as stored. Both sibling paths hide it: PG `scan_nodes` at pg/graph_store.py:681, PG `query_nodes` at 453, and the new DuckDB `scan_changed_nodes` at duckpgq_graph.py:433. This is the #443 cross-owner identifier exposure reintroduced on one backend only. It is masked on a protected store because `AdmittedGraphView` reaches the graph through `scan_nodes`, and the organizer does not surface `superseded_by`; the exposed value is another tenant's node UUID.

### F3 (Medium, protected-path correctness) The first incremental run on a protected store examines nothing
`AdmittedGraphView.scan_changed_nodes` starts `in_window = False` and only sets it from `updated_after`/`created_after` (src/prme/privacy/read_graph.py:170-181). On a job's first run the watermark is absent, so `updated_after` and `created_after` are both `None` (jobs.py:75-90), and the view returns `[]`. The graph-store query has no lower bound in that case (`window` stays empty, duckpgq_graph.py:488-493; pg/graph_store.py:548-558), so it returns the whole ceiling window. The run therefore reports `completed` and stamps `completed_through=ceiling` (jobs.py:114-121) without ever examining pre-existing tentative nodes or duplicates. Not a disclosure (fewer rows, not more), but a silent divergence between the protected and unprotected paths.

### F4 (Low) The watermark API bypasses the owner facade and accepts any `scope_key`
`MemoryEngine.get_organizer_watermark` / `set_organizer_watermark` (src/prme/storage/engine.py:3157-3185) use `self._graph_store` directly, unlike every other read (`_ordinary_graph`). `resume_id` is a node UUID that, under an unscoped run, may belong to another owner; the getter returns it with no ownership check and the setter overwrites any scope's row. There is no API route for either method today, so only in-process callers are exposed, but it is the one new read that does not go through `AdmittedGraphView` by design. Recommend an owner argument matched against the caller, or documenting the metadata-only exception at the call site.

### F5 (Informational) The new tenant filter is untested
No test asserts that a scoped `scan_changed_nodes` excludes another user's nodes: both new suites create a single owner (tests/test_organizer_incremental.py, tests/test_organizer_incremental_pg.py), and the existing tenancy suite runs with incremental mode off (tests/test_organizer_tenancy.py:59-62, 256-263).

### F6 (Informational) The erasure purge is untested and matches the scope by exact string
The new statements at src/prme/storage/erasure.py:682-697 have no test coverage (no test references `organizer_watermarks` outside the incremental suites). The per-node cursor clear (682-690) correctly runs for every memory-mode erase regardless of owner, and the owner delete (691-697) runs once per resumable page inside the same transaction (608-623). The delete matches `scope_key = str(intent.owner_id)`, so it only removes the row if `organize()` received the canonical lowercase UUID string; that is the same convention erasure already uses for `nodes.user_id` (erasure.py:134-135, 672), but the watermark row has no second removal path. `_verify_purge` does not check the table, so a silent failure would not fail the purge.

### F7 (Informational) New job warnings use a plain logger
`src/prme/organizer/jobs.py:296, 734, 776, 954` log `exc_info=True` through a plain `logging.getLogger` (jobs.py:9, 20), not the `ScopedLogger` used in retrieval/vector/lexical modules (privacy/diagnostics.py:151-190). On a protected store the raw exception text is emitted. The scan parameters in these four paths are a user id and timestamps, not memory content, and this matches the existing organizer logging, so no content leak is shown here; flagged because the new incremental code is where a failure is most likely to occur.

## Residual risks accepted
- The table stores `scope_key` (user id) and a node UUID in plaintext outside the privacy journal. It holds no content, it is purged on a memory-mode erase, and the resume UUID is cleared wherever it names an erased node. Export/restore of a protected store copies it, which is consistent with other operational metadata.
- `organizer_watermarks` was added to PG `_RUNTIME_TABLES`, which changes the schema fingerprint and forces a bootstrap of pre-existing PostgreSQL deployments (`postgres_schema_bootstrap_required`). Availability concern only; owned by the infrastructure checklist.
- The opportunistic `MaintenanceRunner` still promotes full-scope and never advances a watermark, so the two paths can interleave. Behavior only, no exposure.
