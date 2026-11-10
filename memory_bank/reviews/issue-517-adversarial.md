# Issue #517 Incremental Organizer — Adversarial Review (checklist 8)

Status: COMPLETE

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- Reviewed snapshot: f6eb75eda452e53eec40538cd10278656f4fe0ef72e2125069d158918d75495d (uncommitted work tree)
- Reviewer: adversarial (postmortem-first, at most 8 ranked scenarios)
- No file was edited except this report.

## Checklist

1. Skipped/duplicated node at a page boundary or timestamp tie
2. The age-window union interacting with the keyset
3. Watermark advanced past work that never ran (scan error, index unavailable, partial page, concurrent writer, clock skew, ceiling captured before a write)
4. Two concurrent organize calls for the same scope
5. Unscoped run and scoped run sharing the `''` key
6. Erasure racing an organize
7. Long-running run holding a stale resume cursor after store restore/migrate
8. Option-off path regression; `''` scope key colliding with a real user id of `''`

Method: read the diff and the real call path (`run_job` -> `_run_incremental` -> per-job handler -> `scan_changed_nodes` on DuckDB, PostgreSQL and the protected `AdmittedGraphView`), the watermark read/write, the erasure purge, `prune`/`migrate`, and the node write path that assigns `updated_at`. Confirmed findings are saved below as they were verified.

---

## Ranked scenarios

### 1. Promote's age-window `OR` is not bounded by the page cursor, so the job can never advance past the age band

- Trigger: `incremental_enabled=True`, and one scope has more than `_INCREMENTAL_PAGE_SIZE` (500) tentative nodes in the age band (`created_at` in `(completed_through - promotion_age_days, ceiling - promotion_age_days]`) that the run does not promote. Ingestion creates `NodeType.ENTITY` nodes as TENTATIVE with an empty `evidence_refs` when there is no evidence event (`src/prme/ingestion/entity_merge.py:129-155`), and the default `promotion_evidence_count` is 1, so those nodes are never promoted. A long gap between runs widens the band (band width = run gap).
- Mechanism: `src/prme/storage/duckpgq_graph.py:485-492` (identical at `src/prme/storage/pg/graph_store.py:552-557` and `src/prme/privacy/read_graph.py:178-179`) appends `created_at > age_floor` to the *same* `OR` as the `(stamp,id) > cursor` keyset. Ordering is `COALESCE(updated_at, created_at) ASC` (`duckpgq_graph.py:497`), so on every page the same low-stamp age-band nodes are re-selected regardless of `after_id`. The promote loop advances the page cursor but the age disjunct defeats it (`src/prme/organizer/jobs.py:283-322`, page advance `:319-320`). When the first page is filled by unpromotable age nodes, page *n+1* returns the same 500, the run budget-stops (`:304-307`) and writes a resume cursor inside that set (`:333-334`); `completed_through` never advances, so `age_floor` never moves and the same wide band persists on the next run.
- Likelihood: Medium. Needs one scope with >500 unpromotable tentative nodes in the band (bulk `ingest()` of entity-heavy conversations, or the first run after a deployment gap), and incremental is opt-in.
- Impact: the promote job makes zero net progress and burns its whole slice of the shared organize budget; because promote is first in `DEFAULT_JOBS` (`src/prme/organizer/jobs.py:33-49`) and `organize()` skips later jobs once `remaining_ms <= 0` (`src/prme/storage/engine.py:4558-4564`), a single stalled scope can skip decay, archive, deduplicate and the rest for that call. It also inflates `nodes_processed` on every page even when promotable.
- Detection: quiet. Status is `budget_exhausted`, no error; the CLI prints "Stopped on the budget" (`src/prme/cli.py:619-626`) and the next call silently repeats.
- Cheapest mitigation: require the keyset on top of the candidate set, i.e. `(stamp,id) > cursor AND ((stamp > run_start) OR created_at > age_floor)`, in all three stores. Size: S.
- Recommended verdict: Fix now.
- Newly introduced: yes (the incremental path). The full-scope promote path has an older, narrower starvation with `limit=500` + `oldest_first` (`jobs.py:214-220`), but this change advertises convergence and adds budget starvation of the other jobs.

### 2. Ceiling captured before a write's `updated_at` is assigned permanently skips that record

- Trigger: a concurrent `store()`/`ingest()` while an incremental run starts. `MemoryNode.updated_at` is assigned by the Pydantic default factory at model construction (`src/prme/models/base.py:35-37`, constructed at `src/prme/storage/engine.py:1330`), then the node is queued and inserted later (`engine.py:1351-1352`). If the row becomes visible only after the scan, its stamp is below the ceiling. Cross-process PostgreSQL (API writer + cron organizer) with any client/DB clock skew produces the same ordering.
- Mechanism: `src/prme/organizer/jobs.py:76` captures `ceiling = now()`, `:83` sets `updated_after = completed_through`, and `:119` advances `completed_through = ceiling` after the run. The next window's lower bound is strict (`>`, `src/prme/storage/duckpgq_graph.py:483`, `src/prme/storage/pg/graph_store.py:549`), so a row with `updated_at < ceiling` that the scan missed is excluded forever. Neither the promote age window nor the deduplicate scan revisits it (`created_at` is too recent for the old-age band, `jobs.py:267-292`).
- Likelihood: Medium. Opportunistic organizer passes run during retrieve/ingest, and every write has a construction-to-insert gap; multi-process PG adds clock skew.
- Impact: a memory can stay TENTATIVE indefinitely and is never deduplicated or snapshotted until something updates it. The docstring's claim that "records written while the job runs are picked up by the next one" (`jobs.py:66-68`) is false for this interleaving.
- Detection: silent; no error, no counter.
- Cheapest mitigation: make the first page's lower bound inclusive (`>= completed_through`) when `updated_after` comes from a completed run rather than a resume cursor, accepting a bounded re-examination of exact ties; or derive the ceiling from the store clock after the first page is read. Size: XS.
- Recommended verdict: Fix now.
- Newly introduced: yes.

### 3. The `''` scope key collides with an empty `user_id`, and one scoped call poisons every later unscoped run

- Trigger: `POST /v1/organize` with `{"user_id": ""}` (or any client that serializes a missing owner as an empty string) while incremental is on. `OrganizeRequest.user_id` has no `min_length` (`src/prme/api/models.py:460`) and `MemoryEngine.organize` does not validate it (`src/prme/storage/engine.py:4536-4540`).
- Mechanism: `scope_key(None)` and `scope_key("")` both return `""` (`src/prme/organizer/watermarks.py:35-37`, used at `src/prme/organizer/jobs.py:75`). The empty-string scoped run filters `user_id = ''`, finds nothing, and completes, writing `completed_through = ceiling` for scope `""` (`jobs.py:115-120`). A later unscoped run reads that row and starts at `> ceiling` for *all* users, skipping every node changed before it.
- Likelihood: Low (requires an empty-string owner), but the cost of the mistake is a whole maintenance window for the whole store; note `store()`-style paths reject empty `user_id`, so a well-behaved client never sends one, which is exactly the kind of default-value bug that ships.
- Impact: promote/deduplicate/snapshot silently skip an intervall for every tenant; a real tenant whose id is `""` would also share the key with the unscoped run.
- Detection: silent.
- Cheapest mitigation: reject an empty `user_id` in `organize()`, or use a sentinel that cannot be an owner (e.g. `"\x00unscoped"` or a separate boolean column). Size: XS.
- Recommended verdict: Fix now.
- Newly introduced: yes.

### 4. Erasure deletes the watermark row once, but the organizer recreates the erased owner's plaintext `scope_key` afterwards

- Trigger: an external cron calls `organize(user_id=X)` for an erased owner X, or an organize for X in flight when the erasure purge commits. `organize()` does not drain erasures (`src/prme/storage/engine.py:4506-4579`), so this is a cross-process ordering, not an in-call one, but deployments that run a per-tenant maintenance loop make it normal.
- Mechanism: the purge deletes `organizer_watermarks WHERE scope_key = str(owner_id)` (`src/prme/storage/erasure.py:691-697`) and bumps `privacy_erasure_epoch` (`erasure.py:709`). Neither `MemoryEngine.set_organizer_watermark` (`src/prme/storage/engine.py:3168-3184`) nor the DuckDB/PG upserts check any erasure epoch, so a later run for X writes the plaintext owner id back. The code comment itself states the row must not survive the purge.
- Likelihood: Low-Medium. Requires incremental enabled plus a post-erasure organizer run for that owner.
- Impact: the erased owner's identifier persists in a new table the erasure path no longer revisits; the change's own comment classifies it as sensitive.
- Detection: silent.
- Cheapest mitigation: store a keyed hash of the user id as `scope_key`, or gate watermark writes on the erasure epoch and re-delete on a purge. Size: S (hash) to M (epoch fence).
- Recommended verdict: Follow-up. Promote to Fix now if the erasure contract is treated as hard (the added code shows it is intended to be).
- Newly introduced: yes (the table and its write path are new).

### 5. Changing `promotion_evidence_count`, `dedup_similarity_threshold`, or an admission policy has no effect on already-scanned records

- Trigger: an operator lowers `dedup_similarity_threshold` (or `promotion_evidence_count`) and re-runs organize expecting the full-scope behaviour to re-evaluate existing data. Same for a privacy/admission change that makes previously hidden nodes readable.
- Mechanism: the watermark holds only `completed_through` and a cursor (`src/prme/organizer/watermarks.py:40-55`); it has no config or policy identity, and `_run_incremental` never resets it (`src/prme/organizer/jobs.py:76-84`). A completed run has already advanced past the records the new setting would affect.
- Likelihood: Medium (this is the documented remedy for a missed duplicate, and the full path currently honours it).
- Impact: a tuning change silently does nothing for existing nodes, unlike the option-off path; for a protected store it also means nodes admitted later are never revisited (see scenario 7).
- Detection: quiet.
- Cheapest mitigation: include a fingerprint of the job-relevant config in the watermark row and reset `completed_through` when it changes. Size: S.
- Recommended verdict: Follow-up.
- Newly introduced: yes (inherent to the incremental design).

### 6. Concurrent organize calls do non-atomic watermark read-modify-write; duplicated work and a backwards watermark, no skip found

- Trigger: two organize calls for the same scope overlap (cron + API, or two worker processes; there is no organize-level lock).
- Mechanism: `_run_incremental` reads the watermark, both runs scan the same window, and both upsert unconditionally (`src/prme/organizer/jobs.py:75-120`; no lock in `src/prme/storage/engine.py:4506-4579`). Whichever finishes last wins; if it has the smaller ceiling, `completed_through` moves backwards, and a completed run's write can null the other run's resume cursor.
- Likelihood: Medium (nothing serialises organize calls).
- Impact: duplicated promotion attempts (each already-promoted node raises `ValueError`, inflating `errors`) and duplicated merge proposals (revalidated in the merge transaction, so no double-archive), plus doubled budget spend. I traced the interleavings and found no path that advances the watermark past unprocessed work, because any regression in `completed_through` only causes a re-scan.
- Detection: quiet (error counts and extra time).
- Cheapest mitigation: make the upsert monotonic (`GREATEST` on `completed_through`, keep the cursor only if newer), or take a per-scope advisory lock for the run. Size: S.
- Recommended verdict: Follow-up. The `completed_through` monotonicity part is cheap enough to do now.
- Newly introduced: yes (the RMW is new; the absence of an organize lock is pre-existing).

### 7. On a protected store, a page that disclosure empties is read as "end of window" and later admitted nodes are skipped; every page also scans the whole scope

- Trigger: a protected (privacy-admitted) store where the next 500 nodes in `(stamp,id)` order are all withheld from the disclosure context.
- Mechanism: `AdmittedGraphView.scan_changed_nodes` builds the whole page, applies the keyset/age filters in Python, sorts, slices, and then discloses (`src/prme/privacy/read_graph.py:153-183`). If `disclose` drops every row the caller receives `[]`, and each incremental job treats an empty page as the end of its window and advances `completed_through` (`src/prme/organizer/jobs.py:301-302`, `:415-417`, `:963-965`). Nodes later in the window that *are* admitted are never examined. The same method also loads the entire scope on every page (via `_all_nodes`, `read_graph.py:105-115`), so the "10k nothing-new under one second" property does not hold on a protected store.
- Likelihood: Low (all-500 withheld is rare) for the skip; Medium for the O(scope)-per-page cost.
- Impact: maintenance silently skipped for a protected tenant; organize latency grows with scope size again.
- Detection: quiet.
- Cheapest mitigation: return the last *scanned* id/timestamp even when disclosure drops rows (advance by scan position, not by disclosed position), and push the keyset into the backend scan. Size: S to M.
- Recommended verdict: Follow-up.
- Newly introduced: yes.

### 8. `updated_at IS NULL` — which the new SQL defends against — breaks the new Python cursor assumption

- Trigger: a nodes row with `updated_at IS NULL` (the schema allows it: `updated_at TIMESTAMPTZ DEFAULT current_timestamp` with no `NOT NULL`, `src/prme/storage/schema.py:135`; the comment at `duckpgq_graph.py:466` explicitly anticipates "a legacy row without updated_at").
- Mechanism: the scan uses `COALESCE(updated_at, created_at)` (`src/prme/storage/duckpgq_graph.py:467`) but the loop reads `node.updated_at` directly for the cursor (`src/prme/organizer/jobs.py:309`, `:319-320`) and calls `.isoformat()` on it (`:333-334`). `_row_to_node` passes that NULL through (`duckpgq_graph.py:2404`) into a non-optional `datetime` field (`src/prme/models/base.py:35-37`), so the row fails validation and the scan raises; if it did not, `updated_after = None` would drop the keyset clause and restart pagination.
- Likelihood: Low (the DDL default prevents NULLs unless a row was inserted explicitly or restored from an older pack).
- Impact: the job errors on every run with no watermark advance (loud in logs, quiet to the operator) and can never complete for that scope; or, if validation is relaxed, the page loop never advances.
- Detection: loud in logs, silent in the API result (job skipped).
- Cheapest mitigation: compute the stamp as `updated_at or created_at` in the loop (as `AdvertisedGraphView` already does at `read_graph.py:165`) and use it for the cursor. Size: XS.
- Recommended verdict: Follow-up.
- Newly introduced: the schema allowance is pre-existing; this code newly depends on a NOT NULL assumption the schema never enforced.

---

## Clean checklist results (one line each)

- Page boundary / timestamp tie under a normal scan (no age clause): correct — `(stamp, id)` keyset with exclusive lower bound on all three stores (`duckpgq_graph.py:475-484`, `pg/graph_store.py:540-551`); no off-by-one found.
- Option-off path: `run_job` gates on `incremental_enabled` (`jobs.py:175-178`), default off (`src/prme/config.py:630-640`); the dedupe refactor returns the same candidates and only moved the budget clock after index open (`src/prme/organizer/deduplication.py:63-98`). No regression found.
- Scan error / partial page: `_promote_incremental` and `_deduplicate_incremental` set `failed` and the writer keeps the old `completed_through` with the processed prefix only (`jobs.py:100-114`); `deduplicate` searches the prefix before merging and advances only over `completed` nodes (`jobs.py:744-778`). Correct.
- Two concurrent organize calls for the *same* scope: no skip found (duplicate work only), see scenario 6.
- Unscoped vs scoped with a real non-empty user id: separate watermark keys, no collision; only the empty-string case collides (scenario 3).
- Erasure's resume-cursor handling: `erasure.py:682-690` nulls `resume_updated_at`/`resume_id`, so `resume_cursor` returns None and the next run restarts from `completed_through`. Correct; only the re-creation in scenario 4 remains.
- Store restore/migrate with a stale cursor: not found for supported paths — `prme migrate`/`compact` copy the whole database with `COPY FROM DATABASE` (`src/prme/storage/pack_tools.py:228`) so watermarks travel consistently, and `rebuild_indexes` leaves nodes and watermarks untouched. A *partial* restore that pairs older nodes with newer watermarks is an operator error, not a code path.

---

## Pre-mortem headline

Scheduled organize stopped promoting and stopped merging for a month: enabling incremental turned the promote age window into a page-cursor-free loop, so every run re-examined the same 500 unpromotable tentative entities, burned the shared budget and skipped the rest of the maintenance queue, while every concurrent write near a run ceiling was silently dropped from later windows.
