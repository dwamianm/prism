# Handoff: extracted-pack baselines for #91 (2026-09-28)

> **Update 2026-10-01:** since this note, the LoCoMo `ingest()` baseline (`ingest-baseline-locomo-deepseek`) and the fact-text build have completed, and the LongMemEval-S baseline has only its 42 slice packs. See "Current state (2026-10-01)" in `GOALS.md`.

This note lets a new machine, and a new Claude Code session on it, pick up the
`ingest()` baseline work for #91 and #102 where it stopped. The builds were
stopped on the original machine because they would tie it up for most of a day.

## Why this work exists

The offline evidence gate replays retrieval over packs that the harnesses built
with `store()`, which never runs extraction. So the gate cannot see a change to
extraction or the graph: PR #210 (claim merge, #209) produced 2,040 of 2,040
identical contexts with the feature on and off. The owner's direction is that
saved events without extracted memory are not useful to the project, and that
tests and benchmarks must have extracted facts to test against.

#91 (self-contained, dated facts extracted over a conversation window) asks
first for a baseline of the current `ingest()` on both benchmarks. PR #212
(merged as `a8da3d4e`) added the tooling for that:

- `benchmarks/diagnostics/extracted_packs.py` builds LoCoMo and LongMemEval-S
  packs through `ingest()`. See the "Packs built through `ingest()` (#102)"
  section of `BENCHMARKS.md` for what it does and why.
- `benchmarks.diagnostics.product_packing gate --packs <build>` replays the
  gate over such a build and counts evidence that extracted records cite
  (`packed_by_extracted`).

A LoCoMo pack has a second purpose, which the gate does not show. It is the
extracted memory for one conversation, so it is also what gets opened in the
memory explorer to see what ingestion actually built, rather than only how it
scores. See "Looking at a pack in the memory explorer" below.

The queued work after the baselines is the #91 windowed extractor itself, then
#92, #64 and #31, all measured against extracted packs.

## State at handoff

| Build | Label | Model | Progress when stopped | Rate |
|---|---|---|---|---|
| LoCoMo | `ingest-baseline` | local `prme-qwen3.5:35b-a3b-8k` | 500 of 5,882 turns, conv-26 complete | 8.8 s per turn alone, 9.8 s while the other build ran |
| LongMemEval-S | `ingest-baseline-lme-deepseek` | `deepseek-v4.1-flash:cloud` | 7,818 of 246,738 turns, no pack complete | 5.3 calls per second at first, 3.5 in the last interval |

Neither build had an extraction error. Both ran on Ollama 0.34.4.

Early result from the one complete LoCoMo conversation (conv-26): 211
extracted claims from 419 turns (0.50 per turn), and only 62 of its 132
annotated evidence turns (47%) are cited by an extracted record. Each call sent
about 5,570 prompt tokens and received about 600.

LongMemEval-S findings from the trials:

- About 5.3 calls per second is the ceiling through Ollama's cloud service. 17
  packs at a time, 34 packs, and two processes of 17 all reached the same rate.
- 23% of LongMemEval-S turns repeat across questions, so the build's response
  cache cuts the calls from 246,738 to 189,520, about 936 million tokens.
- At 5.3 calls per second the full build takes 10 to 11 hours. At the 3.5 seen
  last, it takes about 15. The calls count against the Ollama account's usage
  limits; nothing is billed per request.

The partial builds are not worth resuming on the new machine. A resume must
use the same commit and working tree, and the LoCoMo build ran from an
uncommitted snapshot. Start both again from `main`. The one thing worth
copying is the LongMemEval-S response cache (below).

## 1. Set up the new machine

The machine setup (Ollama, its models, and the `claude-danger` and
`claude-default` launchers) lives in the owner's private `claude-config`
repository, in `machine/`. Clone it and follow `machine/README.md`, which runs
`machine/setup.sh`. That script also checks the Ollama version and model
digests below. Claude Code's local memory for this project is not in this
repository; the same README says how to copy it from the original machine.

The builds expect exactly this setup:

- Ollama 0.34.4. Turn off the Ollama app's automatic updates while builds or
  DeepSeek answer runs are in progress, since an upgrade can change outputs.
- `prme-qwen3.5:35b-a3b-8k`: `qwen3.5:35b-a3b` (Q4_K_M, about 24 GB) with an
  8,192-token context and temperature 0. Its digest here is
  `45870b70b6fa65ab09355aeb7901897763ae40745c6cdd11d28bc33a6b818ffc`.
- `deepseek-v4.1-flash:cloud`, signed in with `ollama signin`. Its manifest
  digest here is `e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`,
  the model identity the DeepSeek answer track's A/A checks were measured with.

## 2. Set up the repository

1. Clone the repository anywhere. The checkout holding `data/` is resolved
   through git by `benchmarks/checkout.py`, so no particular path is needed and
   no symlink. The registered harness still records the original machine's
   absolute paths, because its bytes are pinned by sha256 in the saved
   registration, but those values are re-rooted at import. Set
   `PRME_ORIGINAL_ROOT` only for a checkout git cannot relate.
2. Run `uv sync` in the clone.
3. Get the two datasets:

   ```sh
   uv run python scripts/download_benchmarks.py
   shasum -a 256 data/benchmarks/locomo/locomo10.json \
     data/benchmarks/longmemeval/longmemeval_s_cleaned.json
   ```

   They must match `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`
   (LoCoMo) and `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`
   (LongMemEval-S), or the builder refuses them. If a download differs, copy the
   two files from the original machine's `data/benchmarks/` instead.
4. Warm the embedding model cache once. The builder and the gate run offline,
   so a missing model fails instead of downloading:

   ```sh
   uv run python -c "
   import asyncio
   from prme.config import PRMEConfig
   from prme.storage.embedding import create_embedding_provider
   provider = create_embedding_provider(PRMEConfig(_env_file=None).embedding)
   print(provider.model_name, len(asyncio.run(provider.embed(['warm up']))[0]))"
   ```

   It prints `BAAI/bge-small-en-v1.5 384`.
5. Optional: copy the LongMemEval-S response cache from the original machine,
   so the 7,706 responses already counted against the usage limit are not requested again:
   copy `data/extracted-packs-v1/ingest-baseline-lme-deepseek/extraction-cache/`
   (35 MB) to the same path in the new clone before the first build.

## 3. Run the builds

Run each build from its own worktree at a fixed commit, so later work in the
main checkout cannot change the code a running build imports, and a stopped
build can resume. Keep the output in the main checkout's ignored `data/`:

```sh
cd <your clone>
git worktree add --detach ../prism-extracted-packs main
cd ../prism-extracted-packs
uv sync

# Builds go to the main checkout's data/extracted-packs-v1 by default, so the
# packs outlive this worktree. Put the logs beside them.
PACKS="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/data/extracted-packs-v1"
mkdir -p "$PACKS"

# LongMemEval-S on the DeepSeek cloud model: 17 packs at a time is the ceiling.
nohup uv run python -m benchmarks.diagnostics.extracted_packs build --benchmark longmemeval \
  --label ingest-baseline-lme-deepseek \
  --model deepseek-v4.1-flash:cloud --cloud --jobs 17 \
  > "$PACKS/ingest-baseline-lme-deepseek.log" 2>&1 &

# LoCoMo on the local model. Ollama runs one local request at a time, so --jobs does not help.
nohup uv run python -m benchmarks.diagnostics.extracted_packs build --label ingest-baseline \
  > "$PACKS/ingest-baseline.log" 2>&1 &
```

The two can run at the same time. The LoCoMo build is limited by the local
model and the LongMemEval-S build by the cloud service, but they share the CPU,
which slowed LoCoMo by about 10% here.

Progress: each pack writes one line per turn to `turns.jsonl` and a
`manifest.json` when it is complete.

```sh
cd "$PACKS"
cat ingest-baseline-lme-deepseek/longmemeval/*/turns.jsonl | wc -l   # of 246,738
ls ingest-baseline-lme-deepseek/longmemeval/*/manifest.json | wc -l  # of 500
cat ingest-baseline/locomo/*/turns.jsonl | wc -l                      # of 5,882
grep -c "paused at" ingest-baseline-lme-deepseek.log                  # packs stopped by failures
```

To stop a build, run `pkill -f -- "--label <label> "`. To resume, run the same
command again from the same worktree. A turn whose extraction fails is retried
after 15, 60 and 240 seconds; if it still fails (for example, the Ollama usage
limit), its pack stops at that turn without a manifest, so no later turn is
extracted ahead of it. Rerun the command once the limit resets.

If the LoCoMo build is too slow on the new machine, it can use the DeepSeek
cloud model with `--model deepseek-v4.1-flash:cloud --cloud --jobs 10` under a
new label. It then uses a different extraction model from this plan, so ask
the owner first, and compare LoCoMo results only with runs on the same model.

## Looking at a pack in the memory explorer

Each unit's pack is an ordinary PRME pack at
`<root>/<label>/<benchmark>/<unit_id>/pack/`, holding the `memory.duckdb`,
`vectors.usearch` and `lexical_index` that the memory explorer reads
(`web/README.md`). Nothing has to be exported or converted.

Wait for the pack to be complete, then copy it. A pack is complete when its
unit folder holds a `manifest.json`; before that the build is still writing,
and a `memory.duckdb.wal` beside the database means a copy would catch it part
way through a write.

Copy rather than open in place. The explorer starts a normal engine, which can
write to a pack while it initializes or recovers, and the gate checks each
pack's tree identity against the `pack_sha256` that its manifest recorded. One
incidental write makes the gate refuse that pack, and a pack cannot be rebuilt
byte for byte.

```sh
cp -R "$PACKS/<label>/locomo/conv-26/pack" /tmp/conv-26-pack
PRME_CHAT_DATA_DIR=/tmp/conv-26-pack uv run python -m web.server
```

Run the server from the main checkout, which needs `uv sync --extra api`. Open
http://127.0.0.1:8080 and enter the owner ID: for LoCoMo it is the
conversation's sample ID, such as `conv-26`, and every LongMemEval-S pack uses
`longmemeval-s-baseline`.

The extraction model decides what there is to look at, so a pack built on a
different model shows that model's extraction. Read a pack's `build.json` for
the model it was built with before drawing conclusions from it.

## 4. After the builds

1. Run the gate over each build from the main checkout:

   ```sh
   uv run python -m benchmarks.diagnostics.product_packing gate --benchmark locomo \
     --packs data/extracted-packs-v1/ingest-baseline --output /tmp/gate-ingest-locomo.json
   uv run python -m benchmarks.diagnostics.product_packing gate --benchmark longmemeval \
     --packs data/extracted-packs-v1/ingest-baseline-lme-deepseek --output /tmp/gate-ingest-lme.json
   ```

2. Post the results on #91: per benchmark, the extraction model and Ollama
   version, wall time, calls, tokens, cache hits, claims per turn, the evidence
   claim coverage from the manifests, and the gate summary (all evidence
   packed, evidence turns packed by extracted records, projected accuracy)
   next to the saved `store()` gate numbers. Record the same in `BENCHMARKS.md`
   through a pull request.
3. Then start the #91 windowed extractor behind a configuration option,
   build it with `--set` under a new label on the same model per benchmark,
   and compare its gate report with these baselines using `gate-compare`.

## Rules that still apply

These come from `CLAUDE.md` and the owner's standing feedback; a new session
should read `CLAUDE.md` first.

- Never start a run against a paid API. Local Ollama models are fine, and so
  is `deepseek-v4.1-flash:cloud` through the local Ollama server, which the
  owner chose for this extraction.
- Answer runs follow the DeepSeek track rules in `CLAUDE.md`. The server was
  0.34.4 at handoff while the recorded A/A checks were measured on 0.34.3, so
  a new A/A pair on both benchmarks is needed before any variant pair counts.
- Treat extracted facts as the core test surface: a change to ingestion,
  extraction or the graph is not measured by a `store()`-only gate.
- Commit, push and publish only when the owner asks; no Claude attribution in
  commits; outbound text in complete US English without em or en dashes.
- The coding-memory pilot is shelved (PR #208): do not run routine
  `prme.integrations.coding recall` or `remember`.

## Cleanup on the original machine

After the new machine is running, the original machine can remove its build
worktrees (`/Users/dwamianm/Sites/prism-extracted-packs-2026-09-28` and
`/Users/dwamianm/Sites/prism-extracted-packs-lme-2026-09-28`) and the partial
builds under `data/extracted-packs-v1/`, once the response cache has been
copied.
