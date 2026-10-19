# Mid tier: baseline-cold

Commit `ec2a70223d31bcd7149256f3bb97806ca7dfc1b8`, engine source tree `c8dc31e4a753a48b9b778620144ab1dcbb41ae32`. Settings: the defaults. Extraction: `deepseek-v4.1-flash:cloud` on Ollama 0.40.6.

Sample `mid-v1`: 10 LoCoMo conversations (1,540 questions) and 12 LongMemEval-S questions. Extraction was **cold** (1,187 live calls, 0 cache hits; the cache held 0 responses before and 1,187 after). The run took **24 min 31 s** on a machine with 18 cores.

| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |
|---|---:|---:|---:|---:|---:|---:|
| build-locomo | 15 min 23 s | 275 | 2.07 M | 1.30 M | 0 | 5.10 / 8.41 |
| build-longmemeval | 24 min 21 s | 912 | 6.68 M | 2.39 M | 0 | 5.10 / 7.27 |
| gate-locomo | 3 min 10 s |  |  |  |  | 8.41 / 9.41 |
| gate-longmemeval | 10 s |  |  |  |  | 7.27 / 6.78 |
| **Whole run** | **24 min 31 s** | 1,187 | 8.76 M | 3.69 M | 0 | 5.10 / 6.78 |

Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.

## LoCoMo

Intervals are 95%: `questions` treats questions as independent; `conversations` resamples whole conversations and is the one to trust.

| Category | Questions | All evidence packed | 95% questions | 95% conversations | Evidence recall | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|---:|
| **All** | 1,540 | 1277/1536 (83.1%) | 81.2 to 84.9 | 80.8 to 85.4 | 88.9% | 77.0% | 1510/1536 |
| multi-hop | 282 | 141/282 (50.0%) | 44.2 to 55.8 | 45.1 to 54.4 | 74.2% | 50.2% | 267/282 |
| open-domain | 96 | 52/92 (56.5%) | 46.3 to 66.2 | 42.2 to 68.8 | 65.8% | 60.6% | 84/92 |
| single-hop | 841 | 793/841 (94.3%) | 92.5 to 95.7 | 92.4 to 96.0 | 94.8% | 87.0% | 839/841 |
| temporal | 321 | 291/321 (90.7%) | 87.0 to 93.4 | 86.3 to 94.7 | 92.9% | 79.3% | 320/321 |

## LongMemEval-S

Intervals are 95%: `questions` treats questions as independent, and with this few questions they are wide.

| Category | Questions | All evidence packed | 95% questions | Evidence recall | Projected accuracy | All evidence among candidates |
|---|---:|---:|---:|---:|---:|---:|
| **All** | 12 | 12/12 (100.0%) | 75.8 to 100.0 | 100.0% | 95.5% | 12/12 |
| knowledge-update | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 95.5% | 2/2 |
| multi-session | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 95.5% | 2/2 |
| single-session-assistant | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 95.5% | 2/2 |
| single-session-preference | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 95.5% | 2/2 |
| single-session-user | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 95.5% | 2/2 |
| temporal-reasoning | 2 | 2/2 (100.0%) | 34.2 to 100.0 | 100.0% | 95.5% | 2/2 |

## Limits

- The LongMemEval-S sample is small (a few questions per type), so its intervals are wide. It flags a type that collapses, not a small change. LoCoMo carries most of the signal, and its conversation-level interval rests on 10 conversations.
- It measures evidence coverage, not answers: no reader or judge runs. The projected accuracy is a planning estimate from the 2026-09-23 GPT-5.4 run, not an answer score.
- It is a screen between the fast-cycle checks and the twice-weekly full run. A change that looks good here still needs the full benchmarks, and the default-change rule still needs the answer track.
- A cold run's model output is close to repeatable but not exact, so two cold builds of the same code differ a little. Compare a change with a baseline that replayed the same cache.
