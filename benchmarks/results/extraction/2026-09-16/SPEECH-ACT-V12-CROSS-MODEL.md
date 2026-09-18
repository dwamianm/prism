# Speech-act v12 cross-model development evidence

Two separately registered executions of the unchanged v12 implementation and
14-case, 15-target authored contrast set passed every gate:

| Model profile | Cases | Targets | Unsafe claims | Missing targets | Policy/binding errors |
|---|---:|---:|---:|---:|---:|
| `deepseek-v4.1-flash:cloud` | 14/14 | 15/15 | 0 | 0 | 0 |
| `prme-qwen3.5:35b-a3b-8k` | 14/14 | 15/15 | 0 | 0 | 0 |

Each registration binds its exact model digest, prompt, response schema,
implementation files, cases, gates, temperature, retries, and timeout. Both
workers exited cleanly. DeepSeek completed in 23.147 seconds; the local Qwen
35B-A3B profile completed in 94.076 seconds on the tested host.

The local-model result uses a corrected target-presence definition: an exact
token-bounded target inside a grounded composite object counts as retained. The
same containment rule applies to unsafe claims, so a composite object cannot
hide a completed/current predicate. The initial whole-object-equality result is
retained separately and documents all four composite objects that motivated the
measurement correction.

These are authored development probes over two model artifacts through the
Ollama interface. They do not provide held-out accuracy, provider diversity,
third-person speech-act coverage, or a competitive comparison.
