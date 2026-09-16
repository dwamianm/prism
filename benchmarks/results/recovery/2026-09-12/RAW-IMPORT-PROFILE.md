# Raw import cost attribution

The [installed diagnostic](raw-import-profile-f9665fb.json), using PRME `f9665fb`
and harness `365e162`, completed 128 authored source turns through each existing
API and exited zero. Both paths preserved all source/node text and source clocks,
finished every recovery job, and returned nonempty retrieval. BGE was warmed
before timing; the deferred path ran first.

| Existing API path | Acceptance | Explicit processing | Event-append time within these phases | Lexical commits |
|---|---:|---:|---:|---:|
| `ingest_fast()` loop, then `process_pending()` | 0.119s | 1.830s | 0.113s | 1 |
| Immediate `store()` loop | 10.207s | 0.002s | 0.270s | 128 |

Event append accounted for approximately 5.8% of measured deferred import time.
This probe does not support prioritizing a new transaction-batching API over the
existing deferred workflow. The README now demonstrates accepting a conversation
before processing, explains that each event commits separately, and distinguishes
retrying pending work from resubmitting already accepted events.

This is one authored history per API on one host under concurrent reader load,
with fixed order and no uncertainty estimate. Startup, verification reads,
retrieval and final shutdown are excluded from timing. Independent imports have
different event/node identities and admission times, so exact context parity is
not claimed. The result is not a product-to-product speed comparison or proof of
performance on long/large histories. Reproduce with:

```bash
python -m benchmarks.diagnostics.raw_import_profile --sources 128 --output fresh-report.json
```
