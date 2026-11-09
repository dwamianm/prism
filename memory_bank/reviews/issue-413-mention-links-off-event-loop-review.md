# Consolidated review: issue #413, mention links off the caller's event loop

## Review Scope

- Base commit: `deb11a68b73f299d847efabf0e27195fb433dceb` (`main`).
- Reviewed snapshots: `38f98c59fccd01f4d67323c3f2d8e1775919ef13d4b1c4ab50d56143c01fb932` (full review), then `ea21588b3f5192888c9d92475dbf2826263e4b93e0283719056d04ad9d6a6296` (test-only delta, revalidated by the core reviewer).
- Tier: M. Predicates: NEW_SYMBOLS true; DATA, INFRA, SECURITY and PERSISTED_FIELD false.
- Roster: core (Sonnet) and adversarial (Opus). Checklists 1, 3, 4 and 5 merged into the core report; checklist 8 independent. Checklists 2 (dedicated security), 6 (infrastructure) and 7 (data) not applicable for this diff, and the core reviewer screened basic security and wiring.
- Reports: `memory_bank/reviews/issue-413-mention-links-off-event-loop-core.md` (with addendum) and `memory_bank/reviews/issue-413-mention-links-off-event-loop-adversarial.md`.

## Files Changed

- `src/prme/ingestion/mention_links.py`
- `tests/test_mention_links.py`
- `CHANGELOG.md`

## Approach Summary

`link_event_mentions()` matched every extracted entity name against every source text synchronously inside its loop, so all regex work ran on the caller's event loop. Each `mentions_entity()` call is windowed by #405, but one event's total work was unbounded. The change moves each source's matching into a synchronous helper and awaits it through `run_profile_work`, the shared two-worker pool used by profile builds, so the loop stays free and yields between sources. Edge construction and `create_edge` calls stay on the loop. The predicate, target order, edge ids and metadata are unchanged.

## Findings

### Must Fix

None.

### Should Fix

None.

### Consider

- C1 (resolved): the new test re-implemented the #405 ticker, bound and skip helper. It now imports `MAX_GAP`, `longest_gap` and `measurable` from `tests/test_profile_event_loop.py`.
- C2 (resolved): the timed window included the lazy `prme.organizer.profiles` import and first-use pool creation. Importing the #405 test module loads `prme.organizer.profiles` before the timed region.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs | PASS | No new logging; the existing `mention_links.failed` warning carries only event id and error type. |
| Ownership and tenant scoping | PASS | Entity lookup keeps `user_id` and `extraction.scope` (`mention_links.py:91-94`); no read or write changes owner. |
| Input handling | PASS | No new parsing or interpolation; `isinstance(text, str)` still guards the object text. |
| Threading and data exposure | PASS | Workers read immutable inputs and write nothing; edge writes remain on the loop. |
| Credentials or fixtures | N/A | No credentials; the test uses an in-memory fake graph. |

## Pattern Consistency Assessment

The change matches the #405 shape in `engine.consolidate_knowledge` (`src/prme/storage/engine.py:4638-4648`, `:4694`) and `owner_profiles._mentioning` (`src/prme/organizer/owner_profiles.py:130-142`): a pure synchronous helper awaited through `run_profile_work`, a deferred import inside the function, the shared fork-reset executor, and cancellation that propagates. It yields between sources, which is the analogue of `consolidate_knowledge` yielding between scopes.

## Redundancy Check

The `matching` helper is not redundant with the profile helpers: its inputs (`targets`, `texts`, `linked`) and output (name and entity pairs) are specific to mention links. `asyncio.to_thread` was rejected because it uses the loop's default executor that storage and retrieval share (`src/prme/organizer/profiles.py:19-22`, the reason #405 added the dedicated pool). A single batched `run_profile_work` call was rejected because it would hold one shared worker for a whole event. The issue itself prescribes `run_profile_work`.

## Wiring Findings

- No env or config values are read by the changed code.
- No new module, route or CLI entry point.
- All three call sites still pass the same injected `create_edge` (`pipeline.py:2027`, `reingest.py:537`, `engine.py:2221`), so the graph writes keep their write-queue routing.
- `DERIVATION_VERSION` is unchanged, correctly: the derived records and their edges are identical.

## Data Safety and Read/Write Proof

- No schema, key or persisted field change.
- Edge ids are still `uuid5(UUID(event_id), f"prme:text-link:{source.id}:{entity.id}")` with `TEXT_LINK_METADATA`, so existing packs keep their links and new writes deduplicate exactly as before.
- Retry and duplicate handling is unchanged: a second writer still fails the fixed edge id and takes the existing recheck path.
- Nothing is written from a worker thread.

## Break Scenarios (adversarial)

| # | Scenario | Label | Likelihood | Impact | Detection | Verdict |
|---|---|---|---|---|---|---|
| S1 | Writes queue behind profile builds on the shared two-worker pool (`mention_links.py:125`, `profiles.py:23-33`) | Newly introduced | Medium | Unbounded write and ingest tail latency; retries multiply load | Quiet | Follow-up (#485) |
| S2 | One matching job has no bound on total work and monopolizes a worker (`mention_links.py:106-114`) | Newly introduced | Low to Medium | Mention links and profile work stall; `store()` latency grows | Silent | Follow-up (#485, same root cause as S1) |
| S3 | Adjacent `link_group_claims` still tokenizes session turns on the loop (`group_links.py:426-430`) | Pre-existing | Low to Medium | 0.5 to 2 s loop stall, the class #405 and #413 remove | Silent | Follow-up (#486) |
| S4 | A duplicate-write race re-raises and abandons the rest of the event's links (`mention_links.py:132-143`) | Pre-existing, marginally amplified by one executor round trip | Low | Missing MENTIONS edges until rebuild or republish | Quiet | Follow-up (#487) |

Adjudication reasoning:

- S1 and S2 share a root cause: mention matching now shares the profile pool and has no per-event bound. The issue prescribes `run_profile_work`, and the alternatives each need a design call: a dedicated executor adds a third process-wide pool, `asyncio.to_thread` moves the queue onto the executor that storage and retrieval share, and a wall-clock budget changes which links a slow event gets. Grouped as #485.
- S3 is a different file and outside this issue's acceptance criteria; it needs its own change and tests. #486.
- S4 is pre-existing. The one-liner mitigation (`continue` after a failed recheck) also turns a genuine write failure into a silent skip, so it needs its own test and error-semantics decision. #487.

Pre-mortem headline (Agent 8, verbatim): "Two weeks later: writes stall behind two profile threads nobody was watching, health checks stay green, and the only trace is one warning per event."

## Follow-ups Raised

- #485: mention matching shares the two profile workers with profile builds and has no bound per event.
- #486: group links tokenize session turns on the event loop.
- #487: a duplicate mention-link write can abandon the rest of an event's links.

## Resolution Status

| Item | Class | Status |
|---|---|---|
| C1 reuse the #405 ticker helper | Consider | Resolved |
| C2 cold import inside the timed window | Consider | Resolved |
| S1 shared pool queueing | Adversarial, Follow-up | Issue #485 |
| S2 unbounded per-event matching | Adversarial, Follow-up | Issue #485 |
| S3 group links on the loop | Adversarial, Follow-up | Issue #486 |
| S4 duplicate-write abort | Adversarial, Follow-up | Issue #487 |
