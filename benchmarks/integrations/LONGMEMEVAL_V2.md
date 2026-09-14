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
revision, installs both files atomically, registers the adapter, and is safe to
run again when the installed files are unchanged. It refuses revision drift or
conflicting destination files rather than silently changing the evaluation:

```sh
python -m benchmarks.integrations.install_longmemeval_v2 \
  /absolute/path/to/LongMemEval-V2
```

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
Use the official combine and leaderboard utilities for aggregate metrics.

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

Exact duplicate inserts are idempotent. A changed trajectory or screenshot
content, or an interrupted partial insert, fails explicitly instead of silently
reusing stale evidence or duplicating state. The recovery action for an
interrupted benchmark insert is to rebuild that scratch pack. The manifest
records source-state and inserted-node counts, and long trajectories checkpoint
and print progress every 100 nodes.

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

At the pinned dataset revision, trajectories occupy about 1.20 GB and the two
screenshot archives total about 5.92 GB compressed. Downloading only the small
haystack map does not provide its source trajectories or screenshots. Preserve
the upstream dataset and output hashes with every result.
