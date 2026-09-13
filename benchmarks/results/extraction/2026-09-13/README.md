# Local extraction model check

[`classification-qwen35-9b.json`](classification-qwen35-9b.json) retains the
complete output of the existing 12-case authored classification diagnostic run
against Ollama `qwen3.5:9b` (local digest `6488c96fa5fa`). The fixed checks cover
usage, possible and conditional usage, preferences, dislikes, choices, mixed
claims, temporal phrasing, and namesake entities. This is a development probe,
not held-out accuracy or competitive evidence.

The model passed **10/12 cases** in **348.114 seconds** with three configured
schema attempts. It correctly classified conditional usage, choices, rejected
choices, mixed claims, and ordinary preferences. It failed the namesake contract
by adding an extra `role=engineer` claim and failed to mark a conditional
preference as conditional. The dislike case passed the current structural check,
but inspection found `Noah does not like Redis` represented as the positive
predicate `likes` with the negation retained only in the full evidence passage.
A separate four-statement smoke call likewise emitted a positive `prefers coffee`
triple for `does not prefer coffee`.

The current runtime deliberately materializes the full source passage as claim
content, so those qualifications remain available to a reader. Triple metadata
is still semantically lossy. The 9B model is therefore retained as a local
diagnostic option and is not selected as PRME's default extractor.

The previous `qwen3.5:4b` authored run passed 8/12 cases. That observation does
not establish a controlled model-quality improvement: the reports are single,
unseeded executions and model output can vary. The 9B run's prompt, response
schema, fixtures, inputs, evaluated graph nodes, and raw extractions are preserved
in the JSON report.

Reproduce with an installed Ollama model:

```bash
python -m benchmarks.diagnostics.claim_classification \
  --model qwen3.5:9b --timeout 120 --output classification.json
```
