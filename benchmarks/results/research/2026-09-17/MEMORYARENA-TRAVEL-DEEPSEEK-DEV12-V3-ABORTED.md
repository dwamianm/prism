# MemoryArena travel DeepSeek development trial v3: transport abort

The v3 transport policy worked as registered. The complete eight-traveler
`native_full_history` arm for group 136 was durably checkpointed. Four PRME-arm
travelers then completed. A later response timed out after 120 seconds, retried
once, and timed out again. The runner aborted without producing a PRME group
checkpoint, submissions or scores.

Buffered and live diagnostics exposed partial PRME outputs for group 136, so it
is excluded from the replacement cohort. The attempt also showed that a whole-
group checkpoint wastes valid work when a later cloud request fails. The
replacement runner checkpoints each completed traveler. After interruption it
rebuilds the upstream agent's prior-query and prior-plan state from saved
actions, and replays saved raw memory entries into a fresh PRME pack before the
next traveler. A failed provider request therefore discards no completed
traveler.

The v3 registration remains retained at
[`memoryarena-travel-deepseek-dev12-v3-registration.json`](memoryarena-travel-deepseek-dev12-v3-registration.json).
No task-quality result was computed from this attempt.
