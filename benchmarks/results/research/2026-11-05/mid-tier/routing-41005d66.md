# Mid tier: routing-41005d66

Commit `41005d6698f7355cb262984dccc0bd736ec00874`, engine source tree `28ca0c244ba615bb46b53fe7f168a7ab8ef4afef`. Settings: {"enable_relation_routing": true}. Extraction: `deepseek-v4.1-flash:cloud` on Ollama 0.40.14.

Sample `mid-v1`: 10 LoCoMo conversations (1,540 questions) and 12 LongMemEval-S questions. Extraction was **warm** (0 live calls, 1,186 cache hits; the cache held 1,188 responses before and 1,188 after). The run took **91 min 28 s** on a machine with 18 cores.

| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |
|---|---:|---:|---:|---:|---:|---:|
| build-locomo | 40 min 60 s | 0 | 0 | 0 | 275 | 6.64 / 19.16 |
| build-longmemeval | 91 min 15 s | 0 | 0 | 0 | 911 | 6.64 / 9.21 |
| gate-locomo | 7 min 52 s |  |  |  |  | 19.16 / 24.59 |
| gate-longmemeval | 13 s |  |  |  |  | 9.21 / 8.26 |
| **Whole run** | **91 min 28 s** | 0 | 0 | 0 | 1,186 | 6.64 / 8.26 |

Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.

## LoCoMo

Intervals are 95%: `questions` treats questions as independent; `conversations` resamples whole conversations and is the one to trust.

| Category | Questions | All evidence packed | 95% questions | 95% conversations | Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **All** | 1,540 | 1287/1536 (83.8%) | 81.9 to 85.5 | 81.4 to 86.1 | 89.3% | 69.9% | 77.5% | 1506/1536 |
| multi-hop | 282 | 148/282 (52.5%) | 46.7 to 58.2 | 48.0 to 57.3 | 75.4% | 41.3% | 51.8% | 264/282 |
| open-domain | 96 | 51/92 (55.4%) | 45.3 to 65.2 | 41.9 to 67.3 | 65.0% | 35.2% | 60.1% | 83/92 |
| single-hop | 841 | 795/841 (94.5%) | 92.8 to 95.9 | 92.5 to 96.4 | 95.1% | 80.3% | 87.2% | 839/841 |
| temporal | 321 | 293/321 (91.3%) | 87.7 to 93.9 | 87.0 to 95.2 | 93.2% | 78.0% | 79.8% | 320/321 |

All LoCoMo questions, each measure with its intervals:

| Measure | Value | 95% questions | 95% conversations |
|---|---:|---:|---:|
| All evidence packed | 83.8% | 81.9 to 85.5 | 81.4 to 86.1 |
| All evidence among the candidates | 98.0% | 97.2 to 98.6 | 97.3 to 98.8 |
| Evidence recall | 89.3% | 87.9 to 90.7 | 87.6 to 91.1 |
| Evidence in top 25 | 69.9% | 67.8 to 72.1 | 67.5 to 72.3 |
| Projected accuracy | 77.5% | 76.2 to 78.7 | 76.0 to 79.1 |

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
