# MemoryArena structured presentation V1

## Outcome

The structured presentation restoration API passed its complete model-free
MemoryArena reference-corpus audit. All 10,518 declared qualified city targets
were restored from tool lookup form to their exact source presentation form
across 2,139 plans. There were no missing, ambiguous or incorrect
restorations.

| Measure | Result |
| --- | ---: |
| Reference plans | 2,139 |
| Declared structured targets | 10,518 |
| Exact restorations | **10,518** |
| Restorations with source-argument provenance | **10,518** |
| Failures | **0** |

For every reference plan, the audit decomposed only the typed `Current City`
field into atomic components, converted each qualified source form to its
registered lookup form, and invoked the public
`ToolArgumentResolution.restore_presentations()` API on explicit JSON pointers.
The restored components matched the source exactly. One untargeted complete
lookup value and one surrounding free-text value per plan remained unchanged,
and the caller-owned input document was never mutated.

## Product behavior

The API accepts a structured JSON object plus a map from output JSON pointers
to application-defined binding kinds. A target succeeds only when its complete
string value equals one unambiguous lookup observed in the exact tool-argument
resolution. The returned copy records the output pointer, lookup and
presentation forms, kind, authorizing input pointers, source node IDs and
source binding references.

Missing paths, non-string slots, wrong kinds, unmatched values, invalid pointer
escapes and ambiguous presentations fail explicitly. The API does not search
or rewrite substrings and never touches undeclared fields.

## Decision

Accept structured presentation restoration as the next product candidate. It
provides deterministic output fidelity without placing lookup mappings or
instructions in model-visible context and without rewriting arbitrary prose.

This mechanism result does not reverse the rejection of free-form result
guidance and does not show an answer-quality improvement. The next paid
experiment must integrate restoration into the MemoryArena structured plan
boundary, preregister a matched exact-resolution control, and use a fresh
development cohort before any confirmation.

## Evidence identity

- Audit artifact SHA-256:
  `b5f34403393b5b5bf61173172ededdd16090bc0cee19c26b39cd08314f2b56c4`
- Audit source SHA-256:
  `5835eae2600f267fd1ceb1ae53c3f57a34377a606e849b573f0784bcdab73163`
- Value-binding implementation SHA-256:
  `f99101876cc72c4b3ff22cecd099b46f90158a47c57ed82ded4fab56e2b4a373`
- Dataset content SHA-256:
  `3a1c6b924c5c2816aeda42c57a253ae52e2de0734fcd989726f417a70a4728c3`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`

## Limits

This audit used reference plans and synthetic binding-use records derived from
their admitted source bindings. It did not invoke a model, replay generated
tool traces, parse arbitrary prose, or measure itinerary quality. Generated
plans can omit required values or violate the upstream format; restoration
cannot repair those errors. The corpus is one benchmark domain and does not
establish general product superiority.
