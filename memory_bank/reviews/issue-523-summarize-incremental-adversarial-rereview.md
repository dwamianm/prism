# Agent 8 adversarial rereview: `summarize` stays full-scope (#523)

Status: COMPLETE

- Base: `main` at `57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7`
- Reviewed snapshot: `1abda232806d23170c6def5776607aa54c463716b349093bc9083c965e5f5742`
- Previous snapshot: `65819c30a59078a9412ebb5e09141555fcbce156c7d7c680460ff76a212ee24a`
- Round scope: the delta since my first report (`CHANGELOG.md`,
  `docs/RFC-0015-Self-Organizing-Memory.md`, `src/prme/organizer/jobs.py`,
  `src/prme/organizer/watermarks.py`, `tests/test_organizer_incremental.py`).
- Method: read the delta, then the summarize pipeline
  (`src/prme/organizer/summarization.py`), `_job_summarize` and the organize
  dispatch, `JobResult`, both stores' `update_node`/`delete_node`, the erasure
  purge, the consolidation publication journal, the privacy projection of
  generated nodes, the organizer config defaults, and the surrounding docs. No
  source or test file was modified. Tests are the worker's evidence (the two
  summarize tests passed; the full suite was still running); I did not run them.

## Assigned checklist

| Angle | Result |
|---|---|
| Scenario 1: full-run convergence below the minimum | **Accurate, one unstated consequence.** The limit is now stated correctly; the text does not say the stale day's excerpt also persists into every week/month summary that already embedded it. See scenario 1. |
| Scenario 2: vacated period named by the summary node | **Membership claims accurate; two overclaims remain.** The false impossibility proof is replaced correctly, but "only from the summary" is false (the publication journal records the same membership) and "three dependent per-level cursors" overstates the alternative. The decision still stands as a scope/cost deferral. See scenario 2. |
| Scenario 3: test strength | **Fixed and non-tautological.** Thresholds pinned, membership recorded, `updated_at` bumped, and the id moves between the two day summaries; every assertion is load-bearing. See scenario 3. |
| Scenario 4: budget truncation | **Accurate.** The budget is checked per period and the job always returns the default `completed`; only the no-resume consequence is unstated. See scenario 4. |
| New loophole: too-weak / too-strong claim | Scenario 2 (two overclaims), scenario 1 (one unstated consequence). |
| New loophole: tautological test assertion | **None found.** Each new assertion fails if the mechanism it names breaks. |
| New loophole: doc-to-doc contradiction | **None found.** `jobs.py:958-987`, `RFC-0015:511-533`, `watermarks.py:35-40`, the test class docstring and `CHANGELOG.md:19` agree on the decision and the membership route; "section 5.5" is the right section (`RFC-0015:469`). |
| New loophole: missed sibling path | **Clean.** Only two hard-delete paths and no other `event_time` writer; the membership metadata exists in every summary format and survives the privacy projection. |
| Backward compatibility | **Clean.** Docstring, comment and docs only; `INCREMENTAL_JOBS` is still `{"deduplicate"}` (`watermarks.py:41`); no config, schema, API or dispatch change. |

## Scenarios

### 1. The below-minimum limit is now stated correctly, but the stale excerpt also propagates into the week and month summaries, and that is unstated

- **Label:** newly introduced wording over pre-existing behavior.
- **Trigger.** A day holds exactly `summarization_daily_min_events` (default 5) and one memory moves away or is deleted, so 4 remain; or every memory of the day moves. The operator follows the RFC's full-run repair advice and checks the day, the week and the month.
- **Mechanism.** The daily loop skips a period below the minimum (`src/prme/organizer/summarization.py:485-494`), and the only retirement lives inside `_materialize_summary_node`, which is never called for a skipped period (`:322-329`, `:331-334`). The new text says this correctly (`RFC-0015:529-530`, `jobs.py:983-984`). What it does not say: the stale day summary is therefore unchanged, so the week's request hash is unchanged and `roll_up_weekly` reuses the existing week summary rather than republishing it (`summarization.py:309-334`, `:570-590`); the week's content embeds the day summary's full content (`:127-147`), so the moved memory's text still appears in the week summary and, through it, the month summary. "A full run re-examines every period it reaches, so it reflects a move ... where the period still meets its minimum" (`RFC-0015:527-529`, `jobs.py:981-983`) is true for the vacated day, but a reader can take "it reflects the move" as covering the hierarchy the run also reaches. The test still avoids this boundary: day A has 6 memories before the move and 5 after, exactly the minimum (`tests/test_organizer_incremental.py:493-506`), so the vacated day is rebuilt and nothing pins the below-minimum path.
- **Likelihood: High.** The default minimum is 5 and date corrections, rollback cleanup and re-dating cross it routinely.
- **Impact: Medium.** An operator who inspects the week or month summary still sees the departed memory, and nothing reports that the day was skipped; the week is reused, so `nodes_modified` gives no signal either.
- **Detection: silent.** No warning, no skipped-period detail, no test.
- **Cheapest mitigation.** Add one clause after the minimum sentence: "a below-minimum period keeps its summary, and the week and month summaries built from it keep its excerpt until the period qualifies again." Optionally add the exact-boundary case to the test (store exactly the minimum, move one out, assert the vacated day's summary is unchanged and the week still lists the excerpt).
- **Recommended verdict: Follow-up** (the central claim is now accurate; the clause is ticket-sized).
- **Label:** newly introduced wording over pre-existing behavior.

### 2. The rewritten rationale is accurate about membership, but it still prices the alternative with a false necessity ("three dependent per-level cursors") and a false exclusivity ("only from the summary")

- **Label:** newly introduced.
- **Trigger.** A future engineer treats the paragraph as a design constraint: builds three per-level cursors before considering a cascade, or quotes "only from the summary" and misses the publication journal, or reads the original issue as proven impossible and never revisits it.
- **Mechanism.** Two statements that the code does not support:
  1. **"An incremental form would need a summary-membership read and three dependent per-level cursors"** (`jobs.py:985-987`, `RFC-0015:531-533`). No per-level cursors are needed. The node window names the changed node; one scan of SUMMARY nodes' recorded `source_node_ids` names the vacated period (`summarization.py:354-359`, `:381-397`, preserved by the privacy projection at `privacy/journals.py:90-94`, `:120-142`, `:161-168`); the affected day, then its week, then its month can be rebuilt in the same run, because #517 advances the watermark only after the handler returns (`jobs.py:101-133`), so an interrupted cascade is simply redone from the same window. The alternative is also cheaper than the text implies: a full run is O(periods × every SUMMARY node) because each processed period calls `_hierarchical_lineage_summaries`, which pages every SUMMARY node in the scope (`summarization.py:213-240`, called at `:322-329`), so the membership route wins on a sparse change set, which is the common case. That is the cost argument the record should make if it wants one; the assertion of cursors reads as a technical constraint that does not exist. The decision to defer is still a defensible scope choice; the sentence is not.
  2. **"The vacated period is recoverable only from the summary that recorded its sources"** (`CHANGELOG.md:19`, `watermarks.py:39`, and "Only the summary ... still names the old period" in the test class docstring, `tests/test_organizer_incremental.py:473`). A second durable copy exists: every publication keeps a `CONSOLIDATION_PREPARED` and a `CONSOLIDATION_PUBLISHED` row whose payload is the full publication (`_payload`, `src/prme/storage/consolidation_publication.py:55-58`, inserted at `:371-379` and `:716-722`; PostgreSQL at `:427-434`, `:858-865`), including the source snapshots and the summary node metadata (`period_key`, `source_node_ids`). No `DELETE FROM operations` exists anywhere in `src/`, so the journal retains it. The RFC and `jobs.py` wording ("recoverable by reading the summary") is accurate; only the "only" variants overclaim.
  What is accurate and should be kept: the top-N selection (`summarization.py:497,580,662`), the recorded membership metadata (`:354-359`), the `DERIVED_FROM` edges (`:381-397`), the "different read from the node window" framing, the delete/erasure bullets, and the below-minimum and budget paragraphs (see scenarios 1 and 4). Answering the round question directly: the decision still stands as a cost/scope deferral. The rewrite correctly removes the impossibility argument, so the record no longer claims a different conclusion is necessary; the remaining defect is that it replaces an impossibility proof with an inflated cost sketch.
- **Likelihood: Medium** over the life of a permanent design record; the paragraph is the ticket's only artifact.
- **Impact: Delivery/scope.** The record can still steer a future implementer either to build state it does not need or to reject the cheaper route as too expensive. The "only" wording also hides that a second membership record already exists.
- **Detection: none automated.** Only a reader who checks `summarization.py`, the publication journal and `_run_incremental` against the paragraph.
- **Cheapest mitigation.** One edit in each of `jobs.py:985-987`, `RFC-0015:531-533`, `CHANGELOG.md:19`, `watermarks.py:39` and the test docstring: replace "three dependent per-level cursors" with "a summary-membership read and a cascade through the three levels", and "only from the summary" with "from the summary that recorded it (and the publication journal)".
- **Recommended verdict: Fix now** (wording only; no decision change).
- **Label:** newly introduced.

### 3. The event-time test now exercises the mechanism it claims and cannot pass for the wrong reason

- **Label:** newly introduced test.
- **Trigger.** A reader treats `test_event_time_move_changes_both_periods` as evidence for the class docstring and the paragraph above.
- **Mechanism.** The fixture pins `summarization_daily_min_events=5` and `summarization_max_items_per_summary=10` (`tests/test_organizer_incremental.py:51-55`), so a default flip no longer changes what the test proves. The test stores 6 day-A and 5 day-B memories (`:493-506`), asserts the moved id is recorded in day A's summary before the move (`:513-514`), then moves it with `update_node(event_time=...)` and asserts `updated_at` advanced (`:517-522`), then asserts the pre-run summary still records it (`:526-527`), and after a second full run asserts the id left day A's metadata (`:533`) and content (`:534`) and entered day B's content (`:535`). Failure modes are all real: if `update_node` did not bump `updated_at` the `:522` assertion fails; if it did not move `event_time`, `:533` and `:535` fail; if the second run reused the old summaries, `:533` and `:535` fail; if summaries did not record their sources, `:514` fails. `_active_daily_summary` asserts exactly one STABLE daily summary per period (`:656-671`), so a duplicate or a superseded survivor also fails. The dispatch guard is separate and also real (`:478-487`: if `summarize` joined `INCREMENTAL_JOBS`, `_run_incremental` would add the `incremental` detail and write a watermark; `run_job` at `jobs.py:181-186`).
- **Likelihood: Low.**
- **Impact: None production.** The test is now a sound tripwire for the decision record.
- **Detection:** it fails when the mechanism breaks.
- **Residuals, not defects.** It does not assert `moved.event_time` directly (the content assertions at `:534-535` imply it); it covers the exact-minimum boundary but not the below-minimum case in scenario 1; the helper's `limit=200` assumes a small store (fine for this fixture).
- **Cheapest mitigation.** None required.
- **Recommended verdict: No action.**
- **Label:** newly introduced test.

### 4. The budget statement is now accurate, but repeated truncated runs restart at the oldest periods

- **Label:** pre-existing behavior, newly documented.
- **Trigger.** A store with a few hundred periods (a year of daily memories) runs `prme organize --jobs summarize` at the default 5,000 ms budget (`src/prme/storage/engine.py:4511`); the daily level gets 50% (`jobs.py:1002`), and the newest periods are where a fresh correction lives.
- **Mechanism.** Each level checks its own budget before each period (`summarization.py:485-494`, `:570-577`, `:652-659`), so a full run reflects a change only where that level's budget reaches the period, which the text now states (`RFC-0015:527-530`, `jobs.py:981-984`). `_job_summarize` returns `JobResult` without setting `status` or `stopped_on_budget` (`jobs.py:1039-1046`), and `JobResult` defaults `status="completed"` (`src/prme/organizer/models.py:19-32`), so "it reports `completed` even when the budget truncates the period scan" is accurate. What the text does not add: periods are visited in sorted order (`summarization.py:485`) and every run restarts at the oldest, so repeated default-budget runs never advance past the cut; nothing in `details` records a skipped period, so the only remedy is a larger budget. The timing table documents 2 periods per run at every size (`docs/ORGANIZER-JOBS.md:70,80,90`), not a truncated run.
- **Likelihood: Medium** for stores older than a few months.
- **Impact: Medium.** The documented repair silently does not reach the affected periods; the operator sees `completed`.
- **Detection: silent.** A skipped period is simply not visited, and no counter records it.
- **Cheapest mitigation.** Add "and the next run starts again at the oldest period" to the text, and record skipped periods (or `stopped_on_budget`) in the job result as the bounded code follow-up.
- **Recommended verdict: Follow-up** (the text is now accurate; the behavior stays silent).
- **Label:** pre-existing behavior, newly documented.

## Angles checked and found clean

- **Doc-to-doc consistency.** `jobs.py:958-987`, `RFC-0015:511-533`, `watermarks.py:35-40`, the test class docstring (`tests/test_organizer_incremental.py:467-476`) and `CHANGELOG.md:19` state the same decision. The RFC paragraph sits in section 5.5 (`:469`), which is the section the CHANGELOG cites. The earlier sentence "Only `deduplicate` has a candidate set that a node write fully determines" (`RFC-0015:503-507`) is consistent with, not contradicted by, the new paragraph.
- **Sibling paths.** `DELETE FROM nodes` appears only in `duckpgq_graph.py:1117`, `pg/graph_store.py:1725` and `erasure.py:672`; the erasure purge also deletes the owner's watermark row (`erasure.py:691-699`). No other code writes `event_time` (both allowlists include it: `duckpgq_graph.py:1179-1195`, `pg/graph_store.py:673-689`). Merges, archive, decay, tombstone and consolidation supersede or archive with `updated_at` bumped, so the node window still names their period.
- **Membership metadata across formats.** The original writer (`f8cccad2`) and the v1 source-excerpts writer (`68e443d6`) both wrote `source_node_ids` and `DERIVED_FROM` edges; `selected_source_node_ids` is v2-only. The privacy projection keeps `source_node_ids`, `selected_source_node_ids`, `summarization_level` and `period_key` (`privacy/journals.py:90-94`, `:120-142`, `:161-168`), so the membership read works in a `privacy_admitted` store too; the RFC's "Known limitation" paragraph is about `scan_changed_nodes`, a different read.
- **Tautology screen.** None of the four new assertions can pass without the mechanism it names; the duplicate-summary assert in the helper is a real tripwire.
- **Backward compatibility.** No install sees changed behavior, config, schema or contract; the only cost is the pinned fixture values, which equal the current defaults.

## Unresolved risks

- The issue's "Done when" is not readable from the repository (no external calls in this review). If it required a technical impossibility proof for summarize, no accurate text can satisfy it; the record is now a deferral with a cost sketch, which is the honest form. Scenario 2 is the residual of that gap.
- The alternative's real cost is estimated from code (O(periods × every summary) per full run) and was not measured on a realistic store.

Pre-mortem headline: the record now admits the vacated period is recoverable, but still says the alternative needs three per-level cursors; a future implementer either builds resume state it does not need or defers summarize forever, while a below-minimum vacated day keeps its stale excerpt in the week and month summaries and every truncated full run still reports `completed`.
