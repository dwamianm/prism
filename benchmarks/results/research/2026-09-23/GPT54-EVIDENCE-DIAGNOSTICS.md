# GPT-5.4 post-hoc evidence-retention diagnostics

These diagnostics use completed, authenticated runs. Dataset evidence annotations were used only after execution and did not influence ingestion, retrieval or judging. Retention is not a semantic answerability test: equivalent evidence can exist elsewhere, and retained evidence can still be misinterpreted.

| Benchmark | Annotated turns | In returned candidates | In packed context | Lost at packing |
|---|---:|---:|---:|---:|
| longmemeval | 886 | 886 | 792 | 94 |
| locomo | 2354 | 2343 | 1354 | 989 |

## longmemeval

| Evidence state | Correct | Incorrect |
|---|---:|---:|
| abstention | 24 | 6 |
| all annotated turns packed | 385 | 18 |
| annotated turns missing | 21 | 46 |

## locomo

| Evidence state | Correct | Incorrect |
|---|---:|---:|
| all annotated turns packed | 875 | 108 |
| annotated turns missing | 101 | 443 |
| no annotations | 4 | 0 |
| unresolved annotations | 5 | 4 |

Counts are question/annotation incidences, so the same source turn can occur in several questions. Unresolved LoCoMo annotation identities are reported separately and never silently treated as missing retrieved evidence.

Next experiments should distinguish candidate discovery from packing loss, then test answer-blind evidence selection on complete registered cohorts. Errors with retained annotations need a separate interpretation/conflict/temporal audit. These diagnostics do not authorize default changes or establish that a particular fix will work.

[Full diagnostics and per-question records](gpt54-posthoc-evidence-diagnostics.json).
