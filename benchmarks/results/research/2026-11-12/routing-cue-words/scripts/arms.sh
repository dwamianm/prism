#!/bin/zsh
# Task 27 Part A: gate replays on the address-first build, main 56b51d53, providers blocked, one at a time.
cd ~/Sites/prism-routing-cues
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=
R=~/Sites/prism/data/mid-tier-v1/reports
run() { label=$1; shift; echo "== $label $(date +%T) load $(uptime | sed 's/.*averages: //')"; uv run python -m benchmarks.mid_tier run --label $label --out $R/$label --reuse-build address-first "$@" > /tmp/task27/$label.log 2>&1; echo "   done $(date +%T) exit $? load $(uptime | sed 's/.*averages: //')"; }
run t27-af-v3
(export PYTHONPATH=/tmp/task27/noroute PRME_T27_NO_ROUTE=1; run t27-af-noroute)
run t27-af-route-only --set enable_belief_state=false
run t27-af-none --set enable_belief_state=false --set enable_relation_routing=false
echo ALL DONE $(date +%T)
