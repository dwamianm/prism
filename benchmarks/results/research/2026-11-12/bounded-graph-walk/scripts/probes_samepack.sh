#!/bin/zsh
# Task 21 bar (a), probes: copy each conversation's main build (rc-M) and measure it without any model.
# $1 = worktree, $2 = side label. Settings come from each build's build.json.
set -e
R=/tmp/task21/probes; W=$1; S=$2
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked PRME_EXTRACTION_API_KEY= PRME_BENCH_CACHE_ONLY=1 \
  OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9
for C in defects-v1 chat-v1 member-v1; do
  rm -rf $R/$C/sp-$S; mkdir -p $R/$C
  cp -cR ~/Sites/prism/data/chat-probe-v1/$C/rc-M $R/$C/sp-$S
  (cd $W && uv run python -m benchmarks.diagnostics.chat_probe --conversation benchmarks/conversations/$C.json \
     --root $R measure --label sp-$S > $R/$C/sp-$S.out 2>&1)
  echo "$C sp-$S measured"
done
