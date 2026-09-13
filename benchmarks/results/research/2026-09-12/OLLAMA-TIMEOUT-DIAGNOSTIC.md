# Ollama execution check after two incomplete reader trials

No additional model download is justified by the timeout evidence. The service
responded at 18:59 UTC on September 13, reporting Ollama 0.34.0 and the registered
Qwen3.5 4B, Gemma4 26B and Gemma4 31B digests. A local `ollama` process owned the
listening port. This check made no generation request or server configuration
change.

The fresh Qwen reader saved 275 unique valid responses before its next request
timed out after 600 seconds. In the corresponding local log window, model load
requests were followed by a loading-timeout message, client closure and two
10m31s chat completions, one HTTP 200 and one HTTP 499. No explicit allocation
failure appeared in that window. Without request-level correlation, these
observations do not prove the cause or establish that all lines belong to our
trial. The partial responses were not scored.

Ollama documents that model loading can queue behind other models, and that
parallel requests and context length increase memory requirements. These are
diagnostic hypotheses, not a finding of insufficient memory in this run.
See the [Ollama concurrency FAQ](https://docs.ollama.com/faq),
[running-model API](https://docs.ollama.com/api/ps) and
[troubleshooting guide](https://docs.ollama.com/troubleshooting).

Before another full reader trial, establish controlled model loading and verify
the requested context and generation settings through a separate small service
probe. Preserve the two failed trials; a fresh registered run must retain all
questions and controls. Do not select only failed prompts for a replacement
quality score or change another workload's server configuration.

See the [sanitized observations](ollama-timeout-health-20260913.json).

A separately registered task-server probe has now completed with native exit
zero. The server used the existing assets, one loaded model and one parallel
request, on its own ephemeral loopback port. All six authored canaries completed
at the requested context sizes: 65,536 for both readers and 32,768 for the judge.
First/warm request times were 2.08/0.19 seconds for Qwen, 3.67/0.22 for Gemma26
and 6.85/1.11 for Gemma31. These tiny requests are not a benchmark speed claim.
The server exited zero and its port was closed afterward. The existing service
was not stopped or reconfigured. Six canaries do not prove that a long trial
will finish or identify the cause of earlier shared-service timeouts.

The [probe plan](ollama-load-probe-plan.json) fixes binary/module hashes,
model digests, options and a 90-second single-attempt timeout. Its
[completion record](ollama-load-probe-completion.json) binds all raw artifacts
and observed contexts. Authored checks cover retained timeout/context-mismatch
failures and a real failed child process with owned-server cleanup.

The subsequent full trial on the owned service did fail with a GPU allocation
error. The service logged `Insufficient Memory` and failed graph computation;
the following HTTP 200 body had no model identity or completed answer. Reader
validation rejected it and stopped the chain before judging. This new evidence
supports investigating memory pressure for this run; it does not retrospectively
identify the causes of the two earlier timeouts. Tiny loading canaries did not
exercise the failing sustained workload. See the
[retained trial failure](packing-head-reader-owned-incomplete.json). No larger
model download follows from these observations.
