#!/bin/zsh
# Task 27 Part A: candidate router rules through the gate via the scratch hook (/tmp/task27/rules), no src change.
cd ~/Sites/prism-routing-cues
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=
R=~/Sites/prism/data/mid-tier-v1/reports
run() { label=$1; build=$2; rule=$3; shift 3; echo "== $label $(date +%T) load $(uptime | sed 's/.*averages: //')"; PYTHONPATH=/tmp/task27/rules PRME_T27_RULE=$rule uv run python -m benchmarks.mid_tier run --label $label --out $R/$label --reuse-build $build "$@" > /tmp/task27/$label.log 2>&1; echo "   done $(date +%T) exit $? load $(uptime | sed 's/.*averages: //')"; }
for rule in issue-stoplist issue-plus-use no-derived-cue; do
  run t27-main-route-$rule main-0.17.0a6 $rule --set memory_mode=classic --set enable_relation_routing=true
  run t27-af-$rule address-first $rule
done
echo ALL DONE $(date +%T)
