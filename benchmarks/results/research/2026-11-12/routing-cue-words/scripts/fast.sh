#!/bin/zsh
# Task 27 Part B, bar 1: fast tiers, ROUTING_VERSION 3 (origin/main 7472a46f worktree) against 4 (branch), saved
# extraction cache. Both sides use the branch venv; the v3 side puts the worktree's src first on PYTHONPATH.
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=
PY=~/Sites/prism-routing-cues/.venv/bin/python
V3=/tmp/task27/wt-v3; V4=~/Sites/prism-routing-cues
O=/tmp/task27/fast; mkdir -p $O
M=(--model deepseek-v4.1-flash:cloud --cloud)
probe() { side=$1; dir=$2; conv=$3; shift 3
  (cd $dir && PYTHONPATH=$dir/src $PY -m benchmarks.diagnostics.chat_probe --conversation benchmarks/conversations/$conv.json \
     build --label t27-$side $M --replace "$@") > $O/$conv-$side.log 2>&1; echo "$conv $side exit $?"; }
for side in v3 v4; do
  [[ $side == v3 ]] && d=$V3 || d=$V4
  probe $side $d defects-v1
  probe $side $d chat-v1
  probe $side $d member-v1 --speaker --set enable_speaker_grounding=true
done
cd $V4
for conv in defects-v1 chat-v1 member-v1; do
  $PY -m benchmarks.diagnostics.chat_probe --conversation benchmarks/conversations/$conv.json compare t27-v3 t27-v4 > $O/$conv-compare.md 2>&1; echo "$conv compare exit $?"
done
for side in v3 v4; do
  [[ $side == v3 ]] && d=$V3 || d=$V4
  (cd $d && PYTHONPATH=$d/src $PY -m benchmarks.diagnostics.corrections_probe run $M --label t27-$side --replace --output $O/corrections-$side) > $O/corrections-$side.log 2>&1; echo "corrections $side exit $?"
  (cd $d && PYTHONPATH=$d/src $PY -m benchmarks.fast_cycle run --label t27-$side --out $O/fast-cycle-$side) > $O/fast-cycle-$side.log 2>&1; echo "fast cycle $side exit $?"
done
echo FAST DONE $(date +%T)
