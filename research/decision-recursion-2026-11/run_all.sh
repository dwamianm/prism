#!/usr/bin/env bash
# One full run per arm: Experiment R then Experiment W, each from a cold decision cache (a new run directory).
# Each model arm runs twice (run1, run2) with the same seed, to check that the decisions repeat exactly.
# Before each run of a model arm the model is unloaded (ollama stop), so every run starts with it not loaded;
# the scripts load it before timing starts and record the load time (warm_ms) and what was loaded before.
# Models named clef* have Ollama's "decision" capability and use its native endpoint (--backend systemone).
#   bash research/decision-recursion-2026-11/run_all.sh                      # random, qwen3.5:4b, qwen3.5:9b
#   bash research/decision-recursion-2026-11/run_all.sh clef-flash           # one arm
set -euo pipefail
cd "$(dirname "$0")/../.."
HERE=research/decision-recursion-2026-11
export OPENAI_API_KEY= ANTHROPIC_API_KEY= PRME_EXTRACTION_API_KEY=
if [ $# -gt 0 ]; then arms=("$@"); else arms=(random qwen3.5:4b qwen3.5:9b); fi
for arm in "${arms[@]}"; do
  for run in run1 run2; do
    label="${arm/:/-}-$run"
    dir="$HERE/results/$label"
    rm -rf "$dir"
    case "$arm" in
      random) backend=(--backend random) ;;
      clef*) backend=(--backend systemone --model "$arm"); ollama stop "$arm" >/dev/null 2>&1 || true ;;
      *) backend=(--backend ollama --model "$arm"); ollama stop "$arm" >/dev/null 2>&1 || true ;;
    esac
    start=$(date +%s)
    uv run python "$HERE/read_experiment.py" "${backend[@]}" --seed 7 --run-dir "$dir" 2>/dev/null | tail -8
    uv run python "$HERE/write_experiment.py" "${backend[@]}" --seed 7 --run-dir "$dir" 2>/dev/null | tail -6
    echo "== $label: $(( $(date +%s) - start )) s for the full run (read and write)"
  done
done
uv run python "$HERE/report.py" --results "$HERE/results"
