# Marginal packing development study

Date: 2026-09-22. Branch: `research/opt-in-interactions-2026-09-22`.
All 500 LongMemEval-S histories are examined development data. No production
configuration, source or default changes; no held-out quality claim.

The complete unconditional episode arm lost source coverage and answers. This
research implementation removes the hard episode priority tier and keeps each
record's own relevance. It preserves instruction/pin priorities, one leading
multi-path anchor, whole source text and the ordinary serializer under the same
3,996-token effective budget. A bounded query-dependent episode bonus and a
penalty for repeatedly selecting the same session were tested separately and
together. No labels, reference answers or question categories enter packing.

The [registration](opt-in-marginal-packing-v2-registration.json) froze all three
policies before their source evaluation. All 500 baseline replays, candidate
snapshots and policy contexts completed and authenticated. No hosted answer
call was made in this source assay. Six authored policy tests passed before
registration; the full replay also checked original IDs/scores/contexts and
exact budget accounting.

| Policy: session penalty / episode bonus | Complete source sets / 470 | Mean source fraction | Difference in complete sets, points [95% interval] |
|---|---:|---:|---:|
| Production control | 403 | 0.914645 | Reference |
| 0.25 / 0 | 300 | 0.750071 | −21.91 [−25.96, −17.87] |
| 0 / 0.10 | 407 | 0.916241 | +0.85 [−0.21, +2.13] |
| 0.25 / 0.10 | 319 | 0.779858 | −17.87 [−21.70, −14.26] |

The [source result](opt-in-marginal-packing-v2-source-result.json) includes every
category, source gain/loss, context use and measured packing time. The
[paired intervals](opt-in-marginal-source-intervals-v1.json) are post hoc,
unadjusted 10,000-draw question-bootstrap intervals with seed 20260922. They
describe source retention, not answer correctness, semantic sufficiency or
independent confirmation. There are 500 total cases and 470 with applicable
source annotations; abstention cases remain in the answer cohort.

The repeated-session penalty is rejected at the registered source gate. Its
108 complete-source losses and five gains span every category. Combining the
penalty with the bounded episode bonus still loses 88 complete sets and gains
four. Broad session diversity is not supported as a remedy on this cohort;
necessary evidence often occupies multiple records in the same conversation.
Both negative policies remain in the record and receive no answer trial under
the original gate.

Only `marginal_episode_010` qualifies. It gains six complete source sets and
loses two, with category changes +2 multi-session, +1 temporal, +1 update and
zero elsewhere. Its mean-source-fraction gain is 0.001596, interval
[−0.003334, +0.006915]. Three complete-source gains occur in the baseline's
44 packing-omission errors; three occur in already-correct cases. Its two
complete-source losses also occur in already-correct cases. Five cases lose
some source turns, including three baseline packing errors that were already
incomplete. Every regression remains reported.

The selected policy changes 495/500 contexts. Mean memory use is 3,961.962
tokens, p50 3,971 and p95 3,995. Candidate packing p50/p95 is 0.618/2.403 seconds
under shared host load. The source-assay control's packing-time field is a zero
placeholder for a reused context, not a valid latency comparator or speed gain.

Both registered gates—greater complete-source count and greater mean source
fraction—passed. A new [500-case control repeat](opt-in-marginal-packing-v2-reader-control-registration.json)
and [500-case candidate](opt-in-marginal-packing-v2-reader-candidate-registration.json)
were frozen before inference. Their primary answer comparison uses the same
Ollama DeepSeek model, official prompts/scoring, 8,192/64 output limits and
memory budget; the original 437/500 baseline remains intact. Both full arms
must finish and authenticate before a quality comparison.

Under the [released-lane scheduling amendment](opt-in-answer-lane-scheduling-v1.json),
answers queue after the reranker repair in the first historical lane once its
six original arms settle and its coordinator exits. The completed source
worker was stopped only while idle after all contexts were reauthenticated,
as recorded in the [handoff](marginal-answer-lane-v1-handoff.json). No source or
reader case was interrupted; provider capacity, contexts, selection and scoring
are unchanged. Answer-quality results are pending. The small source advantage
does not authorize a default change.
