# LongMemEval-V2 integration assessment

Reviewed 2026-09-12 against the [official repository](https://github.com/xiaowu0162/LongMemEval-V2/tree/2cc8c540bdb87fe6761629b585e727e1c4704520)
(commit `2cc8c540bdb87fe6761629b585e727e1c4704520`) and the
[public dataset](https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/tree/f152293e235517d504809563c833d7190b8c713b)
(revision `f152293e235517d504809563c833d7190b8c713b`). This is an integration
assessment, **not a PRME result or an implemented adapter**. No question answers
or trajectory bodies were inspected for this assessment.

The benchmark adds agent-environment memory to the existing V1 chat-history
coverage: static and dynamic state, workflows, environment gotchas and premise
awareness. Its 451 questions span web and enterprise domains. The small tier
shares a 100-trajectory haystack within each domain; medium generally has 500.
See the pinned [schema](https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/blob/f152293e235517d504809563c833d7190b8c713b/SCHEMA.md)
and [data card](https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/blob/f152293e235517d504809563c833d7190b8c713b/DATA_CARD.md).
This is materially different coverage from source-turn support recall on V1.

## Integration boundaries

The upstream `Memory` interface accepts `insert(trajectory)` and
`query(query, query_image=None)`, returning text/image context items. It also has
save/load and runtime-configuration hooks. The backend receives only an opaque
query invocation ID through query context; question identity, category, gold
answer, evaluation function and construction metadata must not reach retrieval.
The upstream privacy tests enforce this separation.

Source ingestion needs an explicit allowlist. Public trajectories provide task
goal and outcome, initial URL and ordered state/action records. State fields
include URL, action, agent thought, accessibility-tree observation and screenshot.
These source fields have different evidentiary meanings: a thought is not an
observed outcome, and an action attempt does not establish its effect. State
indices encode within-trajectory order; they are not wall-clock timestamps.
Keep source identities and state ranges available for evidence verification.

The reader receives query images as well as retrieved evidence. A text-only PRME
adapter would be a disclosed ablation and cannot establish complete multimodal
coverage. The public schema does not provide V1-style supporting-turn flags, so
the existing support-recall evaluator cannot be relabeled as V2 accuracy.

## Budget and comparison requirements

The upstream harness counts context with its reader processor, including images
and chat-template overhead. If over budget, it drops entire context items from
the tail. Returning one large PRME text item can therefore lose the entire memory
payload if its token count differs from PRME's tokenizer. An adapter must measure
and record the final downstream payload, preserve complete evidence items, and
expose any truncation. PRME's cl100k budget alone is insufficient for this claim.

The release describes Qwen3.5-9B as its fixed reader and Qwen3-Embedding-8B for
embedding methods, with a separately configured judge. Our existing Qwen3.5:4b
local diagnostics are not the same protocol. Record actual model versions,
processors, prompts, budgets, index preparation, errors and source hashes before
comparing accuracy or latency with the supplied baselines. Shared haystacks permit
reuse of an index; query-dependent state and caches must not leak answers across
questions. A development question split is not an environment-generalization test.

The repository and data card report Apache-2.0 licensing. At the pinned dataset
revision, trajectories occupy about 1.20 GB; the two screenshot archives total
about 5.92 GB compressed. Downloading the small haystack map does not by itself
provide its source trajectories or screenshot assets. Avoid an accidental
whole-dataset download when preparing a narrow integration check.

## Work required before a result

1. Implement and test the source allowlist, provenance and ordered state units,
   including faithful treatment of long accessibility observations.
2. Integrate through the public sync client with isolated, reopenable packs and
   the upstream save/load hooks; test insert/query persistence and source IDs.
3. Handle query images and retrieved screenshots, or explicitly label a text-only
   ablation. Verify final reader token counts and evidence-item retention.
4. Freeze a development/held-out question partition before inspecting answers;
   test against no-memory and supplied RAG baselines with matched reader inputs.
5. Run generated-answer evaluation with the released evaluator, report failures
   and category coverage, and separate indexing cost from query latency.

Current PRME ranking and formatter improvements do not discharge these checks.
