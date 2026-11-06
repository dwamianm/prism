# Mid tier: main-41005d66

Commit `41005d6698f7355cb262984dccc0bd736ec00874`, engine source tree `28ca0c244ba615bb46b53fe7f168a7ab8ef4afef`. Settings: the defaults. Extraction: `deepseek-v4.1-flash:cloud` on Ollama 0.40.14.

Sample `mid-v1`: 10 LoCoMo conversations (1,540 questions) and 12 LongMemEval-S questions. Extraction was **warm** (0 live calls, 1,186 cache hits; the cache held 1,188 responses before and 1,188 after). The run took **102 min 50 s** on a machine with 18 cores.

| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |
|---|---:|---:|---:|---:|---:|---:|
| build-locomo | 48 min 20 s | 0 | 0 | 0 | 275 | 4.06 / 18.89 |
| build-longmemeval | 102 min 37 s | 0 | 0 | 0 | 911 | 4.06 / 8.95 |
| gate-locomo | 8 min 08 s |  |  |  |  | 18.89 / 22.52 |
| gate-longmemeval | 13 s |  |  |  |  | 8.95 / 8.17 |
| **Whole run** | **102 min 50 s** | 0 | 0 | 0 | 1,186 | 4.06 / 8.17 |

Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.

## LoCoMo

Intervals are 95%: `questions` treats questions as independent; `conversations` resamples whole conversations and is the one to trust.

| Category | Questions | All evidence packed | 95% questions | 95% conversations | Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **All** | 1,540 | 1284/1536 (83.6%) | 81.7 to 85.4 | 81.1 to 86.0 | 89.2% | 70.2% | 77.4% | 1506/1536 |
| multi-hop | 282 | 146/282 (51.8%) | 46.0 to 57.5 | 46.9 to 56.3 | 75.0% | 41.4% | 51.4% | 264/282 |
| open-domain | 96 | 51/92 (55.4%) | 45.3 to 65.2 | 41.9 to 67.3 | 64.4% | 35.2% | 60.1% | 83/92 |
| single-hop | 841 | 795/841 (94.5%) | 92.8 to 95.9 | 92.5 to 96.4 | 95.1% | 80.9% | 87.2% | 839/841 |
| temporal | 321 | 292/321 (91.0%) | 87.3 to 93.6 | 86.5 to 95.0 | 93.1% | 77.6% | 79.6% | 320/321 |

All LoCoMo questions, each measure with its intervals:

| Measure | Value | 95% questions | 95% conversations |
|---|---:|---:|---:|
| All evidence packed | 83.6% | 81.7 to 85.4 | 81.1 to 86.0 |
| All evidence among the candidates | 98.0% | 97.2 to 98.6 | 97.3 to 98.8 |
| Evidence recall | 89.2% | 87.8 to 90.5 | 87.4 to 91.0 |
| Evidence in top 25 | 70.2% | 68.1 to 72.4 | 67.7 to 72.6 |
| Projected accuracy | 77.4% | 76.1 to 78.6 | 75.7 to 79.0 |

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
