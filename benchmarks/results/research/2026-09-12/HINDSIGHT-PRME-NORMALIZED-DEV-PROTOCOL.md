# Fresh matched capture with declared control-character normalization

Registered after a source-integrity failure, before new dataset captures and
before inspecting retrieval quality. All settings and analysis gates in
[the original protocol](HINDSIGHT-PRME-DEV-PROTOCOL.md) remain in force except for
the common input transformation below. This is a new complete run, not a patch
or merge of favorable cases from the stopped run.

The original `77486e2` run failed exact source readback at `case-0008`, source
`s18:t0`: Hindsight returned 8,892 characters for an 8,921-character input. A diff
found exactly 29 deleted U+0002 control characters. The pinned implementation's
[`sanitize_text`](https://github.com/vectorize-io/hindsight/blob/bde55237f53bf55aacd048b01e29d7dc23b83a85/hindsight-api-slim/hindsight_api/engine/llm_wrapper.py#L194)
removes selected ASCII controls and Unicode surrogates at ingress. This is an
intentional text policy, not demonstrated loss of an answer qualifier. The
original exact-byte gate must remain failed; its partial captures are not scored.

Both live workers were deliberately interrupted after the failure was diagnosed.
Native exits were 130. Hindsight had recorded 17 cases with one readback error;
PRME had recorded two successful cases. No candidate-quality scores were
inspected. Raw inputs, the readback error, stopped reports and hashes are retained
in `hindsight-prme-strict-capture-stop.json`.

Before either new run, transform source text and question text equally using
`remove-c0-except-tab-lf-cr-plus-del-and-surrogates-v1`: remove U+0000–U+0008,
U+000B–U+000C, U+000E–U+001F, U+007F and U+D800–U+DFFF. Preserve tabs, LF, CR,
printable Unicode and all other text. Do not trim, rewrite, merge or drop turns.
This matches the character set of the pinned sanitizer but is applied in an
independent common exporter before either system receives inputs. A new audit
records every changed source/query ID, character count, removed codepoint and
before/after hash. Original input bytes remain available.

The audit found **three changed source records and 41 removed control characters**
among 59,021 turns. No query changed. The cohort stays at 119 cases, including all
four blank turns and the same source-position labels. This normalized-text
variant must be identified in every result; it is not a test of byte preservation
for the unmodified dataset. The production PRME ingestion policy is unchanged.

Both new workers start fresh memories for every case and use unique output
paths. There are no case exclusions or quality-based retries. Preserve any
further failures, and require all 119 cases plus observed native exit zero for
both systems before running the independent verifier/analyzer. The analyzer is
written before any study-quality inspection and has authored tests separating
pointers/partial text from complete-source credit, validating null metrics for
unlabelled cases, and avoiding a falsely precise interval for a single cluster.
