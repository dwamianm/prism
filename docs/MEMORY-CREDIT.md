# Measure whether a memory changed an answer

PRME can remove one cited record from an already packed context without changing
the memory pack or rerunning retrieval. Re-answering that exact ablation separates
four observed outcomes:

| Baseline | Without cited record | Credit tier | Value |
|---|---|---|---:|
| correct | wrong | `load_bearing` | 1.0 |
| correct | correct | `cited_non_flipping` | 0.6 |
| wrong | correct | `misleading` | -1.0 |
| wrong | wrong | `cited_wrong_noncuring` | 0.0 |

This is context-presence credit for one fixed reader and evaluation protocol. It
does not prove the memory is universally useful, and it is distinct from deleting
the memory bank entry and rerunning candidate generation.

## End-to-end flow

```python
from hashlib import sha256

from prme import (
    AnswerCitationSubmission,
    MemoryClient,
    ablate_context,
    assess_context_presence,
)

with MemoryClient("./memories") as memory:
    response = memory.retrieve("Which database does Aster use?", user_id="alice")

    # These two functions belong to the application. Ask the reader to return
    # the minimal memory IDs it used, then evaluate the answer independently.
    answer, cited_ids = answer_with_memory(response.bundle.render())
    baseline_correct = evaluate_answer(answer)

    citation = memory.record_answer_citations(
        AnswerCitationSubmission(
            request_id=response.metadata.request_id,
            answer_id="assistant-message-42",
            cited_node_ids=tuple(cited_ids),
            answer_sha256=sha256(answer.encode()).hexdigest(),
            method="application_verified",
        ),
        user_id="alice",
    )

    # Run one target at a time. Every retained byte and representation stays
    # fixed; the removed entry is not replaced with a lower-ranked candidate.
    target = citation.cited_node_ids[0]
    ablation = ablate_context(response.bundle, [target])
    counterfactual_answer, _ = answer_with_memory(
        ablation.counterfactual.render()
    )
    counterfactual_correct = evaluate_answer(counterfactual_answer)

    credit = assess_context_presence(
        ablation,
        citation,
        node_id=target,
        baseline_correct=baseline_correct,
        counterfactual_correct=counterfactual_correct,
        evaluation_id="reader-prompt-decoding-and-judge-v1",
        counterfactual_answer_sha256=sha256(
            counterfactual_answer.encode()
        ).hexdigest(),
    )
    print(credit.tier, credit.value)
```

`ablate_context` returns a frozen `ContextAblation` with baseline and
counterfactual context hashes, exact token accounting, the removed IDs, and the
counterfactual bundle. It rejects missing or duplicate IDs. The original bundle
is unchanged.

`assess_context_presence` accepts only a single removed record that appears in
the saved citation set and whose baseline context hash matches that citation.
Use one `evaluation_id` only when the reader model, prompt, decoding settings,
answer evaluator, and reference answer policy are the same in both arms. Store
the returned model with the surrounding evaluation artifacts; PRME does not yet
persist or consume this credit automatically.

## Interpretation boundaries

- A non-flip can mean redundancy. It does not establish irrelevance.
- A citation on a wrong answer receives no positive value. It becomes negative
  only when removing that record changes the answer from wrong to correct.
- Uncited memories and missing citation telemetry are not negative labels.
- This ablation holds the packed context fixed. A memory-bank deletion can also
  change graph traversal, retrieval cutoffs, and which records fill the freed
  token budget, so it needs a separate retrieval-invariant experiment.
- Do not feed these values into retention or ranking without tenant isolation,
  a held-out task gate, versioned activation, and rollback.
