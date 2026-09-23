# PRME default GPT-5.4 benchmark comparison

Completed and authenticated: 2026-09-23T03:40:04.585011+00:00.

| Benchmark | PRME | Zep published reference | Raw difference |
|---|---:|---:|---:|
| LongMemEval-S | **430/500 (86.00%)** | 90.20% | -4.20 points |
| LoCoMo | **985/1540 (63.96%)** | 94.74% | -30.78 points |

Zep values are [vendor-reported](https://www.getzep.com/research/). Both PRME arms use `gpt-5.4-2026-03-05` with medium reasoning as reader and judge. This matches the disclosed reader model family/reasoning and benchmark scope, but is not an exact reproduction or a live paired comparison. Zep does not provide its exact prompts, dataset checksum and current execution artifacts or specify a judge reasoning-effort setting. Its published LoCoMo category counts do not reconcile with its headline.

All experimental flags remained off. No production defaults were changed. LongMemEval reuses all 500 authenticated production-control contexts from September 22; this is a new GPT reader/judge evaluation of those unchanged contexts. LoCoMo freshly materializes all ten conversations with public default APIs, including short turns and supplied image captions, without dataset observations/summaries or answer labels. It evaluates all 1,540 non-adversarial questions. These examined cohorts are development evidence. The descriptive bootstrap groups LoCoMo by its ten conversations and LongMemEval by exact whole-history hash; partially overlapping histories are not merged into one cluster. Zep reports median contexts of 4,408 tokens for LongMemEval and 5,760 for LoCoMo, so its context usage is also different from this fixed PRME budget.

## Complete-arm measurements

### longmemeval

- Correct: 430/500; question-bootstrap 95% interval [82.80, 89.00]%; source-cluster interval [82.80, 89.00]%.
- Context: mean 3964.8, median 3976 tokens; 3,996 effective-token ceiling, including all rendered product context.
- Retrieval p50/p95: 0.621/2.346 seconds. Historical frozen timings for LongMemEval; first-pass shared-host timings for LoCoMo. Not model-response latency.
- Successful reader/judge calls: 1000; HTTP attempts: 1000. HTTP status counts: {"200": 1000}; terminal failures: 0. Reported input/output tokens: 2,173,565/231,040, including 134,786 output reasoning tokens. Observed successful-call cost: $4.4498.

| Category | Correct | Accuracy |
|---|---:|---:|
| knowledge-update | 66/78 | 84.62% |
| multi-session | 98/133 | 73.68% |
| single-session-assistant | 53/56 | 94.64% |
| single-session-preference | 27/30 | 90.00% |
| single-session-user | 69/70 | 98.57% |
| temporal-reasoning | 117/133 | 87.97% |

### locomo

- Correct: 985/1540; question-bootstrap 95% interval [61.56, 66.36]%; source-cluster interval [61.43, 66.85]%.
- Context: mean 3950.6, median 3959 tokens; 3,996 effective-token ceiling, including all rendered product context.
- Retrieval p50/p95: 0.310/0.936 seconds. Historical frozen timings for LongMemEval; first-pass shared-host timings for LoCoMo. Not model-response latency.
- Successful reader/judge calls: 3080; HTTP attempts: 3080. HTTP status counts: {"200": 3080}; terminal failures: 0. Reported input/output tokens: 6,545,347/514,666, including 359,721 output reasoning tokens. Observed successful-call cost: $11.9983.

| Category | Correct | Accuracy |
|---|---:|---:|
| multi-hop | 80/282 | 28.37% |
| open-domain | 48/96 | 50.00% |
| single-hop | 630/841 | 74.91% |
| temporal | 227/321 | 70.72% |

Fresh source build: 5,882 stored turns; summed worker ingestion time 7446.0 seconds. Parallel worker time is not wall time.

## Costs, failures and validation

Total settled reported usage, including authored controls and funded access probes: **$16.4576**. Unresolved conservative reservations: $0.0000. This is usage-rate accounting, not a billing invoice.

The pre-funding project-credit failure remains recorded. The first authored calibration failed on an unrelated missing CLI dependency after one successful authored answer; zero dataset answers had started. An explicitly registered loader amendment retained the exact official prompt function and ran all authored controls anew. The failed attempt and its cost remain. LoCoMo source preparation used a registered ownership handoff and independent conversation workers; no source case was interrupted or omitted. All full-arm request, response, context and result hashes were independently reauthenticated before this report.

The existing DeepSeek 437/500 (87.4%) run remains separate. Changing both reader and judge cannot establish a memory-code improvement. No feature promotion or default change is recommended from this comparison alone.

Artifacts: [registration](gpt54-comparison-v1-registration.json), [verification](gpt54-comparison-v1-verification.json), [LongMemEval](gpt54-longmemeval-v1-result.json), [LoCoMo](gpt54-locomo-v1-result.json). All per-question answers and captures remain in the private run directory; public results bind their checksums.

## What to improve next

The separate [post-hoc evidence audit](GPT54-EVIDENCE-DIAGNOSTICS.md) identifies
context selection as the largest measured gap. LongMemEval returned all 886
annotated evidence instances but packed 792. Of its 70 incorrect answers, 46
lacked some annotated evidence, 18 retained all annotations, and six were
abstention failures. LoCoMo returned 2,343 of 2,354 annotation instances but
packed 1,354; 989 returned instances were lost during packing. Of its 555
incorrect answers, 443 lacked some resolvable annotated evidence, 108 retained
all annotations, and four had unresolved annotation identities.

LoCoMo multi-hop accuracy is the clearest weakness: 80/282 (28.37%), with missing
annotated context on 191 of its 202 incorrect answers. LongMemEval's strongest
category is single-session user recall at 69/70 (98.57%); its weakest is
multi-session reasoning at 98/133 (73.68%). Retaining annotations is not a causal
guarantee of a correct answer, and reference annotations need not be exhaustive.

Prioritize an answer-blind packing experiment that retains the strongest anchor
while selecting complementary evidence across the question's required claims.
Measure annotated retention and complete answer quality together. Audit the
remaining errors with retained evidence separately for temporal interpretation,
updates, conflict handling and reader use. The earlier failed unconditional
episode-routing and broad session-penalty experiments remain negative evidence;
do not enable those policies based on this diagnostic.

No new combination is ready to become a production default. Any candidate must
beat these complete registered controls, pass relevant backend/regression checks,
and then succeed on independently prepared, untouched histories/questions.
All canonical cohorts used here are now examined development data.

Validation: 16 targeted harness/loader/source-partition/verifier tests passed
across their recorded checks; the final verifier authenticated both complete
cohorts and recomputed every score aggregate and interval. The analysis modules
pass Ruff. A harmless unused import in the frozen source scheduler remains
recorded rather than changing its registered execution checksum. No production
source or dependency pins changed.
