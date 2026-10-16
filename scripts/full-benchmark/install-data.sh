#!/bin/bash
# Fetch the two datasets and the official LongMemEval judge for this clone and
# check their checksums. Never overwrites a file that differs; it stops and
# says so instead. The guide is memory_bank/HANDOFF-2026-10-16-FULL-BENCHMARK.md.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/settings.sh"

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
status=0

check() {  # path sha256 label
  if [ ! -f "$1" ]; then
    echo "STOP    $3 is missing at $1"
    status=1
  elif [ "$(sha "$1")" = "$2" ]; then
    echo "ok      $3 ($1)"
  else
    echo "STOP    $3 at $1 has the wrong checksum; copy it from the first machine instead (guide, step B3)"
    status=1
  fi
}

# Datasets: download only what is missing, then check both.
if [ ! -f "$PRISM/data/benchmarks/locomo/locomo10.json" ] || \
   [ ! -f "$PRISM/data/benchmarks/longmemeval/longmemeval_s_cleaned.json" ]; then
  (cd "$PRISM" && uv run --no-sync python scripts/download_benchmarks.py --locomo --longmemeval) || status=1
fi
check "$PRISM/data/benchmarks/locomo/locomo10.json" "$LOCOMO_SHA256" "LoCoMo dataset"
check "$PRISM/data/benchmarks/longmemeval/longmemeval_s_cleaned.json" "$LME_SHA256" "LongMemEval-S dataset"

# The official judge, from the commit the registration pins.
if [ ! -f "$JUDGE" ]; then
  mkdir -p "$(dirname "$JUDGE")"
  curl -fsSL --max-time 60 -o "$JUDGE.download" "$JUDGE_URL" && mv "$JUDGE.download" "$JUDGE"
fi
check "$JUDGE" "$JUDGE_SHA256" "official LongMemEval judge"
exit $status
