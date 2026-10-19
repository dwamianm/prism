# Mid tier: budget-8192

Commit `eda1bc069dfcfb8caa24bcee45c4984ca6ae4799`, engine source tree `c8dc31e4a753a48b9b778620144ab1dcbb41ae32`. Settings: {"packing": {"token_budget": 8192}}. Extraction: `deepseek-v4.1-flash:cloud` on Ollama 0.40.6.

Sample `mid-v1`: 10 LoCoMo conversations (1,540 questions) and 12 LongMemEval-S questions. No pack was built: the gate replayed the packs of the build `main-7f9ce556`, so a change that acts when memories are stored is not measured. The run took **2 min 57 s** on a machine with 18 cores.

| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |
|---|---:|---:|---:|---:|---:|---:|
| build-locomo (packs of main-7f9ce556-locomo) | 0 s | 0 | 0 | 0 | 0 | 2.54 / 2.54 |
| build-longmemeval (packs of main-7f9ce556-longmemeval) | 0 s | 0 | 0 | 0 | 0 | 2.54 / 2.54 |
| gate-locomo | 2 min 57 s |  |  |  |  | 2.54 / 7.20 |
| gate-longmemeval | 11 s |  |  |  |  | 2.54 / 3.54 |
| **Whole run** | **2 min 57 s** | 0 | 0 | 0 | 0 | 2.54 / 7.20 |

Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.

## LoCoMo

Intervals are 95%: `questions` treats questions as independent; `conversations` resamples whole conversations and is the one to trust.

| Category | Questions | All evidence packed | 95% questions | 95% conversations | Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **All** | 1,540 | 1366/1536 (88.9%) | 87.3 to 90.4 | 86.8 to 91.1 | 93.5% | 70.0% | 80.8% | 1510/1536 |
| multi-hop | 282 | 188/282 (66.7%) | 61.0 to 71.9 | 62.4 to 70.3 | 85.6% | 42.0% | 60.9% | 267/282 |
| open-domain | 96 | 63/92 (68.5%) | 58.4 to 77.1 | 53.1 to 81.7 | 78.7% | 37.4% | 65.7% | 84/92 |
| single-hop | 841 | 809/841 (96.2%) | 94.7 to 97.3 | 94.9 to 97.4 | 96.5% | 80.1% | 88.5% | 839/841 |
| temporal | 321 | 306/321 (95.3%) | 92.4 to 97.1 | 93.0 to 97.5 | 96.6% | 77.3% | 82.7% | 320/321 |

All LoCoMo questions, each measure with its intervals:

| Measure | Value | 95% questions | 95% conversations |
|---|---:|---:|---:|
| All evidence packed | 88.9% | 87.3 to 90.4 | 86.8 to 91.1 |
| All evidence among the candidates | 98.3% | 97.5 to 98.8 | 97.7 to 99.0 |
| Evidence recall | 93.5% | 92.4 to 94.5 | 92.0 to 95.0 |
| Evidence in top 25 | 70.0% | 67.9 to 72.0 | 67.8 to 72.1 |
| Projected accuracy | 80.8% | 79.7 to 81.8 | 79.4 to 82.3 |

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
