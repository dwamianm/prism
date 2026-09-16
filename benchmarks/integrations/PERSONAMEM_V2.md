# PersonaMem-v2 packing pilot

The [official dataset](https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2)
provides text histories and personalized multiple-choice questions. Our pinned
text benchmark file has 5,000 questions across 200 personas. This adapter is a
custom **persona-hidden 32K text** variant, not the upstream leaderboard protocol.
The initial system message supplies a detailed persona; this variant omits it.
All subsequent user and assistant content is retained verbatim. Message metadata,
hidden persona fields, preference annotations, related-snippet labels and source
generation metadata never become memories. One selected message has additional
generation keys; the role/content whitelist drops those keys, not its text.

The dataset is CC BY 4.0. Attribute Bowen Jiang and collaborators and link to the
[official project](https://github.com/bowen-upenn/PersonaMem-v2). The audited code
revision is `d29d91d016add354e459dfeb0d24af08bc402e2a`; dataset revision is
`ed956dea41521fc4499acbc63f966e0fd3c053ba`. Dataset card and schema were read before
implementation. Persona 0 was inspected for format only and is outside the
benchmark split. No PRME or reader outcomes were inspected before cohort choice.

The [selection record](../results/research/2026-09-12/personamem-v2-selection.json)
fixes 24 personas and four questions each using SHA-256 identity ordering. Each
persona gets equal representation. No answer, preference type, source length or
measured outcome determines selection. Selected histories contain 5,523 dialog
messages after excluding 24 supplied system personas. Invalid data causes a
failed run; it is not replaced with another question.

## Frozen protocol

- Raw `store()` calls preserve text, role provenance and neutral source IDs in
  one separate local pack per persona. No extraction or generated profile runs.
- The format has no session IDs or timestamps. Those remain absent. One actual
  UTC reference clock is captured after each import and used for all its queries;
  import time is not evidence of historical event time.
- Retrieval receives only the user query. Hidden labels and even the multiple
  choice options do not enter the retrieval or packing functions.
- The native query parser, hybrid candidates and scores are shared across three
  4,096-token contexts: density, alpha .25 length penalty, and score ordering.
  Only the multi-path comparator changes. Whole-source text, provenance and exact
  rendered budgets remain mandatory. A fourth reader arm receives no memory.
- Every density context must reproduce the actual product response. A repeated
  query per persona must reproduce candidates and context after other queries.
  Source records must remain unchanged. Packs and captures are retained.
- A single pinned local `gemma4:26b` reader uses identical options, temperature,
  seed, context headroom and structured-output instructions in every arm. Option
  order is a stable content hash, without a privileged correct-answer position.
  Arm order varies by question identity. One answer repeats per persona to expose
  observed reader variability.
- Prediction output and correct-letter references live in separate artifacts.
  No correctness is computed or examined until all 96 questions and 384 arm
  answers finish with native exit 0. Incomplete/transport failures invalidate the
  run; invalid final answer JSON is retained and scores incorrect. Never replace
  difficult cases or merge successful fragments from different attempts.
- Report every arm, category, invalid answer and repeated-answer disagreement.
  The primary comparison is alpha .25 versus density. Paired bootstrap sampling
  resamples the 24 personas, keeping their questions together, with 2,000 samples
  and seed 42. Small categories and synthetic labels limit inference.

The test is a prospective pilot on this cohort after LongMemEval development
tuning. Its custom protocol, small selected panel and single reader do not prove
general superiority. It cannot override the failed LongMemEval confirmation or
automatically promote a production default. No second LLM judge is used: scoring
compares the final letter with the saved dataset label.

`benchmarks.diagnostics.personamem_packing register` records source/runtime,
model assets, reader identity, dataset and cohort hashes before execution. Its
`run` subcommand rejects changed inputs and records native worker completion.
Authored preflight coverage checks source fidelity, actual control reproduction,
label separation, stable option mapping, malformed input and reader accounting.

After native completion, `benchmarks.diagnostics.verify_personamem` requires the
registered PRME source on `PYTHONPATH` and checks every dataset, capture and reader
request hash. It repacks all 288 memory contexts and reconstructs the product
receipts, then reads the 24 DuckDB packs in read-only mode to verify original
events, graph provenance and journaled receipts. Labels are joined only after
those checks. Invalid predictions remain in the denominator. The report includes
all four category dimensions, persona-cluster intervals, reader repeats and raw
artifact hashes. Its 25 authored checks include a complete synthetic 96-question
run and detection of modified sources, receipts and contexts; these are verifier
tests, not benchmark-quality results.

## Annotation-selected reader diagnostic

After the completed pilot was scored and audited, the separate
`benchmarks.diagnostics.personamem_annotated_reader` diagnostic was designed to
test the reader with short, annotation-selected source contexts. It keeps the
same 96 cases, source corpus, question/options, local model, prompt and runtime
options, and adds a fresh no-memory control. The annotation selects only exact
role/content matches in actual dialog history. Text is rendered from those
source messages in original order; unmatched annotation text and the initial
persona never enter context. All sources are complete, with role/source IDs.
Matched contexts use at most 1,102 tokens. The six empty cases remain empty and
remain in the denominator.

Registration binds the original pilot plan and verification, new prepared inputs,
reference labels, code and reader identity before new answers run. All 192 primary
answers must finish with native exit zero before new correctness is examined.
One response repeats per persona. Invalid JSON is incorrect; transport failure
invalidates the run with partial evidence retained. The analysis reports all
categories and a paired persona-cluster bootstrap against the fresh no-memory
control, using 2,000 samples and seed 42. Comparing the fresh no-memory answers
with the earlier ones exposes observed drift on identical requests.

This is an intentionally annotation-selected, post-hoc diagnostic, not product
retrieval, a fresh holdout or a leaderboard submission. Source selection and
shorter source-only formatting change together, so a gain cannot be attributed
to either alone. The frozen product packing defaults remain unchanged. Authored
checks cover source/role fidelity, exclusion of unmatched oracle text, full
passage budgets, all-case reader completion, label separation and retained
transport failures.
