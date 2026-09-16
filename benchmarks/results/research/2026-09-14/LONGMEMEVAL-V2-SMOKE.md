# LongMemEval-V2 official pipeline smoke

Date: 2026-09-14
Status: completed integration smoke; not an accuracy estimate

## Protocol

- Upstream: `xiaowu0162/LongMemEval-V2` at
  `2cc8c540bdb87fe6761629b585e727e1c4704520`.
- Dataset: `xiaowu0162/longmemeval-v2` at
  `f152293e235517d504809563c833d7190b8c713b`.
- PRME adapter/pack commit: `aed3136d702a46f2d9d648c10ac9f5f42bcade74`.
- Reader/shutdown commit: `fecb3b033f4a6a3119ae81c2e183a23e66de065e`.
- Domain/tier: web/small.
- Selection: the first web question in released order, chosen before reading its
  answer. It is one non-abstention dynamic-environment multiple-choice item with
  no query image.
- Memory: PRME public sync client, FastEmbed `BAAI/bge-small-en-v1.5`, 32,768
  internal `cl100k_base` tokens, at most 100 primary retrieval results, and up to
  eight included source screenshots.
- Reader: Ollama 0.34.0, `qwen3.5:9b`, digest
  `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`,
  262,144-token model context, thinking enabled, one request at a time.
- Downstream context ceiling: 65,536 tokens counted by the official
  `Qwen/Qwen3.5-9B` processor.
- Evaluator: released deterministic `mc_choice_match`; no LLM judge was used.
- Runtime: Python 3.11.6, DuckDB 1.5.5, FastEmbed 0.8.0, NumPy 1.26.4,
  Transformers 5.17.0, Torch 2.14.0 on Apple M5 Pro with 48 GB memory.

The downloaded dataset passed the upstream validator: 451 questions, 1,870
trajectories, 451 small-tier haystacks, and all referenced screenshots present.

## Result

| Measurement | Observed |
|---|---:|
| Indexed trajectories | 100 |
| Indexed source states/images | 1,737 |
| Durable events/nodes/vectors | 9,477 / 9,477 / 9,477 |
| Pending materializations | 0 |
| Indexing wall time | 18m54s |
| Saved pack size | 721 MB |
| PRME query latency | 0.483s |
| Returned context | 12 text items + 8 images |
| Official processor context tokens | 43,417 |
| Context truncated | no |
| Reader prompt/completion tokens | 44,011 / 1,411 |
| Reader generation wall time | 110.5s |
| Deterministic score | 1/1 |
| Unknown/empty response | no |

The complete pack was saved, reopened by the upstream memory loader, queried,
scored, and closed without an index or executor-shutdown error. The indexing run
first exposed a late-executor shutdown defect in unclosed synchronous clients;
`fecb3b0` fixes it and a subprocess regression test reproduces that lifecycle.

## Artifact hashes

| Artifact | SHA-256 |
|---|---|
| Adapter used for the pack | `468b175e4dc58b46da382eee201824b5508652669f5dfbe02de6020ef6a0c80a` |
| Memory config | `6df1b58baf8401ffeeedc463091e40a786b88cee819f6bb20c46046d38801db3` |
| Selected question file | `a026cef28b5f6be804b290d6c38530fc8e052f9a04591fe5f0a510742f6b6cba` |
| Selected small haystack | `58c745d260d63d9db535ec294a68c83611589adf6e93864924679ef335b5e212` |
| Adapter pack manifest | `45a9b5c3e72c9c495c8b14b552551b2b68dcf2f67ab4ca3753d0176161da8ac4` |
| Aggregate result | `c4b756f453c46faa8b8b53d433935bb2681c2dd1c3e9b8aed35a71b93271af55` |

This run establishes integration correctness only. One question cannot estimate
overall or category accuracy, visual-query retrieval remains text-selected, and
the local Ollama reader is not proof of parity with any hosted reference
endpoint. A publishable result requires all released web and enterprise
questions, matched baselines, complete failure accounting, and the released LLM
judges where specified.
