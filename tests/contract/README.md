# Contract scorecard

A fast pytest suite of memory mechanisms with known answers, built from the diagnostics behind
`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md` (the scripts in `memory_bank/thesis-2026-11-10/`). Each test
id is one question about one mechanism: does the newest place of a moved object rank first, does a rare event
survive a flood of routine observations, does a question about something never seen come back small. It makes
no model call and no network connection, and it runs with the product defaults.

It is a scorecard, not a benchmark. The scenes are synthetic and small, and an id passing says the mechanism
works on this data, nothing more.

## Run it

```sh
OPENAI_API_KEY= ANTHROPIC_API_KEY= uv run pytest tests/contract -q
```

About 40 seconds on a laptop (124 ids run, 36 skipped without `--stated`). With the hidden set (below),
`uv run pytest tests/contract tests/contract_holdout -q` takes about 80 seconds.

Under address-first memory (`memory_mode="address_first"`):

```sh
OPENAI_API_KEY= ANTHROPIC_API_KEY= uv run pytest tests/contract -q --memory-mode address_first
```

Every pack is then built with `memory_mode="address_first"`, and the expected failures are not strict: an id the
mode fixes shows as XPASS and nothing fails for being fixed. The default, `--memory-mode classic`, is the product as
configured, with strict expected failures. Run the option from a command that names `tests/contract` or
`tests/contract_holdout`, since only their conftests add it.

At the end of the run a table shows passed, xpassed, xfailed and failed per group (skipped and errors when there are
any). The hidden set prints its total only.

`--stated` also builds the S1 packs written as caller-stated facts (`ingest(assertions=...)`, below). Each stated
fact takes about 200 ms to write, so those packs add about 3 minutes per set, and without the flag their 36 ids per
set are skipped.

## How to read it

- **passed**: the mechanism gives the known answer today.
- **xfailed**: a known failure, listed in `expected.py` with the thesis finding it shows
  (`thesis 2.2 <letter>: <reason>`). The suite is green with these.
- **XPASS(strict) / failed**: an id listed as failing now passes. A change fixed it: remove the id from
  `expected.py`, which turns it into a guard against going back.
- **failed** on an id not in `expected.py`: a regression.

Every listed failure is a strict xfail, so neither a fix nor a regression can go unnoticed.

## What is in it

| File | Scene | One id is | Known answer |
|---|---|---|---|
| `test_state_churn.py` | S1: 20 objects move 2, 5 or 12 times, 10 minutes or 3 days apart | an object in one (spacing, moves) pack; 6 objects sampled per pack with a fixed seed | the newest placement is `results[0]` |
| `test_state_churn.py` (`--stated`) | S1 again, each move written with `ingest_batch()` and an assertion: the object `located_in` the place, status current | the same objects, ids `stated-...` | the newest placement is `results[0]` |
| `test_flood.py` | S2: 400 observations, 8 rare events and 12 repeats of one event among routine lines | a rare event; plus one id for the whole context | the event is in the top 5; routine lines are under 50% of the context lines of the 8 questions |
| `test_time_window.py` | S4: a sighting every 3 minutes for a day | the window named in words; the window passed as `event_time_from`/`event_time_to` | the top 10 are all inside the hour |
| `test_two_hops.py` | S5: a person asks for an object, then the object is seen somewhere; 300 routine lines | a person (8 of 20) | the request and the placement are both in the top 5 |
| `test_unknown.py` | S6: 10 objects seen, 300 routine lines | an object never seen (6) | fewer than 10 records packed (`bundle.included_count`) |
| `test_determinism.py` | S8: the flood pack | a question | the same records and the same context when asked twice |
| `test_chat_store.py` | the chat of `chat_check.py` (4 sessions, 24 turns), written with `store()` | a question | every answer string in the top 3; no stale string above the answer; the unanswerable question packs fewer than 10 records |
| `test_chat_ingest.py` | the same chat through `ingest_batch()`, one call per session, its extraction replayed from `fixtures/chat_extraction.json` | a question | the store() chat's checks under the same ids; "first": the new job and the sister's new city are `results[0]`; "ledger": no claim the ledger marks ended ranks above one it marks current |
| `test_chat_ingest.py` (corrections) | the same chat, and the claims later turns correct (`ChatWorld.retire`, the key) | a key item (`retired-<tag>`); one id for the rest (`no-true-claim-retired`) | every claim of the key item ended retired; no claim outside the key ended retired, other than a copy merged into a repeat of itself |

### Results on 2026-11-11 (memory_mode as the read-time half: no typed extraction, no corrections)

| Group | Ids | Classic: passed / xfailed | address_first: passed / xpassed / xfailed / failed | Thesis finding |
|---|---|---|---|---|
| S1 state churn, 10 minutes apart | 18 | 4 / 14 | 4 / 13 / 1 / 0 | B |
| S1 state churn, 3 days apart | 18 | 13 / 5 | 13 / 4 / 1 / 0 | B |
| S1 churn stated (`--stated`), 10 minutes apart | 18 | 3 / 15 | 3 / 15 / 0 / 0 | B |
| S1 churn stated (`--stated`), 3 days apart | 18 | 9 / 9 | 9 / 9 / 0 / 0 | B |
| S2 flood, rare event in top 5 | 8 | 6 / 2 | 6 / 1 / 1 / 0 | D |
| S2 flood, routine share of context | 1 | 0 / 1 | 0 / 0 / 1 / 0 | D |
| S4 time window | 2 | 1 / 1 | 1 / 1 / 0 / 0 | 4.2 (no 2.2 letter) |
| S5 two hops | 8 | 0 / 8 | 0 / 0 / 8 / 0 | E |
| S6 unknown | 6 | 0 / 6 | 0 / 0 / 6 / 0 | D |
| S8 determinism | 3 | 3 / 0 | 3 / 0 / 0 / 0 | |
| Chat `store()`, answers in top 3 | 13 | 6 / 7 | 6 / 1 / 6 / 0 | A, B, E |
| Chat `store()`, no stale above the answer | 4 | 2 / 2 | 2 / 0 / 2 / 0 | B |
| Chat `store()`, unanswerable | 1 | 0 / 1 | 0 / 0 / 1 / 0 | D |
| Chat `ingest()`, answers in top 3 | 13 | 6 / 7 | 6 / 0 / 7 / 0 | A, B, C, E |
| Chat `ingest()`, no stale above the answer | 4 | 3 / 1 | 3 / 0 / 1 / 0 | B |
| Chat `ingest()`, unanswerable | 1 | 0 / 1 | 0 / 0 / 1 / 0 | D |
| Chat `ingest()`, answer first | 2 | 1 / 1 | 1 / 0 / 1 / 0 | B, E |
| Chat `ingest()`, ended never above current | 14 | 14 / 0 | 14 / 0 / 0 / 0 | |
| Chat `ingest()`, corrected claim retired | 7 | 1 / 6 | 1 / 0 / 6 / 0 | B |
| Chat `ingest()`, no true claim retired | 1 | 1 / 0 | 1 / 0 / 0 / 0 | |
| **Total** | **160** | **73 / 87** | **73 / 44 / 43 / 0** | |

The classic results were the same in every run, and the same on 0.17.0a6 (29a0460d), before address-first memory
existed. In the classic mode one key claim is retired with corrections off ("I'm vegetarian"), by the predicate
supersedence that `ingest()` already runs.

The address_first column is the mode as it stands: the read-time half, with the ledger kept where its claims
already stand and no second round. The ingest() chat replays a recording made with typed extraction and corrections
on, and neither mode turns them on now, so under address_first the typed fields are not read and the correction
labels retire nothing. Against the mode as first written (typed extraction, corrections and the second round in it,
the ledger leading; 72 / 57 / 31 / 0), the two hops (S5, 8 ids) and `chat-first-sister-lives` went from XPASS to
XFAIL and `flood-rare-stranger` from XFAIL to XPASS when the second round left and the ledger stopped leading, and
`chat-answer-sister`, `retired-longest-run-9km`, `retired-night-shifts` and `retired-nurse-st-marys` went from XPASS
to XFAIL when typed extraction and corrections left. No id fails under address_first only
(`XFAIL_INGEST_ADDRESS_FIRST` is empty): `chat-stale-pet`, listed while the ledger led the results, passes, and the
ingest() chat gave the same result on four runs.

`chat-first-work-now` was not deterministic under address_first until 2026-11-11: two claims of one turn share a
stated time, and the ledger broke the tie, and decided which of them an end closes, on node ids, which are new on
every build. The ledger now orders claims of one time by their text and lets an end close only what was stated
before it; the id passed on 5 of 5 fresh builds.

## How it is built

- `scenes.py`: the world types, the data builders (one per scene) and the case lists. Each builder seeds its own
  `random.Random(world.seed)` as the probe did, so the visible world writes the probe's exact sentences.
- `world.py`: the visible data, copied from the thesis scripts.
- `expected.py`: the ids that fail today: `XFAIL` for the robot scenes and the store() chat, `XFAIL_INGEST` for
  the ingest() chat, whose ids repeat the store() chat's, and `XFAIL_INGEST_ADDRESS_FIRST` for ingest() chat ids
  that fail only under address_first. Collection fails if any names an id that no case has.
- `fixtures/chat_extraction.json`: what `deepseek-v4.1-flash:cloud` returned through the local Ollama server
  (0.40.15, model digest e04da138) for the chat's four `ingest_batch()` calls, with the product defaults,
  `memory_mode="address_first"`, and typed extraction and corrections on (the mode turned both on then; the script
  now sets them outright), so the typed fields are in it; each call keeps the earlier claims its request showed. `fixtures/record_chat_extraction.py` made it, has the command and prints which claims
  ended retired against the key (`ChatWorld.retire`). The replay (`ExtractionReplay` in `harness.py`) answers the
  provider's calls from it and fails the pack on any call it cannot answer, and on a request that shows other
  earlier claims than the recorded one (the labels in the answer would then retire the wrong claims). Neither
  mode turns on typed extraction or corrections, so the typed fields and the correction labels are not asked for. A new recording is a new sample, so
  `XFAIL_INGEST` must then be decided again.
- `harness.py`: packs, fixtures and parametrization, shared with the hidden set. Its docstring says how the
  product defaults are kept (`tests/conftest.py` turns several off for other tests) and how the network is
  refused.
- Each pack is built once per session, on first use, and each question is asked once and its answer kept, so
  many ids read one retrieval. Robot packs are written with `ingest_fast_many` and `process_pending`; the chat is
  written with `store()`, as `chat_check.py` did.
- After a pack is written, the opportunistic organizer pass that the first `retrieve()` would start in the
  background is run inline, so later questions cannot race it and an id run alone (`-k`) gets the same answer
  as in the full run. On the chat pack the pass changed no result.

Differences from the thesis scripts: the robot scenes go through `ingest_fast_many` rather than `store()` (the
probe's path), and the flood holds 400 observations rather than 3,000, so the counts here are not the thesis's
counts. The store() chat counts match the thesis (6 of 13 answers in the top 3, the cat question packs every
record). The ingest() chat replays one recorded extraction; the thesis's live runs differed from run to run.

## The hidden set

`tests/contract_holdout/` runs the same test functions (each test file there imports them from here) on
different data: a second hand-written chat and a second seed with other objects, places, people and sentences.
It exists so that work tuned against this visible set can be checked on data it never saw. Whoever changes the
engine against the visible set should not read `tests/contract_holdout/world.py` or its per-id results; report
only its total of passed and xfailed ids. Its `expected.py` follows the same rules as this one.

## CI

`pytest tests/` collects both directories. The embedding model is loaded offline from the FastEmbed cache; on a
runner without the cache, the one-time model download is allowed (as for the other tests that use the default
model) and every pack is still built and asked with the network refused.
