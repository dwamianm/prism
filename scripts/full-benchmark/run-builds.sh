#!/bin/bash
# Part C driver: builds, gates and comparisons for the full benchmark.
# Start it with start-builds.sh, which runs it detached and keeps the Mac awake.
# The guide is memory_bank/HANDOFF-2026-10-16-FULL-BENCHMARK.md.
#
# Order: LoCoMo with the defaults (batched) and one call per turn, their gates
# and comparison; then LongMemEval-S the same way. Every step is skipped when
# its output is already complete, so running this again resumes where it
# stopped. A build that pauses (for example at an Ollama usage limit) is started
# again after RETRY_SLEEP seconds and resumes from the turns it holds.
#
# The scheduled run (weekly-run.sh) uses it with BUILDS=defaults, which leaves
# out the per-turn builds, and EXTRACTION_CACHE set (settings.sh).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/settings.sh"
mkdir -p "$RESULTS_DIR" "$PACKS"
DRIVER_LOG="$RESULTS_DIR/driver.log"

log() { echo "$(date -u +%FT%TZ) $*" >> "$DRIVER_LOG"; }
count() { ls "$PACKS/$1/$2"/*/manifest.json 2>/dev/null | wc -l | tr -d ' '; }
valid_json() { [ -f "$1" ] && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" 2>/dev/null; }
in_worktree() { (cd "$WORKTREE" && "$@"); }

# One driver at a time. A lock left by a driver that was killed is taken over.
LOCK="$RESULTS_DIR/driver.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  holder=$(cat "$LOCK/pid" 2>/dev/null)
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    echo "Another driver (pid $holder) is running; see status.sh." >&2
    exit 1
  fi
  log "taking over the lock of driver ${holder:-unknown}, which is no longer running"
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
trap 'exit 143' TERM INT HUP

if [ "$(git -C "$WORKTREE" rev-parse HEAD 2>/dev/null)" != "$COMMIT" ]; then
  log "STOP: $WORKTREE is not at $COMMIT; run start-builds.sh, which creates it"
  exit 1
fi
if [ -n "$(git -C "$WORKTREE" status --porcelain)" ]; then
  log "STOP: $WORKTREE has local changes; builds must come from committed code"
  exit 1
fi

build() {  # label benchmark expected jobs [--set ...]
  local label=$1 benchmark=$2 expected=$3 jobs=$4 attempt=0
  shift 4
  while [ "$attempt" -lt "$MAX_ATTEMPTS" ]; do
    attempt=$((attempt + 1))
    if [ "$(count "$label" "$benchmark")" = "$expected" ]; then
      log "build $label: all $expected packs complete"
      return 0
    fi
    log "build $label: attempt $attempt, $(count "$label" "$benchmark") of $expected packs complete so far"
    in_worktree uv run --no-sync python -m benchmarks.diagnostics.extracted_packs build \
      --benchmark "$benchmark" --label "$label" --root "$PACKS" --model "$MODEL" --cloud --jobs "$jobs" \
      ${EXTRACTION_CACHE:+--cache-dir "$EXTRACTION_CACHE"} "$@" \
      >> "$PACKS/$label.log" 2>&1
    log "build $label: attempt $attempt exited $?, $(count "$label" "$benchmark") of $expected packs complete"
    [ "$(count "$label" "$benchmark")" = "$expected" ] && return 0
    sleep "$RETRY_SLEEP"
  done
  if [ "$(count "$label" "$benchmark")" = "$expected" ]; then
    log "build $label: all $expected packs complete"
    return 0
  fi
  log "build $label: gave up after $MAX_ATTEMPTS attempts; see $PACKS/$label.log"
  return 1
}

gate() {  # label benchmark
  local label=$1 benchmark=$2 out="$RESULTS_DIR/gate-$1.json"
  if valid_json "$out"; then log "gate $label: already written"; return 0; fi
  log "gate $label: start"
  in_worktree uv run --no-sync python -m benchmarks.diagnostics.product_packing gate --benchmark "$benchmark" \
    --packs "$PACKS/$label" --output "$out" > "$RESULTS_DIR/gate-$label.log" 2>&1
  local status=$?
  log "gate $label: exit $status"
  return $status
}

summary() {  # label benchmark
  in_worktree uv run --no-sync python -m benchmarks.diagnostics.extracted_packs summary --label "$1" \
    --root "$PACKS" --benchmark "$2" > "$RESULTS_DIR/summary-$1.json" 2>> "$RESULTS_DIR/summary-errors.log"
  log "summary $1: exit $?"
}

compare() {  # name before-label after-label
  local out="$RESULTS_DIR/compare-$1.json"
  if valid_json "$out"; then log "compare $1: already written"; return 0; fi
  in_worktree uv run --no-sync python -m benchmarks.diagnostics.product_packing gate-compare \
    "$RESULTS_DIR/gate-$2.json" "$RESULTS_DIR/gate-$3.json" --output "$out" > "$RESULTS_DIR/compare-$1.log" 2>&1
  log "compare $1: exit $?"
}

benchmark_round() {  # benchmark expected jobs defaults-label per-turn-label short-name
  local benchmark=$1 expected=$2 jobs=$3 defaults=$4 per_turn=$5 name=$6 ok=0
  build "$defaults" "$benchmark" "$expected" "$jobs" || ok=1
  if [ "$BUILDS" = all ]; then
    build "$per_turn" "$benchmark" "$expected" "$jobs" --set extraction_batch_target_tokens=0 || ok=1
  fi
  if [ "$ok" -ne 0 ]; then
    log "$name: a build is incomplete, so its gates and comparison are skipped"
    return 1
  fi
  gate "$defaults" "$benchmark" || return 1
  summary "$defaults" "$benchmark"
  if [ "$BUILDS" = all ]; then
    gate "$per_turn" "$benchmark" || return 1
    summary "$per_turn" "$benchmark"
    # Before is one call per turn, after is the batched default, as in the #254 reports.
    compare "$name-defaults-vs-per-turn" "$per_turn" "$defaults"
  fi
}

log "driver started, pid $$, commit ${COMMIT:0:8}, Ollama $(curl -s "$OLLAMA_URL/api/version")"
benchmark_round locomo 10 "$LOCOMO_JOBS" "$LOCOMO_DEFAULTS" "$LOCOMO_PER_TURN" locomo
locomo=$?
benchmark_round longmemeval 500 "$LME_JOBS" "$LME_DEFAULTS" "$LME_PER_TURN" lme
lme=$?
log "Ollama at the end: $(curl -s "$OLLAMA_URL/api/version")"
if [ "$locomo" -eq 0 ] && [ "$lme" -eq 0 ]; then log "ALL DONE"; exit 0; fi
log "FINISHED WITH PROBLEMS (locomo $locomo, lme $lme)"
exit 1
