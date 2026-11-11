# Recursive decisions by a small decision model (2026-11-10)

## Verdict

Passed: write. qwen3.5:9b retired 6 of 10 with zero wrong; in the post-hoc round clef-flash with address-scoped
candidates retired 5 of 10 with zero wrong, at 0.4 decisions and 57 ms mean per new fact.

Failed: read, on every arm and model. False unknowns ran 6 to 17 of 60 against a bar of 1, and judging every record
costs 1.3 to 3.7 s per question against a bar of 300 ms.

Carry forward: a second lookup behind the R4 gate (the only shape under 300 ms, and the only one that gains
two-hop questions, 0 to 6 or 7 of 8), and same-address retirement (loses nothing, under one decision per fact).

Open question: the judge calls a loosely answering record "none", which causes most false unknowns and the one
remaining wrong retirement. Recursion itself decided nothing here.

A small, falsifiable test of the owner's theory: a fast decision model, called recursively, can surface and test
the right memory when reading, and can decide how to store and retire memories when writing. The model only makes
closed choices. Nothing here changes `src/` or `tests/`.

Results: `results/REPORT.md` (tables) and `results/summary.json`; one directory per run under `results/`.
Findings: "What the numbers say" below.

## Setup

- Worktree `/Users/dwamianm/Sites/prism-decision-recursion`, branch `research-decision-recursion-2026-11` off
  `address-first-memory` at `fb050a50` (`src/` clean, no uncommitted change). Nothing committed.
- `tests/contract/` is untracked in the main checkout, so it was copied byte for byte into this worktree on
  2026-11-10 before any run. `tests/contract_holdout/` was not copied and not opened. The lead's checkout changed
  `scenes.py`, `world.py` and `harness.py` afterwards (a `retire` list and replay checks); the snapshot used here
  is the earlier one. SHA-256 of the snapshot: `world.py` 6eee1e7b…, `scenes.py` 3379e8c1…, `harness.py`
  93cf2f3a…, `fixtures/chat_extraction.json` d619c0e2…. On this snapshot the contract suite gives the README's
  classic numbers (59 passed, 57 xfailed).
- Ollama 0.40.15 at `http://127.0.0.1:11434`. Chat models: `qwen3.5:4b` (digest 96f38c74…, Q4_K_M) and
  `qwen3.5:9b` (c97eb11d…, Q4_K_M); thinking off, temperature 0, seed 7, one output token. Decision models (added
  at the lead's request after the first report): `clef-flash` (9.5B, mxfp8, MLX runner) and `clef` (27.5B), both
  with Ollama's "decision" capability, called through their native endpoint (below). Apple M5 Pro, 48 GB. No paid
  API.
- Every run starts with its model unloaded (`ollama stop`); the script loads it before timing starts and records
  the load time (`warm_ms`) and what was loaded (`loaded_before_warm`). The first qwen runs (archived in
  `results/archive-first-qwen-runs/`) did not unload first; their models were already loaded.

## Files

| File | What it is |
|---|---|
| `decide.py` | The one primitive, `decide(question, options) -> (index, confidence)`, with the `ollama` and `random` backends, the disk cache and the call log |
| `prompts.py` | Every decision prompt, frozen (hash checked at run time), and R2's content-word rule |
| `prompt_trials.py` | The one trial of each prompt on made-up cases, before the freeze |
| `write_answer_key.json` | Experiment W's answer key, written before any model ran on the facts |
| `write_causes_posthoc.json` | Post-hoc: which new fact really ends each key retirement (not a bar) |
| `read_experiment.py` | Experiment R (R0 to R3) |
| `write_experiment.py` | Experiment W (W0 to W2) |
| `report.py` | Tables, bars, option counts and the repeat check from the run directories |
| `run_all.sh` | Every arm, twice each from a cold cache, then the report |
| `prme_order_check.py` | Builds the `store()` chat pack twice and lists questions whose PRME order differs (not a decision experiment) |
| `analyze.py` | Probability thresholds, the exact pairs behind wrong retirements, and the R4 split, from the logs and cache only (no model call) |

## Commands

```sh
cd /Users/dwamianm/Sites/prism-decision-recursion
bash research/decision-recursion-2026-11/run_all.sh                 # random, qwen3.5:4b, qwen3.5:9b; two runs each
bash research/decision-recursion-2026-11/run_all.sh clef-flash clef # decision models (native endpoint)
uv run python research/decision-recursion-2026-11/prompt_trials.py --model qwen3.5:4b
uv run python research/decision-recursion-2026-11/prompt_trials.py --backend systemone --model clef-flash
uv run python research/decision-recursion-2026-11/prompt_trials.py --posthoc --model qwen3.5:9b
uv run python research/decision-recursion-2026-11/prme_order_check.py --out research/decision-recursion-2026-11/results/prme_order_check.json
uv run python research/decision-recursion-2026-11/analyze.py --results research/decision-recursion-2026-11/results
uv run python research/decision-recursion-2026-11/report.py --results research/decision-recursion-2026-11/results
```

One run directory is `results/<arm>-run<N>/` with `read.json`, `write.json`, `read_decisions.jsonl`,
`write_decisions.jsonl` (one line per decision: tag, prompt SHA-256, options, choice, confidence, milliseconds,
cached) and `cache/` (one file per prompt hash). Rerunning a script with `--cache-dir` pointing at an existing
`cache/` makes no model call and gives the same decisions.

## The primitive

`decide()` lists the options under letters and asks for one letter. The model generates one token with
`top_logprobs=20`; the choice is the option letter with the highest log probability, so an answer outside the
options cannot happen, and the confidence is that letter's probability renormalized over the option letters. If
no option letter is among the 20 most likely tokens, one retry constrains the output with an Ollama `format`
schema (`{"answer": <enum of letters>}`); that never happened in these runs (see the decision logs, `fallback`).

Why not a JSON string enum, the first thing tried: on a mechanics probe the schema `{"type": "string", "enum":
["yes", "partly", "no"]}` forces a leading quote the model rated at log probability -15, and after the forced
quote the choice flipped (unconstrained first token "yes" at p 0.85; constrained answer "no"). That probe used one
contract question ("What pet does Sam have?") and four chat records to test the API, before any prompt was
written; it shaped the output mechanism only, not any prompt's wording.

The `random` backend returns a seeded random option per prompt (a function of the seed and the prompt hash only)
through the same code. A decision with a single option makes no call and is not counted.

### The decision models' transport (`--backend systemone`)

Found from the server, not guessed. `ollama show clef-flash` lists the capabilities "decision" and "vision" and a
renderer named "clef"; the modelfile and `/api/show` say nothing about the request shape. `ollama run --help`
lists `--ask`, `--choice label="description"` and `--level` for decision models. Running
`ollama run clef-flash --ask ... --choice ...` with `OLLAMA_HOST` pointed at a logging relay showed the CLI posting
to `POST /v1/systemone`:

```json
{"model": "clef-flash", "state": "<the data>",
 "questions": {"q1": {"type": "choice", "instructions": "<the question>",
                      "criteria": {"yes": "yes", "partly": "partly", "no": "no"}}}}
```

and receiving `{"answers": {"q1": {"type": "choice", "choice": "no", "probabilities": {"yes": 0.014, "partly":
0.021, "no": 0.965}, "confidence": 0.84}}, "usage": {"input_tokens": 162, "output_tokens": 0}}`. `--ask` alone
uses `"type": "noul"` (a yes probability) and `--level` uses `"type": "score"`. `criteria` must be an object (a
list is refused) and `state` is required. No token is generated: the answer is a probability over the labels.
The server also answers a repeated identical request from a cache in about 3 ms, which is why every run unloads
the model first.

What changed from the chat transport, and nothing else: the frozen question text is split at its last blank line;
the part before it (question, records, statements) is `state` and the last paragraph (the question asked) is
`instructions`. The options are `criteria` with letters as labels and each option's text, unchanged, as its
description (the CLI's `billing="Payments and refunds"` form). The line "Answer with the letter of one option."
is not sent. The choice is the returned label, and the confidence is that label's probability (the response's own
`confidence` field is logged as `api_confidence`). The returned label was the most probable label in every call.

## Choices made where the brief left room

Read:
- R0 to R3 run on PRME's top 10 for the question. R1 does not show dates to the judge; R3 adds them. This keeps
  R3's contribution (dates and a newest check) separate from R1's.
- R2 picks among partly records not yet expanded; a single candidate needs no call. Its word options come from
  `prompts.content_words` (a fixed function-word list, words of the question removed, first 8 in order) plus
  "none". The follow-up query is the chosen word alone. Follow-up records already judged are not judged again.
  Follow-up retrieve time counts as added time.
- Because R2 runs only when some record is partly and none is yes, R2 can never change whether the answer is
  "unknown": a partly record already rules "unknown" out. Recursion here can only reorder.
- R3 "when two or more records are yes and disagree": disagreement is itself a decision ("Do these two records
  give conflicting answers to the question?"), asked between the current champion and each older yes record,
  newest first. On a conflict, "Which of these is true now?" with both dated and the current time; the loser goes
  below the partly records. Options are listed in R2's order, never by date.
- "Answer at rank 1" for a chat question with several answer strings (household restrictions, running spend):
  the rank-1 record holds at least one of them. "In top 3": every answer string in the top 3, the contract's
  check. Robot answers match whole sentences, as the contract does. Two hops: the answer is the object's
  placement; "both in top 5" is the contract's check.
- An arm that says "unknown" on an answerable question scores a miss on rank 1 and top 3, and no old-above-new.
- "Unknown said when it should be" (of 7) counts the cat question through `store()` and the 6 unseen objects;
  the cat through `ingest()` is shown apart. False unknowns are counted over all 60 answerable questions (13
  `store()` chat, 13 `ingest()` chat, 8 two hops, 18 churn at 10 minutes, 8 flood).
- Added milliseconds per question: the model time of every decision the arm needed, taken from the cache entry
  (so a prompt shared by two questions is charged to both, as it would be without the cache), plus follow-up
  retrieves. Calls run one at a time: a test with 10 parallel requests took as long as serial ones on this server.

Write:
- The fixture has no time field per fact; each fact gets its turn's time from `tests/contract/world.py`, matched
  by the turn's text. All other fields are the fixture's.
- A fact is shown to the model as `subject predicate object` (underscores as spaces), prefixed with "It is not
  true that" when its polarity is negative, with its date. The evidence quote is not shown.
- Entities match after lower-casing and dropping a leading "a", "an", "the" or "my". Facts from the new fact's
  own turn are never candidates (one utterance does not retire its own parts), in W1 and in W2.
- W2's "other live neighbors" of a retired fact use the W1 rule around the retired fact's subject. Depth 1 asks
  about neighbors of facts W1 retired; depth 2 about neighbors of facts retired at depth 1.
- The recording has one dinner fact (F10), not a fact and its copy; the greyhound idea is three facts (F18 to
  F20). The key has 10 retirements, one of them (F02, "planning meals for the week") ended only by time.

## Answer key (Experiment W)

`write_answer_key.json`, written by hand before any model ran on these facts: SHA-256
`790c905303167268f66d43e7af5c2ca10619c6f016d9da69c447975e45675b16`, 2026-11-11T05:30:38Z (10 November local).
`write_experiment.py` refuses to run if the hash changes. Retired: F01 (vegetarian), F02 (planning meals for the
week, time-scoped), F04 (Priya visiting), F08 (works at St. Mary's), F09 (night shifts), F10 (dinner at 6 am),
F14 (longest run 9 km), F18, F19, F20 (thinking about a dog, maybe a greyhound, not decided). Five live facts are
marked contestable (F02, F15, F25, F28, F33).

The lead later added its own `retire` list to the main checkout's `tests/contract/world.py`. Compared with this
key it has no F02, and its word match on "longest" would also cover F15 ("knee hurt after longest run"), which
this key keeps live as a past event.

## Prompt freeze

Each prompt was written once, tried on three made-up cases (none from `tests/contract`; expected answers written
first), and frozen without change: `prompts.py` hash
`fef7bf42e2247fd70efcbceaca9b50d78037972c5c43ace2ae1daaf919f33d74`, checked at run time. Trial results
(`results/prompt_trials_*.json`):

| Prompt | qwen3.5:4b | qwen3.5:9b | What went wrong |
|---|---|---|---|
| R1.judge | 2 of 3 | 2 of 3 | both said "no" for a record that partly answers ("Yusuf hosted a party" for "What did Yusuf cook?") |
| R2.pick_record | 3 of 3 | 3 of 3 | |
| R2.pick_word | 3 of 3 | 3 of 3 | |
| R3.conflict | 3 of 3 | 1 of 3 | 9b called two conflicting places "both can be true" |
| R3.true_now | 2 of 3 | 2 of 3 | 4b picked the older bakery hours; 9b the older city |
| W1.still_true | 1 of 3 | 1 of 3 | "cannot tell" for an unrelated statement; 4b retired "Mira kin of I" when Mira moved |
| W2.dependent | 1 of 3 | 0 of 3 | mostly "cannot tell" |
| Total | 15 of 21 | 12 of 21 | |

The trial already showed the two failures that decide the result below: the judge does not say "partly", and the
write prompts do not separate "unrelated, still true" from "no longer true".

The same frozen prompts through the decision transport (`results/prompt_trials_clef-flash.json`): clef-flash 17 of
21. It too chose "no" for the partly case, and picked the older record in two of the three "true now" cases.

## Post-hoc arms (R2b, R4, W2b)

Designed by the lead after the first report, because R2 and W2 made recursion depend on the answer "partly", which
no model gave, so recursion was never tested. Same pass bars. Their prompts are in `prompts.py`
(`POSTHOC_TEMPLATES`), written once, tried once on three made-up cases each and frozen under their own hash
`27b76bc6245bb9173a79c504a5c5edb4fe9f803dd60ab9522eafc479baf2f5a1`; the original prompts and their hash are
unchanged. Trial: qwen3.5:9b 6 of 9 (it chose "none" for the dependency of "I wakes up at 3 am" on the night
bakery, and "cannot tell" twice on the recheck), clef-flash 8 of 9 (the same dependency miss).

- **R2b, recursion without "partly".** After R1, while no record is yes and fewer than 3 follow-ups ran: "Which
  record is most likely to lead to the answer?" over every judged record not picked before (at most 25) plus
  "none of them"; then a content word as in R2; retrieve it; judge the new top 5 with R1's prompt. "none of
  them" stops. A word choice of "none" uses up that step without a retrieve (the brief did not say; stopping
  there would have made R2b end on a word choice it did not name). "Unknown" when no record ends up yes, so a
  partly record no longer rules "unknown" out in this arm.
- **R4, a cheap gate.** Judge PRME's rank 1, then 2, then 3, stopping at the first yes; that record goes first and
  the rest keep PRME's order. If none of the top 3 is yes, R4 is R2b in full (R1 on all 10, then the recursion),
  and is charged for all of it. The gate's judgments are R1's (same prompts), so R4 makes no call of its own; it is
  charged only for the judgments it needs.
- **W2b, recursion by dependency.** After W1 for a new fact, and only when W1 retired something: for each
  candidate W1 answered "cannot tell", "Which earlier statement, if any, does this one depend on?" over its
  neighbors among the facts live before this step, stated no later than it, not from the new fact's turn (at most
  the 25 most recent), plus "none". If the chosen statement was retired in this step, W1's question is asked
  again with that statement and the new one shown (`W2b.recheck`). Depth 2.

## PRME's own order changes between two builds of the same pack

Found while checking whether the runs repeat. The model's choices repeat exactly (same prompt, same choice, in
every arm), but PRME gave a different order on some questions after a fresh build of the same pack, so later
prompts differed. `prme_order_check.py` builds the `store()` chat twice: 3 of 14 questions differ
(`results/prme_order_check.json`). In every case the reordered records have exactly the same composite score,
0.6 times the score of a hit from the same session (`session_context_rank_fusion_score_decay`), and each tied
group is in ascending node-id order. The sort is `src/prme/retrieval/session_context.py:280`,
`(-composite_score, str(node.id))`, and node ids are random for each build. Within one pack the order is stable
(the contract's determinism check passes). It moved one R2b result between runs: the follow-up search for
"gluten" returned a different top 5, and in one build the rice-noodle record was not in it.

## What the numbers say

Full tables: `results/REPORT.md`; thresholds, wrong pairs and the R4 split: `results/analysis.txt` and
`results/analysis.json`. Numbers are run1; run2 repeated every decision exactly except where PRME's own order
changed (see above).

### Read

| | store rank 1 /13 | store top 3 | ingest rank 1 | hops rank 1 | hops both in top 5 | churn newest first /18 | unknown right /7 | false unknown /60 | median added ms | READ |
|---|---|---|---|---|---|---|---|---|---|---|
| R0, PRME | 5 | 6 | 7 | 0 | 0 | 4 | 0 | 0 | 0 | |
| qwen3.5:9b R1 | 8 | 8 | 7 | 0 | 0 | 4 | 7 | 8 | 1354 | fail |
| qwen3.5:9b R3 | 9 | 8 | 7 | 0 | 0 | 14 | 7 | 8 | 1411 | fail |
| qwen3.5:9b R2b | 9 | 9 | 8 | 0 | 0 | 4 | 7 | 6 | 1376 | fail |
| qwen3.5:9b R4 | 9 | 9 | 8 | 7 | 7 | 4 | 7 | 6 | 137 | fail |
| clef-flash R1 | 7 | 8 | 6 | 0 | 0 | 4 | 7 | 16 | 1268 | fail |
| clef-flash R2b | 8 | 9 | 8 | 6 | 6 | 4 | 7 | 6 | 1357 | fail |
| clef-flash R4 | 8 | 8 | 8 | 6 | 6 | 4 | 7 | 6 | 166 | fail |
| clef R1 | 8 | 8 | 8 | 0 | 0 | 4 | 7 | 17 | 3745 | fail |
| clef R2b | 8 | 8 | 8 | 7 | 8 | 4 | 7 | 10 | 3974 | fail |
| clef R4 | 8 | 7 | 8 | 7 | 8 | 4 | 7 | 10 | 542 | fail |
| random R1 | 2 | 3 | 3 | 0 | 0 | 0 | 1 | 1 | 0 | fail |

- **No arm passes on any model.** Unknown is right 7 of 7 for every real model; the bar that fails is false
  unknowns: at most 1 allowed, 6 to 17 happen. The second failure is latency: one call per record for ten records
  costs 1.3 to 3.7 s. Only the cheap gate R4 meets 300 ms (137 ms for qwen3.5:9b, 166 ms for clef-flash), and it
  still says "unknown" on 6 of 60 answerable questions.
- **Recursion adds little.** R1 against R2b on the chat: 8 to 9 (qwen3.5:9b), 7 to 8 (clef-flash), 8 to 8 (clef).
  Its clear gain is two hops, and only for the clef models: R1 finds 0 of 8 for every model, and R2b finds 6
  (clef-flash) or 7 (clef, with both records in the top 5 for all 8); qwen3.5:9b stays at 0 because it again says
  no to the request record. R4 keeps that gain at a seventh of the cost because the gate usually stops at rank 1.
- **The "newest first" gains come from R3's dated check**, not from recursion: churn 4 of 18 to 14 (qwen3.5:9b),
  4 to 13 (clef). R2b loses that gain again because the follow-up search puts the newest placement in play.
- **The models disagree on "partly".** qwen3.5:9b never chose it (0 of 680); clef-flash 1 of 680; clef 32 of 1081
  and so R2 ran 4 times there. R2b was designed so that recursion does not depend on it.

### Thresholds (no new calls, from the logged probabilities)

Write, retire when p("no longer true") >= t (replayed through W1; prompts the new rule would have asked but the
run never asked cannot be answered from the cache, so they count as "still true"; the count is given):

| t | clef-flash right / wrong | clef right / wrong | qwen3.5:9b right / wrong |
|---|---|---|---|
| model's own choice | 9 / 3 | 9 / 4 | 6 / 0 |
| 0.6 | 6 / 2 | 7 / 1 | 4 / 13 |
| 0.7 | 6 / 1 | 5 / 1 | 4 / 12 |
| 0.8 | 4 / 1 | 4 / 1 | 2 / 8 |
| 0.9 | 1 / 1 | 3 / 0 | 2 / 4 |

A threshold does not separate right from wrong: clef-flash's three wrong retirements sat at p 0.45, 0.90 and 0.62,
so 0.7 removes two of them and costs 3 of 9 right ones; its worst error (the knee) is the most confident one.
qwen3.5:9b's sweep is unreliable: only 37 to 46 of its 165 to 277 judgments have all three option letters among
the five tokens the chat transport logs.

Read, a record counts as answering only when p(yes) >= p:

| p | clef-flash unknown right / false unknown | clef | qwen3.5:9b (39 of 68 questions usable) |
|---|---|---|---|
| 0.4 | 7 / 16 | 7 / 17 | 7 / 5 |
| 0.6 | 7 / 17 | 7 / 19 | 7 / 5 |
| 0.8 | 7 / 25 | 7 / 20 | 7 / 11 |
| 0.9 | 7 / 37 | 7 / 22 | 7 / 17 |

Raising the bar only creates more false unknowns; the bar is already met at 0.4, so the judge's problem is not low
confidence, it is calling a record "no" that does answer.

### R4's split

| model | stopped at rank 1 | stopped at rank 2 or 3 | went to R2b | median ms |
|---|---|---|---|---|
| clef-flash | 37 questions, 138 ms | 2, 288 to 408 ms | 29, 1870 ms | |
| clef | 35, 369 ms | 3, 875 to 1229 ms | 30, 6108 ms | |
| qwen3.5:9b | 46, 134 ms | 1, 413 ms | 21, 1586 ms | |

### Write

| | retired correctly /10 | retired wrongly | WRITE |
|---|---|---|---|
| W0 (nothing retired) | 0 | 0 | fail |
| qwen3.5:9b W1 | 6 | 0 | pass |
| qwen3.5:9b W2 | 6 | 0 | pass |
| qwen3.5:9b W2b | 6 | 0 | pass |
| clef-flash W1 | 9 | 3 (F07, F15, F25) | fail |
| clef-flash W2 | 9 | 3 | fail |
| clef-flash W2b | 9 | 3 | fail |
| clef W1 | 9 | 4 (F21, F22, F25, F26) | fail |
| clef W2 | 10 | 7 | fail |
| clef W2b | 9 | 4 | fail |
| random W1 / W2 / W2b | 8 | 15 | fail |

- qwen3.5:9b passes by saying "cannot tell" (771 of 822 W1 answers): it misses 4 of the key's retirements and
  loses nothing.
- clef and clef-flash reach 9 or 10 of 10 but pay in true facts. clef's W2 retires 10 of 10 correctly and 7
  wrongly: recursion moved right retirements onto the right reasons (8 of 10 for the right reason against 7 of 9
  at W1) and added wrong ones at the same time.
- W2b added nothing over W1 for clef-flash and qwen3.5:9b, and cost clef one more wrong fact than W1 (4 like W1,
  but W1's 4 are the same four; the dependency step changed nothing).
- The failure behind every model's wrong retirements is the same shape: two statements that merely share the
  subject "I" (the extraction makes nearly every claim about "I"). clef-flash retired "I knows what a cup is"
  because "I bought Brooks Ghost" arrived; clef retired "I quit hospital" and "I starts at pediatric clinic"
  because "I eats fish" arrived.

## Round 2 (post-hoc): W3, W3b and R5

Designed by the lead after the first two rounds to test the diagnosis that the judge is fine and the pairs it is
given are wrong. Prompts `W3b.topic` and `R5.any_answer` are new (frozen hash
`f7bb50dece39d9674e963600c9265b1b7491260b84b4cc5873086f397564a47f`, tried once on six made-up cases: clef-flash
5 of 6, it called a partly-answering record "none of them"). clef-flash and the random judge only. Results from
the clean cold run (`results/round2-clef-flash-cold/`), which reproduced the first run's numbers exactly (0 of 147
shared prompts changed choice).

### W3 and W3b

| | retired correctly /10 | retired wrongly | calls per new fact (mean / max) | ms per new fact (mean / p95) | WRITE |
|---|---|---|---|---|---|
| W3, clef-flash | 5 (F01, F08, F09, F18, F19) | 0 | 0.4 / 3 | 57 / 282 | pass |
| W3b, clef-flash | 6 (+F14) | 1 (F15) | 0.9 / 3 | 156 / 428 | fail |
| W3, random | 4 | 3 | 0.35 / 3 | 0 | fail |
| W3b, random | 5 | 8 | 0.98 / 3 | 0 | fail |

Scoping does what it was built to do: F07 (retired because "I bought Brooks Ghost" arrived) and F25 (because "I
will include fish" arrived) are gone, and no wrong retirement is left in W3. But it does not keep the nine right
ones. It keeps five (six with W3b), and the five it misses are the retirements whose correcting statement lives
at another address: F02 (time-scoped in the key anyway), F04 "Priya visiting I" against "Priya moved to Toronto"
(attribute *visit* against *move*), F10 "dinner at 6 am" against the job change (attribute *dinner_time* against
*quit*), and F20 "not decided" against "we adopted" (different subject, "We"). The one that should have matched
and did not is F14 "longest run | distance | 9 km" against "Longest run | is | 19 km": with copulas removed the
new predicate carries no attribute word at all, so the rule fell back to predicate equality. W3b recovers F14.

F15 is the only wrong retirement left, and it enters through W3b's topic question, not through the address rule:
the model picked "knee condition fine this time" as "about the same thing" as "knee hurt after longest run", then
said "no longer true" at p 0.90 (p = [0.042, 0.904, 0.054]).

What F15 should be in the key: retired or live is a judgment about what a memory should hold, and the key keeps it
live, which I still think is right. The recorded fact is a past event at a past time ("my knee hurt a bit after
it", the 9 km run); the later statement is about a different run, and the chat never says the earlier hurt did not
happen. Retiring it makes the store assert the knee never hurt, which is not in the text. If the fact had been
read as a state ("knee is hurt"), retiring it would be right; the extraction chose `hurt_after`, an event. The key
stays as written.

### R5

| | store r1 /13 | store top3 | ingest r1 | hops r1 | hops both in top 5 | churn newest first /18 | flood in top 5 | unknown right /7 | false unknown /60 | median added ms | READ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R0, PRME | 5 | 6 | 7 | 0 | 0 | 4 | 6 | 0 | 0 | 0 | |
| R2b, clef-flash (round 1) | 8 | 9 | 8 | 6 | 6 | 4 | 6 | 7 | 6 | 1357 | fail |
| R5, clef-flash | 6 | 6 | 7 | 3 | 3 | 3 | 6 | 7 | 8 | 187 | fail |
| R5, random | 1 | 4 | 3 | 0 | 0 | 1 | 2 | 0 | 5 | 0 | fail |

Stale above the answer for R5 on clef-flash: 3 of 13 questions through `store()`, 2 of 13 through `ingest()`; the
ten-judgment R1 had 2 and 1. Where the questions stopped: 45 answered in round 1 (median 182 ms), 7 answered after
the follow-up (819 ms), 5 said "none of them" twice (671 ms), and 11 ended at "no record or word picked" (371 ms):
the single question said "none of them", the pick step then chose none, so round 2 never answered. The brief said
"unknown only when both rounds say none of them"; with no round-2 answer that condition is not met, and this arm
calls it unknown. Four of those 11 are answerable and seven are the questions whose answer is "nothing" (the cat
and the six unseen objects). Counting them the other way, as a lookup failure rather than "unknown", leaves 4 false
unknowns but drops "unknown right" to 0 of 7, since the 7 correct unknowns all came through that path. The bar
fails either way.

The false unknowns, by question: chat `store()` pet ("What pet does Sam have?") and sister; chat `ingest()` pet
and sister; `hops-zoe`; two churn questions (`churn-10min-2moves-house-keys`, `churn-10min-12moves-passport-folder`)
where the newest placement was not the record the question named; and `flood-rare-broken` ("Did anything get
broken?"). They are the same kinds as before: the single question says "none of them" when the answer is a record
that does not literally answer the question.

### Latency per decision (this machine, model loaded before timing, one call at a time)

| model | read median / p95 ms | write median / p95 ms | load before timing (warm_ms) |
|---|---|---|---|
| qwen3.5:4b (archived run) | 97 / 119 | 106 / 139 | already loaded |
| qwen3.5:9b | 150 / 181 | 162 / 252 | 6 to 7 s unloaded each run |
| clef-flash | 127 / 233 | 169 / 217 | 6 to 7 s unloaded each run |
| clef (27.5B) | 365 / 557 | 511 / 646 | 7.9 s unloaded |

A full run (read and write) takes 28 s (random), about 3 to 4 minutes (qwen3.5:9b, clef-flash) and about 7
minutes (clef), all inside the 15-minute budget. clef ran once, not twice: its first run alone is the whole
budget, and the lead's list asked for it resumed, not repeated.

### Verdict

The single pass carried everything that worked, with one exception: on two hops and on "newest first", the gain
comes from a second step (a follow-up search in R2b, a dated newest check in R3), and both are affordable only
inside the R4 gate. Recursion as the brief defined it (R2 on "partly", W2 on retirement) added nothing on these
models: R2 fired 0 times for two of three models, and W2 added wrong retirements faster than right ones except
for clef. The read bar is not reachable by any arm that judges every record, on any model tested, because one
call costs 130 to 500 ms and ten are needed; R4 is the only shape that meets the latency bar, and it fails on
false unknowns instead. The write bar is reachable by a cautious judge (qwen3.5:9b) or by a more accurate one
that accepts a few losses (clef and clef-flash, 9 of 10 with 3 to 4 true facts lost).
