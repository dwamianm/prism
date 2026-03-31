#!/usr/bin/env zsh
# Interactive benchmark runner for PRME
# Runs benchmarks inside the project's .venv via uv

set -euo pipefail

# Load .env if present
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

BOLD=$'\033[1m'
DIM=$'\033[2m'
CYAN=$'\033[36m'
GREEN=$'\033[32m'
RESET=$'\033[0m'

# --- Benchmark selection ---
echo "${BOLD}PRME Benchmark Runner${RESET}"
echo ""
echo "${CYAN}Select benchmarks to run:${RESET}"
echo "  1) all            - All synthetic benchmarks (fast)"
echo "  2) all-real        - All real-dataset benchmarks"
echo "  3) all-both        - Synthetic + real"
echo "  4) locomo          - LoCoMo synthetic"
echo "  5) locomo-real     - LoCoMo real dataset"
echo "  6) longmemeval     - LongMemEval synthetic"
echo "  7) longmemeval-real - LongMemEval real dataset"
echo "  8) epistemic       - Epistemic benchmark"
echo ""
echo -n "Choice [1]: "
read bench_choice
bench_choice=${bench_choice:-1}

case "$bench_choice" in
  1) BENCHMARKS="all" ;;
  2) BENCHMARKS="all-real" ;;
  3) BENCHMARKS="all-both" ;;
  4) BENCHMARKS="locomo" ;;
  5) BENCHMARKS="locomo-real" ;;
  6) BENCHMARKS="longmemeval" ;;
  7) BENCHMARKS="longmemeval-real" ;;
  8) BENCHMARKS="epistemic" ;;
  *) echo "Invalid choice"; exit 1 ;;
esac

# --- LLM judge ---
ARGS=()

echo ""
echo -n "${CYAN}Enable LLM judge scoring? (requires API key) [y/N]:${RESET} "
read llm_choice
if [[ "${llm_choice:l}" == "y" ]]; then
  ARGS+=("--llm")

  echo ""
  echo "${CYAN}Select LLM provider:${RESET}"
  echo "  1) openai (default)"
  echo "  2) anthropic"
  echo -n "Choice [1]: "
  read provider_choice
  provider_choice=${provider_choice:-1}

  case "$provider_choice" in
    1) PROVIDER="openai" ;;
    2) PROVIDER="anthropic" ;;
    *) echo "Invalid choice"; exit 1 ;;
  esac
  ARGS+=("--llm-provider" "$PROVIDER")

  echo ""
  if [[ "$PROVIDER" == "openai" ]]; then
    echo "${CYAN}Select model:${RESET}"
    echo "  1) gpt-4o-mini (default)"
    echo "  2) gpt-5-mini"
    echo "  3) gpt-4o"
    echo "  4) custom"
    echo -n "Choice [1]: "
    read model_choice
    model_choice=${model_choice:-1}
    case "$model_choice" in
      1) MODEL="gpt-4o-mini" ;;
      2) MODEL="gpt-5-mini" ;;
      3) MODEL="gpt-4o" ;;
      4) echo -n "Enter model name: "; read MODEL ;;
      *) echo "Invalid choice"; exit 1 ;;
    esac
  else
    echo "${CYAN}Select model:${RESET}"
    echo "  1) claude-sonnet-4-20250514 (default)"
    echo "  2) claude-opus-4-20250514"
    echo "  3) claude-haiku-4-5-20251001"
    echo "  4) custom"
    echo -n "Choice [1]: "
    read model_choice
    model_choice=${model_choice:-1}
    case "$model_choice" in
      1) MODEL="claude-sonnet-4-20250514" ;;
      2) MODEL="claude-opus-4-20250514" ;;
      3) MODEL="claude-haiku-4-5-20251001" ;;
      4) echo -n "Enter model name: "; read MODEL ;;
      *) echo "Invalid choice"; exit 1 ;;
    esac
  fi
  ARGS+=("--llm-model" "$MODEL")
fi

# --- Retry failed ---
echo ""
echo -n "${CYAN}Retry only failed questions from a previous run? [y/N]:${RESET} "
read retry_choice
if [[ "${retry_choice:l}" == "y" ]]; then
  DEFAULT_RETRY="benchmark_results.json"
  echo -n "Path to previous JSON report [$DEFAULT_RETRY]: "
  read retry_path
  retry_path=${retry_path:-$DEFAULT_RETRY}
  if [[ ! -f "$retry_path" ]]; then
    echo "File not found: $retry_path"
    exit 1
  fi
  ARGS+=("--retry-failed" "$retry_path")
fi

# --- Execution options ---
echo ""
echo -n "${CYAN}Run sequentially instead of parallel? [y/N]:${RESET} "
read seq_choice
if [[ "${seq_choice:l}" == "y" ]]; then
  ARGS+=("--no-parallel")
fi

echo -n "${CYAN}Save JSON report? [y/N]:${RESET} "
read json_choice
if [[ "${json_choice:l}" == "y" ]]; then
  DEFAULT_PATH="benchmark_results.json"
  echo -n "Output path [$DEFAULT_PATH]: "
  read json_path
  json_path=${json_path:-$DEFAULT_PATH}
  ARGS+=("--json" "$json_path")
fi

echo -n "${CYAN}Quiet mode (suppress terminal output)? [y/N]:${RESET} "
read quiet_choice
if [[ "${quiet_choice:l}" == "y" ]]; then
  ARGS+=("--quiet")
fi

# --- Run ---
CMD=(uv run python -m benchmarks $BENCHMARKS ${ARGS[@]})

echo ""
echo "${DIM}────────────────────────────────────────${RESET}"
echo "${GREEN}Running:${RESET} ${CMD[*]}"
echo "${DIM}────────────────────────────────────────${RESET}"
echo ""

exec "${CMD[@]}"
