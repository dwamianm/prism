# Agent 8 adversarial review: `summarize` stays full-scope (#523)

Status: COMPLETE

- Base: `main` at `57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7`
- Reviewed snapshot: `65819c30a59078a9412ebb5e09141555fcbce156c7d7c680460ff76a212ee24a`
- Scope read: the five changed files plus `src/prme/organizer/summarization.py`,
  `src/prme/storage/{duckpgq_graph.py,pg/graph_store.py,erasure.py,consolidation_publication.py,organizer_merge.py,write_queue.py}`,
  `src/prme/config.py`, `src/prme/organizer/models.py`,
  `docs/ORGANIZER-JOBS.md`, `benchmarks/diagnostics/organizer_timing.py`,
  `tests/test_summarization.py`.
- Method: read the diff, then the full summarize job and its pipeline, both SQL
  stores' update/delete paths, the erasure purge, summary publication and
  retirement, the timing harness, and the existing summarization tests. No
  source or test file was modified. This review assumes the change shipped and
  asks what breaks; it does not re-own correctness, security or quality items
  outside the assigned adversarial angles.

## Checklist

| Angle | Result | Evidence |
|---|---|---|
| Premise challenge | **Fails as stated.** The narrow "not a watermark over node writes" conclusion holds, but the recorded impossibility proof does not: the summary node itself records the period membership the text says does not exist. See scenario 2. | `src/prme/organizer/summarization.py:356-359`, `:381-397`, `:213-240` |
| Documentation accuracy | **Three defects**: "complete active membership" is false (top-N cap), "no surviving row names it" is false (the old summary names it), and "a full run rebuilds every period" is false below the minimum and under budget truncation. The `_UPDATE_ALLOWED_FIELDS` and hard-delete claims are accurate. | scenarios 1 and 2 |
| Test strength | First test is sound (fixture enables incremental mode, so it exercises the real dispatch). Second test proves only that two full runs change both periods; it does not probe the candidate-set claim, repeats a false comment, and its 6/5 node counts avoid the below-minimum boundary. | scenario 3 |
| Sibling paths / bypasses | **Clean.** The only hard-delete paths are `delete_node` and the erasure purge; merges, archive, decay and consolidation supersede or archive with `updated_at` bumped, so the node window still names their period. No third delete path is missed. | `duckpgq_graph.py:1115-1118`, `pg/graph_store.py:1722-1726`, `erasure.py:672`, `organizer_merge.py:180,221` |
| Existing installs / backward compatibility | **Clean.** Comments and docs only; `INCREMENTAL_JOBS` is still `{"deduplicate"}`, no config, schema, API or dispatch change. | `src/prme/organizer/watermarks.py:39`, `jobs.py:181-186` |
| Detection | The new membership assert is a real tripwire for a future flip, but nothing ties the RFC/docstring to the code; an implementer who edits both the set and the test leaves the RFC silently stale. | `tests/test_organizer_incremental.py:470`, no doc test |

## Scenarios

### 1. "A full run rebuilds every period" is false for a period that falls below its minimum, and the new test is built to miss that case

- **Label:** newly introduced documentation over pre-existing behavior.
- **Trigger.** A day holds exactly `summarization_daily_min_events` (default 5) memories and one of them is moved with `update_node(event_time=...)` or hard-deleted, so 4 remain; or every memory of the day is moved away. Run any full summarize pass (`prme organize --jobs summarize`, or the default `DEFAULT_JOBS` order). The vacated day's old daily summary stays STABLE and still contains the departed source. The same shape holds at the weekly and monthly levels when a level drops below `summarization_weekly_min_summaries` / `summarization_monthly_min_summaries`.
- **Mechanism.** `generate_daily_summaries` iterates only the periods present in the group map (`summarization.py:485`) and skips a period with `len(nodes) < summarization_daily_min_events` (`:493-494`); nothing retires a summary for a period that is skipped or absent (the only retirement is inside `_materialize_summary_node`, which is never called for such a period). Weekly and monthly repeat the pattern (`:570-577`, `:652-659`). The RFC then asserts convergence unconditionally: `docs/RFC-0015-Self-Organizing-Memory.md:521-522`, `src/prme/organizer/jobs.py:974-975`, and `CHANGELOG.md:19`. The persisted state contradicts it: the old daily summary is still STABLE, weekly and monthly rollups built from it are still STABLE, and the moved content appears in both the old day's summary and the new day's.
  The new test hides the case: it stores 6 day-A memories (`tests/test_organizer_incremental.py:486`) so the move leaves exactly 5, the default minimum, and the vacated day is rebuilt. With 5 stored memories the vacated day would fall to 4, the full run would skip it, and the assertion at `:514` would fail.
- **Likelihood: High.** The default threshold is 5 and date correction, rollback cleanup and re-dating all cross it. The test's own docstring ("A source that leaves a day changes that day", `:483`) is false at the boundary it avoids.
- **Impact: Medium-High.** A stale daily summary keeps asserting the old day's membership indefinitely (until the period again reaches the minimum, or the summary TTL — default 365 days — archives it), and the rollups derived from it do too. An operator who follows the RFC's "a full run converges" advice to repair a date correction gets `status: completed`, `nodes_modified: 0`, and no repair.
- **Detection: silent.** No warning, no error count; the skipped period is simply not visited.
- **Cheapest mitigation.** Correct the sentence to scope the guarantee ("every period the run reaches that still meets its minimum; a period that falls below it keeps its last summary"), and add the exact-minimum case to the test (store exactly `summarization_daily_min_events`, move one, assert the vacated day's summary no longer lists it). Retiring summaries for periods that no longer qualify is a separate, bounded code follow-up.
- **Recommended verdict: Fix now** (the false guarantee is the deliverable's central claim; the doc change and the extra test are ticket-sized).

### 2. The recorded "proof" is false: the vacated period *is* named by a surviving row, and top-N selection is not "complete membership"

- **Label:** newly introduced.
- **Trigger.** Anyone who revisits incremental summarize takes the RFC as proof that the information does not exist and builds period-transition history and delete tombstones first, or rejects a cheap design; or an implementer trusts "complete active membership" and assumes a memory outside the top 10 affects its period's summary.
- **Mechanism.** Two concrete false statements:
  1. "complete active membership" (`RFC:513`, `CHANGELOG.md:19`, `jobs.py:960`) is false. Only the top `summarization_max_items_per_summary` (default 10, `config.py:736-739`) sources are passed to the publisher (`summarization.py:497,580,662`), and periods below the minimum get no summary at all. The summary node's own header says "selected source excerpts, not exhaustive" (`:137`).
  2. "no surviving row names it" (`jobs.py:970`) is false. The vacated period's summary node survives as STABLE and records `period_key`, `source_node_ids` and `selected_source_node_ids` (`summarization.py:356-359`), plus `DERIVED_FROM` edges to each source (`:381-397`) and each source id in `evidence_refs` (`:292-294`). An event-time move always bumps the moved row's `updated_at` (`duckpgq_graph.py:1217-1221`, `pg/graph_store.py:728-732`), so the node window does name the changed node; a read of summaries whose recorded ids include it names the vacated period. When the moved node was *not* among the recorded top-N, the recomputed top-N and request hash are unchanged, so the full run republishes nothing and there is nothing to catch. The same membership read covers a deleted recorded source by checking existence; a tombstone is one way to learn about the delete, not a prerequisite. So the stated preconditions ("period-transition history and delete tombstones first", `RFC:525-526`) are not established; the real question is the cost of a summary-membership read, which the note never weighs. The hierarchy argument is also overstated: weekly and monthly summaries record their sources too, and three per-level cursors (or a per-level membership check) are the obvious alternative to "one per-scope cursor would have to page three dependent levels" (`RFC:523-525`).
  What *is* sound is the narrow claim that summarize cannot run under #517's single node-change watermark: the vacated period is not derivable from the node row alone. The decision to keep it full-scope may still be right on cost grounds; the written impossibility proof is not.
- **Likelihood: Medium** over the life of a permanent design record. A future issue or engineer will read this paragraph and either do unnecessary schema work or accept a wrong constraint.
- **Impact: Delivery.** The decision record is the ticket's only artifact; a false proof is the artifact's defect. It also hides a smaller cost lever: a membership read over SUMMARY nodes rather than the whole node corpus.
- **Detection: none automated.** Only a reader who checks `summarization.py` against the paragraph.
- **Cheapest mitigation.** Rewrite the paragraph and the docstring to state what is actually true: selection is top-N; the summary node records the sources it used; the vacated period is recoverable only by reading those recorded ids (a scan/join over summaries), not from the node window; deletes need either a tombstone or a recorded-id existence check; the decision rests on that cost plus the hierarchy, not on nonexistence. Record the recorded-membership route as the alternative that was weighed.
- **Recommended verdict: Fix now.** Reassessing the decision itself is a follow-up.

### 3. The event-time test never exercises the incremental property and is coupled to default thresholds

- **Label:** newly introduced.
- **Trigger.** A reader treats `test_event_time_move_changes_both_periods` as evidence for the class docstring's claim that "a change window cannot name a period a node has left".
- **Mechanism.** The test performs two full runs (`tests/test_organizer_incremental.py:502,510`) and asserts the two full-run summaries differ. A full run rebuilds from current membership by construction, so the test would pass even if no watermark existed at all; it would also pass if `update_node` did not bump `updated_at`, which is the mechanism that makes the moved node visible to any future window. It never asserts the stored membership metadata or the `updated_at` bump, so it cannot distinguish "the window cannot name the vacated period" from "the window can name the changed node, and the old summary names the period". Its comment "The write records only the new event time" (`:504-505`) repeats the false claim; `update_node` also writes `updated_at` (`duckpgq_graph.py:1217-1221`, `pg/graph_store.py:728-732`).
- **Coupled to defaults.** The engine fixture sets only `incremental_enabled` and `opportunistic_enabled` (`tests/test_organizer_incremental.py:48-49`), so the test depends on `summarization_daily_min_events=5` and `summarization_max_items_per_summary=10` (`src/prme/config.py:724-739`). Raising the minimum above 5 makes day B (5 memories) produce no summary and `_active_daily_summary` (asserts exactly one, `:636-651`) fail; lowering the maximum below 6 drops the moved memory from the first run's top-N and breaks the `after_b` assertion. This project flips defaults routinely, so the test's meaning is not pinned.
- **Likelihood: Medium** (the assertion is indirect, and a default flip breaks it later).
- **Impact: Low-Medium** (no production effect; it weakens the evidence attached to a permanent decision).
- **Detection:** the test fails when a default changes, but for the wrong reason.
- **Cheapest mitigation.** Set the two summarization thresholds explicitly for this class; add an exact-minimum case; assert that the vacated period's old summary records the moved id (proving the recorded-membership route) and that `updated_at` moved.
- **Recommended verdict: Follow-up.**

### 4. The "full run" escape hatch is budget-bounded, and the timing evidence covers one day

- **Label:** newly introduced documentation over pre-existing behavior.
- **Trigger.** A store with a few hundred periods (for example a year of daily memories: 365 daily, 52 weekly, 12 monthly). Run `prme organize --jobs summarize` with the default 5,000 ms budget (`src/prme/storage/engine.py:4511`). The daily pass gets 50% (`jobs.py:995`).
- **Mechanism.** The daily pass sorts periods ascending by (owner, scope, period) and breaks when its share is spent (`summarization.py:485,489-490`); weekly and monthly repeat with 30% and 20% shares (`:570,573`, `:652,655`). Every re-run restarts at the same oldest periods, and each period costs a full `_hierarchical_lineage_summaries` scan of every SUMMARY node in the scope (`:213-240`, called at `:322-329`) even when the period is unchanged, so the per-run cost is O(periods x summaries). A run that truncates therefore leaves the newest periods (where a fresh correction lands) unrepaired, and the next run does not resume past the cut. `_job_summarize` never sets `status` or `stopped_on_budget` (`jobs.py:1032-1040`; default in `models.py:31`), so truncation reports `completed`. The timing table (`docs/ORGANIZER-JOBS.md:70,80,90`) was measured with `event_time=None` for nearly every node (`benchmarks/diagnostics/organizer_timing.py:46-66`, plus one 400-day-old batch), i.e. one or two day groups, so it does not bound multi-period cost.
- **Likelihood: Medium** for stores older than a few months at the default budget; the RFC sentence invites the operator to rely on the full run precisely after a correction.
- **Impact: Medium.** The documented repair silently does not reach the affected periods; status and `nodes_modified` report success.
- **Detection: silent.** No budget flag and no per-period skip detail in `details`.
- **Cheapest mitigation.** Scope the RFC sentence to "the periods the run reaches within its budget", and record full-run truncation in the job result (`stopped_on_budget` or a skipped-period detail). A resume cursor for the full job is the bounded code follow-up.
- **Recommended verdict: Follow-up** (doc sentence now; behavior later).

## Detection of future drift

If a later change adds `summarize` to `INCREMENTAL_JOBS`, `tests/test_organizer_incremental.py:470` fails before any behavior ships (the fixture has incremental mode on, so the `details["incremental"]` and empty-watermark assertions exercise the real dispatch). Nothing enforces the RFC or the `_job_summarize` docstring: an implementer who changes the set and the test in one PR leaves the decision record contradicting the code with no automated signal. That is acceptable for a design note, but it is the only tripwire.

## Angles checked and found clean

- **Hard-delete enumeration.** `delete_node` (`duckpgq_graph.py:1087-1118`, `pg/graph_store.py:1722-1726`) and the erasure purge `DELETE FROM nodes` (`erasure.py:672`) are the only node deletes. Merges supersede and bump `updated_at` (`organizer_merge.py:180,221`); archive, decay, tombstone and consolidate change lifecycle state through the same update path. No third delete path changes the conclusion, and the erasure path also drops the owner's watermark rows (`erasure.py:691-699`), so a future incremental job would re-scan that scope.
- **Allowlist claim.** `event_time` is in `_UPDATE_ALLOWED_FIELDS` on both backends (`duckpgq_graph.py:1179-1195`, `pg/graph_store.py:673-690`), and both update the field and `updated_at`. The RFC/docstring statement is accurate, only "writes only the new event time" is loose.
- **Backward compatibility.** No install sees changed behavior, config, schema or contract from this diff; the only cost is one more test that loads the shared FastEmbed provider, the same dependency the module already has.

## Unresolved risks

- The issue's "Done when" is not readable from the repository (no external calls in this review). If it required a proof of the candidate-set question, scenario 2 shows the proof is not established; if it accepted a written decision, the decision may stand once its reasoning is corrected. The CHANGELOG frames it as a decision record, which is consistent with the latter.
- Multi-period full-run cost is unmeasured; scenario 4 estimates it from the per-period lineage scan, not from a run on a realistic store.

Pre-mortem headline: `summarize` was documented as permanently full-scope on a proof that "no row names the vacated period", when every summary node does; a year later a corrected date left a STABLE daily summary pointing at the old day, and the documented full-run repair reported success without touching it.
