# One-head answer trial stopped on a reader timeout

The frozen `70c4fa9` trial did not complete. Qwen 3.5 4B finished all 357 logical
predictions (313 distinct requests), exited zero, and passed reconstruction from
the saved requests and raw responses. Gemma 4 26B logged 245 of 357 logical
predictions and saved 213 distinct valid responses before one request timed out
at the registered runtime's 180-second socket limit. No response was received
for that request. Gemma and the enclosing driver both exited one.

All saved response hashes and structural checks passed. The timeout, partial
state, failed report and native completion records remain intact. No judge or
scorer ran, and partial answers were not inspected for correctness. There is no
answer-quality comparison from this attempt.

A follow-up must use a fresh registration and fresh states for both readers.
A longer uniform request timeout may address availability; it does not establish
that the model needs more capacity, isolate the cause of the timeout, or justify
replacing only failed outcomes. Models, generation settings, prompts, questions,
contexts and scoring rules should remain fixed. No production packing default
is changed by this failed trial.

See [verified incomplete artifacts](packing-head-reader-incomplete.json) and
[the original registration](packing-head-reader-registration.json).
