#!/bin/zsh
# Task 22 step 2, bar 1 rerun for the stated store, once the machine is quiet.
# Waits until the 1-minute load average is at most QUIET, then runs the stated store in both memory modes.
set -u
QUIET=${1:-7}
cd /tmp/task22/robot
while :; do
  load=$(sysctl -n vm.loadavg | awk '{print $2}')
  echo "$(date +%H:%M:%S) load $load"
  if [ "${load%.*}" -le "$QUIET" ]; then break; fi
  sleep 60
done
echo "$(date +%H:%M:%S) quiet at load $load, starting"
for mode in classic address_first; do
  PYTHONPATH=src:. /tmp/task22/stack/.venv/bin/python /tmp/task22/interleaved.py --pack stated --mode $mode \
    --label bar1-quiet --rounds 3 > /tmp/task22/reports-new/quiet-bar1-stated-$mode.log 2>&1
  echo "quiet stated $mode exit $?"
done
