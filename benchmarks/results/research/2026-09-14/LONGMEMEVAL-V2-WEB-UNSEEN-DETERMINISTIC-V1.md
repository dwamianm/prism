# LongMemEval-V2 web-small held-out deterministic comparison

**Completed:** 2026-09-14  
**Systems:** PRME versus the official no-memory adapter  
**Reader:** local Ollama `qwen3.5:9b`, thinking disabled  
**Questions:** 149 web-small static, dynamic and procedure questions  
**Scoring:** unmodified official deterministic evaluation functions

## Result

PRME answered 80 of 149 questions correctly (53.69%). The same reader without
memory answered 10 of 149 (6.71%), a paired improvement of 46.98 percentage
points. PRME won 75 question pairs, lost 5 and tied 69. The question-bootstrap
95% interval for the paired difference is 37.58 to 55.70 points; the exact
two-sided McNemar p-value is `4.25e-17`.

| Category | Questions | PRME | No memory | Paired difference | Wins / losses | 95% bootstrap interval |
|---|---:|---:|---:|---:|---:|---:|
| Dynamic | 49 | 20 (40.82%) | 0 (0.00%) | +40.82 points | 20 / 0 | +26.53 to +55.10 |
| Procedure | 41 | 25 (60.98%) | 3 (7.32%) | +53.66 points | 23 / 1 | +36.59 to +70.73 |
| Static | 59 | 35 (59.32%) | 7 (11.86%) | +47.46 points | 32 / 4 | +30.51 to +62.71 |
| **Overall** | **149** | **80 (53.69%)** | **10 (6.71%)** | **+46.98 points** | **75 / 5** | **+37.58 to +55.70** |

PRME returned `UNKNOWN` on 14 questions (9.40%); no memory returned it on 66
(44.30%). Neither arm produced an empty response. Independent recomputation from
the two `per_question.jsonl` files reproduced the 80/10 totals, all category
counts, and the 75/5/69 paired outcomes.

## Protocol and validation

The cohort was registered before answer generation. It includes every remaining
deterministically scored web-small question after excluding eight question IDs
seen in earlier LongMemEval-V2 smoke, diagnosis or recovery work. The registered
order, 149 rows in each arm, question inputs, reader settings, configuration
paths, source dataset hashes, saved PRME pack manifest and baseline's zero-memory
contract all passed the fail-closed comparator.

PRME ran first and the no-memory arm ran second, as registered. Both arms used
the same `qwen3.5:9b` model digest, temperature 0.6, top-p 0.95, top-k 20, one
concurrent request, 4,096 maximum completion tokens and no reasoning/thinking.
All failures and `UNKNOWN` answers were retained. No model judge was used.

The [registration](longmemeval-v2-web-unseen-deterministic-v1-registration.json)
and [comparison artifact](longmemeval-v2-web-unseen-deterministic-v1-comparison.json)
contain the machine-readable cohort, settings, artifact hashes, paired
statistics and bootstrap seed. The [execution attestation](longmemeval-v2-web-unseen-deterministic-v1-execution-attestation.json)
records the frozen PRME worktree, installed adapter, upstream harness and local
runtime hashes. It was collected after completion because this schema-1 run
predates the schema-2 launch manifest, so its source-state assurance is weaker
than a preflight execution fence.

## Cost and latency

PRME returned a mean 43,195 memory-context tokens per question and used 6,528,210
prompt tokens in total. No memory used 33,751 prompt tokens. Total reader tokens
were 6,652,186 for PRME and 148,547 for no memory. The PRME memory query itself
averaged 0.520 seconds, with p50 0.481 seconds, p95 1.030 seconds and maximum
1.166 seconds. Its arm took 10,683 seconds; the no-memory arm took 2,691 seconds.

The arm order, long local generation and unequal prompt sizes prevent a clean
end-to-end speed comparison. The measurements do show the principal current
cost: this quality result used very large contexts. Reducing context size while
preserving answers is a higher-value next step than downloading a larger local
reader.

## Claim boundary

This is strong evidence that PRME's memory materially helps a fixed local reader
on the selected held-out LongMemEval-V2 web-small cohort. It does not establish
that PRME leads the market. The cohort excludes judge-dependent question types,
enterprise tasks and questions touched by earlier local work. It uses one reader
family, the local development serving stack, one saved PRME memory artifact and
only the no-memory baseline. Question bootstrap intervals also treat questions
as units even though shared haystacks can create dependence.

The next comparative gate should retain this frozen cohort and add current memory
systems under matched reader, context and ingestion-cost controls. A second
reader family, the prescribed serving stack, judge-dependent categories and
interactive task completion remain necessary for a defensible leadership claim.
