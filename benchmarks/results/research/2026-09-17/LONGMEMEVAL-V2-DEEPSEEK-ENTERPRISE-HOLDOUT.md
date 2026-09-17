# LongMemEval-V2 DeepSeek enterprise budget confirmation

## Result

On the registered 140-question enterprise confirmation cohort, increasing
PRME's internal context budget from 32,768 to 49,152 `cl100k_base` tokens did
not improve the DeepSeek v4.1 Flash reader.

| Arm | Correct | Accuracy | Unknown | Mean packed tokens | Total reader tokens | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 32K | 55/140 | 39.29% | 49 | 42,451.50 | 5,367,142 | 839.14 s |
| 48K | 54/140 | 38.57% | 53 | 60,553.62 | 7,749,543 | 909.21 s |

The 48K arm lost one question overall, or 0.71 percentage points. The paired
question-bootstrap 95% interval was -5.71 to +4.29 points. The exact two-sided
McNemar p-value was 1.0, with 6 wins, 7 losses, and 127 ties.

| Category | 32K | 48K | Delta | Wins | Losses |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dynamic | 12/34 | 14/34 | +5.88 points | 2 | 0 |
| Procedure | 16/32 | 18/32 | +6.25 points | 3 | 1 |
| Static | 27/74 | 22/74 | -6.76 points | 1 | 6 |

The category movements are exploratory and none establish a positive promotion
gate. The larger budget used 44.39% more total reader tokens, 42.64% more packed
memory tokens, 22.43% more mean retrieval time, and 8.35% more end-to-end wall
time. It also produced four additional unknown answers.

This confirmation rejects universal promotion of the 48K preset. The prior web
development gain was domain-specific: 48K helped procedure-heavy web questions,
while this fresh enterprise cohort lost enough static questions to erase its
dynamic and procedure gains. Keep 32K as the quality preset and treat larger or
type-adaptive budgets as research until another preregistered holdout passes.

## Registered protocol

- Cohort: 140 deterministic enterprise questions that had not been scored in
  prior PRME work. One question inspected during schema validation was excluded
  before registration.
- Question types: 74 static, 34 dynamic, and 32 procedure.
- Memory payload: identical 3,371-file, 1,174,102,069-byte schema-4 PRME pack in
  both arms.
- Reader: `deepseek-v4.1-flash:cloud`, temperature 0, top-p 1, top-k 20,
  reasoning effort `none`, thinking disabled, one concurrent request.
- Context format: auditable, with up to eight source screenshots.
- Source revisions: PRME `f2b3c5c0d61330215d598fc5042d914ab813f18f` and
  LongMemEval-V2 `2cc8c540bdb87fe6761629b585e727e1c4704520`.
- Registration SHA-256:
  `a2a0981a0798780ca7ba48c4fb7401ee5bc8253faca4d0dfd5a6e971ff743cfa`.
- Comparison SHA-256:
  `daa96cebd97ab44031e39cdcae5fea098e18a55b9437c4e72bf438ef84a7fd9a`.

The portable pack was upgraded before registration from the preceding adapter
schema by matching all 100 immutable trajectory fingerprints, verifying 3,118
existing screenshots, restoring 240 missing screenshots from their
digest-matched released sources, and then validating 3,358 attachment inventory
entries and all 12,944 graph references. The schema-4 adapter subsequently
validated the complete attachment inventory at load. A clean preflight also
confirmed that the runtime used the pack's `fastembed-0.8.0` identity and that
vector retrieval and official multimodal token counting completed without
fallback. Failed pre-registration and preflight artifacts were excluded.

The launcher bound the local Ollama cloud manifest digest, remote host and model
name, capabilities, and Ollama version. Ollama does not expose an immutable
revision for the hosted weights, so the execution records
`remote_weights_pinned: false` rather than treating the manifest digest as a
remote weight hash.

## Claim boundary

This is a registered cross-domain confirmation, not a competitor comparison or
a full LongMemEval-V2 Small score. The cohort has a shared haystack, so
question-level bootstrap intervals do not model every dependency. The result
tests one fixed reader and one source artifact family. It rejects the named 48K
promotion; it does not establish that 32K is universally optimal.

The aggregate-only machine-readable report is
[`longmemeval-v2-deepseek-enterprise-holdout.json`](longmemeval-v2-deepseek-enterprise-holdout.json).
