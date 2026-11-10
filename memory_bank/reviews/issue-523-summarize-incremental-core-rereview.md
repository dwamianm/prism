# Core rereview: issue #523, summarize round

Status: COMPLETE

- Base: `main` at `57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7`
- New reviewed snapshot: `1abda232806d23170c6def5776607aa54c463716b349093bc9083c965e5f5742`
- Previous reviewed snapshot: `65819c30a59078a9412ebb5e09141555fcbce156c7d7c680460ff76a212ee24a`
- First report: `memory_bank/reviews/issue-523-summarize-incremental-core.md`
- Delta scope: the five findings from the first report plus the strengthened
  `event_time` test. Changed paths: `CHANGELOG.md`,
  `docs/RFC-0015-Self-Organizing-Memory.md`, `src/prme/organizer/jobs.py`,
  `src/prme/organizer/watermarks.py`, `tests/test_organizer_incremental.py`.

## Assigned checklist (merged)

1. Correctness and logic.
3. Code quality and test coverage.
4. Pattern consistency and full-configuration comparison.
5. Redundancy and unnecessary code (new test class and helper).
6. Basic security and runtime-wiring screen.

## Verdict

All five findings are addressed. The rewritten text is accurate on the
mechanism and no longer claims a convergence that the code does not deliver.
The strengthened test asserts the right mechanism (the old summary records the
moved id; `update_node` bumps `updated_at`; the second run repopulates both
days) and cannot pass for the reasons the first review worried about. Two
non-blocking precision nits remain in the new text (one `only` overstatement
and one "active" overbreadth); neither changes the decision or blocks the round.

## Verification of the fixes

1. **Finding 1 (write also bumps `updated_at`): fixed.**
   `src/prme/organizer/jobs.py:968-970` and
   `docs/RFC-0015-Self-Organizing-Memory.md:517-519` now say the move writes
   "the new event time and a new `updated_at`". Both backends always set
   `updated_at` (`src/prme/storage/duckpgq_graph.py:1217-1220`,
   `src/prme/storage/pg/graph_store.py:728-731`); `event_time` is in
   `_UPDATE_ALLOWED_FIELDS` on both (`duckpgq_graph.py:1194`,
   `pg/graph_store.py:674`). Accurate.

2. **Finding 2 (top-N over membership): fixed.**
   `src/prme/organizer/jobs.py:960-963` says each level "selects the top
   `summarization_max_items_per_summary` sources from a period";
   `docs/RFC-0015-Self-Organizing-Memory.md:512-515` says each is "a top-N
   selection over that period's active membership"; `CHANGELOG.md:19` says
   "one top-N summary per day, week and month over that period's active
   membership". Matches `_select_top_salient` used at
   `src/prme/organizer/summarization.py:497,580,662` (default cap 10 at
   `src/prme/config.py:736-739`). Accurate.

3. **Finding 3 (full-run limits): fixed.**
   `src/prme/organizer/jobs.py:981-987` and
   `docs/RFC-0015-Self-Organizing-Memory.md:527-534` now scope convergence to
   periods "where the period still meets its minimum and the run's budget
   reaches it", state that a below-minimum summary is not retired, and state
   that the job reports `completed` when the budget truncates the scan. Each
   clause checks out:
   - below-minimum periods `continue` before materialization
     (`summarization.py:493-494`, `576-577`, `658-659`) and nothing else
     retires their summary;
   - the period loops `break` on budget without touching status
     (`jobs.py:488-490`, `572-574`, `654-656`), and `_job_summarize` returns a
     `JobResult` with the default `status="completed"`,
     `stopped_on_budget=False` (`jobs.py:1038-1046`,
     `src/prme/organizer/models.py:31-32`).
   Accurate.

4. **Finding 4 (thresholds pinned): fixed.** The `engine` fixture now pins
   `summarization_daily_min_events=5` and
   `summarization_max_items_per_summary=10`
   (`tests/test_organizer_incremental.py:51-56`), so the test no longer depends
   on the shipped defaults.

5. **Finding 5 (annotation): fixed.**
   `tests/test_organizer_incremental.py:656` now reads
   `async def _active_daily_summary(engine: MemoryEngine, period_key: str) -> MemoryNode:`.

## Strengthened test verification

`tests/test_organizer_incremental.py:489-535` (`test_event_time_move_changes_both_periods`):

- `old_a.metadata["source_node_ids"]` contains the moved id (line 514). Day A
  has six FACT nodes and the cap is pinned at 10, so all six are selected and
  recorded (`summarization.py:113-124`, `307-308`, `358`). The assertion fails
  if the pin is lowered, so it tests the intended property.
- `moved.updated_at > before_move.updated_at` (line 522). `update_node` always
  stamps a fresh `updated_at` (`duckpgq_graph.py:1217-1220`); the first
  organize does not rewrite source nodes, so the comparison is meaningful and
  cannot pass if `event_time` were ignored.
- Before the second run the old summary still records the id (lines 526-527),
  which is the recorded-membership route the doc describes; summary metadata is
  immutable to `update_node`.
- After the second run the id leaves the old day's `source_node_ids` (line 533)
  and appears in the new day's content (line 535). Day A drops 6 -> 5, exactly
  its pinned minimum, so it is still materialized; the changed source set
  changes the request hash, so a new summary is published and the previous one
  is archived (`src/prme/storage/consolidation_publication.py:710-712`). The
  helper asserts exactly one STABLE daily summary per period
  (`tests/test_organizer_incremental.py:656-671`), so an un-archived sibling
  would fail the helper rather than pass silently.
- `"day-a-5"` is the exact content of `ids_a[-1]` and is not a substring of any
  other node's content, so the content assertion cannot pass spuriously.

The test is deterministic: periods come from explicit `event_time`, and the
same ISO week holds only two daily summaries, below the weekly minimum of 3, so
no weekly/monthly summary is produced to confuse the daily helper.

## Newly introduced issues found

None blocking. Two precision nits:

1. **`CHANGELOG.md:19` says "recoverable only from the summary".** The
   vacated period is also recorded by the source event: the event row keeps the
   original `event_time` (`src/prme/models/events.py:28`,
   `src/prme/storage/event_store.py:43`), `store()` writes the same value to
   both the event and the node (`src/prme/storage/engine.py:1263`, `1345`), and
   `update_node` only touches the `nodes` table
   (`duckpgq_graph.py:1197-1222`, `pg/graph_store.py:707-731`). The docstring
   (`jobs.py:971-975`) and the RFC (`RFC:520-523`) correctly avoid "only"; the
   CHANGELOG and the test class docstring
   (`tests/test_organizer_incremental.py:473`) do not. Suggested: drop "only",
   or say the summary is what names the old period without a source-event read.

2. **"active daily summaries" / "active weekly summaries" is slightly broad.**
   `src/prme/organizer/jobs.py:961-963` calls the weekly and monthly inputs
   "active", but the rollups scan only `STABLE` and `TENTATIVE`
   (`summarization.py:556-561`, `638-643`), while the canonical active set also
   includes `CONTESTED` (`src/prme/types.py:284-288`). Daily is accurate (it
   scans the full active set, `summarization.py:468-472`). Cosmetic.

## Checklist results

1. **Correctness and logic:** pass. Findings 1-3 and 5 verified against source;
   no production logic changed, and every added `src/` line is inside a
   docstring or `#` comment.
3. **Code quality and test coverage:** pass. Finding 4 fixed; the strengthened
   test exercises the mechanism and both periods deterministically; helper has
   a return annotation. Only the two documentation nits above.
4. **Pattern consistency and full-configuration comparison:** pass. Docstring
   shape matches the existing `_job_promote` full-scope note; `CHANGELOG`,
   RFC 5.5, `jobs.py` and `watermarks.py` agree; `INCREMENTAL_JOBS` still holds
   only `deduplicate`; pinned fixture values equal the current defaults, so
   other tests in the file are unaffected.
5. **Redundancy and unnecessary code:** pass. `MemoryNode` import, the
   `_active_daily_summary` helper, `before_move`/`still_old_a` and all four new
   assertions are used; nothing duplicates `TestDefaultUnchanged`.
6. **Security and runtime wiring:** pass. No new executable production line,
   no config/env read, no network call; the erasure path already clears
   watermark rows (`src/prme/storage/erasure.py:691-699`) and nulls resume
   cursors referencing an erased node (`erasure.py:682-690`), as the docstring
   states.

## Evidence status

Worker evidence on this snapshot: `uv run ruff check
src/prme/organizer/jobs.py src/prme/organizer/watermarks.py
tests/test_organizer_incremental.py` passed; `uv run pytest -q
tests/test_organizer_incremental.py::TestSummarizeStaysFullScope` passed
(2 passed); full suite in progress. Not re-run here.
