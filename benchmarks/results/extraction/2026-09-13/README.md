# Local extraction model checks

These reports exercise a fixed 12-case authored diagnostic against local Ollama
`qwen3.5:9b` (digest `6488c96fa5fa`). The cases cover ordinary, possible and
conditional usage, preferences and dislikes, explicit and rejected choices,
mixed claims, temporal phrasing, and namesake entities. They are development
probes, not held-out accuracy or competitive evidence.

[`classification-qwen35-9b.json`](classification-qwen35-9b.json) preserves the
pre-qualifier baseline. It passed **10/12 cases** in **348.114 seconds**, but
semantic inspection found that negated preferences could still be represented
as positive triples. This motivated typed claim polarity, exact condition
capture, and stricter modality validation.

[`classification-qualifiers-default-temperature-qwen35-9b.json`](classification-qualifiers-default-temperature-qwen35-9b.json)
is an exact-policy repeat after those schema changes, before PRME explicitly
controlled sampling temperature. It passed **8/12 cases** in **563.579 seconds**.
The model mislabeled possible and conditional future usage as decisions, then
exhausted its retries on the dislike and namesake cases. An earlier unseeded run
of the same candidate happened to pass 12/12. The repeat demonstrates why a
single successful local run is insufficient evidence.

PRME now sends extraction temperature zero by default and rejects uncertain or
contingent future actions classified as completed decisions unless their source
contains an explicit choice or commitment. A targeted check of the possible and
conditional cases passed all six executions across three repetitions in
**243.872 seconds**. The final complete run is preserved in
[`classification-qualifiers-temperature-zero-qwen35-9b.json`](classification-qualifiers-temperature-zero-qwen35-9b.json):
it passed **11/12 cases** in **321.454 seconds**. The remaining mixed-claim case
exhausted retries because the model returned a non-verbatim citation; PRME
correctly rejected that output instead of weakening its source boundary.

The accompanying implementation passed the complete local code suite with live
PostgreSQL: **3,261 passed, 93 skipped**. The 9B model remains a useful local
diagnostic option, but its latency and remaining citation-format failure do not
support selecting it as PRME's default extractor or making a stable accuracy or
leadership claim.

The reports retain prompt, response-schema and fixture hashes, graph assertions,
and raw extraction output. Reproduce the temperature-zero run with an installed
Ollama model:

```bash
PRME_EXTRACTION_TEMPERATURE=0 \
python -m benchmarks.diagnostics.claim_classification \
  --model qwen3.5:9b --timeout 120 --output classification.json
```
