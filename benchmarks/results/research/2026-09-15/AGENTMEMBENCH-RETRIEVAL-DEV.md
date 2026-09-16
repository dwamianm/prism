# AgentMemBench judged retrieval results

PRME achieved 979/1,000 recall@5 on a preregistered, official-size
AgentMemBench retrieval run. The 95% bootstrap interval was 97.0% to 98.7%.
Every source was stored and materialized, and the fail-closed local judge
completed every decision without converting provider or parsing errors into
misses. An earlier 100-record development run scored 95/100.

## Protocol

- AgentMemBench revision:
  `186c9a54edd47aae42d8b6990520f8e902b60303`
- PRME revision: `e237f228fe8aa6e26d48ab718f857f1148ee8b66`
- MemDialogue file SHA-256:
  `33632710ae6495b95724df455ff6f9947d231ee68ebc0ef10eb8291fd55ca2a6`
- Seed: `2027`
- Records: 1,000, stratified across `PERSONAL_FACT` and `TASK_REQUEST`
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
checked-in
[official-size verification artifact](agentmembench-prme-retrieval-35b-full1000-verification.json)
contains aggregates and hashes only. The
[development artifact](agentmembench-prme-retrieval-35b-dev100-verification.json)
retains the preceding 100-record result.

## Results

| Metric | Result |
|---|---:|
| Write success | 1,000/1,000 |
| Materialization success | 1,000/1,000 |
| Recall@5 | 979/1,000 |
| Recall@5 bootstrap interval | 97.0%–98.7% |
| Personal-fact recall | 98.8% |
| Task-request recall | 97.0% |
| Mean local write latency | 77.24 ms |
| Mean local read latency | 10.97 ms |

The earlier development cohort scored 95/100 with a 90% to 99% bootstrap
interval, including 94% personal-fact recall and 96% task-request recall. The
larger run therefore confirmed the result at the benchmark's published record
count and narrowed the interval without motivating a retrieval-weight change.

A post hoc audit of the earlier development run found the exact source record
in the top five for 97/100 questions and at rank one for 85/100. Of its five
judged misses, three returned their exact source at rank one, but the selected
memory text did not contain the reference answer literally. The other two exact
sources ranked sixth and ninth when the owner-scoped ten-record result set was
inspected. This separates a small ranking gap from cases where the benchmark
memory record does not directly state its reference answer.

The raw upstream results remain outside the repository because they contain
source memories, queries, answers, and returned text. Their SHA-256 values are
bound by the verification artifacts. These runs use one dataset and one local
judge, so they do not establish a universal product ranking. Published
AgentMemBench system results use a different judge setup, making their headline
scores unsuitable for a controlled comparison with this run.
