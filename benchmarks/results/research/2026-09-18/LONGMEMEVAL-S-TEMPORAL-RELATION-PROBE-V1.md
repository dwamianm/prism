# LongMemEval-S provenance-linked temporal relation probe

**Decision:** advance a Jev-gated temporal relation hint to a paired answer
trial. Do not integrate it yet. On the observed development cohort, the resolver
usually selected the labeled source sessions, deterministic validation produced
14 relations, and a typed Jev gate removed the one incorrect relation at a
development-calibrated threshold of 0.85.

## Design

The failed chronological-view trial showed that making dates more visible did
not make the reader reliably subtract or order them. This probe moved the hard
step into an auditable pipeline:

1. DeepSeek v4.1 Flash selected minimal bundle records and copied exact quotes
   for each event named by the query. It was forbidden to answer or calculate.
2. Code required bundle-local UUIDs, verbatim quotes, licensed same-day phrases,
   supported temporal expressions, and complete operands.
3. Code resolved accepted dates and performed calendar arithmetic or ordering.
4. Jev evaluated each event-to-time link independently. A relation's gate score
   was the minimum probability across its operands.

The generated relation retains every source UUID and labels itself inferred. A
provider cannot invent an uncited record or bypass deterministic date parsing.
It can still align a real date to the wrong event, which is why the Jev gate is
separate.

## Resolver result

The cohort contains all 29 `temporal-reasoning` questions in the already
observed development split.

| Metric | Result |
|---|---:|
| Questions | 29 |
| Questions whose citations were all in labeled source sessions | 26 |
| Labeled source sessions cited | 47/56 |
| Deterministically validated relations | 14/29 |
| Unsupported or rejected without a relation | 15/29 |
| Correct validated relations under calibrated GPT-OSS judge | 13/14 |

The sole incorrect relation used the January 28 laptop pre-order date for a
question asking when the laptop was obtained; the same evidence also stated
that it arrived February 25. This is precisely the event-versus-milestone
distinction that exact quote validation alone cannot prove.

The validator also rejected `just` as a same-day license after observing a
record that said an event had "just" happened yesterday. Same-day event-time
resolution now requires an explicit phrase such as `today`, `tonight`, or
`this morning`.

Probe result identity:
`45dbbc04aa6b3b9b3f3efccc2218a852a00f32ebdba4b40af6802b4465aa9f4b`.

## Jev gate calibration

Jev 1.13.0 received only the question, event name, verbatim evidence quote, and
proposed time expression. It answered one typed Noul per operand. No arithmetic
or free-form parsing was delegated to Jev.

| Minimum operand probability | Accepted | Correct | False accepts | Precision | Recall of correct relations |
|---:|---:|---:|---:|---:|---:|
| 0.75 | 14 | 13 | 1 | 92.86% | 100.00% |
| 0.80 | 12 | 11 | 1 | 91.67% | 84.62% |
| **0.85** | **9** | **9** | **0** | **100.00%** | **69.23%** |
| 0.90 | 6 | 6 | 0 | 100.00% | 46.15% |
| 0.95 | 1 | 1 | 0 | 100.00% | 7.69% |

The 0.85 threshold used 7,660 input and 488 output tokens over 14 calls. It is a
development-calibrated threshold, not a universal precision guarantee. Jev
correctly scored the unsafe laptop pre-order link below the gate.

Jev result identity:
`03f26f6e3ee7759e2b5bc0863e080a30b0e7d824fb097149e083fb84d493fc9e`.

## Next gate

The nine accepted relations include four questions the fresh auditable reader
answered incorrectly and five it answered correctly. The next trial will add
the accepted relation only to those nine contexts. It will keep the same token
budget, reserve the cited records, and evict only uncited records that no longer
fit. The candidate must improve paired answers with more wins than losses and
zero provider failures before a fresh confirmation is allowed.

Even a passing answer trial cannot promote the feature because the event
resolver, threshold, and cohort have all been observed. A confirmation must run
fresh resolution, deterministic validation, Jev gating, and paired answers on a
separately frozen cohort. Until that passes, this remains benchmark-only code.

The subsequent [paired answer trial](LONGMEMEVAL-S-TEMPORAL-RELATION-ANSWER-DEV-V1.md)
passed every registered development gate: 21/29 versus 17/29, with four wins
and zero losses. The policy therefore advances to a disjoint confirmation; it
is still not integrated or exposed as product behavior.
