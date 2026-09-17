# MemoryArena travel DeepSeek development trial v1: aborted before scoring

The registered v1 paired trial started with group 135 in the
`native_full_history` arm. Seven traveler plans completed, and the eighth
traveler completed its first tool-call step. Its next cloud request remained
open past the OpenAI SDK's 600-second read timeout and entered the SDK's first
automatic retry. The pinned upstream client permits two retries, so one stalled
request could consume roughly 30 minutes.

The run was interrupted at that point. The runner had not completed a group-arm
unit, so `group_checkpoints.jsonl` did not exist. No submission, native score,
strict score or result artifact was produced. Buffered diagnostic output became
visible during interruption, so group 135 is excluded from the replacement
cohort even though its answers and scores were never inspected.

This attempt exposed an omission in the v1 registration: actor transport timeout
and retry behavior were inherited from the SDK rather than bound explicitly.
The replacement protocol sets a 120-second request timeout and one SDK retry,
records both values in the registration, and verifies the live client options
before any task call. It uses a new registration and a fresh cohort excluding
preflight group 1 and partially observed group 135.

The v1 registration remains retained at
[`memoryarena-travel-deepseek-dev12-v1-registration.json`](memoryarena-travel-deepseek-dev12-v1-registration.json).
It is not task-quality evidence.
