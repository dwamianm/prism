#!/bin/zsh
# Task 27 bar 6 (information only, spent sets): graph oracle blind run on ROUTING_VERSION 3 and 4.
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= TOKENIZERS_PARALLELISM=false
PY=~/Sites/prism-routing-cues/.venv/bin/python
O=/tmp/task27/oracle; mkdir -p $O
run() { side=$1; dir=$2; (cd $dir && PYTHONPATH=$dir/src $PY -m benchmarks.graph_oracle blind --out $O/$side) > $O/$side.log 2>&1; echo "blind $side exit $? $(date +%T)"; }
run v3 /tmp/task27/wt-v3
run v4 ~/Sites/prism-routing-cues
