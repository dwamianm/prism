# Issue #517 Incremental Organizer — Adversarial Re-Review

Status: COMPLETE

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- NEW reviewed snapshot: 68700cdab0cf41f480e2d7bc753fd9c09be29aa52914d73f2c2bde0a61a74847 (uncommitted work tree)
- Prior report: memory_bank/reviews/issue-517-adversarial.md
- Reviewer: adversarial, focused re-review of the fixes after the first report
- No file was edited except this report.

## Checklist (this round)

1. Overlap: can a durable write be applied twice in a way that matters?
2. Overlap vs. the resume cursor.
3. Can a completed run's watermark write move `completed_through` backwards relative to a concurrent run?
4. Does the reduced one-job incremental set (`deduplicate` only) leave a new gap?
5. Verify the fixes for prior scenarios 1, 2, 3, 7, 8.
6. Prior scenario 4 (erasure re-creation): still Follow-up?

---

## Fix verification (prior report)

- Scenario 1 (promote age-window livelock): **fixed.** `promote` and `snapshot_generation` are no longer in `INCREMENTAL_JOBS` (`src/prme/organizer/watermarks.py:36`), so the page-cursor-free age disjunct is unreachable from the organizer. The `created_after` alternate bound is now dead code and the protocol docstring still names `promote` (`src/prme/storage/graph_store.py:289-291`) — cosmetic only.
- Scenario 2 (ceiling-before-write skip): **mitigated, not closed.** A completed run stores `ceiling - 60 s` (`src/prme/organizer/jobs.py:56-61`, `:125`), so a write visible with a stamp up to 60 s below the ceiling is re-examined. See scenario 4 below: the constructor-to-insert gap (`src/prme/storage/engine.py:1330` vs `:1351-1354`) is bounded only by the serialized write queue and is unbounded under backlog.
- Scenario 3 (empty-string scope collision): **fixed.** `scope_key` is `""` only for `None` and `"u:" + user_id` otherwise (`src/prme/organizer/watermarks.py:43-49`), and erasure deletes the prefixed key (`src/prme/storage/erasure.py:691-698`).
- Scenario 7 (absent lower bound): **fixed.** The SQL backends omit the window predicate when no bound is given (`src/prme/storage/duckpgq_graph.py:476-492`, `src/prme/storage/pg/graph_store.py:539-551`) and the admitted view initializes `in_window` from both bounds (`src/prme/privacy/read_graph.py:170-183`). The disclosure-empty page and the whole-scope cost remain as documented limitations (RFC 5.5).
- Scenario 8 (`NULL updated_at`): **half fixed.** The dedup cursor now uses `updated_at or created_at` (`jobs.py:685`, `:698`). But `_row_to_node` passes the NULL straight into the required `MemoryNode.updated_at` (`src/prme/storage/duckpgq_graph.py:2406`; `src/prme/models/base.py:35-38`), so such a row still raises during `scan_changed_nodes` and the cursor fallback is unreachable. Low likelihood; the old failure mode (job errors every run, watermark never advances) stands.
- Scenario 6 (concurrent RMW): unchanged; assessed below (scenario 4) — no skip found.

---

## Ranked scenarios

### 1. Deduplicate's candidate set is not determined by node writes: a re-embedded index makes old, unchanged nodes newly duplicate, and the watermark never revisits them

- Trigger: enable `incremental_enabled`, let the scope's watermark advance, then change the vector index without writing nodes — `prme rebuild` re-embeds every active node afresh with the current provider (`src/prme/storage/engine.py:3919-3999`), `prme reembed` + switching `embedding.model_name` writes new vectors and changes the active model (`engine.py:3842-3852`, "writes only vectors: events, the graph and other models' vectors never change"), and `drop_embeddings` does the inverse. Any of these changes which nodes are near each other; every node's `updated_at` stays put.
- Mechanism: `INCREMENTAL_JOBS`'s premise is "a node that was neither created nor updated since the previous run cannot have become newly eligible" (`src/prme/organizer/watermarks.py:24-36`). For deduplicate that is false: eligibility also depends on the vector index, and the incremental scan selects purely on the node stamp (`src/prme/organizer/jobs.py:636-643`). RFC 5.5 only warns about changing a *tuning setting* (`docs/RFC-0015-Self-Organizing-Memory.md:502-503`) and tells the operator to clear the row; it does not mention a rebuild or an embedding-model switch.
- Likelihood: Medium. `prme rebuild` is the documented recovery path after an embedding-model migration or index corruption.
- Impact: duplicates created by the new neighborhoods are never examined; `deduplicate` reports `completed` with `nodes_processed=0` forever. Contradicts the ticket's convergence claim for any store that rebuilds its index.
- Detection: quiet (status `completed`, nothing processed).
- Cheapest mitigation: clear the job's watermark rows in `rebuild_indexes`/`reembed`/`drop_embeddings` (or fingerprint the active embedding identity into the row and reset on change), and say so in RFC 5.5. Size: XS (delete) to S (fingerprint).
- Recommended verdict: Follow-up. Record it in the PR and the RFC; it does not regress the default path.
- Newly introduced: yes (specific to making deduplicate incremental; the config-change blindness was prior scenario 5, this is the runtime-operation instance).

### 2. One vector-search exception now truncates the scan, and in incremental mode it stalls that scope's deduplication permanently and quietly

- Trigger: `index.search` raises for one node. The search embeds the node's own content (`src/prme/storage/vector_index.py:728-734`), so a content-specific provider rejection or a persistent error (`EmbeddingVersionMismatchError`, a missing/rejecting `embed_query`, an HTTP 4xx for long text) fails on that node every run.
- Mechanism: the fix changed the failure path from `continue` to `break` (`src/prme/organizer/deduplication.py:193-203`). Incremental: `completed` counts only the leading nodes whose search finished, so a node that always raises caps the prefix at its index (`jobs.py:679-687`); when it is first on the page `completed == 0`, so no `_resume` is written (`jobs.py:718-719`) and the previous cursor stands. Every later run re-hits the same node, processes nothing, and returns `budget_exhausted` with `errors == 0` (the exception is swallowed at `deduplication.py:200-202`). Every node after it in that scope is never deduplicated until the poison node changes or is deleted.
- Likelihood: Low-Medium for a per-node persistent failure; the node's content embedded successfully at write time, so a global provider outage recovers.
- Impact: silent permanent stall of the incremental job for that scope; on the *option-off* path the same `break` truncates the rest of the run's candidate scan (`deduplication.py:96-99` ignores `completed`), which is a behavior change from the previous `continue` (`src/prme/organizer/jobs.py:560-566` unchanged caller).
- Detection: quiet — debug-level log, `errors=0`, CLI prints "Stopped on the budget".
- Cheapest mitigation: count a search exception as an error and advance past the node (or bound retries per cursor position), keeping the option-off scan as it was. Size: XS.
- Recommended verdict: Fix now, or at minimum record the option-off regression.
- Newly introduced: yes.

### 3. Erasure deletes the owner's watermark row, but a later per-tenant organize recreates it, and the new verify can turn that into a failing erasure

- Trigger: an external cron drives organize as a loop over tenants, so `organize(user_id=X)` still runs after X's erasure, or overlaps it.
- Mechanism: `_purge_sql_step` deletes `u:<owner>` (`src/prme/storage/erasure.py:691-698`) and `_verify_purge` now requires the row to be absent (`erasure.py:843-853`). No erasure epoch fences the write: `MemoryEngine.set_organizer_watermark` (`src/prme/storage/engine.py:3168-3184`) delegates to an unconditional upsert (`src/prme/storage/duckpgq_graph.py:536-557`, `src/prme/storage/pg/graph_store.py:595-618`), and `organize()` does not drain or consult erasures (`engine.py:4506-4579`). So a completed incremental run for X recreates the plaintext row; if that lands between the SQL stage and the verify stage, `_verify_purge` raises and the erasure is retried as `coverage_unavailable`, so a legitimate purge can fail repeatedly while the cron keeps recreating the row.
- Likelihood: Low-Medium. Requires incremental enabled plus a post-erasure run for that owner.
- Impact: the erased owner's identifier returns to a table the purge no longer revisits, and the "verified purge" invariant is not durable. No memory content is recoverable.
- Detection: quiet for the re-creation; loud for the erasure if it hits the verify window.
- Assessment (still Follow-up?): **yes, still Follow-up.** `privacy_erasure_jobs` already retains `owner_id UUID NOT NULL` with no deletion path (`erasure.py:22`, `:78`), so the re-created row adds a second site of an identifier the system already keeps by design, not a new category of exposure. The functional defect is real, though: the purge's own comment says the row must not survive, and the verify now makes an unrelated cron able to block erasure. Cheap fix if promoted: key the scope by a keyed hash of the user id, or skip the watermark write when the owner's erasure epoch advanced. Size: S.
- Recommended verdict: Follow-up (unchanged), with the verify interaction noted in the PR.
- Newly introduced: yes (the table and both write sites are new).

### 4. The 60 s overlap is a bounded mitigation: a write visible more than 60 s below the ceiling is still skipped, and the watermark upsert is still non-monotonic; concurrency causes rescans, not skips

- Trigger (skip): a write whose node is constructed at `t0` and inserted after the run's ceiling but more than 60 s later — `MemoryNode.updated_at` is set at construction (`src/prme/storage/engine.py:1330`) and the insert waits behind the single write consumer (`engine.py:1351-1354`, `src/prme/storage/write_queue.py:44-57`); a bulk ingest backlog or cross-process clock skew beyond 60 s produces it. Trigger (regression): two concurrent completed runs for one scope.
- Mechanism: the next window's lower bound is `ceiling - 60 s` (`jobs.py:56-61`, `:125`), so a stamp below it is excluded forever. Separately, both the completed and stopped writes overwrite the row unconditionally (`jobs.py:104-126`; `duckpgq_graph.py:551-557`; `pg/graph_store.py:610-616`): a completed run with an earlier ceiling that finishes last moves `completed_through` backwards over a newer value, and a stopped run that finishes last rewrites `completed_through` with the value it read at start and can replace a newer resume cursor.
- Likelihood: Medium for the concurrency overlap (nothing serializes organize), Low for a >60 s visibility gap or >60 s skew.
- Impact: the backwards moves only lower the effective lower bound, so the next run re-scans and re-merges; merges are revalidated inside the transaction (`src/prme/storage/organizer_merge.py:95-99`, reached from `duckpgq_graph.py:579-582` and `pg/graph_store.py:796-799`), and the superseded side is rejected on lifecycle, so no durable write is applied twice in a way that matters. The `>60 s` case is a silent permanent skip of one record, the same class as the prior scenario 2.
- Detection: quiet (redundant work, or one record never deduplicated).
- Cheapest mitigation: make the upsert monotonic (`GREATEST` on `completed_through`, keep the newer cursor) and document the 60 s bound in the CHANGELOG sentence that currently says a write below the ceiling is "re-examined rather than skipped"; a stricter fix derives the lower bound from the minimum pending write stamp or the store clock. Size: XS to S.
- Recommended verdict: Follow-up. The monotonic upsert is cheap enough to do now.
- Newly introduced: partially (the overlap and the table are new; the missing organize lock is pre-existing).

---

## Explicit answers to the four required questions

- Double application: **no durable write applied twice that matters.** The overlap re-proposes only pairs whose retained side is still TENTATIVE/STABLE; `merge_duplicates` rechecks equivalence and lifecycle (`deduplication.py:298-324`) and `_prepare` rechecks both inside the write transaction (`organizer_merge.py:92-99`), so a repeated pair returns `None`/`applied=False` and no second SUPERSEDES edge, evidence copy or journal is published.
- Overlap vs. resume cursor: **safe.** The resume cursor always takes precedence (`jobs.py:86-91`), and any interleaving either re-scans or resumes at a position the stopped run actually processed. Clearing a resume cursor with a completed write is safe because that run covered its whole window from the same or an earlier lower bound.
- Backwards `completed_through`: **possible under concurrency, never a skip.** The upsert is unconditional, so the last writer wins even with an earlier ceiling; a lower lower bound only re-examines work, and the overlap makes that cheaper to tolerate. It is duplicated work plus a stale watermark, not lost maintenance.
- Reduced set: **one new gap.** With only `deduplicate` incremental, the remaining jobs are unchanged and no promotion/snapshot work is lost, but deduplicate's soundness now rests on an index property that node writes do not carry — see scenario 1. Also, `JobResult.status` is only ever set by the incremental path; a full-scope job that returns `errors>0` (e.g. `jobs.py:567-574`) still reports `status="completed"`, so the new CLI line (`src/prme/cli.py:619`) mislabels it.

## Clean checklist results (one line each)

- Page boundary and timestamp ties: correct — exclusive `(stamp, id)` keyset on all three stores; the page-advance and resume use the same `updated_at or created_at` stamp.
- Failure / budget-stop watermark handling: correct — old `completed_through` is kept, only the processed prefix is recorded, and a stop with no processed prefix writes nothing.
- Two concurrent organize calls for the same scope: no skip; duplicated work and a possible backwards watermark only (scenario 4).
- Unscoped vs scoped and empty user id: separate keys; erasure deletes the prefixed key.
- Option-off path: no watermark read/write (`jobs.py:181-183`); the one behavior change is the shared `break` on a vector-search failure (scenario 2).
- Protected store: absent lower bound fixed; disclosure-empty page and whole-scope scan remain documented limitations.
- Store restore/migrate: `COPY FROM DATABASE` carries the watermarks consistently; no stale-cursor path found.

## Pre-mortem headline

Incremental deduplicate silently stops converging the moment its inputs move outside the node table: a re-embedded index or one node whose embedding search always fails leaves new duplicates unexamined and the job reporting `completed` or "Stopped on the budget" with nothing processed, while an erased owner's watermark row quietly returns the next time the tenant loop runs.
