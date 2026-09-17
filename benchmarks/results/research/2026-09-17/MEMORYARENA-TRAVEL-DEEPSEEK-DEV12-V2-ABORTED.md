# MemoryArena travel DeepSeek development trial v2: pre-call abort

The v2 launch loaded both upstream tool stacks and started the PRME adapter, then
stopped before the first task call. Its live transport verifier expected
`client.timeout.read`, but OpenAI 2.21 represents an explicitly numeric timeout
as `client.timeout == 120`. The resulting `AttributeError` occurred while actor
clients were being configured.

No group-arm execution started, no cloud task request was made, and no task
output, checkpoint, submission or score exists. The replacement v3 registration
therefore retains the same unobserved cohort and actor policy after correcting
the verifier to accept both numeric and `httpx.Timeout` representations.

The v2 registration remains retained at
[`memoryarena-travel-deepseek-dev12-v2-registration.json`](memoryarena-travel-deepseek-dev12-v2-registration.json).
It is not task-quality evidence.
