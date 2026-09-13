# Fresh one-head reader trial with a longer transport timeout

Outcome: the fresh trial also stopped, this time during Qwen generation, with a
`TimeoutError` and no response. The native reader and outer driver exited one.
All 275 saved unique responses passed structural and checksum verification;
their correctness was not inspected. Gemma generation, judging and scoring did
not start. There is no comparative score, and the failed state is retained.
See [the complete failure record](packing-head-reader-timeout-incomplete.json).
Increasing the timeout did not make the complete trial reliable; investigate
execution before another full model run.

The first trial stopped when one Gemma request returned no response within the
180-second socket timeout. Its partial results remain separately recorded and
are not resumed or selectively repaired.

This fresh trial uses source `9dd6a91` in an isolated, frozen checkout. Every
reader generation has a declared 600-second socket timeout, with one attempt
and no response retry. Both Qwen and Gemma start with empty states on the same
119 questions and three arms. The models and their digests, generation settings,
prompts, exact memory contexts, reference data, category rules, judge and prior
judge calibration are unchanged. These identities were compared with the first
registration before launching generation. The timeout is not a hard deadline
for work on the model server after a caller disconnects.

The native driver still requires both complete readers to exit zero and pass raw
response verification before creating opaque judge inputs. Judging and scoring
remain separate stages. Failed requests stop the chain and remain recorded.
The transport change does not support a model-capacity diagnosis or a speed
comparison. The reused development cohort remains insufficient for promoting a
production default or claiming competitive leadership.

The 36 focused reader, judgment-input, scoring and head-trial tests passed,
including timeout retention and rejection of retrying failed state. The frozen
comparison interpreter has no pytest; its attempted test command exited one
before collection. The actual tests used the project interpreter with the
isolated checkout explicitly selected. No comparison environment was modified.

See [the fresh registration](packing-head-reader-timeout-registration.json) and
[the original incomplete trial](PACKING-HEAD-READER-INCOMPLETE.md).
