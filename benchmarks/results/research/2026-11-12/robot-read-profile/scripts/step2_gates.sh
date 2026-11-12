#!/bin/zsh
# Task 22 step 2: offline evidence gate on read-profile-robot (main 934d60fd + change), four settings.
set -u
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked PRME_EXTRACTION_API_KEY= PRME_BENCH_CACHE_ONLY=1 \
  OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 TOKENIZERS_PARALLELISM=false
PY=/tmp/task22/stack/.venv/bin/python
OUT=/tmp/task22/gates2; mkdir -p $OUT
LME=/Users/dwamianm/Sites/prism/data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline
cd /tmp/task22/robot
run() { name=$1; shift
  PYTHONPATH=src:. $PY -m benchmarks.diagnostics.product_packing gate --lme-captures $LME "$@" \
    --output $OUT/gate-$name.json > $OUT/gate-$name.log 2>&1
  echo "gate $name exit $?"; }
run classic-on --set read_profile='"robot"'
run af-off --set memory_mode='"address_first"'
run af-on --set memory_mode='"address_first"' --set read_profile='"robot"'
run classic-off
cmp() { PYTHONPATH=src:. $PY -m benchmarks.diagnostics.product_packing gate-compare $OUT/gate-$1.json $OUT/gate-$2.json \
    --output $OUT/compare-$1-vs-$2.json > $OUT/compare-$1-vs-$2.log 2>&1; echo "compare $1 $2 exit $?"; }
cmp classic-off classic-on
cmp af-off af-on
PYTHONPATH=src:. $PY -m benchmarks.diagnostics.product_packing gate-compare /tmp/task22/gate-walk-cf709aef.json \
  $OUT/gate-classic-off.json --output $OUT/compare-main-vs-classic-off.json > $OUT/compare-main-vs-classic-off.log 2>&1
echo "compare main classic-off exit $?"
