# Precision diagnostic — 2026-09-12

Unmodified PrecisionMemBench tests and generic provider adapter, upstream commit
`85b48d5fd1b38babc7fe922f3beb0308cf3aa6cb`, against PRME `53b1c55`.
See [adapter contract and reproduction](../../../integrations/README.md).

| Profile | Assertions passed | Mean precision | Mean recall |
|---|---:|---:|---:|
| Single-turn candidate retrieval | 11 / 77 | 0.0653 | 0.9884 |
| Session candidate retrieval | 0 / 12 | 0.0741 | 1.0000 |

The single-turn passes comprise eight structural and three trivially empty
checks, **zero active retrieval passes**. The upstream session runner reports
two failed tests encompassing twelve turn assertions; its JSON `totalCases`
and `turnCount` are twelve, while `caseCount` is two sessions.

Diagnosis: ranking demotes irrelevant candidates but the default API has no
minimum acceptance score; a small corpus therefore returns most of its nodes.
The fixture checks exact narrow inclusion/exclusion, so high support recall
coexists with poor precision. Some lifecycle expectations cannot be inferred
from metadata omitted by the upstream single-turn adapter.

These results measure candidates, not PRME's token-packed context or answer
accuracy. Persona/pins/open questions/relation expansion partly come from the
upstream fixture reader; passing such checks is not native product evidence.
Named scopes are mapped to isolated packs by the wrapper. Benchmark IDs only
round-trip in metadata. No comparisons with vendor scores are made. Latency
was recorded during an overlapping local retrieval evaluation, so these numbers
are not an isolated performance/SLO measurement. HTTP service logs showed
successful requests; test failures are preserved, not counted as infrastructure
skips. No model or ranking setting was tuned before the baseline run.


## Explicit score-floor development sweep

Frozen PRME `7fe66ac`, unchanged embeddings and scoring, the same upstream
revision and scratch wrapper. [Complete reports and manifest](selection/).

| Floor | Single-turn passes | Precision | Recall | Session passes | Session recall |
|---|---:|---:|---:|---:|---:|
| unset | 11/77 | 0.0653 | 0.9884 | 0/12 | 1.0000 |
| 0.3 | 11/77 | 0.0653 | 0.9884 | 0/12 | 1.0000 |
| 0.4 | 31/77 | 0.4208 | 0.9419 | 0/12 | 0.9697 |
| 0.5 | 42/77 | 0.5724 | 0.8439 | 5/12 | 0.7576 |
| 0.6 | 33/77 | 0.0780 | 0.0930 | 1/12 | 0.0758 |
| 0.7 | 34/77 | 0.0000 | 0.0000 | 1/12 | 0.0000 |

The high pass counts for nearly empty output demonstrate why pass rate alone
is misleading: structural/negative cases can pass while positive recall is zero.
The 0.5 floor is an experiment, not a recommended global default. Calibrate
acceptance on independent application queries and validate support recall as
well as irrelevant-memory exclusion. This sweep does not use or tune against
the separate held-out LongMemEval split. Run the documented adapter with each
`--min-score` value to reproduce; omit the flag for the unset profile.
