# PRME–Hindsight: completed common-reader development comparison

Both product captures, both reader runs, judging and final scoring completed
with native exit zero. The scorer reproduced the complete artifact chain.
All 119 questions and all three arms are retained for each reader. The 714
logical judgments required 616 unique calls; identical requests reuse saved
responses under the registered protocol. No failed outcome was replaced.

| Reader | PRME correct | Hindsight correct | Empty correct | PRME minus Hindsight | 95% paired group interval |
|---|---:|---:|---:|---:|---:|
| gemma4:26b | 71/119 | 56/119 | 8/119 | 12.61 pp | 1.74 to 22.69 pp |
| qwen3.5:4b | 53/119 | 52/119 | 9/119 | 0.84 pp | -11.02 to 11.97 pp |

| Category | Questions | Qwen PRME / Hindsight / empty | Gemma PRME / Hindsight / empty |
|---|---:|---:|---:|
| abstention | 8 | 8 / 8 / 8 | 8 / 7 / 8 |
| knowledge-update | 20 | 11 / 12 / 0 | 16 / 15 / 0 |
| multi-session | 30 | 9 / 5 / 0 | 14 / 5 / 0 |
| single-session-assistant | 9 | 1 / 8 / 1 | 1 / 7 / 0 |
| single-session-preference | 7 | 2 / 2 / 0 | 3 / 3 / 0 |
| single-session-user | 17 | 13 / 12 / 0 | 15 / 13 / 0 |
| temporal-reasoning | 28 | 9 / 5 / 0 | 14 / 6 / 0 |

The results do not establish a consistent overall lead across reader families.
PRME loses substantially on assistant-memory questions for both readers. This
agrees with the earlier source audit: the actual PRME bundle lost all nine
labelled assistant sources despite their availability in ranked candidates.
A judged-correct answer without a labelled source hit does not establish that
the missing source was retrieved; it can come from other context or prior knowledge.

The Qwen knowledge-update category also loses one correct answer. Preference
totals tie for both readers, but each has two wins and two losses; tied totals
do not mean identical questions were answered correctly. The complete paired
counts and category intervals are in [the result artifact](public-context-scores-dev-results.json).

Intervals use 2,000 bootstrap samples and seed 42 over 111 base-question/history
groups. They do not account for every partially shared history or remove
selection effects from previously inspected development questions. Answer
categories separate eight abstention questions; this differs from source tables
that retain the original category labels and treat missing source annotations
as null.

These are actual 4K raw-memory contexts from installed PRME `b7521bc` and
Hindsight `bde55237`, with the registered Hindsight rendering adapter. Full
extraction and consolidation modes are not compared. The calibrated local
Gemma 31B judge uses a custom rubric; it is not the official benchmark judge,
and it shares a family with the Gemma reader. Its 41/42 authored calibration
does not prove perfect dataset judgments. No pooled reader score, independent
holdout result, cost/speed ratio or production-default promotion is claimed.

Protocol: [common-reader registration](HINDSIGHT-PRME-READER-PROTOCOL.md).
Completion: [native outer and child exits](public-context-finish-dev-completion.json).
The next work is to diagnose assistant evidence packing and validate improvements
without repeating the preference losses seen in earlier packing changes.
