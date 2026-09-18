# LLM-AggreFact Bespoke sentence fusion v3 — infrastructure failure

The preregistered development run terminated after more than 45 minutes when a
batch of two long prompts exceeded the Apple MPS allocation limit. The runtime
reported 54.54 GiB allocated and rejected a further 14.94 GiB attention
allocation. It wrote no result file or checkpoint, exposed no selected model
scores, and never unlocked the 1,100-claim test cohort.

This run supports no verifier-quality conclusion. Its fixed batch size and lack
of resumable progress are rejected as an execution protocol. A follow-up may
retain the exact model revision, prompts, balanced cohort, sentence-fusion score,
calibration rule, and quality gates while reducing the batch size, releasing
unused MPS allocations between pairs, and saving source-free completed-case
checkpoints. The failed registration and error record remain unchanged.
