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
The paired prediction run is being launched after this plan is committed.


The legacy judged-context correction has two regressions that fail on its prior
implementation and passes a 45-check benchmark/reader set after the fix. A
malformed supplied question date is now an explicit evaluation error before
engine or model work, rather than a fallback to the current clock.

The configured root `.env` cloud judge returned HTTP 429 on a fresh bounded
probe. The paired reader continues locally. Gemma 4 26B is being downloaded as
a separate-family local judge candidate; calibration will precede any judgment
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
