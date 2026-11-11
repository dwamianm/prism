# Address-first memory on the mid tier and the fast probes (2026-11-11)

`memory_mode="address_first"` against the defaults, on the first tests that can see the belief ledger, typed
extraction and extraction corrections on real data: the mid tier (all of LoCoMo and 12 LongMemEval-S questions,
built through `ingest()`, then the offline evidence gate) and the defect, chat and member probes. The thesis is
`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`.

**Verdict.** The switch does not pass the alpha rule. On the mid tier, LoCoMo evidence in the top 25 falls 3.1 points
with both 95% intervals entirely below zero, a loss under the mid tier's rule, while evidence recall (+0.8) and
projected accuracy (+0.6) rise clear of zero. On the probes, the defect probes put 4 and then 5 of 7 answers in the
top 3 (the rule needs 7 of 7), the chat probe 10 and then 11 of 18 against 14, and the member probe misses one
measure on each of two builds. Run alone on the defaults' extraction, relation routing helps every probe, the
belief ledger loses two defect probes, the second round loses on the chat and member probes, and the other four
options change nothing.

## What was run

- Code: branch `address-first-memory` at `3ea8ff8b` (engine source tree `5fc5ac9e`), clean tree. Both sides ran
  this code; the baseline with the defaults, whose retrieval and extraction are those of 0.17.0a6 (the contract
  scorecard gives the same result on every id on both).
- Model: `deepseek-v4.1-flash:cloud` through the local Ollama server, version 0.40.16, manifest digest
  `e04da138d31e`. No paid provider was called.
- No earlier main build could be reused: the newest saved one, `main-41005d66`, is of a source tree 60 files away
  from `29a0460d`.

```sh
uv run python -m benchmarks.mid_tier run --label main-0.17.0a6 --out data/mid-tier-v1/reports/main-0.17.0a6
uv run python -m benchmarks.mid_tier run --label address-first --out data/mid-tier-v1/reports/address-first \
  --set 'memory_mode="address_first"'
uv run python -m benchmarks.mid_tier compare data/mid-tier-v1/reports/main-0.17.0a6 \
  data/mid-tier-v1/reports/address-first --output data/mid-tier-v1/reports/address-first-vs-main.json
```

`--set memory_mode` reaches every option of the mode through the harness: `gate_config` builds `PRMEConfig` from
the settings, so the mode's validator runs. Checked before the run, `gate_config(pack, {"memory_mode":
"address_first"})` turns on typed extraction, relation routing, the belief ledger, extraction corrections (batch
target 2,048), `session_context_direct_first=10`, the clock window filter, identical text collapse, the second
round and `rrf_recency_order`. The harness keeps `enable_pronoun_repair` off on both sides, as for every build.

## Mid tier

| | Baseline (defaults) | address_first |
|---|---|---|
| Extraction | warm: 0 live calls, 1,186 cache hits | cold: 1,180 live calls, 11.06 M prompt and 3.26 M completion tokens |
| Build LoCoMo / LongMemEval-S | 34 min 33 s / 72 min 44 s | 35 min 23 s / 59 min 50 s |
| Gate LoCoMo / LongMemEval-S | 5 min 06 s / 10 s | 5 min 15 s / 10 s |
| Whole run | 72 min 54 s | 60 min 00 s |

The baseline replayed an earlier cold build's cache; the switch changes the extraction request (the typed fields
and the earlier claims corrections show), so its side is a new cold build. Two cold builds of the same code differed
by about a point on LoCoMo (BENCHMARKS.md, "Mid tier", model noise), so a LoCoMo difference of about a point is not
distinguishable from rebuilding. The warm baseline took 73 minutes, against 11 minutes recorded on 2026-10-19 for a
warm run, with the load average at 1.6 to 12; that was not investigated.

### LoCoMo, 1,536 questions with evidence

| Measure | Baseline | address_first | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 84.4% | +0.8 | -0.1 to +1.7 | +0.0 to +1.5 | 29 / 17 / 1490 | no |
| evidence recall | 89.2% | 89.9% | +0.8 | +0.2 to +1.4 | +0.3 to +1.3 | 49 / 27 / 1460 | yes |
| evidence in top 25 | 70.4% | 67.3% | -3.1 | -4.2 to -1.9 | -4.5 to -1.6 | 29 / 90 / 1417 | yes (a loss) |
| all evidence returned | 98.0% | 97.9% | -0.1 | -0.5 to +0.2 | -0.5 to +0.2 | 2 / 4 / 1530 | no |
| projected accuracy | 77.4% | 78.0% | +0.6 | +0.0 to +1.2 | +0.1 to +1.1 | 29 / 17 / 1494 | yes |

All evidence packed, by category:

| Category | Baseline | address_first | Change (points) | 95% questions | 95% conversations | Wins / losses / ties |
|---|---:|---:|---:|---:|---:|---:|
| multi-hop | 51.8% | 52.1% | +0.4 | -3.2 to +3.9 | -3.5 to +3.9 | 13 / 12 / 257 |
| open-domain | 55.4% | 53.3% | -2.2 | -5.4 to +0.0 | -5.4 to +0.0 | 0 / 2 / 90 |
| single-hop | 94.5% | 95.5% | +1.0 | +0.1 to +1.8 | +0.0 to +2.1 | 10 / 2 / 829 |
| temporal | 91.0% | 92.5% | +1.6 | +0.0 to +3.4 | +0.0 to +3.9 | 6 / 1 / 314 |

- No question lost all its candidates: none had zero candidates on either side.
- All evidence left the candidates for 4 questions (conv-41-q0008, conv-48-q0048, conv-49-q0051, conv-49-q0077)
  and came into them for 2 (conv-42-q0081, conv-44-q0031).
- Lost all evidence from the context (17): conv-26-q0048, conv-30-q0017, conv-30-q0023, conv-42-q0005,
  conv-42-q0053, conv-42-q0084, conv-44-q0012, conv-44-q0018, conv-44-q0020, conv-47-q0001, conv-47-q0003,
  conv-47-q0054, conv-48-q0067, conv-48-q0096, conv-50-q0011, conv-50-q0041, conv-50-q0049. Gained it: 29.

### LongMemEval-S, 12 questions

Every measure is unchanged: all evidence packed 12 of 12 on both sides, evidence recall and evidence in the top 25
100%, projected accuracy 95.5%; no question gained or lost.

## Fast probes

Commands as in CLAUDE.md ("Alpha and beta gate"), with labels `af-off-1111` and `af-on-1111`, the on side with
`--set 'memory_mode="address_first"'`; the member probe on both sides with `--speaker --set
enable_speaker_grounding=true`. The off sides replayed the probes' extraction caches (0 calls). The on sides made
live calls, and since a live build is one sample, each was built once more with `--no-cache` (`af-on2-1111`).

| Probe and measure | off | on, first build | on, second build |
|---|---:|---:|---:|
| Defects: answers in the top 3 | 7/7 | 4/7 | 5/7 |
| Defects: answers in the top 5 | 7/7 | 7/7 | 6/7 |
| Defects: user claims on the named owner | 12 | 12 | 10 |
| Chat: answers in the top 3 | 14/18 | 10/18 | 11/18 |
| Chat: answers in the top 5 | 15/18 | 14/18 | 14/18 |
| Chat: user claims on the named owner | 35 | 33 | 35 |
| Chat: repeated records in contexts | 18/1620 | 54/1594 | 19/1560 |
| Member: answers in the top 3 | 10/12 | 10/12 | 11/12 |
| Member: answers in the top 5 | 11/12 | 10/12 | 11/12 |
| Member: user claims on the named owner | 7 | 7 | 6 |
| Extraction calls (defects / chat / member) | 0 / 0 / 0 | 14 / 54 / 30 | 14 / 54 / 30 |

**Each option alone.** The alpha rule says to run each option alone when they fail together. The options that act
at retrieval need no new extraction, so each was run on the off side's cached extraction (0 calls). Typed
extraction and corrections change the extraction request and were not run alone (each would need live builds).

| Option alone | Defects top 3 / top 5 | Chat top 3 / top 5 | Member top 3 / top 5 | Owner claims (all three) |
|---|---:|---:|---:|---:|
| none (off) | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `enable_relation_routing` | 7 / 7 | 15 / 16 | 11 / 12 | 12 / 35 / 7 |
| `enable_belief_state` (turns routing on) | 5 / 7 | 14 / 15 | 11 / 12 | 12 / 35 / 7 |
| `packing.session_context_direct_first=10` | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `packing.clock_window_filter` | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `packing.collapse_identical_text` | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `packing.second_round` | 7 / 7 | 13 / 14 | 10 / 10 | 12 / 35 / 7 |
| `scoring.rrf_recency_order` | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| all of these together | 5 / 7 | 13 / 14 | not run | 12 / 35 / not run |

The belief ledger moves "Which cities will Alex and Jordan visit in Japan?" from rank 2 to 4 and "How does Alex
prefer to travel in Japan?" from 3 to 5. The second round moves four chat answers down one place each ("When did
Dana start the new job?", "Does Dana drink coffee?", "Where will Dana stop overnight on the drive?", "Which
programming language does Dana prefer?").

## Against the alpha rule

| Tier | Result | Why |
|---|---|---|
| Defect probes | fail | 4 and 5 of 7 answers in the top 3 with the switch on; the rule needs 7 of 7 |
| Chat probe | fail | top 3 at 10 and 11 against 14, top 5 at 14 against 15 |
| Member probe | fail | first build: top 5 at 10 against 11; second build: owner claims at 6 against 7 |
| Mid tier | fail | LoCoMo evidence in the top 25 -3.1 points, both intervals entirely below zero |
| Offline evidence gate | not judged here | the lead's runs of each ranking option alone on 2026-11-10 at fb050a50 |
| Fast cycle, corrections probe | not run | |

Of the options alone, relation routing, `session_context_direct_first`, the clock window filter, identical text
collapse and `rrf_recency_order` pass the defect, chat and member probes; the belief ledger and the second round do
not. Typed extraction and corrections were not measured alone. A loss is a result to report: nothing was changed.
