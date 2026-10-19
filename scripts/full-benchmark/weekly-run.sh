#!/bin/bash
# Start one scheduled full benchmark run of origin/main (issue #272). launchd runs
# it on Monday and Thursday at 00:30 local time (install-schedule.sh); run it by
# hand to start a run now. The guide is
# memory_bank/HANDOFF-2026-10-19-WEEKLY-BENCHMARK.md.
#
#   weekly-run.sh             one run, if the rules below allow it
#   weekly-run.sh --force     run even when origin/main is unchanged since the last completed run
#   weekly-run.sh --dry-run   everything except the builds and the gates, to test the plumbing
#
# A trigger does nothing, and says why in state/schedule.log, when
#   - another scheduled run is going (state/weekly.lock holds a live pid; a lock
#     whose pid is gone is cleared), or a build or gate of any other run is going;
#   - origin/main is the commit of the last completed run;
#   - less than MIN_FREE_GB is free (after the older runs' packs are pruned);
#   - Ollama does not answer, the model is missing, or an API key is set.
# An interrupted or incomplete run of the current origin/main commit is resumed
# instead of started again: every finished step is skipped, and a build resumes
# from the turns it holds.
#
# A run: fetch origin, a detached worktree at origin/main under ../prism-weekly-<sha>,
# LoCoMo and LongMemEval-S built with the defaults through run-builds.sh
# (BUILDS=defaults, one shared extraction cache), the offline evidence gate over
# each, gate-compare against the stable reference and the previous scheduled
# run, then report.md and report.json (weekly_report.py). The answer track does
# not run. Then the packs and worktrees of runs beyond the newest KEEP_RUNS are
# deleted; run folders (reports) are kept.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/weekly-settings.sh"

DRY_RUN=0
FORCE=0
# Processes that mean a build or gate is going that this scheduler did not start.
BUSY_PATTERN="${BUSY_PATTERN:-extracted_packs build|product_packing gate}"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $arg (see --help)" >&2; exit 2 ;;
  esac
done

mkdir -p "$STATE" "$RUNS" "$DRY_RUNS" "$WEEKLY_PACKS" "$WEEKLY_CACHE" "$REFERENCE"
TAG=""
[ "$DRY_RUN" = 1 ] && TAG="dry run: "
log() { local line; line="$(date -u +%FT%TZ) ${TAG}$*"; echo "$line"; echo "$line" >> "$STATE/schedule.log"; }
valid_json() { [ -f "$1" ] && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" 2>/dev/null; }
env_value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -1; }  # file key
set_env() {  # file key value
  local tmp="$1.tmp.$$"
  { grep -v "^$2=" "$1" 2>/dev/null; echo "$2=$3"; } > "$tmp"
  mv "$tmp" "$1"
}
free_gb() { df -Pk "$HOME" | awk 'NR==2 {print int($4 / 1048576)}'; }
in_worktree() { (cd "$WORKTREE" && "$@"); }
kill_tree() { local pid=$1 child; for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$child"; done; kill -TERM "$pid" 2>/dev/null; }

# ---- The one-run lock -------------------------------------------------------
LOCK="$STATE/weekly.lock"
LOCK_HELD=0
acquire_lock() {
  local holder
  if ! mkdir "$LOCK" 2>/dev/null; then
    [ -f "$LOCK/pid" ] || sleep 1
    holder=$(cat "$LOCK/pid" 2>/dev/null)
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null && ps -p "$holder" -o command= 2>/dev/null | grep -q 'weekly-run'; then
      log "skip: a scheduled run is going (pid $holder, state/weekly.lock)"
      return 1
    fi
    log "clearing the stale lock of pid ${holder:-unknown}, which is no longer running"
    rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null || { log "skip: another trigger took the lock first"; return 1; }
  fi
  echo $$ > "$LOCK/pid"
  LOCK_HELD=1
}
RUN_ENV=""
CHILD=""
CREATED_WORKTREE=0
WORKTREE=""
on_exit() {
  if [ "$DRY_RUN" = 1 ] && [ "$CREATED_WORKTREE" = 1 ] && [ -n "$WORKTREE" ]; then
    git -C "$PRISM" worktree remove --force "$WORKTREE" >/dev/null 2>&1 && log "removed the dry run's worktree $WORKTREE"
  fi
  [ "$LOCK_HELD" = 1 ] && rm -rf "$LOCK"
}
on_signal() {
  log "stopping: received a signal"
  [ -n "$RUN_ENV" ] && [ "$DRY_RUN" = 0 ] && set_env "$RUN_ENV" STATUS interrupted
  [ -n "$CHILD" ] && kill_tree "$CHILD"
  exit 143
}
trap on_exit EXIT
trap on_signal TERM INT HUP

# ---- Deleting only what this scheduler made ---------------------------------
remove_path() {  # path parent apply
  local path=$1 parent=$2 apply=$3
  case "$path" in
    *..*) log "refusing to delete $path: it contains .."; return 1 ;;
    "$parent"/?*) ;;
    *) log "refusing to delete $path: it is not under $parent"; return 1 ;;
  esac
  [ -e "$path" ] || return 0
  if [ "$apply" = 1 ]; then
    rm -rf "$path" && log "deleted $path"
  else
    log "would delete $path ($(du -sh "$path" 2>/dev/null | cut -f1))"
  fi
}
remove_worktree() {  # path apply
  local path=$1 apply=$2 parent
  parent="$(dirname "$PRISM")"
  case "$path" in
    "$parent"/prism-weekly-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) log "refusing to remove $path: it is not one of this scheduler's worktrees (prism-weekly-<8 hex digits>)"; return 1 ;;
  esac
  [ -d "$path" ] || return 0
  if [ "$apply" = 1 ]; then
    if git -C "$PRISM" worktree remove --force "$path" >/dev/null 2>&1; then log "removed worktree $path"
    else log "could not remove $path (not a registered worktree?); left in place"; fi
  else
    log "would remove worktree $path"
  fi
}
# Keep the packs and worktrees of the newest $1 run folders; delete the rest's.
# Their folders, with the reports, stay. $2 is 1 to delete, 0 to only say what would go.
prune_runs() {
  local keep=$1 apply=$2 index=0 id key label sha8 kept=" "
  for id in $(ls -1 "$RUNS" 2>/dev/null | sort -r); do
    [ -f "$RUNS/$id/run.env" ] || continue
    index=$((index + 1))
    [ "$index" -le "$keep" ] && kept="$kept$(env_value "$RUNS/$id/run.env" COMMIT | cut -c1-8) "
  done
  index=0
  for id in $(ls -1 "$RUNS" 2>/dev/null | sort -r); do
    [ -f "$RUNS/$id/run.env" ] || continue
    index=$((index + 1))
    [ "$index" -le "$keep" ] && continue
    for key in LABEL_LOCOMO LABEL_LME; do
      label=$(env_value "$RUNS/$id/run.env" "$key")
      case "$label" in weekly-?*) ;; *) continue ;; esac
      remove_path "$WEEKLY_PACKS/$label" "$WEEKLY_PACKS" "$apply"
      remove_path "$WEEKLY_PACKS/$label.log" "$WEEKLY_PACKS" "$apply"
    done
    sha8=$(env_value "$RUNS/$id/run.env" COMMIT | cut -c1-8)
    if [ -n "$sha8" ]; then
      case "$kept" in
        *" $sha8 "*) ;;
        *) remove_worktree "$(dirname "$PRISM")/prism-weekly-$sha8" "$apply" ;;
      esac
    fi
    [ "$apply" = 1 ] && [ -z "$(env_value "$RUNS/$id/run.env" PACKS_DELETED)" ] && set_env "$RUNS/$id/run.env" PACKS_DELETED "$(date -u +%FT%TZ)"
  done
  # Dry runs hold only reports; keep the newest 5.
  for id in $(ls -1 "$DRY_RUNS" 2>/dev/null | sort -r | tail -n +6); do
    remove_path "$DRY_RUNS/$id" "$DRY_RUNS" 1
  done
}

# ---- Pre-flight -------------------------------------------------------------
ensure_reference() {  # copy the stable reference's gate reports in once
  seed() {  # source destination
    valid_json "$2" && return 0
    if valid_json "$1"; then cp -p "$1" "$2" && log "copied the stable reference gate report $1"
    else log "WARNING: no stable reference gate report at $1; the comparison against ${STABLE_NAME} will be skipped"; fi
  }
  seed "$STABLE_SEED_LOCOMO" "$REFERENCE/gate-locomo.json"
  seed "$STABLE_SEED_LME" "$REFERENCE/gate-longmemeval.json"
  [ -f "$REFERENCE/reference.env" ] || printf 'NAME=%s\nCOMMIT=%s\n' "$STABLE_NAME" "$STABLE_COMMIT" > "$REFERENCE/reference.env"
}

check_keys() {
  local name
  for name in OPENAI_API_KEY ANTHROPIC_API_KEY PRME_EXTRACTION_API_KEY; do
    if [ -f "$PRISM/.env" ] && grep -qE "^$name=" "$PRISM/.env"; then
      log "refusing to start: $PRISM/.env holds $name; remove it so no run can call a paid API"
      return 1
    fi
  done
}

OLLAMA_NOW=""
MODEL_DIGEST=""
check_ollama() {
  local tags reply
  OLLAMA_NOW=$(curl -s --max-time 5 "$OLLAMA_URL/api/version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' 2>/dev/null)
  if [ -z "$OLLAMA_NOW" ]; then
    log "refusing to start: no Ollama server answers at $OLLAMA_URL; start the Ollama app"
    return 1
  fi
  [ "$OLLAMA_NOW" = "$OLLAMA_VERSION" ] || log "WARNING: Ollama is $OLLAMA_NOW, not the $OLLAMA_VERSION the earlier runs used; the report records both versions"
  tags=$(curl -s --max-time 10 "$OLLAMA_URL/api/tags")
  MODEL_DIGEST=$(printf '%s' "$tags" | python3 -c '
import json, sys
models = json.load(sys.stdin).get("models", [])
print(next((m.get("digest", "") for m in models if m.get("name") == sys.argv[1]), ""))' "$MODEL" 2>/dev/null)
  if [ -z "$MODEL_DIGEST" ]; then
    log "refusing to start: $MODEL is not in Ollama's model list; run: ollama pull $MODEL"
    return 1
  fi
  [ "${MODEL_DIGEST:0:8}" = "$MODEL_ID_PREFIX" ] || log "WARNING: $MODEL has identity ${MODEL_DIGEST:0:8}, not ${MODEL_ID_PREFIX}; the tag moved upstream, so gate-compare may refuse the comparison with the stable reference"
  reply=$(curl -s --max-time 120 "$OLLAMA_URL/api/chat" -d "{\"model\": \"$MODEL\", \"stream\": false, \"messages\": [{\"role\": \"user\", \"content\": \"Reply with the word ready.\"}]}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["message"]["content"].strip()[:40])' 2>/dev/null)
  if [ -z "$reply" ]; then
    log "refusing to start: $MODEL does not answer; run: ollama signin"
    return 1
  fi
}

fetch_main() {
  local attempt=0
  while [ "$attempt" -lt 3 ]; do
    attempt=$((attempt + 1))
    git -C "$PRISM" fetch -q origin "+refs/heads/main:refs/remotes/origin/main" 2> "$STATE/fetch.err" && return 0
    log "fetch attempt $attempt failed: $(tail -1 "$STATE/fetch.err")"
    [ "$attempt" -lt 3 ] && sleep "${FETCH_RETRY_SLEEP:-30}"
  done
  return 1
}

# ---- Comparisons ------------------------------------------------------------
compare_gates() {  # name before-report after-report benchmark
  local name=$1 before=$2 after=$3 benchmark=$4 base="$RUN_DIR/compare-$1-$4" status
  if valid_json "$base.json"; then log "compare $name $benchmark: already written"; return 0; fi
  rm -f "$base.refused"
  if ! valid_json "$before"; then echo "no readable gate report at $before" > "$base.refused"; log "compare $name $benchmark: skipped, $(cat "$base.refused")"; return 2; fi
  if ! valid_json "$after"; then echo "this run has no readable gate report at $after" > "$base.refused"; log "compare $name $benchmark: skipped, $(cat "$base.refused")"; return 2; fi
  in_worktree uv run --no-sync python -m benchmarks.diagnostics.product_packing gate-compare \
    "$before" "$after" --output "$base.json" > "$base.log" 2>&1
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "gate-compare refused the pair (exit $status): $(tail -1 "$base.log")" > "$base.refused"
    log "compare $name $benchmark: skipped, $(cat "$base.refused")"
    return 2
  fi
  log "compare $name $benchmark: written"
}

# ---- The trigger ------------------------------------------------------------
log "trigger: starting, free disk $(free_gb) GB"
acquire_lock || exit 0

if pid=$(pgrep -f "$BUSY_PATTERN" | head -1) && [ -n "$pid" ]; then
  log "skip: a benchmark build or gate is running (pid $pid: $(ps -p "$pid" -o command= | cut -c1-100)), and this scheduler did not start it"
  exit 0
fi

if ! fetch_main; then
  log "skip: could not fetch origin ($(tail -1 "$STATE/fetch.err")); the next trigger tries again"
  exit 1
fi
SHA=$(git -C "$PRISM" rev-parse --verify -q refs/remotes/origin/main)
if [ -z "$SHA" ]; then log "skip: origin/main is unknown in $PRISM"; exit 1; fi
WORKTREE="$(dirname "$PRISM")/prism-weekly-${SHA:0:8}"
LAST_ID=$(env_value "$STATE/last-completed.env" RUN_ID)
LAST_COMMIT=$(env_value "$STATE/last-completed.env" COMMIT)
log "origin/main is ${SHA:0:8}; the last completed run is ${LAST_ID:-none}${LAST_COMMIT:+ at ${LAST_COMMIT:0:8}}"

# Runs left unfinished (the machine restarted, or the driver gave up): mark the
# ones a dead lock left running, and pick the newest of this commit to resume.
RESUME=""
for id in $(ls -1 "$RUNS" 2>/dev/null | sort -r); do
  [ -f "$RUNS/$id/run.env" ] || continue
  state=$(env_value "$RUNS/$id/run.env" STATUS)
  [ "$state" = complete ] && break
  if [ "$state" = running ]; then
    [ "$DRY_RUN" = 1 ] || set_env "$RUNS/$id/run.env" STATUS interrupted
    log "run $id was left running by a process that is gone: it is marked interrupted"
  fi
  if [ -z "$RESUME" ] && [ "$(env_value "$RUNS/$id/run.env" COMMIT)" = "$SHA" ]; then RESUME=$id; fi
done

if [ "$DRY_RUN" = 1 ] && [ -n "$RESUME" ]; then
  log "run $RESUME is unfinished at ${SHA:0:8}: a real run would resume it; the dry run starts a separate folder and leaves it alone"
  RESUME=""
fi
if [ -z "$RESUME" ] && [ "$LAST_COMMIT" = "$SHA" ]; then
  if [ "$FORCE" = 1 ]; then log "origin/main has not changed since run $LAST_ID; running anyway (--force)"
  elif [ "$DRY_RUN" = 1 ]; then log "origin/main has not changed since run $LAST_ID; a real run would skip, the dry run goes on"
  else log "skip: origin/main (${SHA:0:8}) has not changed since the last completed run ($LAST_ID)"; exit 0; fi
fi

# The disk rule. Runs end by pruning to the newest KEEP_RUNS, so this normally
# passes at once; when it does not, the packs of the oldest run go first, to
# make room for the new one, and only then does the run refuse.
FREE=$(free_gb)
if [ "${FREE:-0}" -lt "$MIN_FREE_GB" ]; then
  log "only ${FREE:-?} GB free on $HOME, below the $MIN_FREE_GB GB a run needs: pruning the packs of runs beyond the newest $((KEEP_RUNS - 1)) first"
  prune_runs $((KEEP_RUNS - 1)) $((1 - DRY_RUN))
  FREE=$(free_gb)
  if [ "${FREE:-0}" -lt "$MIN_FREE_GB" ]; then
    log "refusing to start: only ${FREE:-?} GB free on $HOME and a run needs at least $MIN_FREE_GB GB (its packs take about 33 GB). Free disk space, then run weekly-run.sh again or wait for the next trigger."
    exit 1
  fi
fi
check_keys || exit 1
check_ollama || exit 1
ensure_reference

# ---- The run folder ---------------------------------------------------------
if [ -n "$RESUME" ]; then
  RUN_ID=$RESUME
  RUN_DIR="$RUNS/$RUN_ID"
  RUN_ENV="$RUN_DIR/run.env"
  log "resuming run $RUN_ID (it was $(env_value "$RUN_ENV" STATUS) when it stopped)"
  set_env "$RUN_ENV" STATUS running
  set_env "$RUN_ENV" RESUMED "$(date -u +%FT%TZ)"
else
  RUN_ID="$(date -u +%Y%m%d-%H%M%S)-${SHA:0:8}"
  # Two runs of one commit in the same second (a forced rerun) must not share a folder.
  while [ -e "$RUNS/$RUN_ID" ] || [ -e "$DRY_RUNS/$RUN_ID" ]; do sleep 1; RUN_ID="$(date -u +%Y%m%d-%H%M%S)-${SHA:0:8}"; done
  if [ "$DRY_RUN" = 1 ]; then RUN_DIR="$DRY_RUNS/$RUN_ID"; else RUN_DIR="$RUNS/$RUN_ID"; fi
  RUN_ENV="$RUN_DIR/run.env"
  mkdir -p "$RUN_DIR"
  {
    echo "RUN_ID=$RUN_ID"
    echo "COMMIT=$SHA"
    if [ "$DRY_RUN" = 1 ]; then echo "STATUS=dry-run"; else echo "STATUS=running"; fi
    echo "DRY_RUN=$DRY_RUN"
    echo "STARTED=$(date -u +%FT%TZ)"
    echo "HOST=$(hostname -s)"
    echo "LABEL_LOCOMO=weekly-locomo-$RUN_ID"
    echo "LABEL_LME=weekly-lme-$RUN_ID"
    echo "PREVIOUS_RUN="
  } > "$RUN_ENV"
fi
set_env "$RUN_ENV" OLLAMA_VERSION "$OLLAMA_NOW"
set_env "$RUN_ENV" MODEL "$MODEL"
set_env "$RUN_ENV" MODEL_DIGEST "$MODEL_DIGEST"
LABEL_LOCOMO=$(env_value "$RUN_ENV" LABEL_LOCOMO)
LABEL_LME=$(env_value "$RUN_ENV" LABEL_LME)
# The previous run is the last completed one, when its gate reports are still there.
PREVIOUS=""
if [ -n "$LAST_ID" ] && [ "$LAST_ID" != "$RUN_ID" ] && [ -f "$RUNS/$LAST_ID/run.env" ]; then PREVIOUS=$LAST_ID; fi
set_env "$RUN_ENV" PREVIOUS_RUN "$PREVIOUS"
log "run $RUN_ID: commit ${SHA:0:8}, previous run ${PREVIOUS:-none}, folder $RUN_DIR"

# ---- The worktree -----------------------------------------------------------
if [ ! -d "$WORKTREE" ]; then
  if git -C "$PRISM" worktree add --detach "$WORKTREE" "$SHA" >> "$RUN_DIR/worktree.log" 2>&1; then
    CREATED_WORKTREE=1
    log "created worktree $WORKTREE at ${SHA:0:8}"
  else
    log "STOP: could not create $WORKTREE ($(tail -1 "$RUN_DIR/worktree.log"))"
    set_env "$RUN_ENV" STATUS incomplete
    exit 1
  fi
fi
if [ "$(git -C "$WORKTREE" rev-parse HEAD 2>/dev/null)" != "$SHA" ]; then
  log "STOP: $WORKTREE exists but is not at ${SHA:0:8}; remove it (git -C $PRISM worktree remove $WORKTREE) and try again"
  [ "$DRY_RUN" = 1 ] || set_env "$RUN_ENV" STATUS incomplete
  exit 1
fi
if ! (cd "$WORKTREE" && uv sync -q --frozen --dev --extra api --extra mcp) >> "$RUN_DIR/worktree.log" 2>&1; then
  log "STOP: uv sync failed in $WORKTREE ($(tail -1 "$RUN_DIR/worktree.log"))"
  [ "$DRY_RUN" = 1 ] || set_env "$RUN_ENV" STATUS incomplete
  exit 1
fi

# ---- The builds and the gates -----------------------------------------------
if [ "$DRY_RUN" = 1 ]; then
  log "would run: BUILDS=defaults run-builds.sh in $WORKTREE: LoCoMo (10 packs, $LOCOMO_JOBS at a time) then LongMemEval-S (500 packs, $LME_JOBS at a time), each followed by its gate; labels $LABEL_LOCOMO and $LABEL_LME in $WEEKLY_PACKS, extraction cache $WEEKLY_CACHE"
  for pair in "locomo:$LABEL_LOCOMO" "longmemeval:$LABEL_LME"; do
    cp -p "$REFERENCE/gate-${pair%%:*}.json" "$RUN_DIR/gate-${pair#*:}.json" 2>/dev/null \
      && log "stand-in gate report for ${pair%%:*}: a copy of the stable reference's"
  done
else
  log "starting run-builds.sh (BUILDS=defaults, $MIN_FREE_GB GB rule passed with ${FREE} GB free)"
  env COMMIT="$SHA" WORKTREE="$WORKTREE" PACKS="$WEEKLY_PACKS" RESULTS_DIR="$RUN_DIR" \
    LOCOMO_DEFAULTS="$LABEL_LOCOMO" LME_DEFAULTS="$LABEL_LME" EXTRACTION_CACHE="$WEEKLY_CACHE" \
    BUILDS=defaults PRISM="$PRISM" \
    caffeinate -ims /bin/bash "$HERE/run-builds.sh" > "$RUN_DIR/driver.out" 2>&1 < /dev/null &
  CHILD=$!
  wait "$CHILD"
  log "run-builds.sh exited $?"
  CHILD=""
  set_env "$RUN_ENV" OLLAMA_VERSION_END "$(curl -s --max-time 5 "$OLLAMA_URL/api/version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' 2>/dev/null)"
fi

# ---- Comparisons and the report ---------------------------------------------
compare_gates stable "$REFERENCE/gate-locomo.json" "$RUN_DIR/gate-$LABEL_LOCOMO.json" locomo
compare_gates stable "$REFERENCE/gate-longmemeval.json" "$RUN_DIR/gate-$LABEL_LME.json" longmemeval
if [ -n "$PREVIOUS" ]; then
  PREV_ENV="$RUNS/$PREVIOUS/run.env"
  compare_gates previous "$RUNS/$PREVIOUS/gate-$(env_value "$PREV_ENV" LABEL_LOCOMO).json" "$RUN_DIR/gate-$LABEL_LOCOMO.json" locomo
  compare_gates previous "$RUNS/$PREVIOUS/gate-$(env_value "$PREV_ENV" LABEL_LME).json" "$RUN_DIR/gate-$LABEL_LME.json" longmemeval
fi

complete=1
valid_json "$RUN_DIR/gate-$LABEL_LOCOMO.json" || complete=0
valid_json "$RUN_DIR/gate-$LABEL_LME.json" || complete=0
if [ "$DRY_RUN" = 0 ]; then
  grep -q ' ALL DONE$' "$RUN_DIR/driver.log" 2>/dev/null || complete=0
  if [ "$complete" = 1 ]; then set_env "$RUN_ENV" STATUS complete; else set_env "$RUN_ENV" STATUS incomplete; fi
fi
set_env "$RUN_ENV" FINISHED "$(date -u +%FT%TZ)"
python3 "$HERE/weekly_report.py" report "$RUN_DIR" --reference-dir "$REFERENCE" ${PREVIOUS:+--previous-run-dir "$RUNS/$PREVIOUS"} > "$RUN_DIR/verdict.txt" 2>> "$RUN_DIR/report.err"
while IFS= read -r line; do log "verdict, $line"; done < "$RUN_DIR/verdict.txt"
log "report written: $RUN_DIR/report.md"

if [ "$DRY_RUN" = 1 ]; then
  prune_runs "$KEEP_RUNS" 0
  log "done: the dry run changed nothing outside $RUN_DIR and the state folder"
  exit 0
fi
if [ "$complete" = 1 ]; then
  printf 'RUN_ID=%s\nCOMMIT=%s\nFINISHED=%s\n' "$RUN_ID" "$SHA" "$(env_value "$RUN_ENV" FINISHED)" > "$STATE/last-completed.env"
  log "run $RUN_ID complete"
else
  log "run $RUN_ID is incomplete: the next trigger resumes it while origin/main is still ${SHA:0:8}; see $RUN_DIR/driver.log"
fi
prune_runs "$KEEP_RUNS" 1
[ "$complete" = 1 ] || exit 1
