# Adversarial review: issue #413 (mention links off the event loop)

Status: IN_PROGRESS

- Reviewed snapshot: `38f98c59fccd01f4d67323c3f2d8e1775919ef13d4b1c4ab50d56143c01fb932`
- Base commit: `deb11a68b73f299d847efabf0e27195fb433dceb` (`main`)
- Working branch: `issue-413-mention-links-off-event-loop` (uncommitted working tree)
- Changed files: `src/prme/ingestion/mention_links.py`, `tests/test_mention_links.py`, `CHANGELOG.md`
- Mode: read-only adversarial / pre-mortem. No files were modified.

## Assigned checklist

| Check | Result |
|---|---|
| Premise challenge: every entry point that matched names on a loop | Partial. `link_event_mentions` is covered (`src/prme/ingestion/mention_links.py:125`). The adjacent `link_group_claims` step in the same post-commit block still tokenizes session turns on the loop (S3). |
| Guard loopholes | `_MATCH_WORK` bounds one regex call, not the total work of one job (S2). No bypass of the executor was found. |
| Existing data / installs | No schema, edge-format or uuid5 change; existing packs keep their links and are re-linked identically. |
| State and lifecycle | The pool is process-global, lazily created and never reset on failure; a broken pool is permanent for the process (S1 mechanism). |
| Idempotency and retries | Edge ids and metadata unchanged; duplicate handling unchanged, but its abort path is now reached over a wider window (S4). |
| Ordering / timing / consistency | Matching results, target order and edge set are identical to the synchronous loop; one extra loop yield per source. |
| Scale | One executor job per source against a fixed 2-worker pool (S1, S2). |
| Partial failure | `except Exception` still returns a partial count and every call site discards it (`pipeline.py:2029`, `engine.py:2223`, `reingest.py:539`). |
| Backward compatibility | CHANGELOG claim "links, ids and metadata unchanged" verified by reading the edge construction. |
| Blast radius / side effects | Newly shared with profile builds: queueing and failures now couple mention links to `consolidate_knowledge` (S1). |
| Detection | No metric anywhere; the only signal is one `mention_links.failed` warning. Quiet to silent in all four scenarios. |

## Verdict

The change is behavior-preserving and does what the ticket asked: name matching leaves the caller's
loop, edge writes stay on it. I could not construct a scenario in which this change produces
**worse** behavior than the synchronous loop for the same input. The realistic incident is not
corruption or a crash; it is that the write path now inherits the profile subsystem's queue,
latency and failure modes, and nothing reports it.

## Scenarios, ranked by likelihood x impact

### S1. NEW - the write path now queues behind profile builds on a 2-thread pool

**Trigger.** `store()` / `ingest()` runs while `enable_owner_profile_context` is on, or while the
application calls `consolidate_knowledge()` in-process, or while the `consolidate` organizer job runs
in the serving process. Concurrent writes then outnumber the pool.

**Mechanism.** `link_event_mentions` submits one job per source to the process-global
`_profile_executor` (`src/prme/ingestion/mention_links.py:125`), which is
`ThreadPoolExecutor(_PROFILE_WORKERS = 2)` (`src/prme/organizer/profiles.py:23`, `:28-33`), awaited
with no timeout (`profiles.py:46-52`). The same two workers serve `consolidate_knowledge`:
`naming` / `said_by` run `mentions_entity` across the entire scanned namespace
(`src/prme/storage/engine.py:4771-4772`, over the unbounded `all_nodes` paging at `:4706-4730`), and
`build_profile` (`engine.py:4829-4832`) re-encodes the accumulating profile text once per source
node (`profiles.py:106-111`). Either job can hold a worker for seconds. `submit()` appends to an
unbounded `SimpleQueue` without blocking (CPython `concurrent/futures/thread.py:175`), so surplus
callers wait indefinitely. The write path enters this queue at `src/prme/storage/engine.py:2223`.

**Likelihood.** Medium. `enable_mention_links` is on by default (`src/prme/config.py:1135-1136`), so
every write takes the path; the competing profile work needs `enable_owner_profile_context` (off by
default, `config.py:1301`) or an in-process `consolidate_knowledge()` / `prme organize` run, which is
the documented multi-tenant maintenance pattern.

**Impact.** Write and ingest tail latency grows without bound; client timeouts and retries then
multiply the work. Retrieval health checks stay green because the loop is free, which is the point of
the change and also what hides the incident. `end_session()` / `close()` waits on the maintenance
task (`engine.py:5125-5129`), so shutdown inherits the same backlog.

**Detection.** Quiet. No log, no counter, no queue depth exported. Only a latency graph shows it.

**Cheapest mitigation.** Run mention matching on the loop's default executor
(`asyncio.to_thread`) or a dedicated single-worker executor so profile builds cannot queue ahead of
writes. Size: one-liner, but it contradicts the acceptance criterion ("run the matching through
`run_profile_work`"), so the cost is a product decision.

**Recommended verdict.** Follow-up. Needs a product decision, not a silent scope change.

### S2. NEW - one matching job is unbounded in total work and monopolizes a shared worker

**Trigger.** A long pasted note (or a claim's `object` text) and a long extracted entity name, or
many listed entities, in one extraction. The name is model output, so a prompt-injected turn can
influence its length; the text is entirely caller-controlled.

**Mechanism.** `matching` (`src/prme/ingestion/mention_links.py:106-114`) runs *every* target against
*every* text of a source inside a single executor job. `mentions_entity` bounds one regex call to
about `_MATCH_WORK` characters (`src/prme/organizer/profiles.py:55-58`, `:76-82`) but the number of
calls is `len(text) * len(name) / _MATCH_WORK`, so a single `matching` call costs
Theta(len(text) * len(name)) with no cap. The new ticker test measures ~0.19 s for a 200 kB note and
a 2,001-character name (`tests/test_mention_links.py:318-381`), i.e. ~2 ms per window over ~95
windows. A 1 MB document with a 200-character name and 20 listed names is roughly 4 s of pool time
for one event; a burst of those fills both workers, and every later `store()` and profile build
queues behind them.

**Likelihood.** Low-Medium. Extracted names are normally one to four words; the pathological length
needs a model (or an injection) to produce it.

**Impact.** High for the duration: mention links and the whole profile subsystem stall, and
`store()` latency explodes. Total work is the same as before the change, but it used to freeze the
loop and now it occupies a resource other features depend on.

**Detection.** Silent. The queue length is not observable and no warning is logged.

**Cheapest mitigation.** Split the job per target, or have `matching` stop at a wall-clock budget and
return what it has (the caller already treats the result as best effort). Size: contained.

**Recommended verdict.** Follow-up, with a note in the PR that the bound is per regex call, not per
job, and that the job now shares a pool with profile builds.

### S3. PRE-EXISTING - the adjacent group-link step still tokenizes session turns on the loop

**Trigger.** An owner's turn that states a "we" claim in a long-lived session, or a session
containing a few turns longer than 20,000 characters.

**Mechanism.** `link_group_claims` runs immediately after `link_event_mentions` in the same
post-commit block (`src/prme/ingestion/pipeline.py:2037-2046`, `src/prme/ingestion/reingest.py:546`).
`session_group_members` reads up to `SESSION_TURNS = 500` prior events (`src/prme/ingestion/group_links.py:73`,
`:417-425`) and calls `_turn_groups(turn.content)` for each **on the loop** (`group_links.py:428`),
which tokenizes up to `_MAX_TURN_TOKENS = 4096` tokens in pure Python (`group_links.py:165`, `:326`).
`_turn_groups` is cached only for text at most `_CACHED_TEXT = 20_000` characters
(`group_links.py:167`, `:347-351`), so longer turns are re-tokenized on every pass.
`enable_owner_group_links` defaults to True (`src/prme/config.py:1210-1211`).

**Likelihood.** Low-Medium. It needs a session with hundreds of owner turns (real chats are much
shorter) or a few very long turns; the cache absorbs repeat passes for normal turns.

**Impact.** Medium: a loop stall on the order of 0.5-2 s, of exactly the class #405 and #413 exist to
remove. An operator reading the CHANGELOG would reasonably conclude the class is closed for the
post-commit block; it is closed only for mention links.

**Detection.** Silent.

**Cheapest mitigation.** Wrap the `_turn_groups` comprehension at `group_links.py:426-430` in
`run_profile_work`, as `owner_profiles._mentioning` already does. Size: contained.

**Recommended verdict.** Follow-up, separate ticket. It is outside #413's acceptance criteria and the
CHANGELOG wording is accurate as written.

### S4. PRE-EXISTING, marginally amplified - a duplicate-write race abandons the rest of the event's links

**Trigger.** `store()` materializes the note (`src/prme/storage/engine.py:2223`) while the derivation
for the same event publishes (`src/prme/ingestion/pipeline.py:2027`), both writing the same
deterministic edge id; the loser's recheck runs before the winner's write is visible.

**Mechanism.** On a failed `create_edge`, `src/prme/ingestion/mention_links.py:132-139` re-reads
`get_edges(source_id=...)` and **re-raises** when the edge id is absent. The outer handler at
`:141-143` logs `mention_links.failed` and returns the partial count, abandoning every remaining
source for that event. The new `await run_profile_work` at `mention_links.py:125` widens the
`get_edges` (`:116-119`) to `create_edge` (`:133`) window by one executor round trip per source. All
three call sites ignore the return value, so nothing repairs the gap until the plan is republished.

**Likelihood.** Low. Both writers normally share the write queue, so the loser's recheck sees the
winner's committed edge and takes the `continue` branch.

**Impact.** Medium: later sources of the event lose their MENTIONS edges, and entity-to-claim graph
walks degrade quietly until a rebuild or re-publish.

**Detection.** Quiet (one warning per event).

**Cheapest mitigation.** `continue` instead of `raise` after the failed recheck, matching what
`link_group_claims` already does (`group_links.py:516-523`). Size: one-liner.

**Recommended verdict.** Follow-up (pre-existing; take the one-liner the next time this file is
touched).

## Attacked and failed

1. **`lru_cache` / `_mention_pattern` thread safety.** `functools.lru_cache` takes its own lock and
   `re.Pattern` is immutable and safe to `search` concurrently; a duplicated `re.compile` under two
   threads is harmless. `_mention_pattern` (`src/prme/organizer/profiles.py:61-63`) is already used
   from the pool today by `owner_profiles` and `consolidate_knowledge`. Holds.
2. **Result and ordering equivalence.** The rewritten comprehension at `mention_links.py:106-114` is
   a literal restatement of the old inner test; `targets` order is preserved, `linked`
   (`mention_links.py:116-119`) is read-only after construction, and the uuid5 edge id, metadata and
   write order are unchanged. The CHANGELOG's "unchanged" claim checks out.
3. **Fork safety.** `_reset_after_fork` (`profiles.py:36-43`) gives the child a fresh pool and lock.
   The inherited executor becomes unreachable when `_executor` is set to `None`, so refcounting
   drops its `_threads` entries from `concurrent.futures.thread._threads_queues` before
   `_python_exit` can join threads that do not exist in the child. No hang found. (Not verified at
   runtime; read-only review.)
4. **Nested-executor deadlock.** No function submitted to the pool awaits anything or calls
   `run_profile_work` (`profiles.py:91-114`, `:130-140`; `engine.py:4636-4653`, `:4705-4710`,
   `:4771-4772`, `:4829-4832`). There is no executor cycle and no lock ordering between the pool and
   the write queue. Holds.
5. **Cancellation.** `CancelledError` is a `BaseException`, so neither `except Exception` at
   `mention_links.py:134` nor `:141` swallows it; a cancelled caller aborts exactly as before and
   the worker finishes a side-effect-free pure function.
6. **Executor shutdown during `close()`.** `MemoryEngine.close()` never shuts the profile executor
   down, so `run_in_executor` cannot raise "cannot schedule new futures after shutdown" during a
   normal close; only interpreter exit can, and no ingest runs then.

## Residual, accepted

- The new test is wall-clock based (0.1 s threshold, `tests/test_mention_links.py:378-382`). It
  skips rather than passes when the match is too fast, so it cannot pass vacuously, but a heavily
  loaded CI runner can exceed the threshold for unrelated reasons.
- A process that only writes now creates the profile pool (one or two threads) on its first mention
  link. CPython joins them at interpreter exit; no leak observed by inspection.

## Pre-mortem headline

Two weeks later: writes stall behind two profile threads nobody was watching, health checks stay
green, and the only trace is one warning per event.

Status: COMPLETE
