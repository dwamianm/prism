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

## Completed paired development judging

The frozen Gemma4 31B run completed all 238 judgments (230 unique model calls)
with native exit 0 and no failed attempts. The completion manifest preserves
raw report/state hashes and all paired metrics. Under this local rubric, density
packing scored 49/119 (41.18%) and score packing 69/119 (57.98%): +16.81 percentage
points, paired bootstrap 95% interval +6.72 to +26.05 points (2,000 samples,
seed 42). There were 29 wins, 9 losses and 81 ties. `passed` in the raw report
means successful workflow completion, not a product acceptance gate.

| Category | Density | Score | Questions |
| --- | ---: | ---: | ---: |
| Abstention | 100.0% | 87.5% | 8 |
| Knowledge update | 55.0% | 60.0% | 20 |
| Multi-session | 20.0% | 40.0% | 30 |
| Single-session assistant | 0.0% | 100.0% | 9 |
| Single-session preference | 28.6% | 42.9% | 7 |
| Single-session user | 82.4% | 100.0% | 17 |
| Temporal reasoning | 28.6% | 32.1% | 28 |

The judge agreed with all 23 clear verdicts in the previously completed blinded
review. Five ambiguous responses remain excluded from that agreement calculation.
The judge accepted two and rejected three of them, including different verdicts
on two responses that calculated the 12-minute difference but ultimately refused
to answer. That inconsistency limits fine-grained interpretation of these scores;
we retain the frozen verdicts and the ambiguity, without retrospective relabeling.
This review was by the current Codex session, not independent human annotation.

These are development results from one Qwen3.5 4B reader and one control-selected
local Gemma judge using a custom category rubric. They are neither the official
GPT-4o protocol nor a comparative leaderboard result. Shared conversation histories
also limit query-level confidence intervals. The smaller abstention category lost
one answer. The 381-question prospective packing confirmation remains running;
production packing defaults remain unchanged.

A post-study structural overlap audit found 119 distinct exact history sets,
5,381 distinct haystack session IDs, and 284 background session IDs reused across
questions. Transitively, that connects all 119 questions into one component,
so an overlap-component bootstrap cannot provide a useful independent-history
interval. The 219 answer-session IDs in this cohort are all distinct across
questions. This measures exact ID overlap, not the strength of statistical
dependence or semantic duplication. The frozen query-bootstrap results above
remain unchanged. `benchmarks.diagnostics.session_overlap` reproduces these
counts; `reader-session-overlap.json` pins input and diagnostic hashes.

## Second reader family

A prospective development sensitivity run is registered in
`reader-plan-gemma26-a89cfe1.json` before its first generation. It uses installed
`gemma4:26b` with the same frozen `a89cfe1` reader harness, `1f5375a` packing
runtime, prompt, 4,096-token product contexts, 119 question pairs and generation
options. Preparation exited zero and every context/input field matches the
original prepared study except reader identity and preparation time. Reference
file bytes are identical. No generation is replaced or excluded based on quality.

This run was selected after inspecting the original Qwen results, so it is an
exploratory reader-family check, not independent confirmation. The planned
Gemma4 31B judge has a different digest but shares the Gemma family, an additional
source of correlated error. A deterministic 28-response arm-blinded review will
precede judging, retaining ambiguous cases separately. The original results,
production defaults and ongoing test-partition capture remain unchanged.

The second reader's review protocol is separately registered in
`reader-blinded-review-gemma26-v1-plan.json` while generation is still running.
It retains the original deterministic question selection but uses a fresh opaque
ID seed, preventing direct reuse of arm mappings from the completed first study.
The complete packet and its hashes will be recorded after native reader completion.

The second reader and its subsequent judging both completed with native exit
zero. All 238 answers and judgments are retained, with 201 unique judge calls and
no failed attempts. The registered judging plan verifies the frozen inputs and
completed review before the first judge call. The
[completion manifest](reader-gemma26-judging-gemma31-completion.json) pins the
report and raw state. At 4,096 tokens, density produced 71/119 judged-correct
answers (59.66%) and score ordering produced 91/119 (76.47%): +16.81 percentage
points, paired query-bootstrap 95% interval [8.40, 25.21], 26 wins, 6 losses and
87 ties. This matches the original reader's net gain, with different category
behavior. Temporal reasoning improved from 14/28 to 21/28; abstention stayed 8/8.

The pre-judging arm-blinded review accepted 19 responses, rejected seven and
marked two ambiguous. The judge agreed on 24/26 clear cases. It rejected a
partially useful yogurt-preparation answer accepted in review, and accepted a
marathon response that supplied the two times but refused to calculate their
difference. It accepted both ambiguous responses: a qualified follower count
with unclear chronology and a preference recollection with no actual advice.
These disagreements illustrate rubric sensitivity; neither verdict set was
retrospectively changed. The reviewer was this Codex session, not an independent
human. Shared Gemma family errors, development selection and overlapping histories
still limit this result. It supports further evaluation of the packing change;
it does not establish competitor superiority or justify bypassing the separately
registered source-retention confirmation gate.


## Public configuration parity

The development alternative is now available through
`PackingConfig(multipath_ordering="score")`, with density still the default.
`benchmarks.diagnostics.packing_option` verified all 714 frozen development
contexts (119 questions, two policies, three budgets) against the original
experimental hashes, tokens and source-retention measurements. The run completed
with native exit zero in `public-packing-option-dev-parity.json`. No providers
were called and no quality result was recomputed or reclassified. The separate
confirmation continues on its original frozen runtime and comparator.
