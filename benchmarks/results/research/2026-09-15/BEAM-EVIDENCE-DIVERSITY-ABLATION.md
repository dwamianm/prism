# BEAM evidence-diversity retrieval ablation

The registered `max_per_evidence=1` candidate remained at **13/20 (65.0%)**
and reduced mean rubric score from **0.56750 to 0.54833** on the frozen accepted
BEAM extracted-memory pack. It produced one pass-level win, one pass-level loss,
and eighteen ties. The registered failure rule therefore blocks making this cap
a default.

The run changed retrieval only. It copied the previously accepted pack, used the
same 20 questions, answerer, judge, prompts, top-50 cutoff, reference clock, and
source-time policy, and made no new ingestion or extraction calls. Hash checks
confirmed that the original DuckDB, vector, and lexical artifacts were unchanged.
The [registration](beam-100k-evidence-diversity-ablation-v1-registration.json)
and [machine-readable result](beam-100k-evidence-diversity-ablation-v1-results.json)
bind the protocol and aggregate outcome.

## Paired outcome

| Measure | Baseline | Evidence cap | Delta |
| --- | ---: | ---: | ---: |
| Passed questions | 13/20 | 13/20 | 0 |
| Mean rubric score | 0.56750 | 0.54833 | -0.01917 |

The event-ordering question `100K_0_q5_event_ordering` moved from 0.30 and fail
to 0.50 and pass. The instruction-following question
`100K_0_q9_instruction_following` moved from 0.50 and pass to 0.00 and fail.
The temporal arithmetic question improved from 0.50 to 1.00 without changing
its pass state. Information extraction and contradiction resolution also lost
partial credit.

## Finding

Evidence crowding is real, but a hard one-node cap is too lossy. Several derived
nodes can cite one source while retaining different useful details. For q9, the
baseline top 50 included a complete versioned dependency list. The cap kept an
earlier, less complete sibling from the same evidence event and removed the list,
so the answer model responded to an unrelated formatting memory.

The same representation defect explains why the cap did not fix the remaining
temporal failure. The source event contains the January 15 and March 15 dates,
but its highest-ranked representative is a short extracted entity. Selecting
more evidence groups cannot recover details that the chosen representatives omit.

The next design should make source preservation explicit: route a relevant
evidence group through its best derived claim, but expose the direct source
passage with auditable score inheritance. That can retain extraction's routing
value without allowing either derived siblings or low-information entities to
consume the context budget. Any such policy remains opt-in until a newly
registered paired answer trial shows no regression and an untouched cohort
confirms the result.

## Scope

This is a tuned one-conversation development ablation over an already examined
pack. It is useful causal evidence about this retrieval policy, but it is not an
untouched confirmation, an official-scale BEAM result, or a market comparison.
