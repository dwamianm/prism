#!/bin/zsh
# Task 26 bar 1: offline evidence gate on main (56b51d53) and on the change, current defaults, then gate-compare.
set -u
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked PRME_EXTRACTION_API_KEY= PRME_BENCH_CACHE_ONLY=1 \
  OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 TOKENIZERS_PARALLELISM=false
PY=$HOME/Sites/prism-seed-reach/.venv/bin/python
OUT=/tmp/task26/gates; mkdir -p $OUT
LME=$HOME/Sites/prism/data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline
for side in main:/tmp/task26/main change:$HOME/Sites/prism-seed-reach; do
  name=${side%%:*}; dir=${side#*:}
  (cd $dir && PYTHONPATH=$dir/src:$dir $PY -m benchmarks.diagnostics.product_packing gate --lme-captures $LME \
     --output $OUT/gate-$name.json > $OUT/gate-$name.log 2>&1)
  echo "gate $name exit $?"
done
cd $HOME/Sites/prism-seed-reach && PYTHONPATH=src:. $PY -m benchmarks.diagnostics.product_packing gate-compare \
  $OUT/gate-main.json $OUT/gate-change.json --output $OUT/compare-main-vs-change.json > $OUT/compare-main-vs-change.log 2>&1
echo "compare exit $?"
