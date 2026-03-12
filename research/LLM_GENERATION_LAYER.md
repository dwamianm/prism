# LLM Generation Layer — Benchmark Enhancement Plan

*Created: 2026-03-12*

## Why

PRME's retrieval scores use keyword-match on raw retrieval results. Published systems (Mem0, Zep, MemGPT, ENGRAM) use LLM-as-judge on generated answers. Our scores are not directly comparable. Adding an LLM generation layer:

1. Makes scores comparable to published numbers
2. Unlocks inference/temporal categories (need reasoning, not just retrieval)
3. Leverages PRME's strong retrieval (knowledge_update 79.2%, info_extraction 72.5%) with LLM synthesis

## Architecture

### Existing LLM Setup (from research)
- **Instructor** (`instructor>=1.14`) — unified LLM abstraction
- **Providers**: OpenAI, Anthropic, Ollama all available as dependencies
- **Config pattern**: `ExtractionConfig` with env vars `PRME_EXTRACTION__PROVIDER`, `PRME_EXTRACTION__MODEL`
- **Provider string**: `"{provider}/{model}"` format (e.g., `"anthropic/claude-3-5-sonnet-20241022"`)
- **API keys**: Standard env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`)

### New Module: `benchmarks/llm_judge.py`

```python
# Two functions needed:

async def generate_answer(query: str, context: str, provider: str, model: str) -> str:
    """Given retrieval context + query, generate an answer via LLM."""
    # Use instructor or raw SDK
    # System prompt: "Answer the question using ONLY the provided context.
    #                 If the context doesn't contain the answer, say 'I don't know'."
    # Return generated answer string

async def judge_answer(query: str, expected: str, generated: str, provider: str, model: str) -> float:
    """LLM-as-judge: score how well generated answer matches expected."""
    # System prompt: "Score 0.0-1.0 how well the answer matches the expected answer."
    # Return float score
```

### Integration into Benchmarks

In `LoCoMoRealBenchmark.run()` and `LongMemEvalRealBenchmark.run()`:

```python
# After retrieval:
response = await engine.retrieve(query, user_id=user_id)
top_content = " ".join(r.node.content for r in response.results[:15])

# Keyword match (existing, always computed for reproducibility):
kw_score = keyword_match_score([answer], top_content)

# LLM generation (optional, enabled with --llm flag):
if llm_enabled:
    generated = await generate_answer(query, top_content, provider, model)
    llm_score = await judge_answer(query, answer, generated, provider, model)
    score = llm_score  # Use LLM score when available
else:
    score = kw_score
```

### CLI Integration

```bash
# Keyword match only (default, reproducible, no API needed):
python -m benchmarks locomo-real

# With LLM generation + judge:
python -m benchmarks locomo-real --llm

# With specific provider:
PRME_EXTRACTION__PROVIDER=anthropic PRME_EXTRACTION__MODEL=claude-sonnet-4-20250514 \
  python -m benchmarks locomo-real --llm
```

### Config: `benchmarks/config.py` or extend existing

```python
class BenchmarkLLMConfig:
    provider: str = "openai"      # or "anthropic", "ollama"
    model: str = "gpt-4o-mini"    # cheap, fast, good enough for judge
    generation_model: str | None = None  # separate model for generation (optional)
    temperature: float = 0.0      # deterministic
    max_tokens: int = 256
    enabled: bool = False          # opt-in via --llm flag
```

## Expected Impact

| Category | Current (kw-match) | With LLM generation | Why |
|----------|-------------------|---------------------|-----|
| knowledge_update | 79.2% | 85-90% | LLM synthesizes from good retrieval |
| info_extraction | 72.5% | 80-85% | LLM formats answer from context |
| multi_session | 39.7% | 55-65% | LLM combines facts across results |
| temporal | 28.4% | 50-60% | LLM does day-counting arithmetic |
| inference | 25.5% | 50-65% | LLM does counterfactual reasoning |
| single_hop | 43.0% | 60-70% | LLM extracts answer from noisy context |

## Files to Create/Modify

| File | Action |
|------|--------|
| `benchmarks/llm_judge.py` | NEW — generation + judging functions |
| `benchmarks/__main__.py` | MODIFY — add `--llm` flag |
| `benchmarks/runner.py` | MODIFY — pass LLM config to benchmarks |
| `benchmarks/locomo.py` | MODIFY — add LLM generation path in eval loop |
| `benchmarks/longmemeval.py` | MODIFY — add LLM generation path in eval loop |
| `benchmarks/models.py` | MODIFY — add `generated_answer` field to QueryResult |

## Implementation Order

1. `benchmarks/llm_judge.py` — Core generation + judging
2. `benchmarks/__main__.py` — CLI flag
3. `benchmarks/locomo.py` — LoCoMo-real integration
4. `benchmarks/longmemeval.py` — LongMemEval-real integration
5. Run benchmarks, record results
6. Update `research/BENCHMARK_RESULTS.md` with LLM-judged scores
