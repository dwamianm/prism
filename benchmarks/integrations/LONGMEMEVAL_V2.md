# LongMemEval-V2 PRME integration

Reviewed and implemented against the
[official repository](https://github.com/xiaowu0162/LongMemEval-V2/tree/2cc8c540bdb87fe6761629b585e727e1c4704520)
at commit `2cc8c540bdb87fe6761629b585e727e1c4704520` and the
[public dataset](https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/tree/f152293e235517d504809563c833d7190b8c713b)
at revision `f152293e235517d504809563c833d7190b8c713b`.

LongMemEval-V2 evaluates memory from agent experience rather than chat recall.
Its 451 questions cover static and dynamic environment state, workflows,
environment gotchas, and premise awareness across web and enterprise domains.
The small tier shares a 100-trajectory haystack within each domain; medium
generally has 500. This is materially different from LongMemEval-V1 source-turn
retrieval.

## Install the adapter

Run the installer from this PRME checkout. It verifies the pinned upstream Git
revision, installs the adapter and its full and compact configurations
atomically, registers the adapter, and is safe to run again when the installed
files are unchanged. It refuses revision drift or conflicting destination files
rather than silently changing the evaluation:

```sh
python -m benchmarks.integrations.install_longmemeval_v2 \
  /absolute/path/to/LongMemEval-V2
```

## Run with durable reader checkpoints

For long evaluations, use PRME's launcher around the pinned official harness.
It appends and fsyncs each completed reader output before scoring begins. On an
exact rerun it preserves the original prompt rows, validates every prompt and
reader-setting hash, resumes only missing generations, and then invokes the
unchanged upstream scorer. This prevents a late judge quota or network failure
from discarding completed reader work:

```sh
python -m benchmarks.integrations.run_longmemeval_v2 \
  /absolute/path/to/LongMemEval-V2 \
  --registration /absolute/path/to/registration.json \
  -- \
  --domain web \
  --questions-path "$DATA_ROOT/questions.jsonl" \
  --haystack-path "$DATA_ROOT/haystacks/lme_v2_small.json" \
  --trajectories-path "$DATA_ROOT/trajectories.jsonl" \
  --memory-config-path evaluation/memory_configs/prme.json \
  --output-dir runs/prme_web_small \
  --model Qwen/Qwen3.5-9B \
  --base-url http://localhost:8023/v1 \
  --memory-context-max-tokens 65536
```

The launcher installs or verifies the adapter before each run. Its checkpoint
is `reader_outputs.checkpoint.jsonl` inside the official output directory. To
resume, repeat the command with the same prompt and reader arguments. If memory
was saved separately, keep `--load-memory-dir` on both runs. Changed questions,
haystacks, prompt rows, model names, endpoints, sampling controls, or token caps
fail explicitly instead of mixing results.

For new preregistered studies, use registration schema 2 and pass the same file
to both arms with `--registration`. Before prompt construction, the launcher
requires the registered PRME and upstream commits, rejects PRME worktree changes
or upstream changes beyond its installed adapter/configuration and registry
import, and writes an immutable
`execution_manifest.json`. That manifest hashes the launcher, installer, source
and installed adapter, both supplied PRME configurations, upstream harness, and
the actual configuration selected for each arm. For a loaded PRME arm it also
streams every regular file in the saved-memory directory into one deterministic
path, size and content identity before the memory is opened; symlinks and special
entries fail closed. The registration must contain the selected PRME and baseline
configuration hashes plus the complete initial memory-artifact identity. Resuming
under a different source, selected configuration or memory artifact fails before
generation, and a new launcher will not claim outputs created before the manifest
existed. The paired comparator requires matching source manifests and the
registered per-arm invocation hashes for schema-2 registrations. Schema-1
registrations remain readable for studies that were already running when source
manifests were introduced.

For a local Ollama reader, pass its native origin with
`--ollama-api-base-url http://127.0.0.1:11434`. The launcher resolves the exact
installed model digest, size, architecture details, capabilities, required
runtime, and Ollama server version before prompt construction. A registration
can bind that object as `reader.runtime_identity`; a changed tag or runtime then
fails before any generation, and execution-manifest schema 3 preserves the
observed identity for comparison.

Ollama cloud readers use the same option. Their runtime identity records the
local cloud-manifest digest and size, remote host and model name, capabilities,
and Ollama server version. It also records `remote_weights_pinned: false` because
Ollama does not expose an immutable remote weight revision. This makes repeated
cloud runs auditable without misrepresenting the local manifest digest as a hash
of the hosted weights.

Loaded PRME runs also record a separate identity for the `prme_pack/` payload.
This lets a registered multi-budget curve prove that every arm started from the
same graph, indexes, attachments, and adapter manifest even though each clone's
top-level `memory_config.json` declares a different token budget. The curve
comparator additionally requires every other adapter setting to match and emits
only aggregate metrics and artifact hashes.

Ollama 0.34 supports reasoning control through its
[OpenAI-compatible API](https://docs.ollama.com/api/openai-compatibility). For a
faster development reader profile, use the local model name and add
`--reader-reasoning-effort none` before the `--` separator. This is an explicit
reader-configuration change and must be reported with results. The upstream
`--reader-disable-thinking` special case only targets the exact
`Qwen/Qwen3.5-9B` model string; it does not disable reasoning for an Ollama model
named `qwen3.5:9b`.

Install this PRME checkout into the upstream Python 3.11 environment, prepare
the official data, and export its root so relative screenshot paths can be
resolved:

```sh
export DATA_ROOT=/absolute/path/to/longmemeval-v2
python evaluation/harness.py \
  --domain web \
  --questions-path "$DATA_ROOT/questions.jsonl" \
  --haystack-path "$DATA_ROOT/haystacks/lme_v2_small.json" \
  --trajectories-path "$DATA_ROOT/trajectories.jsonl" \
  --memory-config-path evaluation/memory_configs/prme.json \
  --output-dir runs/prme_web_small \
  --model Qwen/Qwen3.5-9B \
  --base-url http://localhost:8023/v1 \
  --memory-context-max-tokens 65536
```

Run the enterprise domain separately with the same reader and context settings.
The adapter asks PRME for at most 32,768 `cl100k_base` tokens. The larger
upstream ceiling leaves room for Qwen's independently measured chat-template
and image tokens; the harness records and enforces the actual final count.
Use `evaluation/memory_configs/prme_compact.json` for an explicit 4,096-token
compact-renderer arm. The full preset explicitly uses the auditable renderer.
A saved-memory run must also use a copied `memory_config.json` with the same
budget and format settings; the comparator hashes that loaded configuration
independently, reports the effective format, and enforces an explicit registered
`context_format` when present. Historical configurations without the field retain
their auditable meaning. Use the official combine and leaderboard utilities for
aggregate metrics.

The completed [4K-budget development study](../results/research/2026-09-14/LONGMEMEVAL-V2-WEB-COMPACT4K-DEVELOPMENT.md)
reduced mean reader memory context by 83.05% but scored 49/149 versus 80/149 for
the earlier 32K arm on the same known cohort. It documents a budget tradeoff and
does not support replacing the larger quality reference. Despite the historical
preset name, both arms in that completed study used auditable rendering. The
current compact preset declares `"context_format": "compact"`; a future study
therefore needs a new registration and saved-memory configuration.

## Data and lifecycle contract

The adapter allowlists the released trajectory ID, domain, environment, goal,
outcome, start URL, and ordered state fields. It does not read question IDs,
categories, answers, evaluation functions, or construction metadata. State
indices must be unique, contiguous, and ordered. An agent thought is labelled
as unverified in every derived trace; it is evidence of the recorded trajectory,
not proof that the thought was correct.

Each trajectory creates an observed overview, compact ordered procedure traces,
and bounded chunks of every raw state. Procedure traces retain goals, page URLs,
unverified thoughts, and observed transition actions without repeating the
large accessibility tree at every step. Raw state chunks preserve the full URL,
action, thought, accessibility tree, state identity, and source screenshot.
Every chunk repeats the domain, environment, trajectory goal, and source
identity. The adapter explicitly labels each action as the incoming transition
to its destination state, matching the released dataset semantics.

PRME's generic adjacent-session expansion is disabled for this adapter because
the compact procedure trace already supplies ordered session context. Expanding
arbitrary neighboring raw chunks duplicates large accessibility trees and can
displace independently relevant evidence from a bounded result set.

The adapter exposes PRME's deterministic two-stage episode route through the
optional `episode_context_top_k`, `episode_context_local_k`, and
`episode_context_score_decay` memory parameters. It remains disabled by default.
When enabled, it can reserve query-relevant records from the best candidate-backed
trajectory sessions without restoring generic adjacent-node expansion. Trial
configurations must bind all three values and compare answer quality before
promoting this policy for LongMemEval-V2.

Use the fail-closed comparator for a registered two-arm episode trial:

```bash
python -m benchmarks.integrations.compare_longmemeval_v2_episode_policy \
  --arm flat=/absolute/path/to/flat/run \
  --arm episode=/absolute/path/to/episode/run \
  --registration /absolute/path/to/registration.json \
  --output /absolute/path/to/comparison.json
```

It verifies the frozen inputs, source revisions, reader runtime, pack payload,
selected configurations, and the effective episode policy captured with every
prompt before producing aggregate paired statistics.

Exact duplicate inserts are idempotent. A changed trajectory or screenshot
content, or an interrupted partial insert, fails explicitly instead of silently
reusing stale evidence or duplicating state. The recovery action for an
interrupted benchmark insert is to rebuild that scratch pack. The manifest
records source-state and inserted-node counts, and long trajectories checkpoint
and print progress every 100 nodes. Schema 3 also records the latest successfully
inserted event time as the retrieval reference clock. Every query against that
pack reuses the same clock, so recency and relative-time scoring do not drift
when a saved run is resumed days later.

Existing schema 2 packs remain queryable without rewriting their evidence. The
adapter derives their clock from the newest stored node and marks the source as
`legacy_max_created_at` in post-query metadata. They are read-only: rebuild a
schema 3 scratch pack before adding trajectories. A schema 3 pack with completed
trajectories but no clock fails closed rather than falling back to wall time.

`save_memory()` includes the adapter manifest, copied screenshot attachments,
PRME event and operation logs, graph tables, vector index, and lexical index.
The manifest pins the adapter schema and upstream code revision. This is an
adapter-level extension to PRME's standard text memory pack. Saving closes the
source client and reopens it lazily only if the upstream harness makes another
query, so a completed save cannot leave a client writing into a discarded
temporary directory during process shutdown.

## Query and budget boundary

PRME receives only the question text. It returns its exact token-budgeted
product context in bounded text items plus up to eight distinct screenshots
belonging to included state nodes. Bounded items matter because the upstream
harness re-tokenizes with the reader processor and truncates at item boundaries;
a small tokenizer difference cannot discard one monolithic context item.

The optional question image is currently not interpreted during retrieval. The
reader still receives that question image from the official harness, and PRME
can return source screenshots, but retrieval selection itself is text-only. The
post-query metadata records this limitation. A result must therefore be labelled
as text retrieval with multimodal evidence return, not visual query retrieval.
The metadata also records the exact query reference time and whether the clock
came from the schema 3 manifest or a read-only schema 2 pack.

## Evaluation status and resources

The synthetic contract tests cover upstream registration, public-field
allowlisting, insert/query, screenshot return, exact duplicate handling,
changed and interrupted input rejection, and save/load. They do not constitute
a benchmark score. A publishable result still requires both domains, the
released evaluator, the fixed reader, the supplied no-memory and RAG baselines,
complete failure accounting, and separate indexing/query latency.

An official one-question pipeline smoke has also completed against the pinned
web-small data and a locally served Qwen3.5 9B reader. It validates the complete
100-trajectory indexing, save/load, downstream token counting, evidence-image
loading, generation, deterministic evaluation, and clean shutdown path. See the
[run record](../results/research/2026-09-14/LONGMEMEVAL-V2-SMOKE.md). One selected
question is not an accuracy estimate and must not be compared with leaderboard
results.

A frozen seven-question web-small development cohort subsequently exposed raw
BM25 leakage in supplementary retrieval, weak procedure packing, and excessive
local-reader completion lengths. The scoring and hierarchy repairs recovered
both failed deterministic questions in a targeted 2/2 confirmation. See the
[development record](../results/research/2026-09-14/LONGMEMEVAL-V2-WEB-DEVELOPMENT.md).
The four judge-dependent questions remain unofficial because the configured
OpenAI account returned HTTP 429 `insufficient_quota`; a local 35B substitute is
reported separately and is not leaderboard compatible.

At the pinned dataset revision, trajectories occupy about 1.20 GB and the two
screenshot archives total about 5.92 GB compressed. Downloading only the small
haystack map does not provide its source trajectories or screenshots. Preserve
the upstream dataset and output hashes with every result.
