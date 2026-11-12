#!/bin/zsh
# Task 22 step 2, bar 2: profile off on read-profile-robot against main 934d60fd, fingerprints with receipts, 1 round.
set -u
for tree in main:/tmp/task22/main robot:/tmp/task22/robot; do
  name=${tree%%:*}; dir=${tree#*:}
  for pack in bench stated; do
    for mode in classic address_first; do
      (cd $dir && PYTHONPATH=src:. /tmp/task22/stack/.venv/bin/python /tmp/task22/robot_read.py --pack $pack \
         --label bar2-$name-$mode --rounds 1 --set memory_mode=$mode --scratch /tmp/task22/scratch-bar2 \
         --out /tmp/task22/reports-bar2 > /tmp/task22/reports-bar2/bar2-$name-$pack-$mode.log 2>&1)
      echo "bar2 $name $pack $mode exit $?"
    done
  done
done
