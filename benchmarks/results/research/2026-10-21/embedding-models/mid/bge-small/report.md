# Mid tier: emb366-bge-small

Commit `349a9926d7e4132952c4b4f5cc82a89875bc9e09` (working tree changed), engine source tree `cda53eae61820c9564056c9c50cdad8b46e7ab97`. Settings: the defaults. Extraction: `deepseek-v4.1-flash:cloud` on Ollama 0.40.6.

Sample `mid-v1`: 10 LoCoMo conversations (1,540 questions) and 12 LongMemEval-S questions. Extraction was **warm** (0 live calls, 1,186 cache hits; the cache held 1,188 responses before and 1,188 after). The run took **75 min 41 s** on a machine with 18 cores.

| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |
|---|---:|---:|---:|---:|---:|---:|
| build-locomo | 36 min 12 s | 0 | 0 | 0 | 275 | 2.50 / 11.44 |
| build-longmemeval | 75 min 31 s | 0 | 0 | 0 | 911 | 2.50 / 4.77 |
| gate-locomo | 5 min 20 s |  |  |  |  | 11.44 / 14.03 |
| gate-longmemeval | 11 s |  |  |  |  | 4.77 / 4.42 |
| **Whole run** | **75 min 41 s** | 0 | 0 | 0 | 1,186 | 2.50 / 4.42 |

Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.

## LoCoMo

Intervals are 95%: `questions` treats questions as independent; `conversations` resamples whole conversations and is the one to trust.

| Category | Questions | All evidence packed | 95% questions | 95% conversations | Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **All** | 1,540 | 1284/1536 (83.6%) | 81.7 to 85.4 | 81.1 to 86.0 | 89.2% | 70.3% | 77.4% | 1506/1536 |
| multi-hop | 282 | 146/282 (51.8%) | 46.0 to 57.5 | 46.9 to 56.3 | 75.0% | 41.4% | 51.4% | 264/282 |
| open-domain | 96 | 51/92 (55.4%) | 45.3 to 65.2 | 41.9 to 67.3 | 64.4% | 35.2% | 60.1% | 83/92 |
| single-hop | 841 | 795/841 (94.5%) | 92.8 to 95.9 | 92.5 to 96.4 | 95.1% | 80.9% | 87.2% | 839/841 |
| temporal | 321 | 292/321 (91.0%) | 87.3 to 93.6 | 86.5 to 95.0 | 93.1% | 78.0% | 79.6% | 320/321 |

All LoCoMo questions, each measure with its intervals:

| Measure | Value | 95% questions | 95% conversations |
|---|---:|---:|---:|
| All evidence packed | 83.6% | 81.7 to 85.4 | 81.1 to 86.0 |
| All evidence among the candidates | 98.0% | 97.2 to 98.6 | 97.3 to 98.8 |
| Evidence recall | 89.2% | 87.8 to 90.5 | 87.4 to 91.0 |
| Evidence in top 25 | 70.3% | 68.2 to 72.4 | 67.8 to 72.7 |
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
