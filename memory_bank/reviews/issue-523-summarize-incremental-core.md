# Core review: issue #523, summarize round

Status: COMPLETE

- Base: `main` at `57d25cb8dad89f1fa0cb93eab33c54a3ee8bd2e7`
- Reviewed snapshot: `65819c30a59078a9412ebb5e09141555fcbce156c7d7c680460ff76a212ee24a`
- Round scope: `summarize` only. This diff takes the "no" branch from the spawn
  brief: it documents summarize as permanently full-scope and adds two tests.
  Reviewed as documentation-plus-tests, not as a missing implementation.

**Verdict: the change holds up.** The decision is correct on the source, the
docs agree with each other, and the tests assert the decision and the period
dependency. Three documentation-precision issues and two test/style nits are
recorded below; none of them changes the decision or blocks the round.

## Assigned checklist (merged)

1. Correctness and logic.
3. Code quality and test coverage.
4. Pattern consistency and full-configuration comparison.
5. Redundancy and unnecessary code (NEW_SYMBOLS: the new test class and helper).
6. Basic security and runtime-wiring screen.

## Verification of the specific questions

- **`event_time` on both backends: yes.**
  `src/prme/storage/duckpgq_graph.py:1179-1195` (`event_time` at 1194) and
  `src/prme/storage/pg/graph_store.py:673-689` (`event_time` at 674) both list
  it in `_UPDATE_ALLOWED_FIELDS`. The public `update_node` is
  `duckpgq_graph.py:545` and `pg/graph_store.py:707`. So the docstring's
  backend claim (`src/prme/organizer/jobs.py:967-968`) is accurate.
- **`delete_node` and the erasure path: both exist and hard-delete.**
  `duckpgq_graph.py:1087-1098` / `_delete_node_sync` at `1115-1118`
  (`DELETE FROM nodes`), `pg/graph_store.py:1722-1726`,
  abstract `src/prme/storage/graph_store.py:648`. The privacy purge deletes
  `DELETE FROM nodes WHERE id=$1 AND user_id=$2` at
  `src/prme/storage/erasure.py:672` and also clears affected watermark resume
  cursors and rows at `682-699`. The docstring claim at `jobs.py:971-972` is
  accurate.
- **The period claim: accurate.** Daily groups active non-summary nodes by
  calendar day (`src/prme/organizer/summarization.py:71-78`, `473-483`);
  weekly groups active daily summaries by ISO week (`529-568`); monthly groups
  active weekly summaries by month (`611-650`). The hierarchy dependency in
  the RFC (`docs/RFC-0015-Self-Organizing-Memory.md:523-525`) and in
  `jobs.py:976-978` is real.
- **The two tests assert the decision and the dependency.** Test 1
  (`tests/test_organizer_incremental.py:469-479`) asserts `summarize` is not in
  `INCREMENTAL_JOBS` (`src/prme/organizer/watermarks.py:39`), that the result
  carries no `incremental` detail (the key `_run_incremental` adds at
  `src/prme/organizer/jobs.py:128-131`), and that no watermark row was written.
  This is a real guard, not a tautology: if summarize were added to
  `INCREMENTAL_JOBS`, `run_job` (`jobs.py:181-184`) would pass a `window`
  keyword the summarizer does not accept, the `except Exception` at
  `src/prme/storage/engine.py:4575-4579` would skip the job, and
  `result.per_job["summarize"]` would raise. Test 2
  (`tests/test_organizer_incremental.py:481-516`) asserts a moved source leaves
  the old day's summary and appears in the new day's, which is the period
  membership dependency.
- **Determinism.** Grouping is by explicit `event_time` in test 2, so wall
  clock does not decide membership. All nodes fit under the default
  `summarization_max_items_per_summary=10`, so the top-N selection does not
  drop any, and `created_at` only breaks ties that cannot change membership.
  Test 1 creates its five nodes with the default event time but asserts only
  job status and watermark state, not that a summary was produced, so a
  midnight split cannot make it flaky. A partial exception is noted as finding
  4: test 2's day sizes sit exactly on the default 5-event minimum.
- **Doc-to-doc consistency.** `jobs.py:958-980`, `watermarks.py:35-38`,
  RFC 5.5 (`docs/RFC-0015-Self-Organizing-Memory.md:511-526`) and
  `CHANGELOG.md:19` all say the same thing: summarize stays full-scope.
  Nothing claims `alias_resolve` or `consolidate` is incremental; the only
  member of `INCREMENTAL_JOBS` is still `deduplicate` (`watermarks.py:39`), and
  `consolidate` is untouched by the diff.

## Findings

Each finding cites exact file and line. All are non-blocking under the bounded
delivery rules: none violates an acceptance criterion, introduces a regression,
or raises a security issue.

1. **"writes only the new event time" is false about the write.**
   `src/prme/organizer/jobs.py:969` and
   `docs/RFC-0015-Self-Organizing-Memory.md:518` say that moving a node "writes
   only the new event time". Both backends also always set `updated_at`
   (`duckpgq_graph.py:1217-1220`, `pg/graph_store.py:728-731`). The conclusion
   that follows is still correct: a window over `updated_at` does see the
   moved node, but its current `event_time` names only the new period and
   nothing names the vacated one. The wording matters here because a reader
   who takes "writes only the new event time" literally would think the update
   never touches `updated_at`, which is the opposite of what makes the argument
   work. Suggested wording: the move writes the new `event_time` and a new
   `updated_at`, so a window finds the node but not the day it left.

2. **"every active source node" / "complete active membership" overstates the
   input.** `src/prme/organizer/jobs.py:960`,
   `docs/RFC-0015-Self-Organizing-Memory.md:513-514` and `CHANGELOG.md:19` say
   a summary is built from the period's complete membership. The candidate set
   is that complete membership, but the summary is a top-N selection over it
   (`summarization.py:113-124`, `497`, `580`, `662`; default cap 10 at
   `src/prme/config.py:736-739`), and its module docstring already says
   "picks top-N by salience" (`summarization.py:9`). The load-bearing claim for
   the decision is about the candidate set, so this does not weaken it, but the
   phrasing should say so: "a top-N selection over that period's complete
   active membership".

3. **"A full run ... converges after either write" is not always true.**
   `src/prme/organizer/jobs.py:974-975` and
   `docs/RFC-0015-Self-Organizing-Memory.md:521-522`. A period whose remaining
   membership falls below its minimum is skipped and never re-materialized
   (`summarization.py:493-494` daily, `576-577` weekly, `658-659` monthly),
   because nothing else retires a summary for a period that lost members: only
   `plan.previous` is archived at publication
   (`src/prme/storage/consolidation_publication.py:710-712`). Concretely, a day
   that had exactly the five minimum nodes and loses one keeps its old summary
   as `STABLE`, and a day whose nodes are all deleted disappears from the
   grouping entirely, so its summary is never revisited. Test 2 avoids this by
   leaving day A at exactly five. Suggested wording: a full run re-examines
   every period's current membership; it does not claim the derived summaries
   always converge when membership drops below the thresholds.

4. **Test 2 is pinned to the default summarization thresholds.**
   `tests/test_organizer_incremental.py:486-487` create six and five nodes, so
   the test depends on `summarization_daily_min_events=5`
   (`src/prme/config.py:724-727`), and it depends on
   `summarization_max_items_per_summary=10` (`config.py:736-739`) to include
   the moved node. The `engine` fixture builds `OrganizerConfig` at
   `tests/test_organizer_incremental.py:47-50` without setting either. A later
   default increase would fail this test at its setup step rather than on the
   property under test. Pinning both thresholds in the fixture would make the
   test independent of the defaults.

5. **Style nit.** `_active_daily_summary`
   (`tests/test_organizer_incremental.py:636-652`) has no return annotation,
   unlike the neighbouring `_content_states`
   (`tests/test_organizer_incremental.py:629`). The configured Ruff rule set
   does not require annotations, so this is cosmetic.

Observation, not a finding: the class docstring
(`tests/test_organizer_incremental.py:462-467`) says "the docstring records
why", but nothing asserts docstring text. That is acceptable because the
decision is asserted behaviorally in test 1.

## Checklist results

1. **Correctness and logic:** pass with findings 1-3 (documentation precision
   only; the full-scope decision and its mechanism are correct on both
   backends).
3. **Code quality and test coverage:** pass. Both required properties are
   covered and deterministic; findings 4-5 are robustness and style nits.
4. **Pattern consistency and full-configuration comparison:** pass. The
   docstring follows the existing `_job_promote` full-scope note
   (`jobs.py:205-207`); the `watermarks.py` comment keeps the file's existing
   tone; CHANGELOG, RFC and docstring agree; no doc claims another job is
   incremental; `consolidate` is untouched.
5. **Redundancy and unnecessary code:** pass. The new class and helper are
   justified; test 1 is not a duplicate of `TestDefaultUnchanged`
   (`tests/test_organizer_incremental.py:534-552`), which covers deduplicate
   only.
6. **Security and runtime wiring:** pass. The diff adds no executable
   production line (verified: every added `src/` line is inside a docstring or
   is a `#` comment), reads no environment or config value, and makes no
   network call. The erasure path cited by the docstring already handles the
   organizer state it touches: it clears watermark resume cursors and rows
   (`src/prme/storage/erasure.py:682-699`) and its closure discovery reaches
   summary nodes through their `evidence_refs` (`erasure.py:570-573`), so the
   docstring does not rely on a gap there. No risk found.
