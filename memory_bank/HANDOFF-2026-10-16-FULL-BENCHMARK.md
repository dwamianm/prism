# Handoff: the full batching benchmark on a second machine (2026-10-16)

This note lets a second Mac, and a Claude Code session on it, run the full
benchmark of batched extraction (#254) while the first machine does other
work. The scripts are in `scripts/full-benchmark/`.

## What it runs and why

v0.15.0 made batched extraction the `ingest()` default
(`extraction_batch_target_tokens=2048`, #271). The owner decided that on the
offline evidence gate alone: all of LoCoMo and a 42-question LongMemEval-S
slice. This run measures what was skipped:

1. **Builds** (Part C, unattended). Four `ingest()` builds with
   `deepseek-v4.1-flash:cloud` through Ollama, all from one commit:

   | Label | Benchmark | Extraction | Estimated time |
   |---|---|---|---|
   | `full-defaults-locomo-deepseek-d810ee55` | LoCoMo, 10 conversations | batched at 2,048 (the default) | about 15 minutes |
   | `full-per-turn-locomo-deepseek-d810ee55` | LoCoMo | one call per turn | about 30 minutes |
   | `full-defaults-lme-deepseek-d810ee55` | LongMemEval-S, 500 questions | batched at 2,048 | about 20 hours |
   | `full-per-turn-lme-deepseek-d810ee55` | LongMemEval-S | one call per turn | about 21 hours |

   Then the offline evidence gate over each build, a `gate-compare` per
   benchmark (one call per turn before, batched after), and build summaries.
   The times scale the 2026-10-15 slice builds (17 packs at a time) and are
   estimates; Ollama's cloud usage limits can pause a build for longer.
2. **Answer runs** (Part D, supervised). The DeepSeek answer track over
   these `ingest()` packs, following the default-change rule in `CLAUDE.md`:
   the batched defaults are the baseline, and one call per turn is the variant.
   This takes several more hours; there is no good estimate yet.

Disk: a full LongMemEval-S build takes about 36 GB (the slice builds took
about 3 GB for 42 packs), so keep at least 120 GB free.

## A. Set up the Mac

Follow `machine/README.md` in the owner's private `claude-config` repository,
steps 1 to 7, with these changes:

1. Steps 1 to 3 as written (tools, Claude Code, `claude-config`).
2. Install Ollama **0.40.4**, the version the first machine ran the slice
   builds with, not the 0.34.4 that `machine/setup.sh` pins. Either edit
   `OLLAMA_VERSION` in `setup.sh` first, or install 0.40.4 by hand from
   https://github.com/ollama/ollama/releases/tag/v0.40.4 (check the zip against
   the digest in that release's `sha256sum.txt`). Run
   `machine/setup.sh --skip-models`, then `ollama pull deepseek-v4.1-flash:cloud`.
   The local `qwen3.5` models are not needed for this run.

   If another Ollama version is already running, stop it before replacing the
   app. The 0.34.4 app refused `osascript -e 'quit app "Ollama"'` ("User
   canceled", error -128), and `setup.sh --install-ollama` quits the app with
   that same command and carries on if it fails. Stop the app with
   `pkill -TERM -f "[O]llama.app/Contents/MacOS/Ollama"` instead (the bracket
   keeps the command from matching itself); the server stops with it. Move the
   old app aside rather than deleting it, open the new one, and check that
   `curl -s http://127.0.0.1:11434/api/version` answers 0.40.4.
3. In the Ollama app's Settings, turn **automatic updates off**. The answer
   track compares only runs answered on the same server version, so an update
   in the middle invalidates the pairs answered so far.
4. Run `ollama signin`. Never copy `~/.ollama/id_ed25519` from another machine.
5. Do **not** put `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or
   `PRME_EXTRACTION_API_KEY` in this machine's shell profile or in the clone's
   `.env`. Then no build, test or answer run can reach a paid API by accident.
   (On 2026-10-16, tests that faked only the per-turn call sent real requests
   with the first machine's OpenAI key once batching became the default.)
6. Optional: copy Claude Code's project memory from the first machine
   (`machine/README.md`, step 8).

## B. Set up the clone

1. Clone the repository to `~/Sites/prism` and install it:

   ```sh
   git clone https://github.com/dwamianm/prism.git ~/Sites/prism
   cd ~/Sites/prism
   uv sync --dev --extra api --extra mcp
   ```

2. Fetch the datasets and the official LongMemEval judge, and check their
   checksums:

   ```sh
   scripts/full-benchmark/install-data.sh
   ```

   The judge is read from `~/Sites/prism-opt-in-study-2026-09-22/data/opt-in-study/LongMemEval-official/`,
   beside the clone (`benchmarks/checkout.py`; `PRME_MATRIX_ROOT` moves it).
   The script fetches its one source file from the pinned commit
   `9e0b455f4ef0e2ab8f2e582289761153549043fc`.
3. If a dataset download has a different checksum, copy the file from the
   first machine instead, to the same path under `data/benchmarks/`:

   ```sh
   rsync -a first-mac:Sites/prism/data/benchmarks/locomo/locomo10.json data/benchmarks/locomo/
   rsync -a first-mac:Sites/prism/data/benchmarks/longmemeval/longmemeval_s_cleaned.json data/benchmarks/longmemeval/
   ```

4. Check the machine. Fix each `FAIL` and run it again until there is none:

   ```sh
   scripts/full-benchmark/check-machine.sh
   ```

   It checks the tools, free disk, the commit, that no paid API key is set,
   the Ollama version, the model and its ID (`e04da138...`), that the model
   answers, the three checksums, and the embedding model and the packing
   tokenizer (which it downloads into `~/.cache/fastembed` and
   `~/.cache/tiktoken` if missing, since the builds and the gate run offline).
   It cannot see the Ollama app's update setting, so it always prints one
   `WARN` about it.

## C. Run the builds

```sh
scripts/full-benchmark/start-builds.sh
```

This creates the worktree `~/Sites/prism-full-benchmark` at the pinned commit
`d810ee5552e388f85aac62f4cf8c6c6038f0b1c8`, syncs it, and starts
`run-builds.sh` detached under `caffeinate`, so closing the terminal does not
stop it and the Mac does not sleep. Keep the Mac on power and online.

The scripts keep the embedding model and the tokenizer file in
`~/.cache/fastembed` and `~/.cache/tiktoken` (`settings.sh`), not in the
default folders under `$TMPDIR`, which macOS clears overnight. A build stalled
that way on 2026-10-06: every pack it opened afterwards failed to load the
embedding model.

- Progress: `scripts/full-benchmark/status.sh` (packs complete per build, and
  the driver log). The driver log is
  `data/full-benchmark-d810ee55/driver.log`; each build's own log is
  `data/extracted-packs-v1/<label>.log`.
- The driver ends with `ALL DONE`, or `FINISHED WITH PROBLEMS` and the reason
  above it.
- A pack whose extraction keeps failing (for example at an Ollama usage
  limit) pauses; the driver waits 15 minutes and starts the build again, which
  resumes it, up to 24 times. `MAX_ATTEMPTS` and `RETRY_SLEEP` change that.
- To stop: `pkill -f full-benchmark/run-builds.sh; pkill -f "extracted_packs build"`.
  To resume, run `start-builds.sh` again. Every finished step is skipped.
- Do not change files in the worktree. A build resumes only from the same
  commit and working tree, and the driver refuses a worktree with changes.

When it is done, `data/full-benchmark-d810ee55/` holds, per build,
`gate-<label>.json` and `.md`, `summary-<label>.json`, and per benchmark
`compare-<benchmark>-defaults-vs-per-turn.json` and `.md`.

## D. Run the answer track (supervised)

Run these from the worktree, one at a time, and read each output before the
next. The rules in `CLAUDE.md` ("Epic #77 work rules") decide what counts;
`BENCHMARKS.md` ("Answers over `ingest()` packs", "Interleaved A/A check")
explains each command. The order below follows the checks in
`benchmarks/integrations/gpt54_baselines.py`, but the steps have not been run
end to end on a fresh machine, so expect a refusal to name a missing file or
condition; fix it rather than working around it.

Two checks fix the order:

- A variant's `prepare` replays the defaults over the packs of the current
  baseline, which is the baseline whose own answer run completed last. So each
  baseline must be prepared and answered with `run` before the per-turn
  variant is prepared. Done earlier, `prepare` stops with "has a complete
  answer run on this track" on a fresh machine. On a machine that already has
  an `ingest()` baseline from an older commit (for example `prme@3389586b` on
  the machine that ran the 2026-10-06 ingest answer track), it checks the
  per-turn packs against that older baseline's packs and refuses them because
  the commits differ.
- `prepare` refuses a tree with changes. A new result file under
  `benchmarks/results/research/` does not count, so answering the baselines is
  fine. A change to a tracked file does: the first A/A `run-pair` appends to
  the tracked A/A record, and `compare` appends to the tracked verdict record.
  So every preparation comes before the first A/A pair.

```sh
cd ~/Sites/prism-full-benchmark
# The cache folders the build scripts use (settings.sh). prepare replays
# retrieval offline, so it needs the embedding model and the tokenizer there.
export FASTEMBED_CACHE_PATH="$HOME/.cache/fastembed" TIKTOKEN_CACHE_DIR="$HOME/.cache/tiktoken"
T="--provider ollama --contexts ingest"
B=benchmarks.integrations.gpt54_baselines

# 1. The judge calibration for this model and settings.
uv run python -m $B calibrate $T

# 2. The two baseline preparations (the batched defaults). Note the baseline
#    arm name that prepare reports for each benchmark (prme or prme@<commit>)
#    and use it as <baseline> for that benchmark below.
uv run python -m $B prepare prme --benchmark locomo $T --packs full-defaults-locomo-deepseek-d810ee55
uv run python -m $B prepare prme --benchmark longmemeval $T --packs full-defaults-lme-deepseek-d810ee55

# 3. The defaults baseline on each benchmark.
uv run python -m $B run prme --benchmark locomo $T
uv run python -m $B run prme --benchmark longmemeval $T

# 4. The two variant preparations (one call per turn), now that both baselines
#    are answered. Finish both before the first A/A pair.
uv run python -m $B prepare prme --variant per-turn --benchmark locomo $T \
  --packs full-per-turn-locomo-deepseek-d810ee55 --set extraction_batch_target_tokens=0
uv run python -m $B prepare prme --variant per-turn --benchmark longmemeval $T \
  --packs full-per-turn-lme-deepseek-d810ee55 --set extraction_batch_target_tokens=0

# 5. The A/A check on both benchmarks (Ollama 0.40.4 is a new condition).
uv run python -m $B run-pair prme --benchmark locomo $T --baseline <baseline>
uv run python -m $B run-pair prme --benchmark longmemeval $T --baseline <baseline>

# 6. The variant's first pair on both benchmarks, then compare each pair's two
#    published results (the files run-pair wrote under benchmarks/results/research/).
uv run python -m $B run-pair prme --variant per-turn --benchmark locomo $T --baseline <baseline>
uv run python -m $B run-pair prme --variant per-turn --benchmark longmemeval $T --baseline <baseline>
uv run python -m $B compare --before <defaults side> --after <variant side>

# 7. The confirmation: the same run-pair commands again start a fresh pair. Compare them too.

# 8. The decision, from the pairs compare recorded.
uv run python -m $B verdict prme --variant per-turn $T
```

Reading the verdict: the variant here is one call per turn. `pass` means one
call per turn beat the batched default by the rule, which argues for
reverting the default; `fail` means it did not, which supports keeping
batching; `incomplete` means a step is missing.

## E. Bring the results back

The packs stay on this machine (about 75 GB). Bring back the small files:

- `data/full-benchmark-d810ee55/` (gates, comparisons, summaries, driver log).
- After Part D, the result files and record lines the answer runs wrote under
  `benchmarks/results/research/` in the worktree. `CLAUDE.md` says to commit
  each new A/A and verdict record line with its pair's published results;
  ask the owner before committing or pushing.

Post the build and gate numbers on #254 and update `BENCHMARKS.md` and
`benchmarks/results/research/2026-10-15/batched-extraction/BATCHED-EXTRACTION.md`
through a pull request, when the owner asks.

## Rules that still apply

- Never start a run against a paid API. `deepseek-v4.1-flash:cloud` through
  the local Ollama server is the model the owner chose for this work; the
  prompts leave the machine, and LoCoMo and LongMemEval-S are public.
- Keep the Ollama server version and the model identity fixed from the first
  answer run to the last.
- Commit, push and publish only when the owner asks; no Claude attribution in
  commits; outbound text in complete US English without em or en dashes.
