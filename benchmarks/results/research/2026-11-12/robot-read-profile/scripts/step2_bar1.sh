#!/bin/zsh
# Task 22 step 2, bars 1 and 3: interleaved off/on pairs, 3 rounds, both packs, both memory modes, real main + change.
set -u
cd /tmp/task22/robot
for pack in bench stated; do
  for mode in classic address_first; do
    PYTHONPATH=src:. /tmp/task22/stack/.venv/bin/python /tmp/task22/interleaved.py --pack $pack --mode $mode \
      --label bar1 --rounds 3 > /tmp/task22/reports-new/main-bar1-$pack-$mode.log 2>&1
    echo "$pack $mode exit $?"
  done
done
