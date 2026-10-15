# Review: issue #247, remaining criteria after PR #248 (branch `issue-247-store-a-local-memory-pack-as-one-file`)

## Review Scope

- Base: `main` at `41e1cfeabf930c7aaec30ddeba5c5bf20699a35f` (the merge of PR #248).
- Reviewed snapshots (`housekeeping.py review-plan --base main --risk DATA`): first pass `7c5a1ade4b82d37773f19300a33ff4bc9113dd1971428d4064df5c9be3e7ae9f`; after the first fixes `795b2ec01cd9bb84b89f86ce2fe075458fca5f4b9491fd219095535d88a909b7` (delta review by the core and adversarial reviewers); `645538974540d363699d34c2211f9d3bacfca27833542965dbe19b13c1484148` (second delta by the same two reviewers); final `970c3fa3f7e9e41334ca038009a23134be279fee44f1e2847c52733453ed2069` (the `settle()` fix for E1, confirmed by the adversarial reviewer).
- Tier L. Predicates: NEW_SYMBOLS true; DATA raised by hand because the new path deletes index rows (Tantivy documents, USearch keys, `vector_metadata`, `vector_payloads`, `vector_staging`); INFRA, SECURITY and PERSISTED_FIELD false.
- Roster: core (Opus; checklists 1 correctness, 3 quality, 4 patterns, 5 redundancy merged into one report), security (Opus; checklist 2), data safety (Sonnet; checklist 7), adversarial (Opus; checklist 8, cap 8 scenarios). Checklist 6 (infrastructure) not applicable: no deployment, environment or registration changes. Merged checklists are covered, not skipped; their findings are attributed below.
- Agent reports (scratchpad of this session): `review-agent1-core.md`, `review-agent2-security.md`, `review-agent7-data.md`, `review-agent8-adversarial.md`, each ending `Status: COMPLETE`.

## Files Changed

- `src/prme/storage/index_eviction.py` (new): `delete_from_indexes` and `evict_from_indexes`, the one eviction helper.
- `src/prme/storage/engine.py`: `_delete_from_indexes` delegates to the helper.
- `src/prme/ingestion/pipeline.py`: `_publish_plan` evicts every node a committed plan replaced.
- `src/prme/storage/lexical_index.py`: an in-memory index drops Tantivy's deleted-document markers before its next search (`_stale_markers`, `_marker_remains`, `_purge_locked`, `_flush_locked`).
- `benchmarks/integrations/gpt54_baselines.py`: `prepare --lme-captures`.
- Tests: `tests/test_single_file_pack.py`, `tests/test_claim_merge.py`, `tests/test_cli_single_file.py`, `tests/test_gpt54_baselines.py`.
- Docs: `docs/RFC-0014-Portability-Sync-and-Federation.md`, `docs/INTEGRATION.md`, `BENCHMARKS.md`, `CLAUDE.md`, `memory_bank/LME-CONTROL-PACKS-2026-10-14.md`.

## Approach Summary

PR #248 left two engineering items for #247. First, `ingest()` committed derivation plans whose replacements retired earlier nodes (the earlier copies of a repeated claim, or a detected correction) without removing those nodes' lexical documents and vectors; only the cooldown-gated `index_compaction` job, `prme rebuild` or the open of a single-file pack did, and lexical search has no lifecycle filter, so BM25 results over a pack built with `ingest()` could change across a restart. The pipeline now evicts every replacement target after the commit through the same helper the engine's `supersede()`, `archive()` and `resolve_contradiction()` use. Review then showed, and execution confirmed, that Tantivy keeps a deleted document in its segment's BM25 statistics until a merge, so an in-memory index now rebuilds itself from its live documents before the next search after a deletion left such a marker; a single-file session, a reopen and a rebuild give the same documents, scores and rankings. Directory packs keep their markers on disk, as before. Second, the DeepSeek track's `prepare` gains `--lme-captures`, so LongMemEval-S contexts can be prepared from the regenerated controls on a machine without the 2026-09-22 captures. A test for migrating an encrypted directory pack with its key closes the last untested acceptance criterion.

## Must Fix

1. (core, data) `tests/test_claim_merge.py`: the new test asserted the retired copy is absent from lexical search on every backend, but PostgreSQL's lexical search reads the `nodes` table without a lifecycle filter, so CI's PostgreSQL job would fail. Resolved: the assertion is guarded with `engine._conn is not None`; the vector assertions hold on both backends.

## Should Fix

1. (core 1, adversarial S1) The claim that a reopen or rebuild scores like the session rested on tests where each retired document was alone in its Tantivy segment. Confirmed by two experiments: with shared segments, `doc_freq` kept counting the deleted document and BM25 scores differed (session 1.007 against fresh 1.310 for one query); on the real path, a reopened single-file pack that evicted a merged claim scored differently from the next open. Resolved: the in-memory index purges deleted-document markers before the next search; `test_an_eviction_from_a_reopened_pack_keeps_the_scores_of_the_next_open` and `test_an_in_memory_index_drops_deleted_document_markers_before_the_next_search` pin it; docs reworded.
2. (core 2, data Consider 2, adversarial S4) The replay-path comment claimed a stop between commit and eviction is repaired by the retry; a managed extraction's completed work row ends its retry before that step. Resolved: comment reworded to name `index_compaction` and `prme rebuild` as the repair; the receipt branch still evicts for unmanaged callers.
3. (core 3) No test covered a failed eviction or the replay path. Resolved: `test_a_failed_eviction_leaves_the_retired_copy_for_compaction` and `test_publishing_a_committed_plan_again_evicts_what_it_replaced`.
4. (data 2) Docs overstated: eviction is best effort and packs built before this change keep stale entries. Resolved in RFC-0014, INTEGRATION.md and CLAUDE.md.
5. (core delta 1, adversarial D1) Tantivy's writer indexed on several threads, so which segment a document landed in varied between runs; tied BM25 hits come back in segment order, so two loads of the same 20,000 rows returned different top-50 sets, and the flag assertion in the reopened-pack test could fail by chance (it did, twice, in the second full-suite run). Resolved: every writer the module creates passes `num_threads=1`; `test_two_loads_of_the_same_documents_rank_ties_alike` pins id order; the docs describe tie order precisely. This also closes a pre-existing determinism gap of single-file opens.
6. (core delta 2) An idle batch writer could outlive the index swap and send later documents to the discarded index. Resolved: `_purge_locked` releases any remaining writer before the swap, and `_do_index` builds the document before it creates the writer.
7. (core delta 3, adversarial D3) The purge ran on every flush, so `close()` and the write path's per-note flush rebuilt the index too. Resolved: `flush()` commits only; the purge runs in `search()`. The remaining per-search cost after an eviction is measured (12 ms at 1,413 documents, 0.88 s at 100,000), stated in the docs and tracked as #252.
8. (core delta Consider 1, adversarial D2) A failed purge left the flag set, so every later search retried and raised, and retrieval would silently lose its lexical channel. Resolved: the purge logs `lexical.purge_failed`, clears the flag and keeps the old index; `test_a_failed_rebuild_keeps_the_old_index_and_warns`.
9. (adversarial E1) `prme rebuild` adds documents in commits of 64, so even on one thread it leaves several segments whose order Tantivy's merges decide, and two rebuilds of 100,000 documents cut a different top-500 set for a tied query; the docs promised a rebuild orders ties like an open. Resolved: `LexicalIndex.settle()` rebuilds an in-memory index into one id-ordered segment and `rebuild_indexes` calls it after its flush, so a rebuilt single-file pack reads like a reopened one (the reopened-pack test asserts one segment after a rebuild); RFC-0014 and INTEGRATION.md describe the remaining case, a session that only added documents, and directory rebuilds.

## Consider

- (core 1, data 3, adversarial S5) Per-replacement cost: two write-queue operations and a Tantivy commit per replaced node, and on a directory pack a full USearch save. Follow-up #250.
- (core 2, security 3) `MemoryEngine._evict_from_indexes` mirrors the helper. Kept, with a docstring saying why: tests inject failures by replacing `_delete_from_indexes`.
- (core 3) `--lme-captures` is accepted on either provider. Kept and documented: a plain arm on the GPT-5.4 track replays the same controls, and `prepare` makes no paid call.
- (core 4) Module docstring of `gpt54_baselines.py` and `_publish_plan` docstring updated.
- (core 5, data 1) Dates in docs named a day that had not come; removed in favor of the issue number.
- (core 6) `RetrievalMode.EXPLICIT` no longer finds `ingest()`-superseded nodes through BM25 before compaction; consistent with `supersede()` and rebuild; noted in the PR.
- (core 7) Duplicated extraction stubs replaced by the `scripted(claims)` factory.
- (data 4, adversarial S3) The eviction module docstring listed "every transition"; it now lists the paths that evict. The two paths that do not are follow-up #249.
- (security 1) The encrypted migration test now also asserts nothing but the new pack is left beside the source.
- (security 2) `benchmarks/diagnostics/product_packing.py` joins a manifest's `source.root` without a containment check. Pre-existing, read-only, operator-supplied input; accepted and recorded here, not fixed in this change.
- (core delta Consider 2) `benchmarks/diagnostics/hybrid_lexical.py` swaps `_index` for a proxy and restores it; it runs on a directory index, which never purges. Accepted.
- (core delta Consider 3) `search()` reads the marker flag without the lock, so a deletion that lands between the check and the scoring is scored once with its marker; the "session equals reopen" statement holds for quiescent searches. Accepted and inherent to the lock-free read, as for the existing uncommitted-count check.
- (core delta Consider 4) The eviction docstring now lists the summarization job.
- (core delta 2) Tie order across segments: once documents span several Tantivy segments, tied hits can come back in a different order per build even on one thread. A single-threaded load or purge makes one segment up to at least 100,000 twenty-word documents (measured: one segment, identical top-500 across two loads), and `prme rebuild` commits in batches and so makes several. INTEGRATION.md item 2 now says so; the second delta found no new Must Fix or Should Fix.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Tenant isolation of eviction | PASS | Replacement targets carry the plan's owner and scope (`models/derivation.py`), and the commit checks every reference's checksum against the stored node before it supersedes anything; eviction runs only after that commit, or after a matching receipt. |
| Secrets or PII in logs | PASS | The helper logs the node id and the traceback, as the engine did. |
| `--lme-captures` path handling | PASS | Read-only; manifest question ids must equal the hash-checked dataset's; packs are cloned to a temporary directory and verified before any engine opens them; writes stay under the arm folder. |
| Deserialization and injection | PASS | Pydantic models and `json.loads`; every delete is parameterized or a Tantivy term on a raw-tokenized field. |
| Encrypted migration residue | PASS | Temporary-file encryption is on before the copy; the test asserts the source layout and the destination folder hold nothing extra. |
| Test credentials | PASS | The fixed passphrase is test-only and pre-existing. |

## Pattern Consistency Assessment

The helper matches the engine's eviction semantics (lexical first, vector metadata as the retry anchor, best effort, stdlib logger). The pipeline evicts after the commit through the write queue, as the engine's lifecycle methods do. The CLI option is threaded through `prepare`, both replays, `_REPLAY_INPUTS`, the refusal that names differing inputs, `_check_args` and the help text, and documented in `BENCHMARKS.md`, like `--packs` and `--archive`. Tests reuse the files' helpers (`layout_config`, `lexical_state`, `ranked_ids`, `extractor`, `say`). The in-memory purge follows `_do_clear`'s pattern of a short-lived writer and a `Manual` reload policy.

## Redundancy Check

`delete_from_indexes` replaces the body of `engine._delete_from_indexes`; no other equivalent exists (the write-queue rollback keeps its own, a different contract). `evict_from_indexes` has one near-twin, the engine wrapper kept for test injection. No duplicate constants; the test stubs share one factory.

## Wiring Findings

The new module is imported by the engine and the pipeline and used at both call sites. No environment or configuration reads were added. The CLI option reaches `prepare` through `main`, and `prepared.json` records `provenance.lme_captures` through the gate report. Checklist 6 otherwise not applicable.

## Data Safety and Read/Write Proof

No schema, table, column, field or file format changed, so the read/write proof for new persisted fields is not applicable. The eviction deletes derived rows only (Tantivy documents; USearch keys with `vector_metadata`, `vector_payloads` and `vector_staging` rows; on PostgreSQL the `lexical_documents` row and the `embedding` column), and only for nodes the same committed plan marked superseded, which lifecycle transitions never reactivate (`types.py`, forward-only). Rebuild re-embeds active nodes only, so no reader needs a superseded node's payload; `engine.supersede()` already deleted the same rows. A second eviction is a no-op in both delete paths; the lexical-first order keeps the anchor `index_compaction` scans. Packs written before this change keep stale entries until compaction or a rebuild, which the docs now say. The in-memory purge reads and writes nothing durable.

## Break Scenarios (adversarial)

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| S1 | A retired document that shares a Tantivy segment keeps counting in BM25 statistics, so a reopen or rebuild scores differently from the session, contrary to the docs | newly introduced claim over a pre-existing mechanism | High | Medium | Fix now | Confirmed by execution; the in-memory index now purges markers before the next search, tests pin it, docs state the directory caveat. |
| S2 | A pack built after this change differs from the saved `ingest()` baselines (8 of 1,116 and 78 of 27,053 indexed nodes there are retired copies), and `_check_defaults_build` cannot see an `ingest()` code change | newly introduced behavior, pre-existing blind spot | Medium | Low | Follow-up | Needs a contract change to the build check; filed as #251. The store() gate shows no change by construction and by measurement. |
| S3 | `decay_sweep`'s deprecate and resumed profile publication do not evict, while the docstring claimed every transition does | pre-existing gaps | Low | Low to Medium | Follow-up | Docstring fixed now; the code change is #249. |
| S4 | A stop between the commit and the eviction leaves entries that the retry never removes, while the comment said it would | newly introduced comment, pre-existing class | Low | Low | Accept | Comment corrected; `index_compaction` and `prme rebuild` repair it, and a single-file pack's open drops the lexical entries. |
| S5 | A directory pack saves its whole USearch file per eviction, now on the `ingest()` hot path | pre-existing mechanism, newly on this path | Low to Medium | Low | Follow-up | Latency only; the fix is a debounce with the existing startup recovery; filed as #250. |
| D1 | Two builds of the same documents cut tied BM25 hits at `lexical_k` differently, because the multithreaded writer split them into segments by scheduling | pre-existing mechanism, newly relied on | Medium | Low to Medium | Fix now | Every writer now runs on one thread; a test compares the unsorted top-50 of two loads. |
| D2 | A failed purge left the flag stuck, so every later search retried the rebuild and raised, and retrieval silently lost its lexical channel | newly introduced | Low | Medium while it lasts | Fix now | The purge logs once, clears the flag and keeps the old index; tested. |
| D3 | Every eviction-then-search cycle rebuilds the whole in-memory index under the shared write lock (0.64 s to 0.88 s at 100,000 documents) | newly introduced | Low to Medium | Low to Medium | Follow-up | Latency only; the purge no longer runs on flush or close; the cost is stated in the docs and the PR and tracked as #252, where the threshold or background options need a product decision. |
| E1 | `prme rebuild` leaves several segments, so its tie order at `lexical_k` differs from an open's even on one thread, against what the docs promised | newly introduced claim over a pre-existing mechanism | Medium at scale | Low | Fix now | `rebuild_indexes` now settles an in-memory index into one id-ordered segment; the docs name the remaining case (a session that only added documents, and directory rebuilds). |
| E2 | A failed purge shows only as one warning while the old index serves on with drifted statistics | newly introduced | Low | Low | Accept | That is the behavior before this change, and the warning names it; recorded in #252. |

Pre-mortem headlines, verbatim. First pass: "Single-file pack retrieval receipts change after reopen even though #247 says ingest() eviction made BM25 restart-stable: Tantivy keeps deleted claims in segment statistics until a merge." Addressed by the in-memory purge. First delta: "Single-file pack receipts still differ between two opens of the same `.prme` file: Tantivy's multithreaded writer cuts tied BM25 hits at lexical_k differently on every rebuild, the purge included." Addressed by the one-thread writer. Second delta: "`prme rebuild` and a reopen of the same pack still cut tied BM25 hits at lexical_k differently: background merges reorder segments, so only a single-batch load gives a reproducible tie order." Addressed by settling the in-memory index after a rebuild; the reviewer rated it a minor difference in receipts, not an outage.

Attacked hardest and held: `--lme-captures` cannot let a pair or verdict through that the rules forbid (both replays must share the controls, context text decides identity, provenance names the controls); the replay path cannot evict a live node (forward-only lifecycle, checksum-matched receipt, idempotent deletes); no reader of superseded nodes depends on index entries.

## Follow-ups Raised

- #249: Deprecation and resumed profile publication leave retired nodes in the search indexes (S3).
- #250: A directory pack rewrites its whole USearch snapshot on every eviction, now on the ingest() path (S5, core Consider 1, data Consider 3).
- #251: The ingest() track's defaults-build check cannot see a change to ingest() itself (S2).
- #252: An in-memory lexical index rebuilds itself before the first search after an eviction, 0.88 s at 100,000 documents (D3, core delta 3).

## Resolution Status

| Finding | Status |
|---|---|
| Must Fix 1 (PostgreSQL branch of the claim-merge test) | Resolved |
| Should Fix 1 (BM25 statistics after a shared-segment delete) | Resolved by the in-memory purge, tests and docs |
| Should Fix 2 (replay comment) | Resolved |
| Should Fix 3 (failure and replay tests) | Resolved |
| Should Fix 4 (docs overstated) | Resolved |
| Should Fix 5 (one writer thread, tie order) | Resolved |
| Should Fix 6 (writer across the swap) | Resolved |
| Should Fix 7 (purge on every flush) | Resolved; residual cost tracked as #252 |
| Should Fix 8 (failed purge) | Resolved |
| Consider items | Resolved as listed, or accepted and recorded above |
| S1 | Fix now, resolved |
| S2 | Follow-up #251 |
| S3 | Follow-up #249 (docstring fixed now) |
| S4 | Accept, comment corrected |
| S5 | Follow-up #250 |
| D1 | Fix now, resolved |
| D2 | Fix now, resolved |
| D3 | Follow-up #252 |
| E1 | Fix now, resolved |
| E2 | Accept |

## Checks

- `uv run ruff check src/ tests/ benchmarks/integrations/gpt54_baselines.py`: clean.
- Full suite on the first snapshot: 5068 passed, 984 skipped, 1 deselected (the MCP test that segfaults intermittently on `main`, deselected as in PR #248). On the second snapshot: 5071 passed, 2 failed, both the flag assertions that D1 predicted could fail by chance under the multithreaded writer. On the one-thread snapshot and again on the committed tree: 5075 passed, 984 skipped, 1 deselected, no failures.
- Offline evidence gate over the saved store() packs, `main` against this branch: `context_sha256` identical for all 1,540 LoCoMo and all 500 LongMemEval-S questions (the latter replayed from the regenerated controls with `--lme-captures`); `gate-compare` reports no change on any metric.
- No paid API or Ollama call was made.
