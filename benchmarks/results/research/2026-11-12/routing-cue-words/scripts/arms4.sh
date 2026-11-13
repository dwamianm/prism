#!/bin/zsh
# Task 27 Part A: the stop list without "want", plus "use", on both builds (scratch hook, no src change).
cd ~/Sites/prism-routing-cues
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=
R=~/Sites/prism/data/mid-tier-v1/reports
run() { label=$1; build=$2; rule=$3; shift 3; echo "== $label $(date +%T)"; PYTHONPATH=/tmp/task27/rules PRME_T27_RULE=$rule uv run python -m benchmarks.mid_tier run --label $label --out $R/$label --reuse-build $build "$@" > /tmp/task27/$label.log 2>&1; echo "   done $(date +%T) exit $?"; }
while pgrep -f arms3.sh > /dev/null; do sleep 10; done
run t27-main-route-use-no-want main-0.17.0a6 use-no-want --set memory_mode=classic --set enable_relation_routing=true
run t27-af-use-no-want address-first use-no-want
echo ALL DONE $(date +%T)
