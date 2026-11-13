#!/bin/zsh
# Task 26 bars 1 and 2: main (56b51d53, /tmp/task26/main) against the change (~/Sites/prism-seed-reach), one run at a time.
# Hosted seed count setting runs 3 rounds (speed and same output); the other settings 1 round (same output).
set -u
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked PRME_EXTRACTION_API_KEY= \
  OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 TOKENIZERS_PARALLELISM=false
PY=$HOME/Sites/prism-seed-reach/.venv/bin/python
B=/tmp/task26/bars; OUT=/tmp/task26/reports; mkdir -p $OUT
HOSTED='{"scoring": {"rrf_graph_seed_count": true, "rrf_graph_weight": 0.5}}'
run() {  # label pack rounds set
  echo "== $1 load $(sysctl -n vm.loadavg) $(date +%T)"
  $PY $B/pairs.py --pack $2 --rounds $3 --set "$4" --label $1 --main /tmp/task26/main \
    --change $HOME/Sites/prism-seed-reach --out $OUT > $OUT/$1.log 2>$OUT/$1.err
  echo "exit $? $(date +%T)"; tail -c 2500 $OUT/$1.log; echo
}
run speed-m3-hosted m3 3 "$HOSTED"
run speed-flat-hosted flat 3 "$HOSTED"
run same-m3-default m3 1 '{}'
run same-flat-default flat 1 '{}'
run same-m3-classic m3 1 '{"memory_mode": "classic"}'
run same-flat-classic flat 1 '{"memory_mode": "classic"}'
run same-m3-robot m3 1 '{"read_profile": "robot"}'
run same-flat-robot flat 1 '{"read_profile": "robot"}'
