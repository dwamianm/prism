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

An equal-context comparison then bounded both local candidates to 8,192 tokens.
[`classification-qualifiers-qwen9b-8k.json`](classification-qualifiers-qwen9b-8k.json)
passed **12/12 cases** in **314.123 seconds** and its worker exited successfully.
The first checked 9B invocation did not publish a worker report after a transport
failure; the preserved report is the successful direct worker retry.

[`classification-qualifiers-qwen35b-a3b-8k-run1.json`](classification-qualifiers-qwen35b-a3b-8k-run1.json)
and [`run2`](classification-qualifiers-qwen35b-a3b-8k-run2.json) each passed
**12/12 cases**, in **145.166** and **135.049 seconds**. Both checked processes
exited zero, and their structured extraction arrays were byte-identical after
canonical JSON serialization (`sha256:87a2fb624150fc5efe7895ba48052f42946acdbbe5d4ff7774f577275b0764d0`).
Ollama reported an approximately 22 GB fully GPU-resident load for the 35B-A3B
profile and 5.7 GB for 9B on the tested 48 GB Apple Silicon machine. The larger
mixture-of-experts candidate was 2.2x faster by the mean elapsed time of its two
runs versus the successful 9B run. This is an observed single-machine result,
not a general hardware benchmark.

The accompanying implementation passed the complete local code suite with live
PostgreSQL: **3,268 passed, 93 skipped**. For capable local hardware, the tested
35B-A3B profile is PRME's recommended high-quality Ollama extractor. The 9B
profile remains the lower-memory option. Neither becomes the package-wide
default because a 23 GB model download and roughly 22 GB loaded model are not a
portable assumption, and these authored probes are not held-out accuracy or
competitive evidence.

The reports retain prompt, response-schema and fixture hashes, graph assertions,
and raw extraction output. Reproduce the temperature-zero run with an installed
Ollama model:

```bash
ollama create prme-qwen3.5:35b-a3b-8k \
  -f examples/ollama/qwen35b-a3b-8k.Modelfile
PRME_EXTRACTION_TEMPERATURE=0 \
python -m benchmarks.diagnostics.claim_classification \
  --model prme-qwen3.5:35b-a3b-8k --timeout 180 --output classification.json
```

Raw-report SHA-256 digests are `9b490f222d8488dd7c0b662abde07fb6b6e1d825de1ac1741a8eb56bb1e1a7ba`
(35B run 1), `b64ece11f2f4f604bf5778bdc5278624086c64a63da5468e8b57ca8704b5d8d5`
(35B run 2), and `f39f2092a8523f05604a2ed406138cf24bd9446f40f077a736f1585fbf048283`
(9B 8K worker).
