# Adversarial review: issue #523, `consolidate` stays full-scope

Status: COMPLETE

- Reviewer: Agent 8, adversarial (devil's advocate), tier M (max 4 scenarios)
- Branch: `issue-523-consolidate-incremental`
- Base commit: `7748252426c754c71f72652449285354f9e95945`
- Head commit: `7748252426c754c71f72652449285354f9e95945` (all work uncommitted)
- Review snapshot: `a9d132a77a8d19cfd74e43094c42f835fd5c3e25b75defb1cc6190379fd9cbea`
- Runtime behavior change: none. The diff adds a job docstring, a comment block, an RFC paragraph, a CHANGELOG entry and four tests.

## Assigned checklist, results

- Premise challenge: the conclusion survives; two supporting sentences do not (F1, F3).
- Guard loopholes: prose-only guard, one tripwire test (S4). The option-on path is exercised, because the `engine` fixture sets `incremental_enabled=True` (tests/test_organizer_incremental.py:60).
- Existing data and existing installs: clean. No schema, config, migration or stored row changes. An install already running `incremental_enabled=True` behaves exactly as before.
- State and lifecycle: clean. The seed order and retirement eligibility described in the new text match the code (src/prme/organizer/consolidation.py:153, 508-556; src/prme/storage/consolidation_retirement.py:37-57).
- Idempotency and retries: fails for the unqualified clause (S3, F3).
- Ordering, timing and consistency: fails to record the consequence of a budget stop (S2).
- Scale: fails to record the newest-500-per-type page and the per-cluster summary scan (S1, S2).
- Partial failure: unchanged by this PR; a pre-existing silent total-failure path is noted as O1.
- Backward compatibility: clean. Docstring and comment text only, no API surface.
- Blast radius and side effects: clean at runtime; one newly added timing-sensitive test is noted as O2.
- Detection: fails in that a truncated or wholly failed run reports `completed` (S2, O1).

## Findings

- F1. "The whole active set" is not what the job reads. `consolidation.py:136-146` reads `limit=500` per type, and `duckpgq_graph.py:1400-1404` orders `created_at DESC`, so the partition is built over the newest 500 FACT, 500 EVENT and 500 NOTE nodes, not the whole active set. The phrase appears at `jobs.py:1034-1036`, `docs/RFC-0015-Self-Organizing-Memory.md:550-551` and `CHANGELOG.md:23`.
- F2. The lineage claim cites the wrong RFC. `docs/RFC-0015-Self-Organizing-Memory.md:557` cites "(RFC-0006)" for "the consolidation key is the exact member set". RFC-0006 is Retrieval Cost and Context Efficiency (token packing) and contains no consolidation text. The statement is already in RFC-0015 itself at lines 285-296 ("The owner, scope, and sorted source identities form its lineage key", "The lineage is intentionally exact for a fixed source-identity set").
- F3. "A second full run creates nothing" is unqualified at `jobs.py:1054-1058`. It holds only when the first run retired no source. See S3.
- F4. The budget bullet records a cursor problem but not the operational consequence. `jobs.py:1050-1052` and RFC-0015:561-563 say a node keyset cursor cannot name where a stopped run stopped. They do not say that the run reports `completed` and restarts at the first cluster, which is exactly what the `summarize` record does say (RFC-0015:544-545). See S2.
- F5. The "full-run limits" that the `summarize` precedent carries are missing. The cluster floor is never mentioned: `consolidation.py:193` silently drops a cluster below `min_cluster_size`, so those nodes are never consolidated and never reported, which is the direct analogue of the summarize record's "a period below its minimum". The newest-500 page of F1 is the other missing limit.
- F6. Minor, newly introduced flake risk. `tests/test_organizer_incremental.py:1074` runs `organize(..., budget_ms=1)`. `engine.organize()` marks a job `not_started` with empty `details` when `remaining_ms <= 0` (`src/prme/storage/engine.py:4560-4567`), and the next line indexes `job.details["clusters_found"]`. Any scheduler delay over 1 ms between the two `time.monotonic()` reads turns the test into a `KeyError` that reads like a code regression.

## Scenarios

### S1. The record says "whole active set" for a job that reads a 500-line recency page

Label: newly introduced (the false claim; the page itself is pre-existing).

- Trigger: one owner has more than 500 active nodes of a single episodic type. This is ordinary: `store()` defaults to `node_type=NOTE`, and `prme organize` with no `--jobs` runs consolidate every time (`jobs.py:38-40`).
- Mechanism: `cluster_similar_memories` calls `engine.query_nodes(..., limit=500)` per type (`consolidation.py:136-146`), and the DuckDB query is `ORDER BY created_at DESC LIMIT ?` (`duckpgq_graph.py:1400-1404`). The greedy partition at `consolidation.py:153-216` therefore sees only the newest 500 of each type. A node pushed out of that page leaves its cluster with no write to itself, which the record's own next sentence at RFC-0015:296 would call a new lineage. The documented timing rows support this: consolidate takes 12,181.95 ms at 1,000 nodes and 8,298.63 ms at 10,000 nodes (`docs/ORGANIZER-JOBS.md:87,97`), which is a fixed page, not a whole-set scan.
- Likelihood: High. The default node type is NOTE and the job is in `DEFAULT_JOBS`.
- Impact: the one sentence a future incremental design will build on misdescribes the input. A node-change window that trusts "the whole active set" will still skip the oldest memories, and an operator reading the RFC will believe old memories are consolidated when they are not. No data loss from this PR itself.
- Detection: silent. `clusters_found` does not reveal the page boundary, and nothing in the job result reports how many active nodes were left out.
- Cheapest mitigation: replace the phrase with "the newest 500 active nodes of each episodic type, which is a recency window, not the whole scope", plus the two missing full-run limits (the cluster floor and this page). About 5 lines across `jobs.py`, RFC-0015 and CHANGELOG.
- Verdict: Fix now. It is the text this PR exists to write, and a one-sentence correction is cheap.

### S2. A truncated or starved full-scope run reports `completed`, and staying full-scope gives it no progress guarantee

Label: the reporting behavior is pre-existing; the omission of it from the record is newly introduced.

- Trigger: a store whose consolidate work exceeds the job budget. `prme organize` defaults to 5,000 ms total (`src/prme/cli.py:1554`), consolidate runs ninth of eleven jobs (`jobs.py:38-40`), and the committed timing table already measures 12.2 s at 1,000 nodes (`docs/ORGANIZER-JOBS.md:87`).
- Mechanism: stage 1, `cluster_similar_memories`, runs before any budget check and can issue up to 1,500 vector searches (`consolidation.py:136-193`); the budget is checked only inside the per-cluster loop (`consolidation.py:602-609`). Cluster order is fixed by `_stable_source_key`, so a stopped run restarts at cluster 1. `ConsolidationResult` has no truncation field (`organizer/models.py:46-54`), and `_job_consolidate` sets no `stopped_on_budget` and hardcodes `errors=0` (`jobs.py:1069-1082`), so `organize()` reports `completed`. The new test pins exactly that (`tests/test_organizer_incremental.py:1078-1080`). A second run is not cheap either: the option-on column records 11,935.37 ms at 1,000 nodes (`docs/ORGANIZER-JOBS.md:87`), which for consolidate is just a second full run.
- Likelihood: Medium-High. The measured 12.2 s against a 5 s default is a 2.4x overrun today, before any growth.
- Impact: later clusters never run, and after the first run the early-return path still pays `_lineage_summaries` per cluster (`consolidation.py:417`), which pages every SUMMARY node of the owner and scope (`consolidation.py:254-272`). A store with many summary lineages can keep the first clusters over budget forever, so no cluster past the cut is ever consolidated, while every report says `completed`.
- Detection: quiet. `completed`, `errors=0` and `summaries_created=0` on a starved run are indistinguishable from "nothing to do".
- Cheapest mitigation: two sentences in the docstring and RFC, matching the summarize paragraph ("the job reports completed even when the budget truncates the cluster scan, and the next run starts again at the first cluster"). Follow-up, roughly 10 lines plus a test, adds a `truncated` detail and a real error count.
- Verdict: Fix now for the documentation; Follow-up for the flag.

### S3. A second full run does create a summary, once the first run retired sources

Label: the behavior is pre-existing; the unqualified claim is newly introduced.

- Trigger: a cluster of six or more similar memories, all older than `consolidation_preserve_recent_days` (7) and all below `consolidation_min_confidence_preserve` (0.8). Extraction-derived claims sit at 0.60 for (asserted, system_inferred) (`src/prme/epistemic/matrix.py:42`), so a matured store has this shape.
- Mechanism: only the three sources selected into the summary are `covered`, and `forget_consolidated` retires only covered sources (`consolidation.py:508-556`; `consolidation_retirement.py:37-57`). The uncovered members stay active. With a cluster of six, three survivors remain, the next run re-partitions them (`consolidation.py:136-193`) into a new cluster, and `consolidation_key` over the new member set is a different key (`src/prme/models/consolidation.py:29-42`), so a second summary is published. The added test `test_second_consolidate_run_creates_no_new_summary` (`tests/test_organizer_incremental.py:986-1008`) stores fresh nodes, which the retirement policy preserves, so it cannot observe this path.
- Likelihood: Medium-Low. It needs six or more paraphrases (exact duplicates are merged by `deduplicate`, which runs earlier), all past the 7-day window and under 0.8 confidence.
- Impact: one extra overlapping summary per affected cluster on the run after the first, then it converges. It falsifies the sentence at `jobs.py:1054-1058` and would trip any operator check that a second run creates zero summaries. No data loss.
- Detection: quiet. A run over a store nobody changed reports `summaries_created=1`.
- Cheapest mitigation: qualify the sentence to "when the previous run retired nothing" and state that a partial retirement leaves the uncovered sources to form their own cluster next run. About 2 lines, plus a test that sets `consolidation_preserve_recent_days=0` and uses six contents.
- Verdict: Follow-up.

### S4. The guard is prose, and nothing ties it to the code it describes

Label: newly introduced (the guard shape).

- Trigger: a later change to `cluster_similar_memories` (seed order, `k`, threshold, coverage rule) or a deliberate addition of consolidate to `INCREMENTAL_JOBS`.
- Mechanism: `run_job` gates only on `job_name in INCREMENTAL_JOBS` (`jobs.py:181-186`), and that set is a literal frozenset (`watermarks.py:52`) with no link to the docstrings. The only tripwire is `assert "consolidate" not in INCREMENTAL_JOBS` (`tests/test_organizer_incremental.py:972`), which restates the code line. Nothing asserts that the documented mechanism still matches the implementation or that the option-on outcome equals the option-off outcome, so the four copies (docstring, watermarks comment, RFC, CHANGELOG) can go stale together. The same paragraph shape already produced a dangling duplicated fragment at RFC-0015:569 ("Known limitation: on a privacy-admitted store,"), which shows drift happens in this file.
- Likelihood: Medium. Four copies, no executable link.
- Impact: a future engineer implements a change-window consolidate that creates parallel summary lineages for the same sources, the exact defect that RFC-0015:295-298 says the exact-key lineage exists to prevent. That is a data-shape incident, not a crash.
- Detection: silent until retrieval starts returning overlapping summaries.
- Cheapest mitigation: one test that runs the same fixture with the option on and off and asserts the same summary ids and zero watermark rows, plus a comment on `INCREMENTAL_JOBS` pointing at the two docstrings. Roughly 15 lines.
- Verdict: Follow-up.

## Attacks that failed

1. "The previous partition is already durable in `summary.metadata['source_node_ids']`, so an incremental form exists." Failed. Only formed clusters leave a record, and the greedy sort order lets a changed node move ahead of an unchanged seed and claim neighbors from clusters arbitrarily far down the order, so a cluster can change membership with no member written. The record's first bullet is the right argument.
2. "Use the whole active set as the candidate set whenever any write occurred." Failed as an answer to the issue: it is provable but not bounded, it re-partitions everything, and it is still limited to the newest 500 per type (S1), so it is a gate, not an incremental contract.
3. "A changed node's own top-50 neighborhood contains its cluster." Falsified by the added test at `tests/test_organizer_incremental.py:1011-1055` and by the cascade: the seed's neighborhood, not pairwise similarity, decides membership.
4. "The retirement-frees-neighbors bullet is wrong because retirement also writes the summary." It does bump the summary's `updated_at` (`consolidation_retirement.py:123`), but summaries are not clustered, so the bullet holds.

## Does "no incremental form" satisfy the issue?

The bounded-candidate-set question is answered correctly, and the reason is recorded where the `summarize` change recorded it, plus the watermarks comment and the CHANGELOG. What the change does not deliver, and what the `summarize` precedent did deliver, is the full-run limits section: the newest-500 page (F1), the below-minimum cluster floor (F5) and the truncated-run report (F4). If the issue's "done when" is read as "exactly as the summarize change did", the record is incomplete rather than wrong. I could not read the issue text from this environment (no external calls permitted), so I judge against the precedent the acceptance criteria name. Nothing in the change adds a bounded or resumable shape; if the issue requires one for a job that stays full-scope, that is undelivered.

## Other observations (not scenarios)

- O1. Pre-existing silent total failure: `run_consolidation_pipeline` catches every per-cluster exception and only logs (`consolidation.py:628-633`), while `_job_consolidate` reports `errors=0` (`jobs.py:1074`). A run in which every cluster fails reports `completed` with zero summaries and zero errors. Follow-up, and it is the detection gap that S2 sits on top of.
- O2. F6 above: the new `budget_ms=1` test can flake to `not_started` and then `KeyError`. Use a budget with margin (for example 5 ms) or assert `job.status` first.

## Pre-mortem headline

Two weeks out, someone builds an incremental `consolidate` from the phrase "an emergent property of the whole active set", seeds it from changed nodes, and the store accumulates parallel summary lineages for the same sources, while every run reports `completed`.
