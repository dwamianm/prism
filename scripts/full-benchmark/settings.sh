# Settings shared by the scripts in this folder. Sourced, not run.
# The guide is memory_bank/HANDOFF-2026-10-16-FULL-BENCHMARK.md.
#
# Override any of these in the environment before running a script, for
# example: MAX_ATTEMPTS=48 scripts/full-benchmark/start-builds.sh

# The main checkout of the prism clone these scripts live in, where data/ is.
# Keep the clone at ~/Sites/prism so Claude Code's project memory folder
# (-Users-<you>-Sites-prism) has the same name as on the first machine.
PRISM="${PRISM:-$(dirname "$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir)")}"

# Every build, gate and answer run uses this one commit, from its own worktree,
# so the two sides of each comparison come from identical code.
# d810ee55 is main on 2026-10-16: v0.15.0 plus the pydantic test fix (#274)
# and two docs and probe PRs (#275, #276) that change no code under src/.
COMMIT="${COMMIT:-d810ee5552e388f85aac62f4cf8c6c6038f0b1c8}"
WORKTREE="${WORKTREE:-$(dirname "$PRISM")/prism-full-benchmark}"

# Model and server. The answer track compares only runs with the same model
# identity and Ollama server version, so keep both fixed for the whole run.
MODEL="deepseek-v4.1-flash:cloud"
MODEL_ID_PREFIX="e04da138"
OLLAMA_VERSION="0.40.4"
OLLAMA_URL="http://127.0.0.1:11434"

# Where the builds and the results go (both inside the clone's ignored data/).
PACKS="${PACKS:-$PRISM/data/extracted-packs-v1}"
RESULTS_DIR="${RESULTS_DIR:-$PRISM/data/full-benchmark-${COMMIT:0:8}}"

# Build labels. "defaults" batch at 2,048 tokens (the v0.15.0 default);
# "per-turn" set extraction_batch_target_tokens=0 (one call per turn).
LOCOMO_DEFAULTS="${LOCOMO_DEFAULTS:-full-defaults-locomo-deepseek-${COMMIT:0:8}}"
LOCOMO_PER_TURN="${LOCOMO_PER_TURN:-full-per-turn-locomo-deepseek-${COMMIT:0:8}}"
LME_DEFAULTS="${LME_DEFAULTS:-full-defaults-lme-deepseek-${COMMIT:0:8}}"
LME_PER_TURN="${LME_PER_TURN:-full-per-turn-lme-deepseek-${COMMIT:0:8}}"

# Parallel packs. 17 is the measured Ollama cloud ceiling for LongMemEval-S;
# LoCoMo has only 10 conversations.
LOCOMO_JOBS=10
LME_JOBS=17

# A build whose packs pause (for example at an Ollama usage limit) is started
# again, and resumes, after RETRY_SLEEP seconds, up to MAX_ATTEMPTS times.
MAX_ATTEMPTS="${MAX_ATTEMPTS:-24}"
RETRY_SLEEP="${RETRY_SLEEP:-900}"

# Checksums of the files install-data.sh fetches.
LOCOMO_SHA256="79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
LME_SHA256="d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
JUDGE_SHA256="ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251"

# The official LongMemEval judge is read from a folder beside the clone
# (benchmarks/checkout.py); PRME_MATRIX_ROOT moves it.
MATRIX="${PRME_MATRIX_ROOT:-$(dirname "$PRISM")/prism-opt-in-study-2026-09-22}"
JUDGE="$MATRIX/data/opt-in-study/LongMemEval-official/src/evaluation/evaluate_qa.py"
# The judge's source at the commit the registration pins (BENCHMARKS.md).
JUDGE_URL="https://raw.githubusercontent.com/xiaowu0162/LongMemEval/9e0b455f4ef0e2ab8f2e582289761153549043fc/src/evaluation/evaluate_qa.py"
