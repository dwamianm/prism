# PRME opt-in interaction study — 2026-09-22

**Status: failed closed during the baseline; feature comparisons are unassessed.**
Keep current production defaults. This study supplies no new answer-quality
evidence for promoting or rejecting any individual feature or combination.

Production source was `main` at `a66ee854325890c6bc28f6b515efeb6ed7df4deb`.
The isolated branch is `research/opt-in-interactions-2026-09-22`, with registration
commit `b40082f`. Production source/defaults were not changed, and nothing was
pushed, merged or deployed.

## What ran

The [protocol](OPT-IN-INTERACTIONS-PROTOCOL.md) and
[machine registration](opt-in-interactions-registration.json) froze all 500
cleaned LongMemEval-S question identities, complete configurations for the fixed
arms, reader/judge prompts, 4,096-token context budget, retry/failure rules and
the rule for selecting a later combined arm. Registration identity:
`88905a6e0073e0d5a268b8e3053146c48c58ada02329e584f653dda6e6155087`.

The initial GPT-5.4 probes failed: the shell credential returned HTTP 401 and
the archived baseline's project credential returned HTTP 429
`credit_balance_exhausted`. Both are retained. The user then explicitly selected
Ollama `deepseek-v4.1-flash:cloud` instead. Its reader/judge and unmodified
query-reformulation preflights passed before dataset execution. This is a
disclosed DeepSeek study, not a matched GPT-5.4 comparison.

The reader and judge used the confirmed local tag digest, temperature zero,
seed 42, thinking disabled, 65,536 context tokens, and the original 1,024/64
output limits. The official evaluator and generation source hashes matched the
pinned upstream revision. All 500 existing memory packs passed integrity checks.
The baseline ran current production retrieval on private copies of those packs;
its ingestion was historical, not newly measured.

Execution ran from **18:04:04 to 18:08:21 UTC**:

| Disposition | Questions |
|---|---:|
| Complete retrieval, reader and judge | 244 |
| Terminal reader failure | 1 |
| Not started after stopping condition | 255 |
| Registered total | 500 |

Question `b46e15ed` returned HTTP 200 but exhausted the reader limit:
`done_reason="length"`, `eval_count=1024`, with 4,142 reported input tokens.
The runner correctly rejected the truncated completion. It did not judge it,
replace it, increase its limit, change its prompt, or restart the failed run.
The native process exited 1. The [execution disposition](opt-in-interactions-execution-disposition.json)
retains every registered question's completion status without publishing a
selected answer-score subset.

## Results by arm

| Arm | Execution | Decision |
|---|---|---|
| Production baseline | 244 complete, 1 failed, 255 not started | Retain existing defaults; no new score |
| Store supersedence | Not started | Opt-in only; unassessed |
| QA pairing | Not started | Opt-in only; unassessed |
| Surprise gating | Not started | Opt-in only; unassessed |
| Reranker | Not started | Opt-in only; unassessed |
| Query reformulation | Preflight only; dataset arm not started | Opt-in only; unassessed |
| Temporal relations | Provider preflight only; dataset arm not started | Opt-in only; unassessed |
| Episode routing, top-k 2 | Not started | Opt-in only; unassessed |
| Evidence augmentation, top-k 10 | Not started | Opt-in only; unassessed |
| Episode + augmentation | Not started | Opt-in only; unassessed |
| Episode + projection, top-k 50 | Not started | Opt-in only; unassessed |
| Reranker + reformulation | Not started | Opt-in only; unassessed |
| Temporal relations + episode | Not started | Opt-in only; unassessed |
| Supersedence + balanced | Alias of its individual arm | No independent observation |
| QA pairing + balanced | Alias of its individual arm | No independent observation |
| Surprise gating + balanced | Alias of its individual arm | No independent observation |
| Best individuals combined | No eligible selection was computed | Unassessed |
| Full-feature exploratory | Not started; projection excluded because mutually exclusive with augmentation | Exploratory only |

The [per-arm result file](opt-in-interactions-results.json) records configuration
checksums, artifact availability and explicit nulls for unmeasured metrics.
Answer/category scores, regressions, omission/conflict comparisons, paired
differences, confidence intervals, and comparative retrieval p50/p95 and context
usage are **unavailable**, not zero. No partial answer score or favorable subset
was published. Raw per-request latency and token measurements remain in the
private captures for audit.

No fresh ingestion arm started, so there is no new ingestion-cost measurement.
Consumed resources are recorded separately from benchmark estimates: 245 reader
calls and 244 judge calls reported **1,105,433 input tokens and 64,548 output
tokens**, including the failed completion. All 489 HTTP attempts returned without
an HTTP error; one completion failed the output-length gate. Monetary cost is
unknown because billing was not available.

## Verification and separate Jev workflow

The [independent artifact verification](opt-in-interactions-verification.json)
authenticated all **245 retained captures and 490 durable retrieval receipts**,
recounted their contexts, checked exact reader/judge requests and saved verdict
parsing, and verified the complete private pack trees. All **500 original master
packs remained unchanged** after execution. The retained partial artifact
manifest is `5c7e4908363cca557d8de5c37bf3458da7188f75c0d73af1847e4a83f60519ae`.
Successful artifact verification does not make this an eligible answer study.

The separately [registered explicit Jev workflow](opt-in-explicit-workflow-preflight-registration.json)
completed [three authored pair cases](opt-in-explicit-workflow-preflight-result.json):
explicit acceptance, explicit rejection, and a negative pair with no proposal.
Positive proposals remained inert before review; accepted links survived restart,
rejected links remained non-traversable, both source entities remained active,
review retries were idempotent, and another owner saw no inbox entries. The
reviewer is explicitly identified as a benchmark fixture, not a human reviewer.
The three Jev requests used 1,501 input and 213 output tokens; observed provider
median/p95 were 0.311/0.436 seconds for this tiny authored assay.

A separate answer-blind temporal probe returned exact cited operands, and the
pinned Jev gate returned 0.97 for both operands against its 0.85 threshold.
These are positive operational findings, not held-out accuracy, precision,
availability guarantees, or retrieval interaction evidence. No bulk candidate
selection or automatic merge was tested or introduced.

[Local validation](opt-in-validation.json) passed **172 tests, with 26 skips**,
covering packing/evidence/episode behavior, reranking, query reformulation,
supersedence/oscillation, surprise gating, QA pairing, temporal safety,
proposal/review behavior, receipts and the new fail-closed research gates.
These were source-tree tests. Live PostgreSQL and installed-wheel checks were
not performed; this is not complete release or promotion validation.

## Limitations and next decision

The immediate negative finding is that the amended reader did not complete the
registered baseline at the preserved output limit. This does not measure any
feature's answer quality or show that DeepSeek is generally unsuitable.

The protocol audit also found a registration inconsistency: the JSON marks the
baseline `fresh_ingestion_required=true`, while the explicit Markdown protocol
and runner reuse historical baseline packs. Actual execution used historical
packs. The original registration is retained unchanged; no fresh baseline or
matched ingestion-effect claim is made. A later ingestion comparison needs a
fresh no-flag control as well as fresh flag-specific packs.

The old LongMemEval-S registration lacked a complete transitive dependency lock.
Its stored `fastembed-0.7.4`/BGE identity matches this environment, and current
dependencies/assets were frozen, but full historical environment equality is
unknown. Raw-turn packs may not exercise source projection/augmentation, and
the native MAB bulk-ingestion adapter bypasses the three store-time hooks.
Future treatment claims must demonstrate actual activation.

MemoryAgentBench Banking, EventQA, Conflict and Detective did not start. BEAM,
MemoryArena, best-arm selection and untouched confirmation also did not start.
Their task-specific manifests remain future registration work. The inspected
500-question LongMemEval-S cohort cannot itself become an untouched holdout.

**Recommendation: promote no new combination and preserve current defaults.**
Further evaluation requires a separate preregistered protocol that resolves the
reader ceiling and registration/control issues while retaining this failed run.
No automatic replacement study was launched.

Private artifacts are retained at
`/Users/dwamianm/Sites/prism-opt-in-study-2026-09-22/data/opt-in-study/longmemeval-v1/`.
Frozen execution sources are recoverable from `b40082f`; verify against that
checkout when later research documentation has changed. No benchmark answers
or partial verdict subset were committed to the repository.
