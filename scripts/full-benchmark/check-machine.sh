#!/bin/bash
# Check that this machine is ready for the full benchmark. Changes nothing,
# except that the last two checks download the embedding model and the packing
# tokenizer into their cache folders (settings.sh) if they are missing, since
# the builds and the gate run offline later.
# Run it until every line says PASS (WARN lines need a person to look).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/settings.sh"

failures=0
pass() { echo "PASS  $*"; }
warn() { echo "WARN  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }

# Tools.
for tool in git uv curl python3; do
  if command -v "$tool" >/dev/null 2>&1; then pass "$tool is installed"; else fail "$tool is missing"; fi
done

# Disk: two full LongMemEval-S builds take about 75 GB; keep room to spare.
free_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}')
if [ "${free_gb:-0}" -ge 120 ]; then pass "$free_gb GB free"; else fail "only ${free_gb:-?} GB free; the builds need about 75 GB, keep 120 GB free"; fi

# The clone and the commit.
if [ -d "$PRISM/.git" ]; then
  pass "prism clone at $PRISM"
  if git -C "$PRISM" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
    pass "commit ${COMMIT:0:8} is in the clone"
  else
    fail "commit ${COMMIT:0:8} is not in the clone; run: git -C $PRISM fetch origin"
  fi
else
  fail "no prism clone at $PRISM (guide, step B1)"
fi

# No paid API keys anywhere a run could pick them up.
for name in OPENAI_API_KEY ANTHROPIC_API_KEY PRME_EXTRACTION_API_KEY; do
  if [ -n "${!name:-}" ]; then
    fail "$name is set in this shell; remove it from your shell profile so no run can call a paid API"
  else
    pass "$name is not set in this shell"
  fi
done
if [ -f "$PRISM/.env" ] && grep -qE '^(OPENAI_API_KEY|ANTHROPIC_API_KEY|PRME_EXTRACTION_API_KEY)=' "$PRISM/.env"; then
  fail "$PRISM/.env holds an API key; remove it so no run can call a paid API"
else
  pass "no API key in $PRISM/.env"
fi

# Ollama server, version and model.
version=$(curl -s --max-time 5 "$OLLAMA_URL/api/version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' 2>/dev/null)
if [ -z "$version" ]; then
  fail "no Ollama server answers at $OLLAMA_URL; start the Ollama app"
elif [ "$version" = "$OLLAMA_VERSION" ]; then
  pass "Ollama server $version"
else
  fail "Ollama server is $version; install $OLLAMA_VERSION (guide, step A2)"
fi
if command -v ollama >/dev/null 2>&1; then
  model_id=$(ollama list 2>/dev/null | awk -v m="$MODEL" '$1 == m {print $2}')
  if [ -z "$model_id" ]; then
    fail "$MODEL is not pulled; run: ollama pull $MODEL"
  elif [ "${model_id:0:8}" = "$MODEL_ID_PREFIX" ]; then
    pass "$MODEL has ID $model_id"
  else
    warn "$MODEL has ID $model_id, not $MODEL_ID_PREFIX...; the tag moved upstream, so tell the owner before any answer run"
  fi
else
  fail "the ollama command is missing"
fi
reply=$(curl -s --max-time 120 "$OLLAMA_URL/api/chat" -d "{\"model\": \"$MODEL\", \"stream\": false, \"messages\": [{\"role\": \"user\", \"content\": \"Reply with the word ready.\"}]}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["message"]["content"].strip()[:40])' 2>/dev/null)
if [ -n "$reply" ]; then pass "$MODEL answers (\"$reply\")"; else fail "$MODEL does not answer; run: ollama signin"; fi
warn "check by hand that automatic updates are off in the Ollama app's Settings (guide, step A3)"

# Data files.
check_file() {  # path sha256 label
  if [ ! -f "$1" ]; then fail "$3 is missing at $1; run scripts/full-benchmark/install-data.sh"
  elif [ "$(sha "$1")" = "$2" ]; then pass "$3 matches its checksum"
  else fail "$3 at $1 has the wrong checksum"; fi
}
check_file "$PRISM/data/benchmarks/locomo/locomo10.json" "$LOCOMO_SHA256" "LoCoMo dataset"
check_file "$PRISM/data/benchmarks/longmemeval/longmemeval_s_cleaned.json" "$LME_SHA256" "LongMemEval-S dataset"
check_file "$JUDGE" "$JUDGE_SHA256" "official LongMemEval judge"

# The clone's environment, the embedding model and the packing tokenizer. Both
# model files live in the cache folders that settings.sh exports, not in $TMPDIR.
if [ -d "$PRISM/.venv" ]; then
  embed=$(cd "$PRISM" && uv run --no-sync python -c "
import asyncio
from prme.config import PRMEConfig
from prme.storage.embedding import create_embedding_provider
provider = create_embedding_provider(PRMEConfig(_env_file=None).embedding)
print(provider.model_name, len(asyncio.run(provider.embed(['warm up']))[0]))" 2>/dev/null | tail -1)
  if [ "$embed" = "BAAI/bge-small-en-v1.5 384" ] && [ -n "$(ls -A "$FASTEMBED_CACHE_PATH" 2>/dev/null)" ]; then
    pass "embedding model is cached ($embed, in $FASTEMBED_CACHE_PATH)"
  else fail "embedding model check printed '$embed' or $FASTEMBED_CACHE_PATH is empty; run uv sync in $PRISM and try again"; fi
  tokenizer=$(cd "$PRISM" && uv run --no-sync python -c "
from prme.config import PRMEConfig
from prme.retrieval.tokenization import count_tokens
name = PRMEConfig(_env_file=None).packing.tokenizer
assert count_tokens('warm up', name) > 0
print(name)" 2>/dev/null | tail -1)
  if [ -n "$tokenizer" ] && [ -n "$(ls -A "$TIKTOKEN_CACHE_DIR" 2>/dev/null)" ]; then
    pass "packing tokenizer is cached ($tokenizer, in $TIKTOKEN_CACHE_DIR)"
  else fail "tokenizer check printed '$tokenizer' or $TIKTOKEN_CACHE_DIR is empty; run uv sync in $PRISM and try again"; fi
else
  fail "no .venv in $PRISM; run: cd $PRISM && uv sync --dev --extra api --extra mcp"
fi

echo
if [ "$failures" -eq 0 ]; then echo "Ready: no failures."; else echo "$failures check(s) failed."; fi
exit $((failures > 0))
