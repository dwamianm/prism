#!/bin/bash
# Show the state of the scheduled full benchmark: the job, the next run, the
# lock, the last run and its verdict, the disk. Changes nothing and makes no
# network call (origin/main is the commit the last fetch saw).
#   status-weekly.sh          the summary
#   status-weekly.sh --sizes  also the size of the packs (slow: walks them)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/weekly-settings.sh"

SIZES=0
[ "${1:-}" = "--sizes" ] && SIZES=1
env_value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -1; }  # file key
count() { ls "$WEEKLY_PACKS/$1/$2"/*/manifest.json 2>/dev/null | wc -l | tr -d ' '; }

echo "Scheduler: $SCHEDULER_HOME"
echo

# The job.
info=$(launchctl print "gui/$(id -u)/$JOB_LABEL" 2>/dev/null)
if [ -n "$info" ]; then
  field() { printf '%s\n' "$info" | sed -n "s/^[[:space:]]*$1 = //p" | head -1; }
  echo "Job:       $JOB_LABEL is loaded (state: $(field state); runs so far: $(field runs); last exit code: $(field 'last exit code'))"
else
  echo "Job:       $JOB_LABEL is NOT loaded; run install-schedule.sh"
fi
python3 - "$SCHEDULE_WEEKDAYS" "$SCHEDULE_HOUR" "$SCHEDULE_MINUTE" <<'EOF'
import sys
import time
from datetime import datetime, timedelta

days = {int(day) % 7 for day in sys.argv[1].split()}  # launchd: 0 or 7 is Sunday, 1 is Monday
hour, minute = int(sys.argv[2]), int(sys.argv[3])
names = "Sunday Monday Tuesday Wednesday Thursday Friday Saturday".split()
now = datetime.now()
for offset in range(9):
    candidate = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > now and candidate.isoweekday() % 7 in days:
        zone = time.strftime("%Z", time.localtime(time.mktime(candidate.timetuple())))
        print("Schedule:  {} at {:02d}:{:02d} local time".format(" and ".join(names[d] for d in sorted(days, key=lambda d: (d + 6) % 7)), hour, minute))
        print("Next run:  {} {}".format(candidate.strftime("%a %Y-%m-%d %H:%M"), zone))
        break
EOF

# The lock.
holder=$(cat "$STATE/weekly.lock/pid" 2>/dev/null)
current=""
if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null && ps -p "$holder" -o command= 2>/dev/null | grep -q 'weekly-run'; then
  for dir in "$RUNS" "$DRY_RUNS"; do
    for id in $(ls -1 "$dir" 2>/dev/null | sort -r); do
      case "$(env_value "$dir/$id/run.env" STATUS)" in running) current="$dir/$id"; break 2 ;; esac
    done
  done
  echo "Lock:      held by pid $holder, up $(ps -p "$holder" -o etime= | tr -d ' ')${current:+ ($(basename "$current"))}"
else
  echo "Lock:      free${holder:+ (stale pid $holder, cleared by the next trigger)}"
fi

# A run that is going.
if [ -n "$current" ]; then
  label_locomo=$(env_value "$current/run.env" LABEL_LOCOMO)
  label_lme=$(env_value "$current/run.env" LABEL_LME)
  echo "Progress:  LoCoMo $(count "$label_locomo" locomo) of 10 packs, LongMemEval-S $(count "$label_lme" longmemeval) of 500 packs"
  echo "           $(tail -1 "$current/driver.log" 2>/dev/null || echo "no driver log yet")"
fi

# The last run, the last completed run, and origin/main.
newest=$(ls -1 "$RUNS" 2>/dev/null | sort -r | head -1)
echo
if [ -n "$newest" ]; then
  echo "Last run:  $newest, status $(env_value "$RUNS/$newest/run.env" STATUS), started $(env_value "$RUNS/$newest/run.env" STARTED), finished $(env_value "$RUNS/$newest/run.env" FINISHED)"
  if [ -f "$RUNS/$newest/report.json" ]; then
    python3 "$HERE/weekly_report.py" verdict "$RUNS/$newest" | sed 's/^/  verdict, /'
  fi
else
  echo "Last run:  none yet"
fi
last_id=$(env_value "$STATE/last-completed.env" RUN_ID)
last_commit=$(env_value "$STATE/last-completed.env" COMMIT)
echo "Completed: ${last_id:-none}${last_commit:+ at ${last_commit:0:8}}"
main=$(git -C "$PRISM" rev-parse --verify -q refs/remotes/origin/main 2>/dev/null)
if [ -n "$main" ]; then
  if [ -z "$last_commit" ]; then
    echo "origin/main: ${main:0:8} (as of the last fetch); the next trigger will run"
  elif [ "$main" = "$last_commit" ]; then
    echo "origin/main: ${main:0:8} (as of the last fetch), unchanged since that run; the next trigger will skip"
  else
    total=$(git -C "$PRISM" rev-list --count "$last_commit..$main" 2>/dev/null || echo "?")
    code=$(git -C "$PRISM" diff --name-only "$last_commit" "$main" -- src benchmarks pyproject.toml uv.lock 2>/dev/null | wc -l | tr -d ' ')
    echo "origin/main: ${main:0:8} (as of the last fetch), $total commit(s) past that run, $code changed file(s) under src, benchmarks, pyproject.toml or uv.lock; the next trigger will run"
  fi
fi

# Disk and packs.
free=$(df -Pk "$HOME" | awk 'NR==2 {print int($4 / 1048576)}')
verdict="enough"
[ "${free:-0}" -lt "$MIN_FREE_GB" ] && verdict="BELOW the rule, so a run would refuse"
echo
echo "Disk:      $free GB free (a run needs $MIN_FREE_GB GB): $verdict"
echo "Packs:     $(ls -1 "$WEEKLY_PACKS" 2>/dev/null | grep -vc '\.log$' | tr -d ' ') build folder(s) in $WEEKLY_PACKS; the newest $KEEP_RUNS runs keep theirs"
[ "$SIZES" = 1 ] && echo "           $(du -sh "$WEEKLY_PACKS" 2>/dev/null | cut -f1) in packs, $(du -sh "$WEEKLY_CACHE" 2>/dev/null | cut -f1) in the extraction cache"
echo
echo "Last trigger log lines ($STATE/schedule.log):"
if [ -f "$STATE/schedule.log" ]; then tail -6 "$STATE/schedule.log" | sed 's/^/  /'; else echo "  (no log yet)"; fi
