# Answerability and grounded abstention

Retrieval score measures ranking, not whether the packed evidence establishes
the exact relation a question asks about. `AnswerabilityEvaluator` provides an
optional check after retrieval. It decomposes compound questions into separate
requirements, asks a configured model to classify each requirement, validates
its bundle-local citations, and derives the final verdict in code.

```python
from pydantic import SecretStr

from prme import AnswerabilityConfig, AnswerabilityEvaluator

response = await engine.retrieve(
    "Which lint rules did I enforce?",
    user_id="alice",
)

evaluator = AnswerabilityEvaluator(
    AnswerabilityConfig(
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://127.0.0.1:11434/v1",
        api_key=SecretStr("ollama"),
    )
)
assessment = await evaluator.assess(
    "Which lint rules did I enforce?",
    response.bundle,
)

match assessment.recommended_action.value:
    case "answer":
        pass  # The packed evidence supports every requirement.
    case "answer_partially":
        print(assessment.missing_information)
    case "surface_conflict":
        print(assessment.conflicts)
    case "abstain":
        print("The saved memory does not establish that.")
```

Pass `answer="..."` to assess a draft answer as well as question coverage. The
evaluator then asks the model to split the draft into atomic factual claims.
Use one evaluator instance for repeated calls so its provider client is reused.
`assess_answerability()` is the equivalent one-shot helper.

## Verdicts

| Verdict | Recommended action | Meaning |
| --- | --- | --- |
| `answerable` | `answer` | Every independently answerable requirement has cited support. |
| `partial` | `answer_partially` | At least one requirement is supported and at least one is missing. |
| `insufficient` | `abstain` | No requested requirement has validated support. |
| `conflicting` | `surface_conflict` | At least one requirement has two cited, incompatible answers. |

The provider does not control the set-level verdict. PRME resolves compact
references such as `m3` through `MemoryBundle.context_references`. The default
auditable format uses the exact UUID already present in each record's `id`
field. In both formats, a citation is accepted only when its token occurs in
the exact rendered bundle. Unknown citations are discarded. A claimed
supported requirement without one valid citation is downgraded to
`unsupported`; a claimed conflict requires two. The final verdict and action
are then derived deterministically from the validated requirement states.

An empty bundle returns `insufficient` without a model call. Provider, timeout,
or schema failures raise `AnswerabilityError`; they do not become an abstention
or an answerability verdict. The older `should_abstain()` Boolean helper keeps
its documented fail-open compatibility behavior and does not offer citation
validation, partial coverage, or conflict reporting.

## Audit and trust boundary

Each assessment records the provider and model, exact prompt and non-secret
configuration hashes, query hash, rendered-context hash, optional draft-answer
hash, and a deterministic `evaluation_id` for the fixed inputs.
`assessment_sha256` identifies the actual validated output, so repeated model
calls with the same evaluation identity can be compared without implying they
were identical. The evaluator does not persist the assessment, mutate memory,
activate a ranking profile, or change the deterministic retrieval receipt. The
configured provider receives the question, exact packed context, and optional
draft answer. Credentials are held as `SecretStr` and are excluded from the
assessment and configuration hash.

Model inference remains best-effort and can vary even at temperature zero. The
verdict is auditable, not calibrated probability. Evaluate both unsafe-answer
rejection and answerable-query coverage on the target workload before enforcing
it in production.

The first registered repeated
[BEAM development trial](../benchmarks/results/research/2026-09-16/BEAM-ANSWERABILITY-DEV-V1.md)
failed every promotion gate. The
[speech-act-aware v2 same-cohort rerun](../benchmarks/results/research/2026-09-16/BEAM-ANSWERABILITY-DEV-V2.md)
also failed all five machine-evaluated gates: it produced 2 unsafe full answers
in 12 abstention samples, only 62 full answers in 108 ordinary samples, 5
citation errors, and stable actions for 26/40 questions. This API therefore
remains explicit and experimental. These trials flattened frozen integration
results to text and IDs. The next registered evaluation must test the draft-answer
claim-verification path; future end-to-end trials must also preserve PRME's typed
time, lifecycle, and epistemic fields.

The design follows the distinction between retrieval and evidence verification
studied by [SURE-RAG](https://arxiv.org/abs/2605.03534), the corrective retrieval
gate in [CRAG](https://arxiv.org/abs/2401.15884), and claim-level entailment in
[RAGChecker](https://arxiv.org/abs/2408.08067). PRME adds compound-query partial
coverage, exact memory-ID resolution, and fail-closed citation handling to its
public retrieval bundle contract.
