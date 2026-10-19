#!/bin/bash
# Bring a finished scheduled run's small result files from the Mac mini to a
# branch of a fresh worktree, and open the results pull request in dwamianm/prism
# the way PR #290 did for the full batching run (issue #272). Run it on the
# MacBook, where the GitHub credentials are: the Mac mini holds none. Nothing
# runs it automatically yet; run it by hand after a scheduled run completes.
#
#   export BENCHMARK_HOST=dmac@<the Mac mini's address>
#   export BENCHMARK_SSH="-i ~/.ssh/<key> -o IdentitiesOnly=yes"   # optional ssh options
#   scripts/full-benchmark/publish-results.sh               newest completed run not on main yet
#   scripts/full-benchmark/publish-results.sh --list        every run on the Mac mini and whether it is published
#   scripts/full-benchmark/publish-results.sh --run ID      one run
#   scripts/full-benchmark/publish-results.sh --no-pr       copy and commit, stop before the push and the pull request
#   scripts/full-benchmark/publish-results.sh --with-gates  also copy the gate reports' JSON (about 2.7 MB per run)
#
# What is copied, into benchmarks/results/research/weekly/<run id>/: report.md,
# report.json, run.env, driver.log, the build summaries, the gate comparisons
# (.json, .md and .refused) and the gate reports' Markdown. Never the packs (about
# 33 GB) and never logs. Every copied file is checked against its sha256 on the
# Mac mini. The pull request text is weekly_report.py pr-body. A dry run
# (RUNS_SUBDIR=dry-runs with --run and --no-pr) rehearses this without a pull request.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PRISM="${PRISM:-$(dirname "$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir)")}"
REPO="${PRISM_REPO:-dwamianm/prism}"
REMOTE_HOME="${REMOTE_SCHEDULER_HOME:-Sites/prism-weekly-scheduler}"
RUNS_SUBDIR="${RUNS_SUBDIR:-runs}"
WORKTREES="${WORKTREES:-$PRISM/.claude/worktrees}"
RESULTS="benchmarks/results/research/weekly"

RUN=""; NO_PR=0; LIST=0; GATES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --run) RUN="${2:-}"; shift ;;
    --no-pr) NO_PR=1 ;;
    --list) LIST=1 ;;
    --with-gates) GATES=1 ;;
    -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

log() { echo "$(date -u +%FT%TZ) $*"; }
if [ -z "${BENCHMARK_HOST:-}" ]; then
  echo "Set BENCHMARK_HOST to the Mac mini, for example dmac@<address> (BENCHMARK_SSH holds extra ssh options)." >&2
  exit 2
fi
# shellcheck disable=SC2206
SSH=(ssh ${BENCHMARK_SSH:-} "$BENCHMARK_HOST")
remote() { "${SSH[@]}" /bin/bash -s -- "$@"; }

# The runs on the Mac mini, newest first: "<id> <status>".
runs=$(remote "$REMOTE_HOME/$RUNS_SUBDIR" <<'EOF'
cd "$HOME/$1" 2>/dev/null || exit 0
ls -1 | sort -r | while read -r id; do
  [ -f "$id/run.env" ] && echo "$id $(sed -n 's/^STATUS=//p' "$id/run.env" | tail -1)"
done
EOF
) || { log "STOP: cannot reach $BENCHMARK_HOST"; exit 1; }

git -C "$PRISM" fetch -q origin main || { log "STOP: cannot fetch origin"; exit 1; }
published() {  # run id: is it on main, or on a results branch already?
  [ -n "$(git -C "$PRISM" ls-tree --name-only origin/main "$RESULTS/$1/" 2>/dev/null)" ] && return 0
  [ -n "$(git -C "$PRISM" ls-remote --heads origin "weekly-results-$1" 2>/dev/null)" ] && return 0
  return 1
}

if [ "$LIST" = 1 ]; then
  echo "$runs" | while read -r id status; do
    [ -n "$id" ] || continue
    if published "$id"; then echo "$id  $status  published"; else echo "$id  $status  not published"; fi
  done
  exit 0
fi

if [ -z "$RUN" ]; then
  RUN=$(echo "$runs" | while read -r id status; do
    [ "$status" = complete ] || continue
    published "$id" || { echo "$id"; break; }
  done)
  [ -n "$RUN" ] || { log "nothing to publish: every completed run on $BENCHMARK_HOST is on main or has a results branch"; exit 0; }
fi
status=$(echo "$runs" | awk -v id="$RUN" '$1 == id {print $2}')
if [ -z "$status" ]; then log "STOP: no run $RUN in $REMOTE_HOME/$RUNS_SUBDIR on $BENCHMARK_HOST"; exit 1; fi
if [ "$status" != complete ] && ! { [ "$status" = dry-run ] && [ "$NO_PR" = 1 ]; }; then
  log "STOP: run $RUN has status $status; only a completed run is published (a dry run only with --no-pr)"
  exit 1
fi
if published "$RUN" && [ "$NO_PR" = 0 ]; then log "STOP: run $RUN is already on main or has a results branch"; exit 1; fi

BRANCH="weekly-results-$RUN"
WT="$WORKTREES/$BRANCH"
[ ! -e "$WT" ] || { log "STOP: $WT exists; remove it (git -C $PRISM worktree remove $WT) and run again"; exit 1; }
mkdir -p "$WORKTREES"
git -C "$PRISM" worktree add -q -b "$BRANCH" "$WT" origin/main || { log "STOP: cannot create the worktree"; exit 1; }
log "worktree $WT, branch $BRANCH, from origin/main $(git -C "$WT" rev-parse --short=8 HEAD)"

# The file list, the files and their checksums, all from the Mac mini.
DEST="$WT/$RESULTS/$RUN"
mkdir -p "$DEST"
files=$(remote "$REMOTE_HOME/$RUNS_SUBDIR/$RUN" "$GATES" <<'EOF'
cd "$HOME/$1" || exit 1
ls report.md report.json run.env driver.log summary-*.json compare-*.json compare-*.md compare-*.refused gate-*.md 2>/dev/null
[ "$2" = 1 ] && ls gate-*.json 2>/dev/null
exit 0
EOF
) || { log "STOP: cannot list the run's files"; exit 1; }
[ -n "$files" ] || { log "STOP: run $RUN has no result files"; exit 1; }
remote_sums=$(remote "$REMOTE_HOME/$RUNS_SUBDIR/$RUN" $files <<'EOF'
cd "$HOME/$1" || exit 1
shift
shasum -a 256 "$@"
EOF
) || { log "STOP: cannot checksum the run's files"; exit 1; }
remote "$REMOTE_HOME/$RUNS_SUBDIR/$RUN" $files <<'EOF' | tar -xf - -C "$DEST" || { log "STOP: the copy failed; worktree left at $WT"; exit 1; }
cd "$HOME/$1" || exit 1
shift
COPYFILE_DISABLE=1 tar -cf - "$@"
EOF
local_sums=$(cd "$DEST" && shasum -a 256 $files)
if [ "$local_sums" != "$remote_sums" ]; then
  log "STOP: a copied file differs from its original on the Mac mini; worktree left at $WT"
  exit 1
fi
count=$(echo "$files" | wc -l | tr -d ' ')
bytes=$(du -sk "$DEST" | cut -f1)
if [ "$bytes" -gt 12000 ]; then log "STOP: $bytes KB is more than a results pull request should hold; worktree left at $WT"; exit 1; fi
log "copied $count files ($bytes KB), every checksum matches the Mac mini's"

# Commit, push, open the pull request.
SHA=$(sed -n 's/^COMMIT=//p' "$DEST/run.env" | tail -1)
verdict=$(python3 "$HERE/weekly_report.py" verdict "$DEST" | head -1 | sed 's/^stable: //')
git -C "$WT" add "$RESULTS/$RUN"
subject="Add the scheduled full benchmark results for ${SHA:0:8}"
message="$subject

Run $RUN on the Mac mini, at commit $SHA.
Verdict against the last stable release: ${verdict}.

The result files were copied from the run folder over ssh by
scripts/full-benchmark/publish-results.sh, and each matches the sha256 of its
original. The packs and the gate reports' JSON stay on the Mac mini.

Part of dwamianm/prism-monorepo#272."
git -C "$WT" commit -q -m "$message" || { log "STOP: the commit failed; worktree left at $WT"; exit 1; }
log "committed $(git -C "$WT" rev-parse --short=8 HEAD) on $BRANCH"
if [ "$NO_PR" = 1 ]; then
  log "stopping before the push and the pull request (--no-pr); worktree left at $WT"
  exit 0
fi
git -C "$WT" push -q -u origin "$BRANCH" || { log "STOP: the push failed; worktree left at $WT"; exit 1; }
body_file="$(mktemp "${TMPDIR:-/tmp}/weekly-pr-body.XXXXXX")"
python3 "$HERE/weekly_report.py" pr-body "$DEST" --files-verified "$count" > "$body_file"
url=$(cd "$WT" && gh pr create --repo "$REPO" --base main --head "$BRANCH" --title "$subject" --body-file "$body_file") \
  || { log "STOP: gh pr create failed; the branch is pushed, worktree left at $WT"; rm -f "$body_file"; exit 1; }
rm -f "$body_file"
log "opened $url"
git -C "$PRISM" worktree remove "$WT" >/dev/null 2>&1 && log "removed worktree $WT"
echo "$url"
