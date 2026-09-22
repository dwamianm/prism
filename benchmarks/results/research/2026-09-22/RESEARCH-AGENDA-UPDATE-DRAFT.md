# Pending agenda update from the opt-in study

Prepared 2026-09-22 while registered ingestion and repair trials remain active.
This is an interim evidence note. Apply the final findings to
`docs/RESEARCH-AGENDA.md` only after pending registrations finish validating its
frozen checksum. The canonical [study report](OPT-IN-SUCCESSOR-REPORT.md) retains
all complete comparisons, failures, costs and remaining work.

## Findings already supported by complete runs

The new matched DeepSeek LongMemEval-S production control scored 437/500.
Current unconditional episode routing scored 345/500 and the current reranker
284/500, with large source-retention losses. Their requested combinations did
not reverse those losses. Evidence augmentation and projection did not activate
on these direct-turn artifacts; unchanged-input answer variation must not be
presented as feature effects. Original query reformulation changed only two
contexts while adding retrieval latency.

A concrete reranker integration defect was identified and repaired in isolation:
the neural prefix and untouched tail previously competed on inconsistent score
scales. The repaired complete candidate scored 428/500. Its new primary control
failed on a truncated reader response, so that primary comparison is unavailable.
Its original-baseline secondary result does not support a production gain.
The repaired code has score-replay, installed-wheel and live-backend evidence;
that operational evidence does not substitute for answer confirmation.

The completed marginal-packing follow-up tied its matched control at 433/500.
Its multi-session and preference gains were offset by update and assistant
losses. Broad session penalties sharply reduced source completeness. Stop
tuning those fixed session-penalty variants on this inspected cohort.

The annotation-assisted diagnostic scored 473/500, but it uses labels and is
not deployable. It identifies an opportunity for answer-blind evidence selection.
All 886 annotated turns had reached the returned pool, while 94 were omitted
from packed context. Among four complete runs with identical baseline reader
requests, 58 questions failed every time and 25 varied. These results prioritize
source retention while also demonstrating why small hosted-model transitions
need confirmation.

Temporal-only failed closed and remains unscored. Temporal plus episode routing
completed at 343/500. Against episode routing, all six answer differences occurred
on unchanged inputs; the 32 accepted temporal changes produced no score
transition. Review of all four accepted-but-incorrect cases found incomplete
query coverage and event/date-binding questions. Keep the confirmed answer-blind
Ollama/Jev protocol unchanged; accepted operands do not establish complete query
coverage or resolve disputed dates automatically.

## Current development work and promotion gate

Two new fixed research policies are registered: preserve the original strongest
ordinary multi-path anchor while assigning reranker scores; and merge existing
candidate signals from recorded alternate queries before scoring. Each requires
all 500 control replays, a positive whole-cohort source gate, and complete new
paired answer arms before an answer-quality conclusion. Their results remain
pending here. Neither adds a public default or replaces a failed trial.

Finish the separate fresh-ingestion control, supersedence, QA pairing, surprise
gating and exploratory full-feature arms. Then execute the registered Banking,
EventQA, Conflict and Detective sequence. Report inapplicable mechanisms and
failures explicitly. The failed temporal individual makes a best-individual
combination unavailable under the frozen rule; selecting a successful subset
would change that rule after outcomes.

No studied combination currently qualifies as a new default. A future positive
development result must survive an independently audited untouched cohort with
matched inputs, models, prompts, scoring and budgets, plus relevant backend,
restart, isolation and installed-package checks. Any proposed default change
still requires the user's separate explicit review. Product alignment/Jev stays
an explicit caller-selected pair and review workflow, outside retrieval flags.

This study does not establish a competitive leadership claim. Such a claim still
requires a named matched competitor, untouched source families, complete failure
accounting and disclosed ingestion/retrieval/model costs.
