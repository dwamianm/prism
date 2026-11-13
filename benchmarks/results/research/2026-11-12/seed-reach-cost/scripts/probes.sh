#!/bin/zsh
# Task 26 bar 1 (task 24 script), probes: copy each conversation's main build (rc-M) and measure it without any model, once per tree.
# $1 = tree, $2 = side label. Settings come from each build's build.json. Python is the change's venv; the tree's
# own src comes first on the path.
set -e
R=/tmp/task26/probes-out; W=$1; S=$2
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked PRME_EXTRACTION_API_KEY= PRME_BENCH_CACHE_ONLY=1 \
  OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 TOKENIZERS_PARALLELISM=false
for C in defects-v1 chat-v1 member-v1; do
  rm -rf $R/$C/sp-$S; mkdir -p $R/$C
  cp -cR ~/Sites/prism/data/chat-probe-v1/$C/rc-M $R/$C/sp-$S
  (cd $W && PYTHONPATH=$W/src:$W ~/Sites/prism-seed-reach/.venv/bin/python -m benchmarks.diagnostics.chat_probe \
     --conversation benchmarks/conversations/$C.json --root $R measure --label sp-$S > $R/$C/sp-$S.out 2>&1)
  echo "$C sp-$S measured"
done
