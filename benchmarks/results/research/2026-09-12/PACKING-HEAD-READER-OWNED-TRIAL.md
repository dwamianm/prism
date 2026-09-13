# Fresh head-reader trial on a task-owned model service

After the successful six-canary loading probe, this new trial gives the frozen
reader/judge/scorer chain its own Ollama server. The existing shared service is
not stopped or reconfigured. The owned server permits one loaded model and one
parallel request; it is terminated and reaped on either success or failure.
It shares host hardware and installed model files with other processes.

The reader uses frozen source `9dd6a91` and the unchanged comparison interpreter.
The new reader registration is identical to the 600-second-timeout trial except
for its registration timestamp: models/digests, generation options, prompts,
contexts, cohort, reference labels, code hashes, judge and calibration are all
unchanged. Both readers start with empty states, each producing all 357 logical
predictions. The earlier two failed trials are retained and are not resumed.
The execution wrapper has separately frozen source `1cc4795`, native binary and
interpreter hashes, server options and an explicit loopback proxy bypass.

Native successful reader completion and raw response verification remain
prerequisites for judging. Native judge completion and independent scoring
remain prerequisites for any comparative score. Failure stops the chain without
retry. A mocked failure test uses real child processes and verifies that the
failure exit is retained and the owned server is reaped; all three execution
diagnostic tests passed with native exit zero.

This is a reused development cohort and the same custom judge calibration;
neither the canary probe nor an eventual answer gain establishes competitive
leadership. No production packing default changes follow from this trial alone.

See [reader registration](packing-head-reader-owned-registration.json),
[execution registration](packing-head-reader-owned-execution-plan.json) and
[loading observations](OLLAMA-TIMEOUT-DIAGNOSTIC.md).
