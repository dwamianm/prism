# MemoryArena travel development trial V10 — aborted

V10 produced no quality score. It completed 13 of 24 group arms and durably
checkpointed 82 traveler executions: both arms for the first six groups, the
native arm for group 54, and the first four travelers of group 54's PRME arm.
All 82 checkpointed travelers had a valid named plan block.

The fifth PRME traveler in group 54 repeatedly failed at the model transport
boundary. The native Ollama endpoint returned a response that did not satisfy
the registered complete-response contract on both allowed attempts. Restarting
the runner reconstructed actor and PRME state from the checkpoints and failed
the same traveler on both fresh attempts. The client therefore raised
`Ollama native actor attempts exhausted`, as required, instead of scoring an
incomplete response. The response schema did not expose enough detail through
the registered error to distinguish truncation from another incomplete-response
condition.

This does not contradict V9's complete score and does not measure the plan-marker
correction. The next registration must retain explicit invalid-response
diagnostics and bound runaway response generation symmetrically for both arms.
The held-out confirmation cohort remains untouched.

## Evidence identity

- Registration SHA-256:
  `72b3e75ce2fb35303d90a0ba5cee8a774b398b1b7da4c26cefde8c5239c7158b`
- Registered PRME source revision:
  `43cb63132b8db50bf6c3e8c18d714f11122a041d`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Group-checkpoint SHA-256:
  `07a94d6ab484047fba5ca76adec57359dd19c6951355fe0bab0f42eea566fdd0`
- Person-checkpoint SHA-256:
  `2cf01956df77838edf441ba5a2ee53364aa81364d8cd17465000072bf85bfcbb`
- Execution-manifest SHA-256:
  `de6c8548ef94e1224c7c31507dee1a140a30daf5fab2f14a402b18c9a820dfdf`
- Actor: `deepseek-v4.1-flash:cloud` through Ollama native chat, temperature
  zero, seed 17, thinking disabled

The checkpoint files remain in the owned local run directory. They contain full
benchmark prompts and responses and are not copied into the repository. This
aborted development attempt carries no success-rate or non-inferiority claim.
