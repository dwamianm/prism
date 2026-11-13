#!/bin/zsh
# Task 27 bar 3: offline evidence gate, ROUTING_VERSION 3 (origin/main worktree) against 4 (branch), then gate-compare.
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= TOKENIZERS_PARALLELISM=false
PY=~/Sites/prism-routing-cues/.venv/bin/python
CAP=~/Sites/prism/data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline
O=/tmp/task27/gate; mkdir -p $O
gate() { side=$1; dir=$2; (cd $dir && PYTHONPATH=$dir/src $PY -m benchmarks.diagnostics.product_packing gate --lme-captures $CAP --output $O/$side.json) > $O/$side.log 2>&1; echo "gate $side exit $? $(date +%T)"; }
gate v3 /tmp/task27/wt-v3 &
gate v4 ~/Sites/prism-routing-cues &
wait
cd ~/Sites/prism-routing-cues && $PY -m benchmarks.diagnostics.product_packing gate-compare $O/v3.json $O/v4.json --output $O/compare.json > $O/compare.md 2>&1; echo "compare exit $?"
