# Mid tier: cold-2

Commit `eda1bc069dfcfb8caa24bcee45c4984ca6ae4799`, engine source tree `c8dc31e4a753a48b9b778620144ab1dcbb41ae32`. Settings: the defaults. Extraction: `deepseek-v4.1-flash:cloud` on Ollama 0.40.6.

Sample `mid-v1`: 10 LoCoMo conversations (1,540 questions) and 12 LongMemEval-S questions. Extraction was **cold** (1,181 live calls, 0 cache hits; the cache held 0 responses before and 1,180 after). The run took **34 min 17 s** on a machine with 18 cores.

| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |
|---|---:|---:|---:|---:|---:|---:|
| build-locomo | 21 min 45 s | 275 | 2.07 M | 1.39 M | 0 | 5.72 / 18.30 |
| build-longmemeval | 34 min 05 s | 906 | 6.65 M | 2.31 M | 0 | 5.72 / 27.03 |
| gate-locomo | 3 min 47 s |  |  |  |  | 18.30 / 14.70 |
| gate-longmemeval | 11 s |  |  |  |  | 27.03 / 23.64 |
| **Whole run** | **34 min 17 s** | 1,181 | 8.72 M | 3.70 M | 0 | 5.72 / 23.64 |

Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.

## LoCoMo

Intervals are 95%: `questions` treats questions as independent; `conversations` resamples whole conversations and is the one to trust.

| Category | Questions | All evidence packed | 95% questions | 95% conversations | Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **All** | 1,540 | 1283/1536 (83.5%) | 81.6 to 85.3 | 81.3 to 85.9 | 89.1% | 70.0% | 77.3% | 1510/1536 |
| multi-hop | 282 | 145/282 (51.4%) | 45.6 to 57.2 | 46.1 to 56.0 | 74.6% | 41.3% | 51.1% | 268/282 |
| open-domain | 96 | 51/92 (55.4%) | 45.3 to 65.2 | 41.9 to 67.3 | 65.1% | 37.9% | 60.1% | 83/92 |
| single-hop | 841 | 796/841 (94.6%) | 92.9 to 96.0 | 93.0 to 96.1 | 95.1% | 80.4% | 87.3% | 839/841 |
| temporal | 321 | 291/321 (90.7%) | 87.0 to 93.4 | 86.3 to 94.7 | 92.9% | 77.0% | 79.3% | 320/321 |

All LoCoMo questions, each measure with its intervals:

| Measure | Value | 95% questions | 95% conversations |
|---|---:|---:|---:|
| All evidence packed | 83.5% | 81.6 to 85.3 | 81.3 to 85.9 |
| All evidence among the candidates | 98.3% | 97.5 to 98.8 | 97.6 to 99.0 |
| Evidence recall | 89.1% | 87.6 to 90.5 | 87.5 to 90.9 |
| Evidence in top 25 | 70.0% | 67.9 to 72.0 | 67.9 to 71.9 |
| Projected accuracy | 77.3% | 76.0 to 78.5 | 75.9 to 78.9 |

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
