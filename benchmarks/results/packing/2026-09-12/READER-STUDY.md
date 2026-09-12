# Paired development reader study

Source retention is not answer accuracy. This study submits frozen product
contexts directly to a fixed local reader. The legacy LongMemEval judged runner
now also consumes the product bundle and uses the question's reference date;
its custom metric still excludes preferences and uses a separate context-
sufficiency check for abstention, so it is not the official answer protocol.

The cohort is all 119 questions in the completed development capture at
`1f5375ad7fb755c4f75a03f3d4bb9f263b27a7e0`, including abstention questions.
Each source capture must reproduce its original control context and token count
before either arm is prepared. The paired arms are the original density ordering
and the already selected score ordering within the multi-path tier, both at
4,096 product tokens. Candidate inputs, formatting and all other packing rules
are shared. Gold answers are exported to a separate reference file that the
reader worker does not read. No test-partition data enters this study.

The declared reader is local `qwen3.5:4b`, currently resolving to manifest digest
`2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`.
The prepared artifact pins the observed digest, Ollama version, runner hash,
existing generation system prompt and options: temperature 0, seed 42,
`num_ctx=65536`, `num_predict=1024`, `think=false`, nonstreaming output.
Per-question arm order is a fixed hash-based choice. Identical complete prompts
reuse one recorded generation; they are not independent reader samples.

The worker checks model identity around every generation, rejects incomplete or
length-limited answers, retains raw responses and failed attempts, and checkpoints
atomically under a process lock. Resume requires the same input artifact,
runner, model and runtime declaration. A conservative UTF-8 byte headroom check
precedes requests; it is not exact tokenizer instrumentation. Responses retain
reported prompt/output token counts and completion reasons. Per-arm hypothesis
exports require the worker to finish normally with exit zero and complete paired
coverage. A prediction file alone is not a correctness result.

The [upstream LongMemEval evaluator](https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py)
judges hypotheses separately with category-specific rules, including abstention,
preferences, updates and temporal tolerance. This harness exports compatible
`question_id`/`hypothesis` JSONL and separate references for independent judging.
It does not label Qwen's own opinions as independent answer-quality evidence.
Ollama's [chat API](https://docs.ollama.com/api/chat) supplies the completion and
token observations retained by the runner.

These are development predictions from one reader. Even a judged improvement
would require replication and independent task/competitor evidence before a
leadership claim. The separate prospectively registered 381-question source-
retention confirmation remains frozen; no reader outcomes will tune that arm.

The preparation/generation safeguards pass 39 tests, including gold exclusion,
context reproduction, corruption and changed-model rejection, interruption/
resume, shared identical prompts, exclusive state writes and native-exit export
gating. Actual study preparation and generation status will be recorded below.

Preparation completed with native exit zero using the frozen original runtime:
all 119 controls reproduced and all 238 paired contexts obeyed their product
budget. The [registered plan](reader-plan-a89cfe1.json) records prepared-input and
reference-file hashes, the exact cohort and reader identity before generation.
All 238 full prompts are distinct; the largest contains 19,136 UTF-8 bytes,
comfortably below the runner's conservative 65,536-context headroom check.

A separate [authored provider preflight](reader-preflight-a89cfe1.json), using the
same requested reader options, completed with native exit zero and a `stop`
completion reason. It answered the supplied blue-telescope fact correctly.
This establishes the local API shape and completion checks, not benchmark quality.
The paired prediction run launched after this plan was committed and completed
with native exit zero: 238 unique generations, no failures. The
[completion manifest](reader-completion-a89cfe1.json) pins all input, output and
raw-state hashes. Every answer was verified against its raw response and frozen
prompt before judging; no answer-quality conclusion follows from completion.


The legacy judged-context correction has two regressions that fail on its prior
implementation and passes a 45-check benchmark/reader set after the fix. A
malformed supplied question date is now an explicit evaluation error before
engine or model work, rather than a fallback to the current clock.

The configured root `.env` cloud judge returned HTTP 429 on a fresh bounded
probe. The paired reader completed locally. Gemma 4 26B downloaded successfully
as a separate-family local judge candidate; calibration precedes any judgment
of the study predictions. Its [official model listing](https://ollama.com/library/gemma4:26b)
reports a 19 GB artifact. Model availability or size is not evidence of judge
reliability, and a local judge result will not be presented as an official
GPT-4o benchmark score.

The [judge calibration controls](../../../fixtures/reader_judge_controls.json)
are authored separately from the study answers: 42 short cases, balanced between
21 correct and 21 incorrect responses, spanning seven evaluation categories.
The declared calibration gate is at least 40 correct with zero false accepts.
It includes missing facts, incorrect current-state updates, unsupported guesses,
partial preference matches and numeric temporal tolerance. Passing these simple
controls would not establish judge accuracy on the real study; they are a
minimum check before using the separate local judge. The fixture is frozen
before any Gemma inference, and will not be tuned against study predictions.


`benchmarks.diagnostics.reader_judge` freezes the judge model digest, Ollama
version, runner/helper hashes, category rubric, JSON schema, context/generation
options and the entire control fixture (including its acceptance gate).
Only the question, reference and response are sent as user data; expected labels,
arm names and case identities are excluded. It rejects truncation, model changes,
nonboolean verdicts and corrupted raw responses, and resumes under an exclusive
state lock. Duplicate full prompts reuse their original judgment.

Study judging requires a complete calibration with native exit zero and a gate
recomputed from recorded raw responses. It also verifies all paired reader
answers against their original raw generations, prompt hashes, registered model
and prepared product contexts. Results report paired question bootstrap intervals
(2,000 draws, seed 42), both overall and by category. Shared conversation histories
and judge errors limit interpretation; these are development comparisons with
one local reader/judge pairing, not independent confirmation or the official
GPT-4o protocol. No production packing default changes on these results alone.


The first [local calibration](judge-calibration-2611be7.json) completed normally
with 42 recorded raw responses but **failed its quality gate**: 38/42 correct,
one false accept and three false rejects. It incorrectly accepted an obsolete
current-state answer and rejected two valid partial preference matches and one
allowed temporal tolerance case. Its pipeline `passed` field records successful
execution; `metrics.calibration_gate_passed` records the failed acceptance gate.
No study answers were scored by this judge.

The harness and benchmark boundary suite passed 84 tests in 3.43 seconds with
native exit zero. A second candidate, the dense Gemma 4 31B model, is being
obtained under the same unmodified rubric and 42-case gate. This is model
selection on authored controls, not an independent estimate of judge accuracy.
The [official model listing](https://ollama.com/library/gemma4:31b) distinguishes
the dense 31B model from the 26B mixture-of-experts variant; its published
capabilities are a reason to test it, not proof that it can judge this study.


The LoCoMo judged runner now also sends `response.bundle.render()` directly to
the reader. A regression with a valid raw candidate fails on the old formatter
path and passes on the product bundle path. The combined benchmark, failure-
accounting and reader/judge suite passes 97 tests in 123.35 seconds with native
exit zero. Existing failure fixtures now supply the public bundle contract;
they still verify that provider errors remain visible and outside accuracy.
LoCoMo's custom score excludes category 5, and its judged path does not create
the convenience profiles used by its keyword path. These remaining differences
are explicit in the runner documentation.


A [blinded spot-check plan](reader-blinded-review-v1-plan.json) selected two
question pairs per category using a fixed hash before inspection. The
[recorded review](reader-blinded-review-v1.json) assessed all 28 opaque answers
before reading local judge verdicts: 14 clear accepts, nine clear rejects and
five ambiguous responses. These are qualitative labels from the current Codex
session, not a pinned independent model, human review or accuracy estimate.

Several ambiguous responses state the right fact, count components or arithmetic
and then refuse to answer. The upstream intermediate-step rubric can reward
such responses even when task completion is poor. Two personalization responses
reject the user's newly stated music-store visit because it was not already
recorded, despite knowing the current guitar and desired upgrade. This separates
reader instruction-following failures from missing retrieval evidence.

Subsequent unblinded source inspection found another interpretation limit:
question `a2f3aa27` has reference answer `1300`, but its source turns say `1250`
and later report being *close to* 1300 with uncertainty. Both packed arms retain
those user turns. A trustworthy answer should preserve that uncertainty rather
than manufacture an exact updated count. The benchmark reference and frozen
judging protocol remain unchanged; reference correctness and source faithfulness
are separate observations. For `09ba9854_abs`, the wrong bus-savings answer uses
transport estimates from another session, illustrating the need to resolve which
trip a generic follow-up refers to before asserting personalized costs.


The dense Gemma 4 31B candidate completed the same authored calibration with
native exit zero: [41/42 correct, zero false accepts](judge-calibration-gemma31-2611be7.json).
It passed the previously fixed gate with one false reject. The
[paired judging plan](reader-judging-gemma31-plan.json) was committed before
judging all 238 completed answers. Its exact declaration, controls, calibration,
reader inputs, raw state and predictions are pinned. This passing calibration
permits the study; it does not prove general judge reliability. The paired
judgment run is underway, and no partial outcome metrics are being used to tune
its prompts or the independent source-retention confirmation.
