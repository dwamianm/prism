#!/bin/zsh
# Task 27 Part B, bar 2: the real ROUTING_VERSION 4 code (no hook), retrieval half over the saved builds.
cd ~/Sites/prism-routing-cues
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=
R=~/Sites/prism/data/mid-tier-v1/reports
run() { label=$1; build=$2; shift 2; echo "== $label $(date +%T)"; uv run --no-sync python -m benchmarks.mid_tier run --label $label --out $R/$label --reuse-build $build "$@" > /tmp/task27/$label.log 2>&1; echo "   done $(date +%T) exit $?"; }
run t27-af-v4 address-first
run t27-main-route-v4 main-0.17.0a6 --set memory_mode=classic --set enable_relation_routing=true
echo ALL DONE $(date +%T)
