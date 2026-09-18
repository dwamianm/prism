# LLM-AggreFact native-tool recovery diagnostic

**Result: native Ollama tool calls removed the transport bottleneck, but the
unregistered cloud verifier remained semantically unstable.** This was post hoc
development work over the 25 cases that had already failed the registered
typed-reference v2 run. It did not access the external test split and cannot
support a classification-quality or leadership claim.

## Why this diagnostic was run

The registered v2 verifier used Instructor JSON mode with three in-process
validation attempts. Seventeen of 310 observed cases ended in `TimeoutError`,
and provider work consumed 6,356 wall-clock seconds before the frozen 98%
integrity gate became impossible. A direct Ollama sanity check also showed that
the DeepSeek cloud route did not enforce an object JSON schema supplied through
the `format` field. The same route did correctly return typed native tool
arguments.

The replacement harness therefore:

- calls `/api/chat` with one `submit_typed_verdict` tool;
- writes a private, atomic checkpoint before and after every provider request;
- separates transport attempts from semantic repair attempts;
- binds every job, request, provider manifest, prompt, tool schema and limit to
  hashes in the resumable state;
- detects response tampering and resumes cancellation, interruption and the
  post-response crash window without repeating completed work; and
- accepts only the 25 opaque identities already observed as failures in v2.

Six fault-injection tests cover transport retry, semantic retry, cancellation,
response recovery, tampering and frozen transport exhaustion. The complete
LLM-AggreFact benchmark test group passed 50 tests after the implementation.

## DeepSeek cloud results

Both runs used `deepseek-v4.1-flash:cloud`, manifest
`e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`,
temperature 0, seed 17, four concurrent jobs, two transport attempts per
semantic request and three semantic attempts per case.

| Measure | Basic repair | Expanded repair |
|---|---:|---:|
| Prior failures replayed | 25 | 25 |
| Valid completions | 20 (80%) | 17 (68%) |
| Native tool calls | 45 | 48 |
| Transport failures or timeouts | 0 | 0 |
| Provider wall time | 25.96 s | 32.02 s |
| Median call time | 2.26 s | 2.23 s |
| Maximum call time | 4.20 s | 6.76 s |

The initial request was identical across the runs: the system prompt and tool
schema hashes match. Only 3 of 25 first argument payloads were identical, and
first-attempt validity agreed on 16 of 25 cases (64%). The repair instruction
changed after that first response, so final outcomes are not a pure repeatability
measurement. Fourteen cases completed in both runs, six only under basic repair,
three only under expanded repair, and two under neither.

The native path is a decisive transport improvement: 93 consecutive tool calls
across the two runs completed without a transport failure, with 26–32 second
end-to-end provider time. Prompt-only repair did not provide a stable semantic
contract. Remaining errors were almost entirely incomplete typed coverage and
component ranges outside their atom boundaries.

## Pinned local control

The same diagnostic then used the already-installed, content-addressed
`qwen3.5:35b-a3b` Q4_K_M artifact, manifest
`3460ffeede5453ead027dbd2f821b12ad0aa3de54630971993babdb2165221f7`,
with serial execution. The first ten fully observed cases all exhausted three
semantic attempts. Thirty-two calls completed at the transport layer and one
eleventh-case call was cancelled when the diagnostic stopped. Failures were
dominated by incomplete typed coverage, followed by reversed or out-of-atom
ranges.

This partial result is sufficient only to reject this representation for the
local control; it is not a 25-case model-quality estimate. It also shows that a
larger download is premature: the schema asks models to generate several
redundant, mutually constrained ranges, and increasing model size does not fix
that protocol defect.

## Artifact integrity

| Artifact | File SHA-256 | Canonical result SHA-256 |
|---|---|---|
| Basic-repair result | `a000e4dd3673a882f81a5be5c96b691a1417ebc11ca621718bc6947da759c689` | `6be0a074ea99c20a989ce2d27dbdcd763bbf16852abcd03e922a50f3256ea4da` |
| Expanded-repair result | `dae7d4d88d5eae2ef9a197014259ddebe34ca0fc85912aa82f3472a0d7256e80` | `d048db821a39a8dffd89aaa21e740729949bd8ef2e4780f73e2b184a5fa2509e` |
| Cross-run comparison | `bf2b6983df983ec668c452d8f265cc9ab75f572c2f42d3335597aa8faffb7dfd` | `49f15da861e9ad274f96cef86e7f7997a0cd49776620ce92dc8a2e52d3674c5d` |
| Qwen 35B partial control | `ef992157b6cd4626eac79387c82d0b21ac2b7596f9f2a950222e461154cce69b` | `3d7eceae1b331e2a9904de3ae611f19e7fb9ef5b9e2f71ac6d00eed6705c399a` |

The public artifacts contain counts, opaque identities and hashes, never claim
or evidence text. Raw tool responses remain in mode-0600 private state. Both
result artifacts state `test_accessed: false`.

## Decision

Use DeepSeek cloud through Ollama for rapid development and native tool calls for
structured benchmark work. Do not ship the current typed verifier or treat the
cloud alias as reproducible evidence: the alias pins an Ollama manifest but not
the remote weights, and the repeated first responses were unstable.

Keep the local content-addressed Qwen 35B artifact as the reproducibility
control; no larger local download is justified before testing the already
installed model. The next representation should remove redundant generated
boundaries, make token coverage structural, and fail closed as an explicit
abstention when a semantic decomposition remains invalid. It must pass on
already-observed tuning cases before registration on a new disjoint development
cohort. The external test split remains sealed.

That follow-up is now complete. The
[source-token atomic-partition diagnostic](LLM-AGGREFACT-ATOMIC-PARTITION-V2.md)
made coverage structural and reached 310/310 valid DeepSeek partitions plus
307/310 valid Qwen partitions with three safe abstentions. On all 310 observed
cases, however, both atomic paths underperformed the paired unsplit FactCG
control and failed the 90%-precision/60%-recall target. Provider-authored claim
fragmentation is therefore rejected; the external test split remains sealed.
