#!/bin/bash
# Remove the launchd job that runs the scheduled full benchmark (issue #272).
# It unloads $JOB_LABEL and deletes its plist, and nothing else: the scheduler
# folder (state, run folders and reports, packs, worktrees) stays, so reports
# are kept and a later install-schedule.sh picks up where this left off.
#
#   uninstall-schedule.sh             refuses while a scheduled run is going
#   uninstall-schedule.sh --stop-run  unloads the job even then, which stops the run
#                                     (the next run resumes it if origin/main is unchanged)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/weekly-settings.sh"

PLIST="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}/$JOB_LABEL.plist"
DOMAIN="gui/$(id -u)"
log() { echo "$(date -u +%FT%TZ) $*"; }
loaded() { launchctl print "$DOMAIN/$JOB_LABEL" >/dev/null 2>&1; }

holder=$(cat "$STATE/weekly.lock/pid" 2>/dev/null)
if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null && ps -p "$holder" -o command= 2>/dev/null | grep -q 'weekly-run'; then
  if [ "${1:-}" != "--stop-run" ]; then
    log "STOP: a scheduled run is going (pid $holder). Unloading the job would stop it. Wait for it, or pass --stop-run."
    exit 1
  fi
  log "a scheduled run is going (pid $holder); unloading the job stops it"
fi

if loaded; then
  launchctl bootout "$DOMAIN/$JOB_LABEL" || { log "STOP: launchctl bootout failed"; exit 1; }
  log "unloaded $JOB_LABEL from $DOMAIN"
else
  log "$JOB_LABEL is not loaded"
fi
if [ -f "$PLIST" ]; then rm -f "$PLIST" && log "deleted $PLIST"; else log "no plist at $PLIST"; fi
log "left in place: $SCHEDULER_HOME (reports, packs, state); delete it by hand only if you want them gone"
