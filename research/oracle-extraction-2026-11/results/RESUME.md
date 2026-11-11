# Task 10 (round 2): state of the work (2026-11-11)

Branch `fact-fields` in `/Users/dwamianm/Sites/prism-fields`. Nothing was pushed by this session. The round 2
result is in `round2/REPORT.md`; this file records how the work went and how to rerun it.

## Finished

**10a, the mention rule, in the engine** (`src/prme/ingestion/assertions.py`, `src/prme/storage/engine.py`,
`src/prme/ingestion/entity_merge.py`, `src/prme/ingestion/pipeline.py`). A subject or object is accepted when the
content mentions it (`text`); when it is the source's `speaker` and the content says I, me, my, mine, myself, we,
us or our (`speaker`; it reuses `grounding.mentioned_or_spoken` for the singular forms); or when it names an entity
with the same name and type that the owner's memory already holds in the source's scope (`memory`, looked up with
`EntityMerger.find_entity`, the scan `find_or_create_entity` already used, now shared). A literal value must be in
the content, unless it repeats the value of a live claim (tentative or stable) at the same subject and attribute
(`closure`). Refusals keep their index and reason. The engine reads the graph only for the ends the content and
the speaker do not settle (`read_held`); admission is now `async`.

What the stored event records: each assertion is stored with `found`, how its subject and its object were found.
The in-pipeline check (`_structured_record`, through `stored_assertions`) and `reingest()` check `text` and
`speaker` again from the content and the stored speaker, and take `memory` and `closure` as recorded, since they
were read from the graph at admission. A rebuild from the log alone therefore accepts exactly what admission
accepted, whatever the graph holds by then. `_structured_record` no longer runs the text grounding on a stated
source (an end found in memory is not in the text by design); the assertion check replaces it, and a mismatch
fails the source.

**10b, time.** Optional `time` on an assertion: an ISO date (`2026-05-04`) or an ISO datetime with its offset
(`2026-05-04T09:30:00-06:00`); a datetime without an offset is refused. It travels as the fact's `temporal_ref`, so
the pipeline's resolver gives the claim `resolved_date`, `event_time` and `valid_from`, as for an extracted date.
Without it the claim takes the source's time. `ASSERTION_VERSION` is 2 (stored version 1 is no longer read; the
feature is unreleased).

**10c, the claim's text.** A stated claim's text is a sentence rendered from its fields (`claim_text`): present
tense for current ("Dana works at Brightpath as analytics manager (since 2026-05-04)."), "used to" for past and
superseded ("Dana used to work at Northwind."), "is going to" for planned, the simple past for done, "does not",
"is not", "did not" or "was not" for negative polarity, and "<subject>'s <attribute> is <value>" for `has`. The
turn's own text stays the stored message and the claim's `evidence_quote`. `render_content` (content omitted) uses
the same sentences. `_PHRASES` was replaced by `_FORMS`.

- Claim merge: its key reads node type, epistemic and source type, predicate, object, polarity, `resolved_date`
  and the object entity, not the text, so the rendered text changes no merge. `time` is in the key through
  `resolved_date`: the same fact with another time, or without one, stays separate. Status and role are not in
  the key, so an O2 closure (past) of a current claim at the same value merges into it, as in round 1.
- `fold_repeated_text`: a record folds only when its text lies inside another record of the same source. A
  rendered claim is not inside its turn's text, so the claim and its turn's note are now both packed (checked on a
  one-turn pack: two lines, the claim with its own date).

**Docs and tests.** `docs/RELATIONS.md` (stated facts), `CHANGELOG.md`, `tests/contract/README.md`.
`tests/test_structured_assertions.py`: the speaker, memory and closure rules, the scope and owner refusals, a
rebuild from the log alone into an empty pack, `reingest()` with no model, a recorded verdict that no longer
holds, `time`, and the claim text of every status.

**The harness** (`research/oracle-extraction-2026-11/`): the round 1 run-time patch is gone. `check_facts.py`
gives each assertion to the engine's own `prepare_assertions`, turn by turn, with the probe's speaker and the
memory earlier accepted assertions built. `oracle.py` asks the engine about each assertion against the pack
(`_prepare_assertions`) and records whether the check agreed with the build. `union_ranks.py` (extra measure, see
below). `run_oracle.py --sentence-text` (the diagnostic arm B1s).

## Checks that passed (summary lines)

- `tests/test_structured_assertions.py`: `66 passed, 14 skipped` (the skips are PostgreSQL).
- Targeted suites (structured assertions, assertion aggregation and state, belief state, claim merge, derivation
  ingestion, durable ingestion, entity references and resolution, extraction grounding, group members, ingest
  names and claims, ingested corrections, mention links, owner carry and name carry, reingest, relation routing,
  relationship claims, remention, speaker grounding and references, historical ingestion, address-first repairs):
  `658 passed, 252 skipped in 134.17s`.
- Model extraction unchanged: `tests/test_derivation_version.py` from outside the checkout: `2 passed`. The M0 and
  M1 arms rebuilt from the af-off-1111 cache (`fx-M0`, `fx-M1`, 0 calls) give the round 1 probe rows, graph and
  summary on defects-v1 and member-v1. On chat-v1 the graph and summary are the same and only `record_types` of
  three contexts differ by one fact against one note; two more rebuilds on the same tree alternate between the two
  values (`fx-M0-2` equals round 1 exactly), so that is rebuild noise.
- Contract scorecard, visible set, `--stated`: classic `80 passed, 80 xfailed`; address_first `80 passed,
  37 xpassed, 43 xfailed, 0 failed`. The stated churn test now accepts the newest placement's claim (its rendered
  text) or its sentence first; in classic 8 stated ids left `XFAIL` and `stated-10min-5moves-wool-scarf` joined it,
  in three runs that agreed (`tests/contract/expected.py`).
- Hidden set, totals only: address_first `82 passed, 42 xfailed, 38 xpassed`, as before; classic `7 failed,
  79 passed, 76 xfailed`. **Open:** the 7 are strict results in its stated churn group, and its expected list needs
  deciding again by someone allowed to read its per-id results. This session did not read them.

## Round 2 arms (10d), first run, built before the restart

Facts: commit fb1f103d. All written facts load: defects 31 of 31, chat 57 of 57 (53 at O1), member 23 of 23; the
checker agreed with every build. Accepted by the turn's text alone, needing the speaker, memory, closure: defects
9, 0, 22, 0; chat 25, 0, 32, 1; member 0, 8, 15, 0. 0 model calls on every build.

| Probe | M0 | M1 | A0 | A1 | B0 | B1 | Bar (B1 top 3) | Result |
|---|---|---|---|---|---|---|---|---|
| defects-v1 | 7/7 / 7/7 | 7/7 / 7/7 | (= B) | (= B) | 4/7 / 5/7 | 5/7 / 5/7 | 7 | fail |
| chat-v1 | 14/18 / 15/18 | 15/18 / 16/18 | 8/18 / 11/18 | 12/18 / 13/18 | 7/18 / 11/18 | 12/18 / 13/18 | 17 | fail |
| member-v1 | 10/12 / 11/12 | 11/12 / 12/12 | (= B) | (= B) | 8/12 / 9/12 | 9/12 / 9/12 | 11 | fail |

Round 1 B1 was 6/7, 13/18 and 10/12. Round 2 gains the job title (7 to 1), coffee (9 to 1), Ivy's birth (not found
to 1, through `time`) and the member's trip (6 to 1), and loses list answers and answers in the turn's words: a
rendered claim says one fact, and the probe needs every answer string in one record (the cities 5 to 25, Go and
TypeScript 1 to 18, the July plan 1 to 22, the dependents 2 to 7 and 8), and catalog questions where many short
"Dana ..." claims crowd out the turn that holds the answer (start date 22, niece 7, race time 10). The router
misses remain (the dog routed to `involved_in`, rank 21; the cities stored as `decided`).

Extra measure (`union_ranks.py`, not the guide's bar): the first k at which the top k records together hold every
answer string. B1: defects 6/7 in the top 3, chat 12/18, member 11/12. A fresh B1 rebuild (`fx-B1r`) repeats every
answer rank except one chat question, race time, at 10 and then 8 (tied claims ordered by node id; outside the top
5 both times).

Tables: `results/round2/tables.md`; per-question ranks in `results/round2/union-<probe>.json`; receipts and top 5
in `results/round2/serve-<probe>-B1.json`; each arm's report, build and oracle record in `results/round2/arms/`.

## After the restart

- The diagnostic arm `fx-B1s` was built on the three probes (0 model calls; the check agreed with every build), with
  `serve_paths.py` and `union_ranks.py` on it. `first_ranks.py` (new) records where each answer string first ranks
  for every miss (`round2/first-ranks.txt`). `report_tables.py` adds the B1 against B1s table.
- B1s top 3: defects 6/7, chat 14/18, member 12/12 (B1: 5, 12, 9). `round2/REPORT.md` has the per-question table
  and which misses are read side and which are form.
- Full unit suite on this revision: 1 failed, 7464 passed, 1644 skipped, 2 xfailed. The one failure is
  `tests/test_checkout.py` (it expects a plain clone, not a worktree); `round2/REPORT.md`, "Engine checks on this
  tree", explains it.

## Commands to rerun

From `/Users/dwamianm/Sites/prism-fields`, with providers blocked:

```sh
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=

# Full unit suite (about 35 minutes; run nothing heavy beside it).
uv run pytest tests -q -p no:cacheprovider --ignore=tests/contract --ignore=tests/contract_holdout

# Model extraction unchanged, from outside the checkout.
cd /tmp && uv run --project /Users/dwamianm/Sites/prism-fields pytest \
  /Users/dwamianm/Sites/prism-fields/tests/test_derivation_version.py -q --rootdir /Users/dwamianm/Sites/prism-fields
cd /Users/dwamianm/Sites/prism-fields

# Contract scorecard, both modes, and the hidden set (totals only).
uv run pytest tests/contract -q --stated
uv run pytest tests/contract -q --stated --memory-mode address_first
uv run pytest tests/contract_holdout -q --stated
uv run pytest tests/contract_holdout -q --stated --memory-mode address_first

# The six arms of round 2 (M arms replay the af-off-1111 cache; PRME_BENCH_CACHE_ONLY is set by the script).
research/oracle-extraction-2026-11/run_arms.sh fx research/oracle-extraction-2026-11/results/round2/logs
for n in defects-v1 chat-v1 member-v1; do
  uv run python research/oracle-extraction-2026-11/serve_paths.py $n fx-B1 \
    --json research/oracle-extraction-2026-11/results/round2/serve-$n-B1.json
  uv run python research/oracle-extraction-2026-11/union_ranks.py $n fx-M0 fx-M1 ox-B1 fx-B0 fx-B1 \
    --json research/oracle-extraction-2026-11/results/round2/union-$n.json
done
uv run python research/oracle-extraction-2026-11/report_tables.py fx results/round2 \
  > research/oracle-extraction-2026-11/results/round2/tables.md

# The diagnostic arm B1s on each probe (member-v1 also takes --speaker --set enable_speaker_grounding=true),
# then its serve paths and union ranks (this rewrites union-<probe>.json with fx-B1s), the first rank of each
# answer string (round2/first-ranks.txt), and the tables again.
uv run python research/oracle-extraction-2026-11/run_oracle.py defects-v1 fx-B1s --levels O1,O2 \
  --set 'memory_mode="address_first"' --sentence-text --replace
uv run python research/oracle-extraction-2026-11/serve_paths.py defects-v1 fx-B1s \
  --json research/oracle-extraction-2026-11/results/round2/serve-defects-v1-B1s.json
uv run python research/oracle-extraction-2026-11/union_ranks.py defects-v1 fx-M0 fx-M1 ox-B1 fx-B0 fx-B1 fx-B1s \
  --json research/oracle-extraction-2026-11/results/round2/union-defects-v1.json
uv run python research/oracle-extraction-2026-11/first_ranks.py defects-v1 fx-B1 fx-B1s --ids cities work-languages
```

## The nine assistant-turn "your" facts in member-v1

There is no "you" or "your means the owner" rule in the engine. The probe build passes a speaker only on user
turns, so an assistant turn has no speaker, and the speaker rule cannot apply there. All 13 assistant-turn facts of
member-v1 (the nine plan facts among them) are admitted by the memory rule: their subject, Maya Okafor, names the
person entity that earlier user-turn facts created through the speaker rule. From the stored events of `fx-B1`:
assistant turns, subject found in memory, 13; user turns with the speaker, subject found as the speaker, 8, and in
memory, 2. Because the engine never reads "you", a conversation with more than one human participant is not
handled specially: the engine checks only that the named subject is an entity the owner holds, and which person an
assistant's "your" points at is the caller's decision when it writes the subject.
