#!/bin/bash
# Install the launchd job that runs weekly-run.sh on Monday and Thursday at
# 00:30 local time (issue #272). Safe to run again: with an unchanged job it
# does nothing; with a changed one it replaces it. Run it on the Mac that does
# the benchmark, as the user who owns it, from the installed copy of the scripts
# (deploy-schedule.sh puts it in $SCHEDULER_HOME/bin).
#
#   install-schedule.sh          write the LaunchAgent plist, load it, show when it fires next
#   install-schedule.sh --print  only print the plist
#
# The job has no RunAtLoad and no KeepAlive: installing it, or logging in,
# never starts a run. It needs the Mac user to be logged in (a LaunchAgent
# lives in the user's gui domain). It does not touch any other launchd job.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/weekly-settings.sh"

PLIST_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST="$PLIST_DIR/$JOB_LABEL.plist"
DOMAIN="gui/$(id -u)"

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$(xml "$JOB_LABEL")</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(xml "$HERE/weekly-run.sh")</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
EOF
  for day in $SCHEDULE_WEEKDAYS; do
    echo "    <dict><key>Weekday</key><integer>$day</integer><key>Hour</key><integer>$SCHEDULE_HOUR</integer><key>Minute</key><integer>$SCHEDULE_MINUTE</integer></dict>"
  done
  cat <<EOF
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$(xml "$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")</string>
    <key>SCHEDULER_HOME</key>
    <string>$(xml "$SCHEDULER_HOME")</string>
    <key>PRISM</key>
    <string>$(xml "$PRISM")</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>$(xml "$SCHEDULER_HOME")</string>
  <key>StandardOutPath</key>
  <string>$(xml "$STATE/launchd.out")</string>
  <key>StandardErrorPath</key>
  <string>$(xml "$STATE/launchd.err")</string>
</dict>
</plist>
EOF
}

if [ "${1:-}" = "--print" ]; then plist; exit 0; fi

log() { echo "$(date -u +%FT%TZ) $*"; }
loaded() { launchctl print "$DOMAIN/$JOB_LABEL" >/dev/null 2>&1; }

for file in weekly-run.sh weekly-settings.sh settings.sh run-builds.sh weekly_report.py status-weekly.sh; do
  [ -f "$HERE/$file" ] || { log "STOP: $HERE/$file is missing; install from a complete copy of scripts/full-benchmark"; exit 1; }
done
mkdir -p "$STATE" "$PLIST_DIR"
[ "$HERE" = "$SCHEDULER_HOME/bin" ] || log "note: the job will run the scripts in $HERE, not an installed copy in $SCHEDULER_HOME/bin (deploy-schedule.sh makes one)"

tmp="$(mktemp "${TMPDIR:-/tmp}/weekly-plist.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
plist > "$tmp"
plutil -lint "$tmp" >/dev/null || { log "STOP: the generated plist is not valid"; exit 1; }

if loaded && cmp -s "$tmp" "$PLIST"; then
  log "$JOB_LABEL is already installed and loaded, and its plist is unchanged: nothing to do"
else
  # Unloading a job that is running a benchmark stops it, so wait for the run to end.
  holder=$(cat "$STATE/weekly.lock/pid" 2>/dev/null)
  if loaded && [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null && ps -p "$holder" -o command= 2>/dev/null | grep -q 'weekly-run'; then
    log "STOP: a scheduled run is going (pid $holder); replacing the job now would stop it. Run this again when it has finished."
    exit 1
  fi
  cp "$tmp" "$PLIST" && chmod 644 "$PLIST" || { log "STOP: cannot write $PLIST"; exit 1; }
  if loaded; then
    launchctl bootout "$DOMAIN/$JOB_LABEL" || { log "STOP: launchctl bootout failed"; exit 1; }
    for _ in 1 2 3 4 5 6 7 8 9 10; do loaded || break; sleep 1; done
  fi
  launchctl enable "$DOMAIN/$JOB_LABEL" >/dev/null 2>&1
  if ! launchctl bootstrap "$DOMAIN" "$PLIST"; then
    log "STOP: launchctl bootstrap $DOMAIN failed. The Mac user must be logged in at the console for a LaunchAgent to load."
    exit 1
  fi
  log "installed $PLIST and loaded $JOB_LABEL in $DOMAIN"
fi

if ! loaded; then log "STOP: $JOB_LABEL is not loaded after the install"; exit 1; fi
echo
launchctl print "$DOMAIN/$JOB_LABEL" | sed -n '/^[[:space:]]*state = /p;/^[[:space:]]*program = /p;/^[[:space:]]*runs = /p;/descriptor = {/,/}/p'
echo
/bin/bash "$HERE/status-weekly.sh" | sed -n '1,5p'
