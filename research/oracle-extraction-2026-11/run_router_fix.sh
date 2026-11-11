#!/bin/zsh
# Task 12, the router fix: rebuild M1 (the af-off-1111 cache, mode on) and tx-B1 on the three probes, 0 model calls,
# then the serve paths and union ranks. Usage: run_router_fix.sh <prefix> <results dir>
# (the after side: rf and results/router-fix).
cd "${0:A:h}/../.."  # the worktree this script is in
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= PRME_BENCH_CACHE_ONLY=1
P=$1
R=$2
L=$R/logs
mkdir -p $L
MODE='memory_mode="address_first"'
for name in defects-v1 chat-v1 member-v1; do
  conv=benchmarks/conversations/$name.json
  extra=(); [[ $name == member-v1 ]] && extra=(--speaker --set enable_speaker_grounding=true)
  uv run python -m benchmarks.diagnostics.chat_probe --conversation $conv build --label $P-M1 \
    --model deepseek-v4.1-flash:cloud --cloud --replace $extra --set $MODE > $L/$name-M1.log 2>&1
  echo "$name M1 exit $?"
  uv run python research/oracle-extraction-2026-11/run_oracle.py $name $P-B1 --levels O1,O2 --set $MODE \
    --replace $extra > $L/$name-B1.log 2>&1
  echo "$name B1 exit $?"
  uv run python research/oracle-extraction-2026-11/serve_paths.py $name $P-B1 --json $R/serve-$name-B1.json \
    > $L/$name-serve-B1.log 2>&1
  uv run python research/oracle-extraction-2026-11/serve_paths.py $name $P-M1 --json $R/serve-$name-M1.json \
    > $L/$name-serve-M1.log 2>&1
  uv run python research/oracle-extraction-2026-11/union_ranks.py $name fx-M1 tx-B1 $P-M1 $P-B1 \
    --json $R/union-$name.json > $L/$name-union.log 2>&1
  echo "$name measured exit $?"
done
echo DONE
