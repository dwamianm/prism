# BEAM integration

PRME can run behind the unmodified OSS HTTP client in the official
[Mem0 memory-benchmarks repository](https://github.com/mem0ai/memory-benchmarks/tree/4b61c5d31b9c668a12b4f5e78064248a02c82d2b).
The integration was reviewed against commit
`4b61c5d31b9c668a12b4f5e78064248a02c82d2b`. BEAM covers ten abilities over
100K through 10M-token conversations: information extraction, multi-session
reasoning, knowledge updates, temporal reasoning, abstention, contradiction
resolution, event ordering, instruction following, preference following, and
summarization.

The adapter implements only the two Mem0 OSS calls used by this runner:
`POST /memories` and `POST /search`. It receives source chat messages during
ingestion and the neutral probing question during retrieval. It never loads the
dataset, question rubric, answer nuggets, question type, source-chat labels, or
the generated answer. Non-empty benchmark-side metadata and custom extraction
instructions are rejected so labels cannot silently enter memory.

BEAM does not send an explicit session identifier. Adapter schema 2 groups
chunks that share the same observation timestamp, which is the strongest source
session boundary present on the neutral HTTP request. A request without a
timestamp remains its own session. This avoids treating a user's complete
multi-session history as one adjacent-turn sequence; equal timestamps from two
independent source sessions remain indistinguishable at this boundary.

## Start PRME

Use a fresh directory for a new run:

```sh
uv sync --dev --extra api --extra evaluation
uv run python -m benchmarks.integrations.beam_service \
  --directory /tmp/prme-beam-100k \
  --profile raw \
  --port 8889
```

`raw` stores every user and assistant source turn through the public
`store_with_receipt()` path. It is deterministic apart from the configured
embedding runtime and does not call an extraction model. `extracted` exercises
the complete durable `ingest()` path:

```sh
uv run python -m benchmarks.integrations.beam_service \
  --directory /tmp/prme-beam-100k-extracted \
  --profile extracted \
  --extraction-provider ollama \
  --extraction-model qwen3.5:9b \
  --extraction-base-url http://127.0.0.1:11434/v1 \
  --port 8889
```

The service writes `beam_adapter_manifest.json` beside the pack. Restart an
interrupted service with the same flags plus `--resume`; a profile or adapter
revision mismatch fails before opening the pack. The manifest fingerprints the
adapter source, PRME version, embedding, scoring, packing, thread count, and
non-secret extraction settings. Extracted runs use a 60-second durable-work
lease so a crashed service can recover inside the upstream client's request
timeout. Exact upstream retries are
idempotent across service restarts using a hash of the source timestamp and
ordered messages. That necessarily treats an intentionally repeated identical
HTTP request at the same timestamp as a retry because the upstream request has
no chunk identity.

## Run the pinned upstream harness

For the registered one-conversation raw retrieval workflow, use the launcher
from a clean PRME worktree at the registration's revision. It starts the raw
adapter, invokes the pinned upstream client, fixes the run identity and complete
question selection, and writes source and dataset attestation before ingestion:

```shell
uv run --extra api python -m benchmarks.integrations.run_beam \
  /absolute/path/to/memory-benchmarks \
  /absolute/path/to/new-beam-execution \
  --registration benchmarks/results/research/2026-09-14/beam-100k-raw-v1-registration.json \
  --dataset /absolute/path/to/frozen/beam_100K.json
```

The dataset cache must use the upstream runner's normalized JSON format. The
registration pins its SHA-256 digest and the Hugging Face dataset revision. Use
`--resume` with the same output directory and registration after an interrupted
run; both PRME and upstream checkpoints must already exist.

Registration schema 2 supports a scored raw-memory run through the same
launcher. It binds separate answerer and judge model names, their complete
installed Ollama manifest digests, the loopback OpenAI-compatible endpoint, worker/rate controls,
question selection, cutoff, dataset, and source files before the first model
call. The launcher verifies both installed model digests and supplies the
registered values to the unmodified upstream harness. The execution manifest
retains the exact protocol and model identities; registered validation rejects
any drift. Answerer and judge must be distinct models so a scored run cannot
silently grade itself with the same model. An Ollama cloud manifest identifies
its remote host and model but does not pin remote model weights; registrations
and reports must disclose that weaker reproducibility boundary.

Registration schema 3 also supports scored `extracted` runs. In addition to the
answerer and judge identities, it binds the Ollama extraction model digest,
endpoint, reasoning mode, temperature, timeout, and durable-work lease before
ingestion. The launcher passes those exact settings to the adapter and the
validator compares the executed adapter manifest with the registration. Schema
1 predict-only and schema 2 raw scored artifacts retain their original meaning.

Registration schema 4 additionally binds the extraction schema-retry count.
Adapter schema 3 reports the applied count, preventing the library default from
changing an attested extracted run without detection. Earlier schemas retain
their original fields and meaning.

In a checkout of the pinned upstream commit, start with one retrieval-only
conversation. This downloads only the selected public BEAM split and preserves
the upstream ingestion and prediction checkpoints:

```sh
python -m benchmarks.beam.run \
  --project-name prme-beam-100k-smoke \
  --backend oss \
  --mem0-host http://127.0.0.1:8889 \
  --chat-sizes 100K \
  --conversations 0 \
  --top-k 50 \
  --top-k-cutoffs 50 \
  --predict-only \
  --dataset-cache-dir /absolute/path/to/beam-cache
```

The upstream runner logs failed memory additions but still checkpoints those
chunks, and its model client can return an empty string after exhausting
retries. Validate the output before reading aggregate quality:

```sh
uv run --extra api python -m benchmarks.integrations.validate_beam \
  /absolute/path/to/new-beam-execution/upstream-results/predicted_prme-beam-100k-raw-v1 \
  --chat-sizes 100K \
  --conversations 0 \
  --question-types abstention,contradiction_resolution,event_ordering,information_extraction,instruction_following,knowledge_update,multi_session_reasoning,preference_following,summarization,temporal_reasoning \
  --registration benchmarks/results/research/2026-09-14/beam-100k-raw-v1-registration.json \
  --execution-root /absolute/path/to/new-beam-execution \
  --output /tmp/prme-beam-100k-smoke-validation.json
```

Add `--scored --cutoffs 50` for the scored command above. Validation requires
every selected ingestion checkpoint, zero failed chunks, no partial progress,
two questions per selected ability and conversation, exact owner/query
continuity, finite retrieval records, and complete non-empty answer and nugget
verdicts. It hashes every accepted artifact and exits nonzero on any gap.

For a scored run, create a schema-2 registration and pass it to the same
`run_beam` command. The current registered profile is intentionally narrow: one
100K conversation, every ability, top-50 recall, one cutoff, raw source storage,
and Ollama through `http://127.0.0.1:11434/v1`. Models may execute locally or
through an explicitly recorded Ollama cloud manifest. The upstream harness fixes
generation temperature at zero and its own retry/timeout behavior; those
runtime sources are hashed by the registration. Use `--resume` after an
interruption so the launcher rechecks every bound input before reusing output.

## Interpretation boundaries

- The raw and extracted profiles measure different systems and must be reported
  separately. Raw mode evaluates source-preserving indexing; extracted mode also
  evaluates the named extraction model.
- The upstream `top-k` is a memory-count cutoff, not a token budget. PRME may
  return fewer candidates when its configured candidate paths are exhausted.
  Report actual result counts and serialized answer-prompt tokens.
- Returned timestamps use source `event_time` when present, and retrieval decay
  uses the latest ingested source time for that owner, falling back to its saved
  admission time. This avoids changing rankings because the benchmark was
  resumed on another day.
- The official result combines retrieval, answer generation, and rubric judging.
  Pin all three layers and retain every error. A retrieval-only smoke is not an
  answer-quality score.
- The fail-closed validator confirms structural completion. It cannot determine
  whether a fluent answer or judge rationale is semantically correct.
- Mem0's published platform scores use proprietary managed behavior and a
  different model stack. Running PRME through this open harness does not make
  those numbers a matched baseline.
- The adapter is a loopback benchmark service. It does not provide the
  authenticated production API, deletion semantics, or hosted tenant grants.

The source inspection record is
[`beam-upstream-audit.json`](../results/research/2026-09-14/beam-upstream-audit.json).
The pinned official client also passed an
[authored loopback preflight](../results/research/2026-09-14/beam-native-client-preflight.json).
The registered raw predict-only execution and its interpretation boundary are
reported in
[`BEAM-100K-RAW.md`](../results/research/2026-09-14/BEAM-100K-RAW.md).
The first accepted scored raw development execution and its rejected precursor
trials are reported in
[`BEAM-100K-RAW-SCORED.md`](../results/research/2026-09-15/BEAM-100K-RAW-SCORED.md).
