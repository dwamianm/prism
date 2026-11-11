#!/bin/zsh
# Every arm of GUIDE.md on the three probes, one build at a time, no model call. Usage: run_arms.sh [prefix [logs]]
# (round 1: ox and results/logs; round 2: fx and results/round2/logs).
# M0 and M1 replay the saved off build's extraction cache (PRME_BENCH_CACHE_ONLY: a miss fails, never calls).
# Only chat-v1 has O2 facts, so on defects-v1 and member-v1 the A arms equal the B arms and are not built twice.
cd "${0:A:h}/../.."  # the worktree this script is in
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= PRME_BENCH_CACHE_ONLY=1
P=${1:-ox}
L=${2:-research/oracle-extraction-2026-11/results/logs}
mkdir -p $L
MODE='memory_mode="address_first"'
probe() { uv run python -m benchmarks.diagnostics.chat_probe "$@"; }
oracle() { uv run python research/oracle-extraction-2026-11/run_oracle.py "$@"; }
for name in defects-v1 chat-v1 member-v1; do
  conv=benchmarks/conversations/$name.json
  extra=(); [[ $name == member-v1 ]] && extra=(--speaker --set enable_speaker_grounding=true)
  probe --conversation $conv build --label $P-M0 --model deepseek-v4.1-flash:cloud --cloud --replace $extra > $L/$name-M0.log 2>&1; echo "$name M0 exit $?"
  probe --conversation $conv build --label $P-M1 --model deepseek-v4.1-flash:cloud --cloud --replace $extra --set $MODE > $L/$name-M1.log 2>&1; echo "$name M1 exit $?"
  if [[ $name == chat-v1 ]]; then
    oracle $name $P-A0 --levels O1 --replace $extra > $L/$name-A0.log 2>&1; echo "$name A0 exit $?"
    oracle $name $P-A1 --levels O1 --replace $extra --set $MODE > $L/$name-A1.log 2>&1; echo "$name A1 exit $?"
  fi
  oracle $name $P-B0 --levels O1,O2 --replace $extra > $L/$name-B0.log 2>&1; echo "$name B0 exit $?"
  oracle $name $P-B1 --levels O1,O2 --replace $extra --set $MODE > $L/$name-B1.log 2>&1; echo "$name B1 exit $?"
  oracle $name $P-B1r --levels O1,O2 --replace $extra --set $MODE > $L/$name-B1r.log 2>&1; echo "$name B1r exit $?"
done
echo DONE
