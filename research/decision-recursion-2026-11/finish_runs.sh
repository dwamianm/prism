#!/usr/bin/env bash
# The remaining arms: qwen3.5:9b and the random judge, twice each from a cold cache, one model at a time.
# Full stderr goes to results/<label>.log so a failure cannot be lost.
set -uo pipefail
cd "$(dirname "$0")/../.."
HERE=research/decision-recursion-2026-11
export OPENAI_API_KEY= ANTHROPIC_API_KEY= PRME_EXTRACTION_API_KEY=
run_arm() {
  local arm="$1" run="$2" label dir backend
  label="${arm/:/-}-$run"; dir="$HERE/results/$label"; rm -rf "$dir"
  if [ "$arm" = random ]; then backend=(--backend random); else backend=(--backend ollama --model "$arm"); ollama stop "$arm" >/dev/null 2>&1; fi
  local start=$(date +%s)
  if ! uv run python "$HERE/read_experiment.py" "${backend[@]}" --seed 7 --run-dir "$dir" >"$HERE/results/$label.read.log" 2>&1; then
    echo "!! $label read FAILED (see results/$label.read.log)"; return 1
  fi
  if ! uv run python "$HERE/write_experiment.py" "${backend[@]}" --seed 7 --run-dir "$dir" >"$HERE/results/$label.write.log" 2>&1; then
    echo "!! $label write FAILED (see results/$label.write.log)"; return 1
  fi
  tail -8 "$HERE/results/$label.read.log"; tail -6 "$HERE/results/$label.write.log"
  echo "== $label: $(( $(date +%s) - start )) s"
}
for arm in qwen3.5:9b random; do
  for run in run1 run2; do run_arm "$arm" "$run"; done
done
uv run python "$HERE/report.py" --results "$HERE/results" >/dev/null && echo "report written"
