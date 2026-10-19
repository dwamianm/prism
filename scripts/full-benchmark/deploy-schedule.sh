#!/bin/bash
# Copy the scheduler's scripts to the Mac mini and install the launchd job there
# (issue #272). Run it on the MacBook, from a checkout of the repository, after
# the scripts change. Nothing else on the Mac mini is touched, and a run that is
# going is never stopped (install-schedule.sh refuses while one is).
#
#   export BENCHMARK_HOST=dmac@<the Mac mini's address>
#   export BENCHMARK_SSH="-i ~/.ssh/<key> -o IdentitiesOnly=yes"   # optional ssh options
#   scripts/full-benchmark/deploy-schedule.sh               copy, check, install
#   scripts/full-benchmark/deploy-schedule.sh --no-install  copy and check only
#
# The copy goes to ~/Sites/prism-weekly-scheduler/bin on the Mac mini
# (REMOTE_SCHEDULER_HOME, relative to its home folder), outside any worktree.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REMOTE_HOME="${REMOTE_SCHEDULER_HOME:-Sites/prism-weekly-scheduler}"
FILES="settings.sh run-builds.sh weekly-settings.sh weekly-run.sh weekly_report.py install-schedule.sh uninstall-schedule.sh status-weekly.sh"

if [ -z "${BENCHMARK_HOST:-}" ]; then
  echo "Set BENCHMARK_HOST to the Mac mini, for example dmac@<address> (BENCHMARK_SSH holds extra ssh options)." >&2
  exit 2
fi
# shellcheck disable=SC2206
SSH=(ssh ${BENCHMARK_SSH:-} "$BENCHMARK_HOST")
log() { echo "$(date -u +%FT%TZ) $*"; }

for file in $FILES; do [ -f "$HERE/$file" ] || { log "STOP: $HERE/$file is missing"; exit 1; }; done
"${SSH[@]}" "mkdir -p '$REMOTE_HOME/bin'" || { log "STOP: cannot reach $BENCHMARK_HOST"; exit 1; }
COPYFILE_DISABLE=1 tar -C "$HERE" -cf - $FILES | "${SSH[@]}" "tar -C '$REMOTE_HOME/bin' -xf -" || { log "STOP: the copy failed"; exit 1; }

# Every file must match its original byte for byte.
local_sums=$(cd "$HERE" && shasum -a 256 $FILES)
remote_sums=$("${SSH[@]}" "cd '$REMOTE_HOME/bin' && shasum -a 256 $FILES")
if [ "$local_sums" != "$remote_sums" ]; then
  log "STOP: the copied files differ from the originals:"
  diff <(echo "$local_sums") <(echo "$remote_sums")
  exit 1
fi
log "copied $(echo $FILES | wc -w | tr -d ' ') files to $BENCHMARK_HOST:$REMOTE_HOME/bin, checksums match"

[ "${1:-}" = "--no-install" ] && exit 0
"${SSH[@]}" "'$REMOTE_HOME/bin/install-schedule.sh'"
