# MemoryArena travel DeepSeek development trial v4: compatibility-route abort

V4 proved that traveler-level checkpoints prevent completed work from being
lost: Thomas and Aurora in group 137 remained durable across invocations. The
next traveler's post-tool request then timed out twice in each of two separate
invocations. The exact repeated failure after successful tool selection points
to Ollama's OpenAI-compatible proxy path for this conversation, rather than
random cloud tail latency.

No group arm completed and no submission or score was produced. Partial outputs
for group 137 were visible, so it is excluded from the replacement cohort.

The replacement uses Ollama's native `/api/chat` endpoint. This is the same
route that completed 930 successful calls with zero transport failures in the
separate support-certificate trial. The replacement client keeps the pinned
MemoryArena messages and tool schemas, disables thinking, pins temperature 0
and seed 17, validates response model/completion/token fields, supports parallel
tool calls, and allows two 180-second transport attempts. Traveler checkpoints
and replay remain unchanged.

The v4 registration remains retained at
[`memoryarena-travel-deepseek-dev12-v4-registration.json`](memoryarena-travel-deepseek-dev12-v4-registration.json).
It is not task-quality evidence.
