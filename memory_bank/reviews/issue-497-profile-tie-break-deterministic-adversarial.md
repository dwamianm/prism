# Issue #497 adversarial review: deterministic profile tie-break

**Status: COMPLETE**

- Repository: `/Users/dwamianm/Sites/prism`
- Base branch / commit: `main` @ `52d99de1be90477abcfa7256088fd8359fea2eb5`
- Reviewed snapshot: `9767aaea7609dc5ad909fa3980277d438fa8f1217395da1289c37f678dbda1ff`
- Role: adversarial reviewer (Agent 8); assume this change causes a production incident.
- Changed files reviewed: `src/prme/organizer/profiles.py`, `tests/test_profile_excerpts.py`,
  `tests/test_profile_graph_sources.py`, `CHANGELOG.md`.
- Method: read the diff, then the real callers and callees (`engine.consolidate_knowledge`,
  `organizer/owner_profiles.py`, `ingestion/pipeline.py`, `ingestion/claim_merge.py`,
  `privacy/journals.py`, `retrieval/profile_sources.py`, node/event models). I did not re-run
  the test suites the worker already ran; I did not build or modify anything.

## Assigned checklist

1. Premise: does the sort-key change fix the root cause or only the reported symptom?
2. Guard loopholes: can a narrower claim still outrank its turn?
3. Existing data: what happens to profiles stored under the old ordering?
4. State/lifecycle, idempotency/retries, ordering/consistency, scale, partial failure,
   backward compatibility, blast radius, detection.
5. Explicitly: is `(date, type, -terms, content, id)` a total order, and can two records
   with identical content and event time still flip selection?

## What the change does (verified, not restated from the diff)

`build_profile` (`src/prme/organizer/profiles.py:127-149`) still sorts oldest-first and
greedily skips a source that does not fit. Only the tie-break changed: at one event time a
`NOTE`/`EVENT` ranks first, then more distinct `\w+` terms, then raw `content`, then
`str(id)` (`profiles.py:96-124`, used at `profiles.py:133`). The event-time component
(`_date`) and the header/budget logic are untouched. There is exactly one production caller,
`MemoryEngine.consolidate_knowledge` (`src/prme/storage/engine.py:4831`); grep across
`src/`, `tests/` and `benchmarks/` confirms no other production call site.

## 1. Premise challenge

The reported symptom is real and the root cause is exactly the old id-primary key: the turn
`NOTE` and the claims extracted from it share one event time
(`engine.py:2196-2198` sets the NOTE `event_time=event.event_time`; `pipeline.py:1858-1862`,
`1877` set a claim's `event_time` to `resolved_date` or `event.event_time or event.timestamp`),
so under the old `(_date, str(id))` key a random UUID decided which fit. The new key removes
the id from the effective decision for that class, so for the reported case this is a real
root-cause fix, not a symptom patch.

Two caveats qualify "root cause":
- The id tie-break is reduced, not removed. `str(node.id)` is still the final key
  (`profiles.py:123`). For byte-identical records the selection still depends on random ids,
  though the rendered source text is then identical apart from the embedded `[source=<id>]`.
- The change fixes *future* builds only. Already-published profiles are not repaired (section 3).

Sibling paths do not build or re-order profile sources, so the "only the tie-break changes"
claim holds: `organizer/owner_profiles.py` `_mentioning`/`_is_stale` read sources without
ordering them (`owner_profiles.py:136-215`); `organizer/consolidation.py:74-84` has its own
`_stable_source_key` for a different artifact; `organizer/summarization.py:286` sorts a
publication manifest by id for a different node type; `privacy/journals.py` stores
`source_node_ids` in the order given and does not re-sort; `retrieval/profile_sources.py`
verifies sources but does not order them. No retrieval/packing code depends on profile
source order.

## 2. Guard loopholes

For a normal turn (`NOTE`) versus a claim (`FACT`/`DECISION`/`PREFERENCE`/`TASK`, see
`engine.py:176` `PROFILE_CLAIM_TYPES`), the type rank at `profiles.py:120` dominates, so the
turn wins regardless of term count, content, or id. I could not make a narrower claim beat a
NOTE turn at an exact event-time tie. Residual loopholes:

- **Resolved-date inversion (real).** When a claim's `temporal_ref` resolves, its
  `event_time` is the resolved date (`pipeline.py:1858-1862`, `1877`), not the turn's time. If
  that date is *older* than the turn, the claim's first key sorts before the turn and can take
  the only budget slot. The guarantee "a turn is not dropped for a narrower claim from it"
  is therefore scoped to exact-time ties. Likelihood is bounded to claims that carry a
  resolvable temporal reference; the reported `my husband is Theo` case has none.
- **Non-`NOTE`/`EVENT` turns.** `store()` lets a caller choose `node_type` (default `NOTE`,
  `engine.py:1117`). A turn stored as `FACT` ranks with claims; the "more distinct terms"
  proxy then decides, and a normalized claim (`Theo is spouse of Maya Okafor`) can out-term a
  short turn (`he's my husband`). This needs a non-default caller choice, so it is a
  heuristic limit, not the reported path.
- **Merged claims** (`claim_merge.py:138-157`) keep the newest statement's times, so a merged
  claim ties with its turn and correctly ranks after it. Not a loophole.
- **No explicit `event_time`.** The NOTE uses `event.created_at` (`engine.py:2196`) while the
  claim uses `event.timestamp` (`pipeline.py:1858-1862`). These are two separate
  `default_factory` timestamps assigned in field order (`models/base.py:31-38` before
  `models/events.py:25-31`), so `created_at < timestamp` and the turn sorts first; when equal,
  the type rank still puts the turn first. Deterministic and correct, so not a bug, but it is
  an undocumented coupling.
- `NodeType.EVENT` is included in `_TURN_TYPES` (`profiles.py:98`) but no code creates EVENT
  graph nodes (grep across `src/prme`), so that branch is inert. `NOTE` is a catch-all that
  also covers vision observations and any `store()` note; treating those as "turns" is
  harmless for the reported class but is a heuristic.

## 3. Existing data

This is the most consequential gap. The CHANGELOG says a profile built before the change
"is not rewritten until a rebuild publishes a replacement", but the automatic rebuild paths
cannot publish one:

- `_is_stale` short-circuits to `False` whenever a profile already dropped sources:
  `owner_profiles.py:208-210` (`included < available` -> `return False`), before it ever
  checks newer mentions (`:211-215`). Its docstring premise is explicit at
  `owner_profiles.py:198-200`: "its oldest sources come first, so its text would not change."
  After this change that premise is false: a pre-change profile that dropped the turn would,
  on rebuild, now select the turn. Yet `_is_stale` still says it is not stale.
- Both automatic paths go through `_is_stale`: `refresh_stale_profiles`
  (`owner_profiles.py:275-277`) and `refresh_owner_profiles` (`:314`). The maintenance job is
  the only automatic caller (`organizer/maintenance.py:193-196`), gated by
  `enable_owner_profile_context`.
- There is no CLI to force a knowledge-profile rebuild: `prme` exposes only
  `profile-jobs`, `process-profiles`, `resume-profile`, `discard-profile`,
  `collect-profile-staging` (`cli.py:1518-1537`) and `organize`. The only forced rebuild is
  the Python API `MemoryClient.consolidate_knowledge` (`client.py:1104-1118`) called by the
  application.

So a store that reported #497 still serves the profile that names only Nico, indefinitely,
unless the application calls `consolidate_knowledge` itself or a quoted source retires. The
reported bug is fixed for new builds and not repaired for the population that filed it. The
CHANGELOG is honest about not rewriting, but does not say the automatic refresh will never
do it.

A one-time repair is non-trivial to bolt on cheaply: `profile_format_version` is pinned to 2
in three places (`engine.py:4866`, `privacy/journals.py:105`, `privacy_migration.py:683`), and
the privacy journal projection rejects anything else, so a version bump must update those
together.

## 4. Lifecycle / idempotency / scale / compatibility / detection

- **Idempotency / retries.** The key is a pure function of the node set; `sorted` computes the
  key once per element. Same nodes -> same order -> same `profile_request_hash`
  (`models/profile.py:41-56`) -> the reusable plan is found on retry. The change improves
  retry stability rather than harming it. In-flight prepared profiles resume from stored
  sources, not from a re-sort, so they are not invalidated.
- **Ordering / consistency.** `sorted` is stable; identical full keys (impossible without an
  identical id) fall back to input order. See the total-order analysis below.
- **Scale.** `_distinct_terms` (`profiles.py:103-105`) runs one compiled `\w+` regex over each
  source, once per source (not per comparison); it cannot ReDoS. It does build a set of words
  for every source and hold the full `content` in each sort key until sorting completes, so
  peak sort memory is O(total words over the source set). `count_tokens` already re-tokenizes
  the accumulated candidate per accepted source, which is heavier, so this is unlikely to be
  the bottleneck, but the recent very-large-budget work (#414/#422) makes it worth a
  measurement rather than an assumption.
- **Partial failure.** `content` is a required `str` (`models/nodes.py:25`), so the regex cannot
  raise on `None`. If a build did raise, `_rebuild` logs and keeps the previous profile
  (`owner_profiles.py:237-239`); an explicit `consolidate_knowledge` propagates.
- **Backward compatibility.** Build bytes are unchanged when event times never tie, because
  the first key alone orders the set (acceptance criterion 4 holds). For stores whose event
  times do tie, the selected set can change on rebuild; that is the intended fix. No config,
  budget, default, or API signature changed.
- **Blast radius.** Serving the profile requires `enable_owner_profile_context` (off by
  default), and the profile format/hash are unchanged, so exposure is limited to opt-in
  owner-profile users and to explicit `consolidate_knowledge(include_speaker_turns=True)`
  callers.
- **Detection.** None built in. The changed profile is visible only by comparing profile
  content with the sources; `source_count_included < source_count_available` in metadata
  (`engine.py:4847-4854`) is the closest signal, and nothing alerts on it.

## Total-order question (explicitly requested)

`(str date, int rank, int -terms, str content, str id)` is a **total order** on any node set:
all components are mutually comparable within their position, and ids are unique. Therefore
`sorted` is fully deterministic for a fixed set of stored nodes, and two records with identical
content, type, term count and event time are ordered by `str(id)` with no exception.

That is not the same as id-independent ordering. Two distinct records that are byte-identical
in `content` and share `event_time` (for example the same utterance stored twice, or two
claims with identical text) still fall through to `str(id)`. Across a re-ingest those ids are
new UUIDs, so which one is quoted can flip. The impact is low because the rendered content is
identical; only the `[source=<id>]` provenance line (and any `recorded_at`/epistemic
difference on otherwise-identical records) changes. It is a genuine residual determinism gap
against "same source text builds the same profile", not against "same memories (same ids)
build the same profile", which is already exact.

## Scenarios, ranked by likelihood x impact

### S1 - Existing bad profiles are never repaired (M)

- **Trigger.** Upgrade a store that already holds an owner profile built under the old
  ordering, one that dropped the turn for its narrower claim (the #497 population).
- **Mechanism.** `_is_stale` returns `False` for any profile with `included < available`
  (`src/prme/organizer/owner_profiles.py:208-210`), before the newer-mention check at
  `:211-215`, on the now-false premise at `:198-200`. Both automatic rebuild paths rely on it
  (`:275-277`, `:314`); the maintenance job is the only automatic caller
  (`organizer/maintenance.py:193-196`); no CLI forces a rebuild (`cli.py:1518-1537`).
- **Likelihood.** High for the affected population (that is precisely the set that reported
  the bug).
- **Impact.** Medium: the exact reported symptom (husband missing from the served profile)
  persists; the fix appears not to work.
- **Detection.** Compare the stored profile content with its still-active sources; the
  `source_count_included < source_count_available` metadata is the fingerprint. No built-in
  alert.
- **Cheapest mitigation.** Either give `_is_stale` an explicit "ordering changed" signal (bump
  `profile_format_version` and treat the old version as stale once, updating
  `engine.py:4866`, `privacy/journals.py:105`, `privacy_migration.py:683` together), or add an
  operator command that runs `consolidate_knowledge` for a named entity, and document it.
- **Recommended verdict.** Follow-up (the ticket scopes itself to future builds), but record
  it loudly: the fix does not reach the profiles that motivated the issue.
- **Classification.** Pre-existing gap, newly load-bearing. The change does not introduce the
  short-circuit, but it makes the "text would not change" premise false, so the gap now
  actively withholds the fix.

### S2 - A claim resolved to an older date still beats its turn (M, low likelihood)

- **Trigger.** A claim extracted from a turn carries a `temporal_ref` that resolves to a date
  earlier than the turn, at a budget that fits one source.
- **Mechanism.** `pipeline.py:1693` resolves the reference and `:1858-1862`, `:1877` set the
  claim's `event_time` to that resolved date, so the first sort key differs from the turn's
  and the claim sorts first; the type rank at `profiles.py:120` never runs.
- **Likelihood.** Low to medium (only claims with a resolvable past reference).
- **Impact.** Low to medium: acceptance criterion 2 ("a broader source is not dropped for a
  narrower claim from the same turn") does not hold for this class; the fact usually survives
  in the claim, but the turn's context is lost.
- **Detection.** Compare selected claims whose `resolved_date` predates their evidence turn
  with the turns that were excluded.
- **Cheapest mitigation.** Extend the key to group a claim with its evidence turn and sort the
  group by the turn's time (a larger change), or document that criterion 2 is exact-tie only.
- **Recommended verdict.** Accept / Follow-up (out of the exact-tie scope the ticket states).
- **Classification.** Pre-existing / inherent to the time-first ordering.

### S3 - Byte-identical records still flip on random id (L)

- **Trigger.** Re-ingest a transcript that contains two records with identical content, type,
  term count and event time, at a budget that fits one of them.
- **Mechanism.** The final key is `str(node.id)` (`profiles.py:123`); new UUIDs on re-ingest
  swap the selected record. The rendered body is identical; the `[source=<id>]` provenance
  line (and any differing `recorded_at`/epistemic fields) changes.
- **Likelihood.** Low (needs duplicate identical records competing for the last slot).
- **Impact.** Low (same content; provenance-only difference; determinism criterion 1 speaks of
  the same stored memories, which is already exact).
- **Detection.** Diff two builds of the same transcript and look for differing `source=`
  values with identical bodies.
- **Cheapest mitigation.** None needed for the reported class; if required, tie-break on the
  rendered source line before id.
- **Recommended verdict.** Accept.
- **Classification.** Pre-existing (id was the primary key before; it is now only the last).

### S4 - Sort-key word sets raise peak memory at very large budgets (L)

- **Trigger.** A profile at a very large `max_profile_tokens` (#414/#422) for a person with
  many long sources.
- **Mechanism.** `_profile_order` (`profiles.py:103-124`) builds a set of distinct words for
  every source and holds every full `content` string in the sort keys until `sorted`
  completes, so sorting costs O(total words) extra memory on top of the loaded nodes.
- **Likelihood.** Low (sources are bounded by the records mentioning the entity, not the whole
  store, and `count_tokens` already dominates CPU).
- **Impact.** Low to medium (slower build; possible memory spike on a pathological store).
- **Detection.** Profile-build wall time / process RSS before and after at a large budget.
- **Cheapest mitigation.** Replace the distinct-term set with a cheap breadth proxy, or cache
  the computed term count on the node snapshot.
- **Recommended verdict.** Accept (measure if large-budget profiles are a production shape).
- **Classification.** Newly introduced (the regex and per-source set are new).

## Scenarios attacked hardest, and why they failed

- **Turn vs claim, random ids, one event time.** Broken by design of the fix: rank 0 vs 1 at
  `profiles.py:120`; covered by `tests/test_profile_excerpts.py:40-60` (50 uuid4 pairs) and
  `tests/test_profile_graph_sources.py:99-131` (real engine, claim forced to `UUID(int=0)`).
- **Several claims extracted from one turn (all share the turn's event time).** All rank 1;
  ordered by distinct terms, then content, then id. Deterministic whenever content differs;
  only the S3 byte-identical case falls to id.
- **Merged claim.** `merged_claim` keeps the new statement's times
  (`claim_merge.py:138-157`), so it ties with its turn's NOTE and ranks after it.
- **No explicit event_time.** Turn key uses `event.created_at`, claim key uses
  `event.timestamp`; field order makes `created_at <= timestamp`, so the turn sorts first, and
  equal values are broken by the type rank. Deterministic.
- **Same-type, equal term count, different content.** The raw `content` string breaks the tie
  deterministically before id.
- **Sibling builders.** `consolidation._stable_source_key`, `summarization`,
  `privacy/journals`, `retrieval/profile_sources`, and `owner_profiles._mentioning`/`_is_stale`
  neither reorder profile sources nor depend on their order.
- **ReDoS / thread safety.** `\w+` is linear; the compiled `_WORD` is module-level and read-only
  and runs on the profile worker threads (`profiles.py:48-57`).

## Residual test-coverage note (not incident-level)

`test_equal_event_times_order_by_content_not_by_id` (`tests/test_profile_excerpts.py:63-70`)
actually exercises the type rank (rich is `note`, narrow is `fact`), not the `content`
tie-break named in its title; the raw-content tie-break is unexercised.
`test_equal_event_time_sources_with_more_terms_come_first` does cover the term-count key. The
graph-level test covers the end-to-end selection but not a second build of the same
conversation after re-ingest.

## Pre-mortem headline

**We shipped the deterministic tie-break, but the owners who reported #497 still see a profile
that names only Nico, because `_is_stale` never rebuilds a profile that already dropped
sources and no CLI can force one.**
