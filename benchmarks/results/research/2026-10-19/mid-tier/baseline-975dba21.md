# Mid tier: main-975dba21

Commit `5ad7a9e77faf878eff22cb289f8bd743537379f5`, engine source tree `546fc8e7d4fc00ceb8776d5a5a889e398d06aed3`. Settings: the defaults. Extraction: `deepseek-v4.1-flash:cloud` on Ollama 0.40.6.

Sample `mid-v1`: 10 LoCoMo conversations (1,540 questions) and 12 LongMemEval-S questions. Extraction was **warm** (0 live calls, 1,186 cache hits; the cache held 1,188 responses before and 1,188 after). The run took **11 min 57 s** on a machine with 18 cores.

| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |
|---|---:|---:|---:|---:|---:|---:|
| build-locomo | 6 min 39 s | 0 | 0 | 0 | 275 | 11.28 / 21.98 |
| build-longmemeval | 11 min 45 s | 0 | 0 | 0 | 911 | 11.28 / 29.39 |
| gate-locomo | 4 min 46 s |  |  |  |  | 21.98 / 34.63 |
| gate-longmemeval | 12 s |  |  |  |  | 29.39 / 26.25 |
| **Whole run** | **11 min 57 s** | 0 | 0 | 0 | 1,186 | 11.28 / 26.25 |

Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.

## LoCoMo

Intervals are 95%: `questions` treats questions as independent; `conversations` resamples whole conversations and is the one to trust.

| Category | Questions | All evidence packed | 95% questions | 95% conversations | Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **All** | 1,540 | 1277/1536 (83.1%) | 81.2 to 84.9 | 80.8 to 85.4 | 88.9% | 69.9% | 77.0% | 1510/1536 |
| multi-hop | 282 | 141/282 (50.0%) | 44.2 to 55.8 | 45.1 to 54.4 | 74.2% | 42.2% | 50.2% | 267/282 |
| open-domain | 96 | 52/92 (56.5%) | 46.3 to 66.2 | 42.2 to 68.8 | 65.8% | 37.4% | 60.6% | 84/92 |
| single-hop | 841 | 793/841 (94.3%) | 92.5 to 95.7 | 92.4 to 96.0 | 94.8% | 79.9% | 87.0% | 839/841 |
| temporal | 321 | 291/321 (90.7%) | 87.0 to 93.4 | 86.3 to 94.7 | 92.9% | 77.3% | 79.3% | 320/321 |

All LoCoMo questions, each measure with its intervals:

| Measure | Value | 95% questions | 95% conversations |
|---|---:|---:|---:|
| All evidence packed | 83.1% | 81.2 to 84.9 | 80.8 to 85.4 |
| All evidence among the candidates | 98.3% | 97.5 to 98.8 | 97.7 to 99.0 |
| Evidence recall | 88.9% | 87.5 to 90.2 | 87.3 to 90.7 |
| Evidence in top 25 | 69.9% | 67.8 to 72.0 | 67.8 to 71.9 |
| Projected accuracy | 77.0% | 75.7 to 78.3 | 75.5 to 78.5 |

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
