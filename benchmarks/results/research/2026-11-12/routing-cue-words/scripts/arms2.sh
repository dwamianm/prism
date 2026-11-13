#!/bin/zsh
# Task 27 Part A: the 6 routing-alone losses on the main-0.17.0a6 build, current main code, providers blocked.
cd ~/Sites/prism-routing-cues
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=
R=~/Sites/prism/data/mid-tier-v1/reports
run() { label=$1; shift; echo "== $label $(date +%T) load $(uptime | sed 's/.*averages: //')"; uv run python -m benchmarks.mid_tier run --label $label --out $R/$label --reuse-build main-0.17.0a6 "$@" > /tmp/task27/$label.log 2>&1; echo "   done $(date +%T) exit $? load $(uptime | sed 's/.*averages: //')"; }
run t27-main-classic --set memory_mode=classic
run t27-main-route --set memory_mode=classic --set enable_relation_routing=true
echo ALL DONE $(date +%T)
