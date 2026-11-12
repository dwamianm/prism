#!/bin/zsh
# Task 21 bar (b), chat packs: P4's chat_read_cost.py on each LoCoMo pack, origin/main and the walk branch in turn,
# the order swapped on every other pack so drift and load hit both sides alike. Providers blocked.
set -e
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= PYTHONPATH=.
P4=~/Sites/prism-chat-reads/research/chat-read-cost-2026-11/chat_read_cost.py
O=/tmp/task21/chat; rm -rf $O; mkdir -p $O
i=0
for u in conv-26 conv-30 conv-41 conv-42 conv-43 conv-44 conv-47 conv-48 conv-49 conv-50; do
  if (( i % 2 == 0 )); then order=(main walk); else order=(walk main); fi
  for s in $order; do
    if [[ $s == main ]]; then W=/tmp/task21/main; else W=~/Sites/prism-walk; fi
    (cd $W && .venv/bin/python $P4 --packs /tmp/p4/packs --scratch $O/scratch-$s --out $O/$s-$u.json \
       --benchmarks locomo --units $u > $O/$s-$u.log 2>&1)
    echo "$u $s done load $(sysctl -n vm.loadavg)"
  done
  i=$((i + 1))
done
