# MemoryAgentBench integration

PRME has a pinned adapter for the official incremental MemoryAgentBench harness.
The benchmark covers accurate retrieval, test-time learning, long-range
understanding, and conflict resolution. The adapter uses only the context chunks
and formatted questions exposed to every memory method. Answers, evaluator
labels, keypoints, and question metadata do not enter the memory pack.

## Pinned inputs

- MemoryAgentBench: `fe1735de8cf8b9908e1e3d3b5612afc815698062`
- `ai-hyz/MemoryAgentBench` dataset:
  `7ea066982b140a19337e17e60d45d4076e042faf`
- PRME embedding: FastEmbed `BAAI/bge-small-en-v1.5`, 384 dimensions
- PRME context budget: 4,096 `cl100k_base` tokens
- PRME packing: `balanced`
- PRME context format: `auditable` by default; `compact` is an explicit trial arm

The installer patches the upstream dataset loader to use the pinned dataset
revision. It refuses a different upstream source revision unless the operator
explicitly accepts the unsupported mismatch.

The installed PRME configuration uses `Agentic_memory_prme_rag`. Upstream
dispatch sees `prme` and invokes the PRME adapter, while template selection sees
`rag` first and supplies the same formatted question used by
`Simple_rag_bm25`. Registrations require that exact name so a matched trial
cannot silently return to the distinct agentic-memory reader prompt.

Adapter schema 6 stores blank-line-delimited units and serial-numbered facts
independently, applying the configured character limit only when one semantic
unit is too large. This keeps individual demonstrations and facts from becoming
mixed-topic embedding records while preserving the concatenated source text
exactly, including units split across upstream chunk boundaries. It also removes
the pinned test-time-learning classifier wrapper from retrieval queries and
embeds the terminal `Question:` body. Other tasks retain the pinned upstream
query extraction. The manifest records both policies, registrations bind the
derived retrieval-query hash before inference, and captures bind that hash to
the durable receipt.

## Install

Clone and check out the pinned upstream revision, install its dependencies, and
install PRME from the revision being evaluated. From the PRME checkout:

```sh
python -m benchmarks.integrations.install_memoryagentbench \
  /absolute/path/to/MemoryAgentBench
```

The operation is idempotent. It copies `methods/prme.py` and the PRME agent
configuration, adds the narrow dispatch hooks to `agent.py`, and pins the
dataset loader. It also gives PRME saved-state paths the sub-dataset identity so
sequential competency runs cannot silently reuse another task's pack. Existing
conflicting files or ambiguous source anchors fail without leaving a partial
install.

## Run

Create a registration before the first reader call. Run this from a clean,
frozen PRME worktree and write the registration outside that worktree so adding
the artifact cannot change the evaluated commit:

```sh
PRME_REVISION=$(git -C /absolute/path/to/prme rev-parse HEAD)
python -m benchmarks.integrations.register_memoryagentbench \
  --prme-root /absolute/path/to/prme \
  --upstream-root /absolute/path/to/MemoryAgentBench \
  --agent-config /absolute/path/to/MemoryAgentBench/configs/agent_conf/RAG_Agents/gpt-4o-mini/PRME_gpt-4o-mini.yaml \
  --dataset-config /absolute/path/to/MemoryAgentBench/configs/data_conf/Accurate_Retrieval/EventQA/Eventqa_64k.yaml \
  --expected-prme-revision "$PRME_REVISION" \
  --output /absolute/path/to/run/eventqa-registration.json
```

The registrar uses the pinned upstream preprocessing path. It records hashes
for every prepared source chunk, query, answer, query-to-context assignment,
configuration file, adapter file, and upstream harness file. It also binds the
installed dataset, NLTK and tiktoken versions, the English Punkt resource tree,
and the exact `gpt-4o-mini` tokenizer table used by upstream sentence chunking.
It contains no model outputs or scores and refuses uncommitted benchmark source
code.

Set a distinct path-safe `prme_run_id` for each PRME arm. The installer includes
it in the saved-agent directory, and the adapter binds it into manifests and
retrieval captures. This permits auditable and compact configurations using the
same reader model to coexist without deleting, reusing, or overwriting either
memory pack.

For a registered development subset, copy the upstream dataset configuration,
add a positive `max_test_queries`, register that copied file, and pass the same
value to upstream `--max_test_queries_ablation`. The registration includes only
the source contexts the harness reaches before that query limit. Do not apply an
unregistered command-line cap to a full-task registration.

Then use the upstream entry point with the registered configuration. These
four commands exercise one representative configuration per competency:

```sh
python main.py \
  --agent_config configs/agent_conf/RAG_Agents/gpt-4o-mini/PRME_gpt-4o-mini.yaml \
  --dataset_config configs/data_conf/Accurate_Retrieval/EventQA/Eventqa_64k.yaml

python main.py \
  --agent_config configs/agent_conf/RAG_Agents/gpt-4o-mini/PRME_gpt-4o-mini.yaml \
  --dataset_config configs/data_conf/Test_Time_Learning/ICL/ICL_banking77.yaml

python main.py \
  --agent_config configs/agent_conf/RAG_Agents/gpt-4o-mini/PRME_gpt-4o-mini.yaml \
  --dataset_config configs/data_conf/Long_Range_Understanding/Detective_QA.yaml

python main.py \
  --agent_config configs/agent_conf/RAG_Agents/gpt-4o-mini/PRME_gpt-4o-mini.yaml \
  --dataset_config configs/data_conf/Conflict_Resolution/Factconsolidation_mh_6k.yaml
```

After a task completes, verify the result before reading or publishing its
aggregate score:

```sh
python -m benchmarks.integrations.verify_memoryagentbench \
  --prme-root /absolute/path/to/prme \
  --upstream-root /absolute/path/to/MemoryAgentBench \
  --registration /absolute/path/to/run/eventqa-registration.json \
  --result /absolute/path/to/MemoryAgentBench/outputs/prme-gpt-4o-mini/Accurate_Retrieval/RESULT_FILE.json \
  --agent-config /absolute/path/to/MemoryAgentBench/configs/agent_conf/RAG_Agents/gpt-4o-mini/PRME_gpt-4o-mini.yaml \
  --dataset-config /absolute/path/to/MemoryAgentBench/configs/data_conf/Accurate_Retrieval/EventQA/Eventqa_64k.yaml \
  --output /absolute/path/to/run/eventqa-verification.json
```

Verification requires complete, ordered result rows and metrics, exact
registered inputs, one context capture per query, valid PRME request IDs,
durable retrieval receipts, exact token recounts within the registered budget,
and completed memory manifests with every registered source chunk. A mismatch
stops verification instead of producing a partial report.

The verifier opens each completed DuckDB pack read-only and resolves the exact
owner-scoped `RETRIEVAL_REQUEST`. It validates the stored receipt checksum,
request and owner identity, replayable candidate ranking, context hash, packing
budget and format, result limit, project scope, and in-context candidate count.
Its report commits an aggregate hash of the authenticated receipt checksums;
the capture's `receipt_persisted` flag alone is not accepted as evidence.

The checked-in configuration uses the same `gpt-4o-mini` temperature and
reader family as the upstream memory baselines. For an explicitly labelled
local development run, change the copied config's model and point the PRME arm
at an OpenAI-compatible reader:

```sh
export PRME_MAB_OPENAI_BASE_URL=http://127.0.0.1:11434/v1
export PRME_MAB_OPENAI_API_KEY=ollama
```

A local-reader result is not directly comparable to a published result using a
different reader. Run every compared arm with the same model, generation
parameters, task data, and judging path.

Copied PRME and baseline configs may set `reader_reasoning_effort` and
`reader_seed`. The pinned installer validates these fields and forwards them to
the OpenAI-compatible request; the BM25 path also always passes the dataset's
generation limit. Use the same values in every arm. PRME records them in its pack
identity and each retrieval capture, while the registration and upstream result
retain the complete agent configuration. Omitted values preserve the provider
defaults.

For a registered compact-context trial, set `prme_context_format: compact` in a
copied agent configuration before registration. The setting is included in the
adapter manifest, every retrieval capture, and the outcome-free registration's
configuration hash. Use a distinct agent/output path so no auditable-format pack
can be reused. Compact output remains bound by the same 4K token budget and
durable receipt checks.

## Matched BM25 control

For a common-reader comparison, copy the pinned upstream BM25 configuration and
set the same `model`, `temperature`, `reader_reasoning_effort`, and `reader_seed`
as the PRME arm. Also add a unique `retrieval_run_id` and a fixed
`memory_timestamp`:

```yaml
agent_name: Simple_rag_bm25
model: qwen3.5:9b
temperature: 0.0
reader_reasoning_effort: none
reader_seed: 42
retrieval_run_id: bm25-dev20-v1
memory_timestamp: "2000-01-01 00:00:00"
input_length_limit: 10000000
buffer_length: 200
output_dir: ./outputs/bm25-qwen35-9b-dev20
retrieve_num: 10
```

Register the BM25 arm before its first reader request:

```sh
python -m benchmarks.integrations.register_memoryagentbench_bm25 \
  --prme-root /absolute/path/to/prme \
  --upstream-root /absolute/path/to/MemoryAgentBench \
  --agent-config /absolute/path/to/BM25_qwen35-9b-dev20.yaml \
  --dataset-config /absolute/path/to/Eventqa_64k-dev20.yaml \
  --expected-prme-revision "$PRME_REVISION" \
  --output /absolute/path/to/run/eventqa-bm25-registration.json
```

After the upstream run completes, verify it before inspecting its aggregate
score:

```sh
python -m benchmarks.integrations.verify_memoryagentbench_bm25 \
  --prme-root /absolute/path/to/prme \
  --upstream-root /absolute/path/to/MemoryAgentBench \
  --registration /absolute/path/to/run/eventqa-bm25-registration.json \
  --result /absolute/path/to/MemoryAgentBench/outputs/bm25-qwen35-9b-dev20/Accurate_Retrieval/RESULT_FILE.json \
  --agent-config /absolute/path/to/BM25_qwen35-9b-dev20.yaml \
  --dataset-config /absolute/path/to/Eventqa_64k-dev20.yaml \
  --output /absolute/path/to/run/eventqa-bm25-verification.json
```

The fixed timestamp replaces the upstream wall-clock string inside each BM25
document. It is constant across all documents and is recorded in the hashed
configuration. The verifier reloads the pinned official inputs, recreates the
formatted documents, repeats query extraction and `rank-bm25` ordering, and
requires every isolated context capture to match exactly. It also binds the
installed NumPy and `rank-bm25` versions and hashes the latter's ranking source.
The installer uses LangChain's current `BM25Retriever.invoke()` entry point, and
registration binds the exact installed wrapper version and source hash.
The same preprocessing identity used by the PRME arm is required here as well.
These checks establish a reproducible matched lexical control; they do not make
it a product-equivalent memory system or a published-reader comparison.

## Paired comparison

After both arms verify, create a JSON manifest with one entry per task:

```json
{
  "schema_version": 1,
  "kind": "memoryagentbench-paired-manifest",
  "tasks": [
    {
      "label": "eventqa",
      "prme_registration": "eventqa-prme-registration.json",
      "bm25_registration": "eventqa-bm25-registration.json",
      "prme_verification": "eventqa-prme-verification.json",
      "bm25_verification": "eventqa-bm25-verification.json",
      "prme_result": "/absolute/path/to/prme-result.json",
      "bm25_result": "/absolute/path/to/bm25-result.json"
    }
  ]
}
```

Relative paths resolve from the manifest directory. Compare the complete matrix:

```bash
python -m benchmarks.integrations.compare_memoryagentbench \
  --manifest /absolute/path/to/paired-manifest.json \
  --output /absolute/path/to/paired-comparison.json
```

The comparator requires each result hash to match a complete independent
verification, rechecks identical source, question, answer, reader and
preprocessing identities, and applies the upstream task-to-accuracy mapping:
`substring_exact_match` for accurate retrieval and conflict resolution, and
`exact_match` for test-time learning and long-range understanding. Its report
contains per-task and aggregate paired intervals, exact McNemar tests and context
sizes without copying raw questions, references or model answers.

## Storage and recovery contract

The upstream harness chunks each source before it reaches a memory method. PRME
further splits those inputs losslessly at 6,000 characters so a single large
source record cannot consume the entire 4K output budget. Every piece is stored
as observed tool output under one owner and one context session. Generic
opportunistic organization, store-time supersedence, query reformulation, QA
pairing, surprise gating, and reranking are disabled for the benchmark.

The adapter writes a manifest before the first source insert, records every
source digest and piece count, then marks the pack complete only when the
upstream harness calls `save_agent()`. Interrupted and configuration-mismatched
packs fail closed. A completed pack reuses its stored UTC query clock, so
retrieval ranking does not drift when a run resumes later.

Every query writes the complete rendered context, its SHA-256 digest, the PRME
retrieval request ID, query and context identity, measured token and entry
counts, receipt durability, completed-manifest hash, and both pinned revisions
under the configured output directory. The reader layout matches the upstream
BM25 baseline: retrieved memory precedes the unchanged formatted question under
the common system message.

MemoryAgentBench reuses saved agent directories even with its `--force` option.
A genuinely fresh arm therefore needs an absent agent directory and output
file. Preserve the old artifacts, then select a new experiment path or remove
the scratch state before launch. The patched RAG capture path includes
`retrieval_run_id`, so separate BM25 trials do not silently read or overwrite
one another's retrieved contexts.

The installer also fixes the pinned upstream resume path so list-valued
reference answers remain lists. Without that patch, resuming converts them to a
single scalar and can change evaluation inputs. Timing checkpoint arrays in the
upstream output cover only the current process after a resume; the verifier
accepts that narrow timing limitation while still requiring every result,
metric, retrieval capture, and registered input.

## Interpretation boundary

The public conflict-resolution data presents a numbered fact pool and instructs
the reader to prefer the larger serial number. This adapter preserves that pool
as observed source text. A result measures whether retrieval retains the needed
facts and whether the fixed reader resolves them; it is not a direct test of
PRME's explicit supersedence transactions.

Likewise, the test-time-learning suite retrieves labelled demonstrations into a
fixed reader. It evaluates in-context adaptation from memory, not PRME's scoped
feedback fitting or profile activation. Long-range-understanding scores combine
retrieval and reader reasoning. Report these boundaries with every result, plus
task coverage, failures, context tokens, ingestion time, and query latency.

The integration tests prove adapter registration, exact source preservation,
manifest fencing, restart behavior, retrieval-context capture, and pinned-tree
installation. They are not task-quality evidence. A scored claim requires the
complete registered upstream tasks under a fixed common reader.

The [four-suite real-data ingress smoke](../results/research/2026-09-14/MEMORYAGENTBENCH-INGRESS-SMOKE.md)
also passed one source and retrieval from each competency family with no reader
or answer scoring. It is a transport, persistence, budget, and receipt check.
