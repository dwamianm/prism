# LLM-AggreFact Bespoke sentence fusion v3b — infrastructure failure

The preregistered batch-size-one run completed 250 of 1,100 development cases
and saved 491 source-free pair scores. It then stopped making progress while
scoring case 251, whose prompt was only 716 tokens. Sampling located the wait
inside the MPS model forward. The process reached a 49.2 GiB physical footprint.

Restarting the exact command from the completed-case checkpoint reproduced the
stall on the same case. The fresh process reached a 46.3 GiB physical footprint
with 45.4 GiB of graphics allocations, including 17.1 GiB swapped, on a 48 GiB
machine. Both attempts were interrupted before the case produced a score.

The private checkpoint is mode `0600`, contains identifiers, labels, scores and
counts, and contains no document, claim or prompt text. Its score values were
not inspected. No aggregate development metric or calibrated threshold was
produced, no result file was written, and the test cohort remained sealed.

This run supports no verifier-quality conclusion. It rejects the one-token
`model.generate` implementation on this hardware. A synthetic experiment found
that a direct next-token forward pass with the KV cache disabled retained the
Yes probability within 0.00001 while reducing MPS driver allocation from roughly
45 GiB to 17 GiB. Any trial using that implementation must have a new runner
hash and registration and must score the development cohort from the beginning.
