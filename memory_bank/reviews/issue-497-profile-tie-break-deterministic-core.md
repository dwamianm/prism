# Core review — issue #497, deterministic knowledge-profile tie-break

Status: COMPLETE

- Repo: /Users/dwamianm/Sites/prism
- Base branch and commit: main @ 52d99de1be90477abcfa7256088fd8359fea2eb5
- Reviewed snapshot: 9767aaea7609dc5ad909fa3980277d438fa8f1217395da1289c37f678dbda1ff (working tree on top of the base commit)
- Changed files: src/prme/organizer/profiles.py, tests/test_profile_excerpts.py, tests/test_profile_graph_sources.py, CHANGELOG.md

Verdict: the change holds up. No Must Fix or Should Fix findings. The sort key is a
total order, it only reorders sources that share one `_date(event_time or
created_at)` value, and both production wiring and the regression tests are real.
The items below are Consider-level notes and one documented limitation.

## Findings

### Consider — the rebuild-skip justification in `_is_stale` and `docs/PACKING.md` is no longer strictly true

- src/prme/organizer/owner_profiles.py:196-200 ("``build_profile`` keeps a profile's oldest sources within its token budget, so a profile that already left sources out and still has all of its own would be rebuilt with the same text; it is left as it is.")
- docs/PACKING.md:501-503 (same sentence in the docs)
- Behavior: `_is_stale` returns False when `source_count_included < source_count_available` (owner_profiles.py:208-210). Before this change a rebuild of such a profile could not change its text; now, if the profile has sources tied on `_date(event_time or created_at)`, a rebuild can swap which tied source wins the last budget slot. A profile built before the fix that kept the narrow claim is therefore never repaired by the refresh, exactly as CHANGELOG.md:12 says ("A profile built before the change is not rewritten until a rebuild publishes a replacement"), but the code comment and docs still give a reason ("the text would not change") that is no longer universally true. Rebuilding such profiles on upgrade would change bytes for stores where the tie never mattered (criterion 4), so leaving the behavior is right; the comment and the two doc sentences deserve a clause. Documentation/comment only, no code change requested.

### Consider — missing direct coverage of the content and id tie-break levels; one test name overstates what it proves

- tests/test_profile_excerpts.py:63-70, `test_equal_event_times_order_by_content_not_by_id`: `rich` is a `note` (rank 0) and `narrow` is a `fact` (rank 1), so the assertion passes on the type rank alone; it does not exercise the `node.content` element of the key.
- tests/test_profile_excerpts.py:73-80 covers the distinct-term element; nothing covers two same-type sources with equal distinct-term counts where `node.content` decides, nor the `str(node.id)` fallback. The turn-vs-claim rank and the term count (the two elements that carry acceptance criteria 1-2) are covered, so this is a coverage nit, not a gap in the acceptance criteria.
- tests/test_profile_graph_sources.py:99-133 is not vacuous: the claim's text names "Maya Okafor", so it enters `related` through `named` even before the HAS_FACT binding (engine.py:4772), and the assertion would fail on the base code because `UUID(int=0)` sorts first. Asserting `source_count_available == 3` (or the `spoken`/`subject` path counts) would make that explicit.

### Consider — the rule applies only at an exact tie; a claim with a resolved event time can still displace its turn

- src/prme/ingestion/pipeline.py:1858-1862: an extracted claim's `event_time` is the resolved date when extraction resolved a temporal reference, otherwise the turn's time. So a claim from a turn saying "last March" carries a March event time and sorts ahead of its (later) turn by `event_date`, not by the tie-break. At a tight budget it can still win the only slot.
- That is an event-time ordering decision, not the random-UUID tie, and changing it would alter bytes for stores where the tie never mattered, so it is correctly out of scope for criterion 4. Worth stating as a known limit of the fix if criterion 2 ("a source that covers more of what the person said is not dropped for a narrower claim extracted from the same turn") is read beyond the tie.

### Consider — nit: `re.UNICODE` is redundant

- src/prme/organizer/profiles.py:100: `re.compile(r"\w+", re.UNICODE)` — for `str` patterns `re.UNICODE` is already the default and cannot be disabled, so the flag adds nothing. Cosmetic; ruff accepts it.

## Findings that checked out (verification notes)

- Total order: `_profile_order` (profiles.py:108-124) ends in `str(node.id)`, which is unique per node (ids are UUIDs and `related` is deduplicated by id at engine.py:4823), so the five-element tuple never falls through ambiguously and `sorted` never compares incomparable values (all elements are `str`/`int`).
- Tie-only reordering: the first element `_date(node.event_time or node.created_at)` is exactly the old key's first element (base profiles.py:101), and tuple comparison is lexicographic, so sources with different date strings keep their previous relative order. Criterion 4 holds.
- Determinism under ids: the `uuid4`-varying loop in tests/test_profile_excerpts.py:44-60 and the permutation tests at lines 66-79 cover the two levels that matter. The residual id fallback only fires when date, type rank, distinct-term count and text are all equal; such records render identically except their printed `source=<id>` (and contrived metadata), so profile bytes already differ by id and this is not a practical residual nondeterminism.
- Type safety: `MemoryNode.node_type` is `NodeType` and `content` is a required `str` (models/nodes.py:24-25); `NodeType` is a `str` Enum, and membership in `_TURN_TYPES` is true even for a raw string (verified `"note" in frozenset({NodeType.NOTE})`), so no silent rank loss.
- `_TURN_TYPES` including `NodeType.EVENT` is defensive, not wrong: no engine path creates graph EVENT nodes, but `store(node_type=NodeType.EVENT)` (engine.py:1117) allows an application to.
- Selection scope: `build_profile`'s body is unchanged apart from the key (profiles.py:127-149); budget, the `continue` skip for over-budget sources, rendering and the `None` return are untouched. At a tie the chosen source consumes a different number of tokens, so the set after the tied group can follow; that is inherent to ordering tied sources and is covered by CHANGELOG.md:12's "otherwise unchanged".
- Return-value sentinels: `count_tokens` returns a plain `int` (retrieval/tokenization.py:16-18), no sentinel; `_date` returns the string `"unknown"` for `None` (profiles.py:90-93) but `_profile_order` always reads `event_time or created_at` where `created_at` is non-optional, so no sentinel can enter the key, while the rendered line's `"unknown"` is intended; `run_profile_work` returns the callee's value and `build_profile`'s `None` is handled at engine.py:4834.
- Async/concurrency: the new helpers are pure functions called from `sorted` inside `build_profile`, which the engine runs through `run_profile_work` on the profile thread pool; `_WORD`/`_TURN_TYPES` are immutable module state and safe to share.
- Performance: the key function is called once per source by `sorted`, and the regex scan is O(len(content)) per source, comparable to the `count_tokens` calls the build already makes per candidate, on the profile workers rather than the event loop.
- Pattern consistency/DRY: `_stable_source_key` (organizer/consolidation.py:74-84) is the nearest analogue and has a deliberately different contract (datetime comparison, `user_id`/`scope`, no turn rank), so reuse would be wrong; `_TURN_TYPES` correctly uses `NodeType` members rather than raw strings and is private to this module. No existing utility counts distinct terms; `product_candidates._WORD` is an ASCII `[a-z0-9]+` product tokenizer in another domain.
- Runtime wiring: `_profile_order` is used at profiles.py:133; `build_profile` has one production caller (engine.py:4830-4833) and its signature is unchanged; both test files match pytest's discovery and the new tests fail on the base code (claim `UUID(int=0)` sorts first; `source(..., 1)` sorts before `source(..., 2)`).
- Security: no new logging, env/config reads, network or DB calls; the ordering depends only on content the caller already holds and that the profile quotes, and protected builds pass only `AdmittedGraphView`-filtered nodes (engine.py:4631), so no additional data is exposed.
- CHANGELOG.md:12 is accurate (default budget 500 confirmed at config.py:34, public issue number used, `dwamianm/prism#497`).
- Tests run by the worker cover the affected surface; I statically checked the remaining profile tests (tests/test_knowledge_profile_scopes.py, tests/test_profile_publication.py, tests/test_owner_profile_context.py, tests/test_profile_retirement.py, tests/test_profile_work.py, tests/test_organizer.py) and none builds sources that share an event time, so none depends on the old id order.

## Checklist results

1. Correctness and logic — total order confirmed, tie-only reordering confirmed, no edge-case or type-safety defect; one documented scope limitation (resolved claim event times).
3. Code quality — clear helpers, no dead code, sound error surface; content/id tie-break levels lack a direct test; cost is proportional to work the build already does.
4. Pattern consistency and DRY — matches repo patterns (`NodeType` members, private module helpers, bare `tuple` key like `_stable_source_key`); no duplication.
5. Redundancy / sentinels — no existing utility is duplicated; `count_tokens`, `_date`, `run_profile_work` sentinels are not misused. No security or wiring issue.
