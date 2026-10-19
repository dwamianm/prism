# Settings for the scheduled full benchmark (issue #272). Sourced by
# weekly-run.sh, install-schedule.sh, uninstall-schedule.sh and status-weekly.sh,
# not run. The caller sets HERE (the folder these scripts are in) first.
# The guide is memory_bank/HANDOFF-2026-10-19-WEEKLY-BENCHMARK.md.
#
# Override any of these in the environment, for example
# SCHEDULER_HOME=/tmp/rehearsal scripts/full-benchmark/weekly-run.sh --dry-run

# Where the scheduler keeps its scripts, state, run folders and packs. It is
# not inside a worktree, because worktrees are deleted when their run ages out.
#   bin/         the installed copy of these scripts (deploy-schedule.sh)
#   state/       the lock, last-completed.env, schedule.log, launchd.out and launchd.err
#   runs/<id>/   one folder per scheduled run: run.env, driver.log, gate reports,
#                summaries, comparisons, report.md and report.json. Kept forever.
#   dry-runs/    the same for --dry-run, never published, the newest 5 kept
#   reference/stable/  the gate reports of the last stable release's run
#   packs/       this scheduler's packs, one folder per build; the newest KEEP_RUNS runs keep theirs
#   cache/extraction/  the extraction cache every scheduled build shares
SCHEDULER_HOME="${SCHEDULER_HOME:-$HOME/Sites/prism-weekly-scheduler}"
STATE="$SCHEDULER_HOME/state"
RUNS="$SCHEDULER_HOME/runs"
DRY_RUNS="$SCHEDULER_HOME/dry-runs"
REFERENCE="$SCHEDULER_HOME/reference/stable"
WEEKLY_PACKS="$SCHEDULER_HOME/packs"
WEEKLY_CACHE="$SCHEDULER_HOME/cache/extraction"

# The prism clone: its git objects, its ignored data/ (the datasets) and the
# folder the worktrees are made beside. Set before settings.sh, which would
# otherwise look for a git checkout around HERE.
PRISM="${PRISM:-$HOME/Sites/prism}"
export PRISM

# The schedule, in the Mac's local time. launchd weekdays: 1 is Monday, 4 is Thursday.
JOB_LABEL="${JOB_LABEL:-com.prism.weekly-benchmark}"
SCHEDULE_WEEKDAYS="1 4"
SCHEDULE_HOUR=0
SCHEDULE_MINUTE=30

# Refuse to start with less free disk than this (GB), and keep the packs of this
# many newest runs. A run's packs take about 33 GB (LongMemEval-S 32 GB, LoCoMo
# 0.4 GB) and its worktree about 0.5 GB.
MIN_FREE_GB="${MIN_FREE_GB:-120}"
KEEP_RUNS="${KEEP_RUNS:-3}"

# A detached run gets no login shell: add the tools' folders after whatever PATH
# the caller set (uv is in ~/.local/bin on the Mac mini), and keep any paid API
# key out of every process.
export PATH="$PATH:$HOME/.local/bin:/usr/local/bin"
unset OPENAI_API_KEY ANTHROPIC_API_KEY PRME_EXTRACTION_API_KEY

# The last stable release's run. Its two gate reports are copied into
# $REFERENCE the first time they are needed, from the full batching run of
# 2026-10-16 (commit d810ee55, the v0.15.0 code: no change under src/,
# pyproject.toml or uv.lock between the tag and that commit). They are the
# batched defaults builds, because batching at 2,048 tokens is what v0.15.0
# ships; the per-turn builds are the variant. When a new stable version is
# released, replace them (see the guide, "After a stable release").
STABLE_NAME="${STABLE_NAME:-v0.15.0}"
STABLE_COMMIT="d810ee5552e388f85aac62f4cf8c6c6038f0b1c8"
STABLE_SEED_DIR="$PRISM/data/full-benchmark-d810ee55"
STABLE_SEED_LOCOMO="$STABLE_SEED_DIR/gate-full-defaults-locomo-deepseek-d810ee55.json"
STABLE_SEED_LME="$STABLE_SEED_DIR/gate-full-defaults-lme-deepseek-d810ee55.json"

# Model, server, retries, cache folders and parallel packs come from settings.sh.
. "$HERE/settings.sh"
