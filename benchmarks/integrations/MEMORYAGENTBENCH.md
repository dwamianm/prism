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

The installer patches the upstream dataset loader to use the pinned dataset
revision. It refuses a different upstream source revision unless the operator
explicitly accepts the unsupported mismatch.

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
configuration file, adapter file, and upstream harness file. It contains no
model outputs or scores and refuses uncommitted benchmark source code.

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
the scratch state before launch.

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
