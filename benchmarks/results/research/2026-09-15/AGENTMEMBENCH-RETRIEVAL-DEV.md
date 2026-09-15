# AgentMemBench judged retrieval development result

PRME achieved 95/100 recall@5 on a preregistered AgentMemBench retrieval
development cohort. The 95% bootstrap interval was 90% to 99%. Every source was
stored and materialized, and the fail-closed local judge completed every
decision without converting provider or parsing errors into misses.

## Protocol

- AgentMemBench revision:
  `186c9a54edd47aae42d8b6990520f8e902b60303`
- PRME revision: `4132a4b27d052b769eabf594aae13e0348985e4d`
- MemDialogue file SHA-256:
  `33632710ae6495b95724df455ff6f9947d231ee68ebc0ef10eb8291fd55ca2a6`
- Seed: `2027`
- Records: 100, stratified across `PERSONAL_FACT` and `TASK_REQUEST`
- Isolation: ten records per owner
- Retrieval limit: 5
- Judge: local `prme-qwen3.5:35b-a3b-8k`, Ollama digest
  `45870b70b6fa65ab09355aeb7901897763ae40745c6cdd11d28bc33a6b818ffc`
- Judge controls: temperature 0, reasoning disabled, 32 output tokens, JSON
  object response, 32 concurrent requests, three attempts, abort on failure

Registration resolved and froze the model digest before execution. Verification
matched the exact PRME and AgentMemBench revisions, dataset, adapter, installer,
registrar, verifier, installed harness, run arguments, retained pack generation,
operation counts, saved judge configuration, and live Ollama digest. The
checked-in [verification artifact](agentmembench-prme-retrieval-35b-dev100-verification.json)
contains aggregates and hashes only.

## Results

| Metric | Result |
|---|---:|
| Write success | 100/100 |
| Materialization success | 100/100 |
| Recall@5 | 95/100 |
| Recall@5 bootstrap interval | 90%–99% |
| Personal-fact recall | 94% |
| Task-request recall | 96% |
| Mean local write latency | 78.38 ms |
| Mean local read latency | 9.30 ms |

A post hoc retrieval-path audit found the exact source record in the top five
for 97/100 questions and at rank one for 85/100. Of the five judged misses,
three returned their exact source at rank one, but the selected memory text did
not contain the reference answer literally. The other two exact sources ranked
sixth and ninth when the owner-scoped ten-record result set was inspected. This
separates a small ranking gap from cases where the benchmark memory record does
not directly state its reference answer.

The raw upstream result remains outside the repository because it contains
source memories, queries, answers, and returned text. Its SHA-256 is bound by the
verification artifact. This one 100-record cohort and one local judge support a
development measurement, not a universal product ranking. The published
AgentMemBench system results use 1,000 records and a different judge setup, so
their headline scores are not a controlled comparison with this run.
