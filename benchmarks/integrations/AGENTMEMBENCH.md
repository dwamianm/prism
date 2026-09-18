# AgentMemBench operational integration

This integration runs PRME through AgentMemBench's common five-method adapter
contract at the pinned upstream revision
`186c9a54edd47aae42d8b6990520f8e902b60303`. It covers retrieval, rapid
conflicts, tenant isolation, post-delete retrieval absence, concurrent writes,
and scale. The adapter stores every phase in a separate retained pack generation
and records aggregate operation counts for verification.

AgentMemBench calls its deletion metric “audited deletion.” PRME maps the
adapter's `delete(ids)` operation to owner-scoped archival. Archived nodes leave
ordinary retrieval, while the immutable source event remains in the event log.
The published report therefore calls this post-delete retrieval absence and does
not claim physical erasure or GDPR deletion.

## Install

Clone AgentMemBench, check out the pinned revision, and install its declared
dependencies. From a clean PRME checkout, install the adapter and exact dispatch
patch:

```sh
python -m benchmarks.integrations.install_agentmembench \
  /absolute/path/to/AgentMemBench
```

The installer is idempotent. It refuses an unrecognized revision, a conflicting
adapter, or ambiguous patch anchors. The upstream harness imports its supported
systems eagerly, so its declared dependencies are required even when a run
selects only PRME.

## Register

Register the complete workload before execution. Keep registration and run
outputs outside the clean PRME checkout.

```sh
python -m benchmarks.integrations.register_agentmembench \
  --prme-root /absolute/path/to/prme \
  --upstream-root /absolute/path/to/AgentMemBench \
  --data /absolute/path/to/AgentMemBench/data/memdialogue_v2.jsonl \
  --run-id prme_ops_dev \
  --phases conflict,isolation,deletion,concurrency,scale \
  --conflict-pairs 100 \
  --isolation-users 20 \
  --isolation-facts 3 \
  --deletion-records 20 \
  --concurrency-records 40 \
  --workers 1,4,8 \
  --scales 100 \
  --scale-read-queries 100 \
  --top-k 3 \
  --seed 2027 \
  --warmup-writes 0 \
  --output /absolute/path/to/run/registration.json
```

Registration requires clean tracked PRME files. It binds the PRME and upstream
revisions, dataset, adapter, installer, registrar, verifier, installed harness,
and every workload parameter without copying source records.

## Run and verify

Use the same registered parameters with the upstream entry point and an absolute
history directory:

```sh
cd /absolute/path/to/AgentMemBench
PYTHONPATH=/absolute/path/to/prme/src \
  /absolute/path/to/prme/.venv/bin/python \
  -m agentmembench.evaluation.unified_benchmark \
  --system prme \
  --phases conflict,isolation,deletion,concurrency,scale \
  --data /absolute/path/to/AgentMemBench/data/memdialogue_v2.jsonl \
  --conflict-pairs 100 \
  --isolation-users 20 \
  --isolation-facts 3 \
  --deletion-records 20 \
  --concurrency-records 40 \
  --workers 1,4,8 \
  --scales 100 \
  --scale-read-queries 100 \
  --top-k 3 \
  --seed 2027 \
  --warmup-writes 0 \
  --run-id prme_ops_dev \
  --output-dir /absolute/path/to/run/raw \
  --history-dir /absolute/path/to/run/history
```

Then verify before using any aggregate:

```sh
cd /absolute/path/to/prme
python -m benchmarks.integrations.verify_agentmembench \
  --prme-root /absolute/path/to/prme \
  --upstream-root /absolute/path/to/AgentMemBench \
  --data /absolute/path/to/AgentMemBench/data/memdialogue_v2.jsonl \
  --history-dir /absolute/path/to/run/history \
  --registration /absolute/path/to/run/registration.json \
  --result /absolute/path/to/run/raw/prme_prme_ops_dev.json \
  --output /absolute/path/to/run/verification.json
```

Verification checks the registered sources and arguments, the runtime PRME
revision and clean state, each retained phase generation and its operation
counts, and the raw result structure. Its output contains aggregates and hashes
only. It removes retrieval details and concurrency error examples. Do not commit
or publish the upstream raw result because its retrieval phase contains source
records, questions, reference answers, and returned text.

The retrieval phase additionally needs AgentMemBench's configured LLM judge.
Operational phases do not call that judge. Retrieval registration requires an
OpenAI-compatible Ollama endpoint and resolves the named model through
`/api/tags`, binding its immutable digest and the harness's fixed temperature,
disabled reasoning, token limit, response format, concurrency, retry count, and
failure policy:

```sh
python -m benchmarks.integrations.register_agentmembench \
  --prme-root /absolute/path/to/prme \
  --upstream-root /absolute/path/to/AgentMemBench \
  --data /absolute/path/to/AgentMemBench/data/memdialogue_v2.jsonl \
  --run-id prme_retrieval_dev \
  --phases retrieval \
  --retrieval-records 100 \
  --group-size 10 \
  --top-k 5 \
  --seed 2027 \
  --warmup-writes 0 \
  --llm-base-url http://127.0.0.1:11434/v1 \
  --llm-model prme-qwen3.5:35b-a3b-8k \
  --output /absolute/path/to/run/registration.json
```

Pass the same `--llm-base-url` and `--llm-model` arguments to the upstream run.
Verification checks the saved configuration and arguments, then resolves the
live Ollama model again and rejects a changed digest. The installer also changes
the pinned harness's permissive judge behavior: invalid JSON or three failed
requests now abort the run instead of being silently counted as retrieval
misses. A verified development diagnostic supports a bounded engineering
decision; it is not evidence of universal product leadership or a controlled
cross-system latency comparison.
