# Mid tier: emb366-bge-large-b

Commit `349a9926d7e4132952c4b4f5cc82a89875bc9e09` (working tree changed), engine source tree `cda53eae61820c9564056c9c50cdad8b46e7ab97`. Settings: the defaults. Extraction: `deepseek-v4.1-flash:cloud` on Ollama 0.40.6.

Sample `mid-v1`: 10 LoCoMo conversations (1,540 questions) and 12 LongMemEval-S questions. Extraction was **warm** (0 live calls, 1,186 cache hits; the cache held 1,188 responses before and 1,188 after). The run took **83 min 33 s** on a machine with 18 cores.

| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |
|---|---:|---:|---:|---:|---:|---:|
| build-locomo | 39 min 55 s | 0 | 0 | 0 | 275 | 7.62 / 10.43 |
| build-longmemeval | 83 min 17 s | 0 | 0 | 0 | 911 | 7.62 / 3.30 |
| gate-locomo | 5 min 39 s |  |  |  |  | 10.43 / 14.69 |
| gate-longmemeval | 16 s |  |  |  |  | 3.30 / 3.31 |
| **Whole run** | **83 min 33 s** | 0 | 0 | 0 | 1,186 | 7.62 / 3.31 |

Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.

## LoCoMo

Intervals are 95%: `questions` treats questions as independent; `conversations` resamples whole conversations and is the one to trust.

| Category | Questions | All evidence packed | 95% questions | 95% conversations | Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **All** | 1,540 | 1297/1536 (84.4%) | 82.5 to 86.2 | 82.2 to 86.8 | 90.0% | 75.1% | 78.0% | 1519/1536 |
| multi-hop | 282 | 148/282 (52.5%) | 46.7 to 58.2 | 47.2 to 57.1 | 75.8% | 47.4% | 51.8% | 274/282 |
| open-domain | 96 | 52/92 (56.5%) | 46.3 to 66.2 | 42.9 to 69.3 | 66.3% | 44.1% | 60.6% | 85/92 |
| single-hop | 841 | 802/841 (95.4%) | 93.7 to 96.6 | 93.6 to 97.0 | 95.9% | 85.8% | 87.8% | 840/841 |
| temporal | 321 | 295/321 (91.9%) | 88.4 to 94.4 | 88.1 to 95.4 | 94.0% | 80.3% | 80.2% | 320/321 |

All LoCoMo questions, each measure with its intervals:

| Measure | Value | 95% questions | 95% conversations |
|---|---:|---:|---:|
| All evidence packed | 84.4% | 82.5 to 86.2 | 82.2 to 86.8 |
| All evidence among the candidates | 98.9% | 98.2 to 99.3 | 98.4 to 99.3 |
| Evidence recall | 90.0% | 88.6 to 91.3 | 88.4 to 91.8 |
| Evidence in top 25 | 75.1% | 73.1 to 77.1 | 72.5 to 77.3 |
| Projected accuracy | 78.0% | 76.7 to 79.2 | 76.5 to 79.5 |

## LongMemEval-S

Intervals are 95%: `questions` treats questions as independent, and with this few questions they are wide.

| Category | Questions | All evidence packed | 95% questions | Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|
| **All** | 12 | 12/12 (100.0%) | 75.8 to 100.0 | 100.0% | 100.0% | 95.5% | 12/12 |
| knowledge-update | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 100.0% | 95.5% | 2/2 |
| multi-session | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 100.0% | 95.5% | 2/2 |
| single-session-assistant | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 100.0% | 95.5% | 2/2 |
| single-session-preference | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 100.0% | 95.5% | 2/2 |
| single-session-user | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 100.0% | 95.5% | 2/2 |
| temporal-reasoning | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 100.0% | 95.5% | 2/2 |

All LongMemEval-S questions, each measure with its intervals:

| Measure | Value | 95% questions |
|---|---:|---:|
| All evidence packed | 100.0% | 75.8 to 100.0 |
| All evidence among the candidates | 100.0% | 75.8 to 100.0 |
| Evidence recall | 100.0% | 100.0 to 100.0 |
| Evidence in top 25 | 100.0% | 100.0 to 100.0 |
| Projected accuracy | 95.5% | 95.5 to 95.5 |

## Limits

- The LongMemEval-S sample is small (a few questions per type), so its intervals are wide. It flags a type that collapses, not a small change. LoCoMo carries most of the signal, and its conversation-level interval rests on 10 conversations.
- It measures evidence coverage, not answers: no reader or judge runs. The projected accuracy is a planning estimate from the 2026-09-23 GPT-5.4 run, not an answer score.
- It is a screen between the fast-cycle checks and the twice-weekly full run. A change that looks good here still needs the full benchmarks, and the default-change rule still needs the answer track.
- A cold run's model output is close to repeatable but not exact, so two cold builds of the same code differ a little. Compare a change with a baseline that replayed the same cache.
