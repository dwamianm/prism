# LongMemEval-V2 DeepSeek episode-routing development trial

## Result

On the registered 149-question web development cohort, PRME's opt-in episode
routing scored 79/149 with DeepSeek v4.1 Flash, compared with 80/149 for flat
retrieval at the same 32K internal context budget.

| Arm | Correct | Accuracy | Unknown | Mean packed tokens | Mean recorded query time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Flat | 80/149 | 53.69% | 35 | 43,195.28 | 0.531 s |
| Episode | 79/149 | 53.02% | 34 | 43,195.28 | 0.529 s |

The paired difference was -0.67 percentage points. The question-bootstrap 95%
interval was -4.03 to +2.68 points. The exact two-sided McNemar p-value was 1.0,
with 3 wins, 4 losses, and 142 ties for episode routing relative to flat
retrieval.

| Category | Flat | Episode | Delta | Wins | Losses |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dynamic | 21/49 | 20/49 | -2.04 points | 1 | 2 |
| Procedure | 27/41 | 27/41 | 0.00 points | 2 | 2 |
| Static | 32/59 | 32/59 | 0.00 points | 0 | 0 |

The policy changed the rendered evidence for 146/149 questions while preserving
the exact per-question packed-token totals. The comparator also verified equal
non-episode policy hashes and identical 1,768-file, 762,468,935-byte memory
payloads. The recorded retrieval latency was effectively unchanged.

Episode routing remains opt-in. This larger second-reader trial does not confirm
the gains seen on the earlier 20-question MemoryAgentBench development samples.
The seven discordant questions suggest a concrete limitation: concentrating on
one or two locally relevant trajectories can recover a missing procedural detail,
but it can also omit initial navigation or global environment facts needed to
answer the complete question. Any future policy should use a new cohort and a
fixed rule that activates episode expansion only when the evidence supports it;
these observed questions must not become tuning cases.

## Registered protocol

- Cohort: all 149 web questions in the previously scored development slice.
- Memory payload: identical PRME graph, indexes, attachments, and adapter
  manifest in both arms.
- Flat policy: `episode_context_top_k=0`.
- Episode policy: `episode_context_top_k=2`,
  `episode_context_local_k=8`, and `episode_context_score_decay=0.95`.
- Both policies: 32,768-token internal budget, balanced ordering, auditable
  rendering, and up to eight source screenshots.
- Reader: `deepseek-v4.1-flash:cloud`, temperature 0, top-p 1, top-k 20,
  reasoning effort `none`, thinking disabled, one concurrent request.
- Source revisions: PRME `d5f4807244c095f7a39f79f55eb3dbcf039748f3` and
  LongMemEval-V2 `2cc8c540bdb87fe6761629b585e727e1c4704520`.
- Registration SHA-256:
  `3ba147242394eda684573a03757d4dd4c49b84613f0740af4dfa54a55e8699fa`.
- Comparison SHA-256:
  `fe8a804ab0a6b157360091eb47ee0599456ffc86a528265da48ee5d80ac1f362`.

The episode arm's first prompt experienced an 83-second socket connection wait
while a local Qwen evaluation was using Ollama. Subsequent prompt construction
returned to the flat arm's rate, and the saved per-query retrieval measurements
were comparable. The wall-clock generation difference is not attributed to the
routing policy.

The launcher bound the local Ollama cloud manifest digest, remote host and model
name, capabilities, and Ollama version. Ollama does not expose an immutable
revision for the hosted weights, so the execution records
`remote_weights_pinned: false`.

## Claim boundary

This is a development episode-routing trial on a previously scored, web-only
cohort with a shared haystack. It rejects default promotion for this policy; it
does not prove that episode routing is harmful on all workloads. It is not a
fresh holdout, a full LongMemEval-V2 Small score, or a competitor comparison.

The aggregate-only machine-readable report is
[`longmemeval-v2-episode-routing-deepseek-dev.json`](longmemeval-v2-episode-routing-deepseek-dev.json).
