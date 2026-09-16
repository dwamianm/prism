# Fresh normalized raw-memory comparison using native returned provenance

Registered after complete operational failure coverage from the prior run and
before any dataset quality inspection. This supersedes the strict custom-metadata
capture protocol at `f5a2a69`; the old run remains failed and its outputs will not
be reused or merged into this comparison.

## Evidence for the amendment

The [complete failure audit](hindsight-normalized-native-failure-audit.json)
records all 119 attempted cases and 59,021 source turns. Hindsight exited 1 with
exactly two return-validation failures, `case-0046` and `case-0112`: five and two
units respectively omitted optional custom metadata. All seven retain their
native source-role context and supplied timestamp. Exact returned text and
retained unit/document identities pass. No other failure category occurred.
The PRME companion was stopped at nine completed cases with native exit 130.

The [native-provenance preflight](native-provenance-preflight-verification.json)
passed both fresh products on three authored histories and the first failing
operational case. All 24 context reproductions passed with no evidence labels.
The default validation/renderer remain unchanged for older protocols; this study
explicitly selects the new policy. This is an adapter correction, not a change
to either product's source code, extraction mode or retrieval settings.

## Complete fixed inputs and products

Reuse the exact common normalized neutral input from the previous protocol:
119 opaque queries, all 59,021 source turns including four blanks, supplied roles
and timezone-aware dates. Common declared normalization removed 41 non-printing
characters across three sources, with no query change, turn removal or printable
text change. Original inputs and their audit remain retained. Answers, original
question IDs, categories and evidence annotations remain in a separate reference
file and never enter either capture worker.

All settings in the [original raw capture protocol](HINDSIGHT-PRME-DEV-PROTOCOL.md)
remain fixed except the explicit omission policy and Hindsight rendering below:
installed PRME `b7521bc` (124 source files), installed Hindsight `bde55237` (323
source files), Python 3.13.3, BGE small English v1.5 with matching asset digests,
FastEmbed 0.8.0, ONNX Runtime 1.24.2, NumPy 2.4.2 and tiktoken 0.14.0. PRME uses
local DuckDB with one worker, exact vectors, raw notes and default density
packing. Hindsight uses a fresh PostgreSQL database per case, raw chunks, native
text search, RRF, high traversal budget, retain batches of 64 and 65,536 returned
fact-text tokens. No LLM extraction, reranking, observations or consolidation is
introduced. All memory stores, captures and output paths are fresh.

## Native return handling

Require exact document readback before retrieval and exact returned source-text
substrings, retained unit/document ownership and unique returned IDs. Permit
absent custom metadata keys while recording every omission per unit. Present
conflicting values or invalid metadata types still fail. Do not impute missing
metadata from original sources.

Select `native_fields_v2` to render returned unit ID, document ID, text, context,
occurred_start, occurred_end and mentioned_at. Preserve their exact returned
values, including nulls; these fields carry the source role and source timestamp
in the observed raw profile. Greedily pack whole JSONL records at complete
serialized ceilings of 2,048/4,096/8,192 cl100k tokens. Do not truncate text,
reconstruct original documents, infer dates, parse roles or fill missing fields.
The Hindsight rendering remains a disclosed evaluator adapter, not a claimed
native string renderer. PRME's actual public/default 4K bundle and offline 2K/8K
bundles remain unchanged, including its reserved overhead and fidelity rules.

## Analysis and completion

Retain the original capture's full analysis choices: first 100 unique ranked
source IDs under a shared whole-turn packer as the primary descriptive 4K source
ranking comparison, with 2K/8K and every category; keep actual content-bearing
source hits and whole original turns contained within one record separate.
The shared packer reconstructs originals and cannot substitute for actual product
context quality. Unlabelled cases have null source scores, not successful abstention.
Record query bootstrap and base-question/identical-history cluster sensitivity;
partial history overlap remains a limitation. Preserve metadata omission counts.

Both workers must complete all 119 cases without errors and exit natively with
code zero before any quality inspection. The analyzer must reproduce every
context and verify input/configuration/source/capture identities. No outcome
retries, selective omissions, prior-result reuse or merging. A failure remains
failed. Complete raw capture and analysis may feed the separately registered
[common-reader study](HINDSIGHT-PRME-READER-PROTOCOL.md), using exact actual 4K
contexts, two reader families and fresh empty controls.

No independent holdout, full extraction comparison, production-default gate or
system-leadership conclusion follows from this development study. Record native
API request counts and timings without a fair speed or dollar-cost claim under
concurrent host load. Do not pool earlier Mem0 budget results, different date
rendering, prior Hindsight contexts or reconstructed source metrics with these
actual-context reader results.
