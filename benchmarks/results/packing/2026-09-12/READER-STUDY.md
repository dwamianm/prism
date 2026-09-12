# Paired development reader study

Source retention is not answer accuracy. The existing LongMemEval judged path
reformats ranked results independently of the product bundle, so it cannot be
used to establish the answer effect of the measured packing change. This study
instead submits frozen product contexts directly to a fixed local reader.

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
