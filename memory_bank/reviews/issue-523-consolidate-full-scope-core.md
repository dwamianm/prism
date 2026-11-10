# Review: issue #523, `consolidate` stays full-scope (core)

Status: COMPLETE

- Review snapshot: a9d132a77a8d19cfd74e43094c42f835fd5c3e25b75defb1cc6190379fd9cbea
- Base commit: 7748252426c754c71f72652449285354f9e95945
- Head commit: 7748252426c754c71f72652449285354f9e95945 (all work uncommitted)
- Reviewed: `git diff` of the five changed files plus the production paths they
  describe (`src/prme/organizer/consolidation.py`, `src/prme/models/consolidation.py`,
  `src/prme/storage/consolidation_retirement.py`, `src/prme/organizer/jobs.py` dispatch
  and `_run_incremental`, `src/prme/storage/engine.py organize()`,
  `src/prme/privacy/journals.py generation_search`/`matching_summaries`,
  `src/prme/config.py incremental_enabled`, `docs/RFC-0006`, RFC-0015 section 5.5
  and the prior `summarize` round c7991a32).
- Verdict: the change holds up. It is documentation plus tests only, no runtime
  behaviour changes, `consolidate` stays out of `INCREMENTAL_JOBS`, no watermark
  row or resumable-cursor shape is left behind, and the recorded reason is
  technically sound. Two should-fix items (one wrong RFC citation, one
  timing-sensitive test) and several considerations follow.

## Checklist results

1. **Correctness and logic.** No runtime defect: the only production edits are a
   docstring and a comment. The recorded reason matches the code (verified claim
   by claim in the table below). Findings F1 (wrong citation) and F2 (timing
   dependence) are the substantive defects.
2. **Security and runtime wiring screen (merged).** PASS. No config read added, no
   dispatch change, no new external call, no secret in the diff. `run_job` still
   routes only `INCREMENTAL_JOBS` members to `_run_incremental`
   (`src/prme/organizer/jobs.py:181`); `consolidate` dispatches directly to
   `_job_consolidate` (`:173`). `_job_consolidate` has no `window` parameter, so a
   future accidental addition to `INCREMENTAL_JOBS` would raise `TypeError` and
   is also caught by the new test.
3. **Code quality.** Docstrings are long but parallel to `_job_summarize`
   (`src/prme/organizer/jobs.py:1173-1205`). Test names are descriptive, use the
   surrounding `@pytest.mark.asyncio` + fixture style, and reuse `_store_many` /
   `_watermark_rows`. Coverage is good for the "not incremental" property; two
   assertions do not add proof (F6) and one advertised property is not exercised
   (F7).
4. **Pattern consistency / DRY.** The docstring, watermarks comment, RFC
   paragraph and CHANGELOG entry all follow the `summarize` round (c7991a32):
   same "stays full-scope even when ... is on (#523)" opening, same
   bullet-then-alternative shape, same "so a later change does not assume the
   option covers the job" close. `incremental_enabled`'s description
   (`src/prme/config.py:632-639`) names only `deduplicate` and `alias_resolve`,
   and `INCREMENTAL_JOBS` (`src/prme/organizer/watermarks.py:52`) contains only
   those two, so Q4 is consistent. Two divergences from the analogue: the RFC
   cites the wrong document (F1) and the reason omits the hard-delete path the
   `summarize` paragraph records (F4).
5. **Redundancy / NEW_SYMBOLS.** No new production constants, classes or
   functions. New test symbols: `CONSOLIDATION_CONTENTS` and
   `TestConsolidateStaysFullScope` with four tests. `CONSOLIDATION_CONTENTS`
   repeats four strings that `tests/test_consolidation.py` already inlines at
   lines 95-100, 160-163, 220-223 and 434-439; test-local duplication is that
   file's existing convention, so no new shared helper is warranted.

## Findings

### F1 (Should Fix): the new RFC paragraph cites RFC-0006 for a rule RFC-0006 does not contain

`docs/RFC-0015-Self-Organizing-Memory.md:557`

The paragraph reads "the consolidation key is the exact member set (RFC-0006)".
RFC-0006 is "RMS Retrieval Cost and Context Efficiency" (token budgeting,
representation levels, context packing); a case-insensitive search of
`docs/RFC-0006-Retrieval-Cost-and-Context-Efficiency.md` finds no occurrence of
"consolidat". The rule the sentence states is written in RFC-0015 itself, at
lines 285-296: "The owner, scope, and sorted source identities form its lineage
key ... The lineage is intentionally exact for a fixed source-identity set. If
clustering adds or removes a source, it forms a new lineage". The implementation
is `consolidation_key` in `src/prme/models/consolidation.py:29-42`.

This matches the project review point that docs must match the paths they cite.
The `consolidate | RFC-0006` row in the RFC-0015 jobs table (`:258`) is the likely
source of the mistake, but that row is pre-existing.

Fix: replace `(RFC-0006)` with `(RFC-0015 section 5.3)` or with
`(``consolidation_key`` in ``src/prme/models/consolidation.py``)`.

### F2 (Should Fix): the budget-truncation test depends on wall-clock latency and can fail for the wrong reason

`tests/test_organizer_incremental.py:1073-1075` (`engine.organize(... budget_ms=1)`),
assertion at `:1077`.

`organize()` skips a job entirely when `remaining_ms <= 0`
(`src/prme/storage/engine.py:4558-4567`), setting `per_job["consolidate"]` to
`status="not_started"`. With `budget_ms=1`, `remaining_ms` is `1 - elapsed`,
where elapsed includes the `await self._materialization_queue.debt()` SQL round
trip before the job loop. On a loaded CI runner that can exceed 1 ms, the job
never starts and `assert job.status == "completed"` fails. This file already
documents that gate: `test_a_budget_skipped_job_still_reports_a_result`
(`tests/test_organizer_incremental.py:535-548`) asserts that `budget_ms=0` leaves
every requested job `not_started`, so the new test is riding a one-millisecond
margin between "skipped" and "runs". Once the job starts, FastEmbed clustering
costs far more than the remaining sub-millisecond budget, so the "stops before
the first cluster" half is reliable; the fragile part is the job-start gate. The
existing deterministic budget tests call the pipeline directly
(`tests/test_consolidation.py:486`), and this test should do the same.

Fix (uses the already-imported `run_job` at line 30):

```python
truncated = await run_job(
    "consolidate", engine, engine._config.organizer, budget_ms=0, user_id=USER,
)
assert truncated.status == "completed"
assert truncated.stopped_on_budget is False
assert truncated.details["clusters_found"] == 1
assert truncated.details["summaries_created"] == 0
assert await _watermark_rows(engine) == []
completed = await engine.organize(user_id=USER, jobs=["consolidate"], budget_ms=60000)
assert completed.per_job["consolidate"].details["summaries_created"] == 1
```

`budget_ms=0` deterministically trips the per-cluster check
(`src/prme/organizer/consolidation.py:603-609`) after clustering, which is exactly
the "reports completed, writes no cursor" property the test documents.

### F3 (Consider): the three organize-based tests depend on two `[HYPOTHESIS]` consolidation defaults

`tests/test_organizer_incremental.py:39-46` (constant comment),
`:973/:981`, `:987/:992`, `:1079`.

`test_consolidate_is_not_an_incremental_job`, `test_second_consolidate_run_creates_no_new_summary`
and `test_budget_truncated_run_reports_completed_and_restarts` all require the
four `CONSOLIDATION_CONTENTS` memories to cluster under the config defaults
`consolidation_min_cluster_size=3` and `consolidation_similarity_threshold=0.80`
(`src/prme/config.py:686-696`), both tagged `[HYPOTHESIS]`. The same fixture
already pins the summarization thresholds with the explicit rationale "so a
later default flip does not change what that test proves"
(`tests/test_organizer_incremental.py:62-66`), and `tests/test_consolidation.py`
always passes `similarity_threshold=0.70` explicitly for these same strings.

Fix: add `consolidation_min_cluster_size=3` and
`consolidation_similarity_threshold=0.70` to the `engine` fixture's
`OrganizerConfig` (no other test in this file runs `consolidate`), and update the
constant's comment. This keeps the tests about the incremental property rather
than a provisional tuning value.

### F4 (Consider): the reason omits the hard-delete path the `summarize` round recorded

`src/prme/organizer/jobs.py:1046-1049`; `docs/RFC-0015-Self-Organizing-Memory.md:559-561`.

The "no member written" bullet covers a retired seed but not a hard-deleted
member. `GraphStore.delete_node` (`src/prme/storage/duckpgq_graph.py:1091`)
removes a row outright, and a deleted member changes the greedy partition (the
freed neighbors, or the cluster itself, disappear) with nothing left in any
node-change window. That is the same hazard, and it is the stronger case because
the leaving node is not even a write. The `summarize` docstring explicitly names
`GraphStore.delete_node` and the erasure path
(`src/prme/organizer/jobs.py:1191-1194`), and the acceptance criterion says to
record the reason "exactly as the `summarize` change did".

Fix: add one clause to the bullet and the RFC paragraph, for example "or a hard
delete removes a member outright and leaves no row for the window". The existing
reason is already sufficient to justify "no"; this only closes the gap against
the analogue.

### F5 (Consider): RFC section 5.5 now contains a sentence the new paragraph corrects

`docs/RFC-0015-Self-Organizing-Memory.md:502-503` ("or a cluster can become
eligible without any node being written") versus the new text at `:559-560`
("with no member written: retiring a seed frees the neighbors it had claimed").

Strictly, the older sentence says no node is written, while the new paragraph
(and the docstring at `jobs.py:1049`) says the write is the retired seed's, a
non-member of the new cluster. The new wording is the accurate one. Since this
change adds the explanatory paragraph immediately below, the loose sentence now
reads as a contradiction.

Fix: change `without any node being written` to `without any member of the new
cluster being written`.

### F6 (Consider): `bridge_neighbourhood` asserts the planted fake, not the code under test

`tests/test_organizer_incremental.py:1057` and `:1064-1066`.

`bridge_neighbourhood = await planted_search("Bridge memory", USER)` calls the
test's own fake directly and asserts `[ids["Seed memory"]]`, which is exactly
what `neighbours["Bridge memory"]` says. It cannot fail because of a production
regression; it only restates the fixture. The meaningful assertion is
`len(clusters) == 1` plus member/centroid equality at `:1059-1063`, which does
fail if the greedy seed order regresses.

Fix: drop the direct `planted_search` call and assertion, or move the premise
into a comment ("Bridge's planted neighbourhood is Seed only, so a cluster seeded
at Bridge has 2 members and is below `min_cluster_size`").

### F7 (Consider): the "second run" and "restarts" halves do not pin the branches they advertise

`tests/test_organizer_incremental.py:986-1008` and `:1083-1089`.

`test_second_consolidate_run_creates_no_new_summary` passes whether the second
run reaches the idempotent publication match
(`src/prme/organizer/consolidation.py:417-424`) or finds no clusters at all. On
the current defaults the first run archives nothing (sources are inside
`consolidation_preserve_recent_days=7`) and the second run does re-cluster, but
the test does not assert that, so a future change that archives aggressively
would keep the test green while no longer exercising the documented path.
Similarly, `test_budget_truncated_run_reports_completed_and_restarts` truncates
before the first cluster, so "the next run starts again at the first cluster"
is true but no run finishes one cluster and stops before another; the
partially-progressed restart is not exercised.

Fix: add `assert first.per_job["consolidate"].details["nodes_archived"] == 0` and
`assert job.details["clusters_found"] == 1` to the second-run test (both hold
today), and either rename the budget test to say it covers a fully-truncated run
or add a two-cluster case that finishes one cluster before the budget stops.

## Documented claims checked against source

| Claim (docstring / RFC / CHANGELOG) | Source | Result |
|---|---|---|
| up to 500 active FACT/EVENT/NOTE per type | `consolidation.py:136-143` (`limit=500`, TENTATIVE+STABLE) | Accurate ("active" = TENTATIVE/STABLE here) |
| seed = earliest unassigned node under `_stable_source_key` | `consolidation.py:153-156` (`sorted(..., key=_stable_source_key)`, `if nid in assigned: continue`) | Accurate |
| members = seed's top-50 similar unassigned neighbors in namespace | `consolidation.py:160-191` (`k=50`, assigned/owner/scope/threshold filters) | Accurate |
| a changed non-seed node produces a different member set | `consolidation.py:153-223` greedy order + global `assigned` | Accurate, and demonstrated by F6's test |
| consolidation key is the exact member set | `models/consolidation.py:29-42`; `consolidation.py:403`, `:417-419` | Accurate; citation wrong (F1) |
| cluster becomes eligible with no member written when a seed retires | `consolidation.py:186`, `:221`, `:546-556`; `consolidation_retirement.py:113-143` | Accurate; hard delete omitted (F4) |
| budget checked once per cluster, list recomputed each run, no node cursor | `consolidation.py:584-609` | Accurate |
| second full run creates nothing (publication match + ineligible retirement no-op) | `consolidation.py:417-424`; `consolidation_retirement.py:126-128` (ROLLBACK, return False) | Accurate on unchanged input |
| `consolidate` not incremental; no watermark row; no "incremental" detail | `watermarks.py:52`; `jobs.py:181`; `_run_incremental` at `jobs.py:128-131` only | Accurate (Q3, Q4) |
| `incremental_enabled` description names only deduplicate/alias_resolve | `config.py:632-639` | Consistent (Q4) |

## Adversarial scenarios

- **`consolidate` added to `INCREMENTAL_JOBS` by a later change.** `_job_consolidate`
  takes no `window` keyword, so `_run_incremental` would raise `TypeError`;
  `organize()` swallows it as a skipped job and the new test's
  `per_job["consolidate"]` access/`status == "completed"` fails. The invariant is
  protected.
- **A changed node that is not the full run's seed.** `test_a_cluster_can_seed_at_an_unchanged_node`
  shows the full partition forms a 3-node cluster from Seed while Bridge's
  neighbourhood is a single node, so a changed-node scan seeded at Bridge cannot
  reproduce the lineage. Sound.
- **A seed retires while its neighbours stay active (recent/high-confidence).**
  The freed neighbours become seeds on the next run and form a different member
  set, hence a different key/lineage, while no new-cluster member was written.
  The reason holds.
- **A member is hard-deleted.** No row remains, so a node-change window cannot
  see it; the cluster changes silently. Not recorded (F4).
- **Budget stop after some clusters.** Budget is checked only between clusters,
  and a started cluster finishes consolidate+forget, so a stop leaves whole
  clusters done; the next run recomputes the list. No cursor/corruption. Not
  exercised by the tests (F7).
- **Concurrent `consolidate` runs on overlapping clusters.** The publication
  path converges only for the same key and request hash; a different member set
  is a different lineage by design (`consolidation.py:417-424`,
  `consolidation_publication.py`). This reinforces, not weakens, the "no" decision.
- **Privacy-admitted store.** `cluster_similar_memories` uses
  `generation_search` (`consolidation.py:129-130`), so the recorded reason is
  independent of the privacy projection; nothing in the change touches it.
- **Option on with no changes.** No watermark row is written and the job reports
  the same details as option-off; `engine` fixture runs with
  `incremental_enabled=True` (`tests/test_organizer_incremental.py:59-60`), so the
  new tests do verify the option-on path rather than the trivially-safe off path.

## Pre-existing observations (not part of this change, no action required)

- `docs/RFC-0015-Self-Organizing-Memory.md:569` is a dangling half-line,
  `Known limitation: on a privacy-admitted store,`, duplicated by the full
  sentence at `:570`. It is present at the base commit and sits immediately after
  the new paragraph, so it will look introduced in the diff.
- `docs/ORGANIZER-JOBS.md:67-100` labels the option-on runs of every job,
  including full-scope `consolidate`, as "Incremental ms / Incremental
  processed". Pre-existing from the #518 audit.

## Unresolved risks

- The FastEmbed dependency underlies three new tests. That is already true of
  `tests/test_consolidation.py`, so it is not new risk, but it is why F3's
  threshold pinning matters.
- The budget test's latency coupling (F2) is reasoned from the `organize()`
  skip path and the thread-hopping `materialization_count()` call; it was not
  reproduced as a failure in this review, and the worker's run passed.
