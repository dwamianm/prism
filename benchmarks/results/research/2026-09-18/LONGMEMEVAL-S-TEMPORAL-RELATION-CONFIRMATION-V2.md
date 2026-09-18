# LongMemEval-S temporal relation confirmation v2

**Decision:** advance the provenance-linked temporal relation policy to product
implementation behind an explicit provider configuration. On the disjoint
104-question temporal confirmation cohort, it improved paired answer accuracy
from 50/104 to 60/104 with ten wins, zero losses, and zero reader or judge
failures. This confirms the development gain under the registered gate; it does
not establish an official LongMemEval score or general memory-system
leadership.

## Frozen method

The cohort contains every temporal-reasoning question in the 381-question
complement of the frozen 119-question development split. The resolver could
select only exact record UUIDs and verbatim quotes from PRME's existing
4,096-token auditable context. Code validated citations and temporal licenses,
then performed date and duration arithmetic deterministically. Jev applied the
development-fixed 0.85 minimum operand probability. Accepted hints could retain
only control records, had to keep every cited record, used the same token
budget, and could evict only uncited whole records.

DeepSeek v4.1 Flash generated answers at temperature zero in counterbalanced
arm order. The registered GPT-OSS 120B judge read references only after all 208
reader predictions were complete. Byte-identical arm pairs shared one model
call, yielding 126 reader and 126 judge calls for 208 logical predictions in
each stage.

## Resolver recovery disclosure

Confirmation v1 stopped before Jev at 99/104 resolver generations because one
complete DeepSeek response used `evidence_id=unsupported` where the frozen
schema required a UUID. No answer reference had been deserialized. The aborted
state and report were preserved rather than edited or retried.

Before v2 touched the five missing questions, a new registration bound the v1
registration and state hashes, imported only the 99 schema-valid responses by
exact prompt/model/response identity, and froze one generic structured-output
repair. V2 made four fresh original calls and three repair calls: the preserved
v1 failure plus two newly schema-invalid originals. All repaired successfully;
104/104 resolver outputs completed with zero terminal failures and produced 35
deterministically validated relations.

The registered reader CLI later passed an unused resolver-input argument and
stopped before state creation or model calls. A committed results-scoped
launcher called the exact frozen `run_reader` function with its declared
parameters. Its first launch also stopped before state creation because the
repository was absent from `sys.path`; setting `PYTHONPATH` fixed module
resolution. Neither orchestration failure caused a model retry or changed the
registered evaluation functions or inputs.

## Paired answer result

| Metric | Auditable control | Temporal relation |
|---|---:|---:|
| Correct | 50/104 | 60/104 |
| Accuracy | 48.08% | 57.69% |
| Paired wins / losses / ties | — | 10 / 0 / 94 |

The absolute gain is 9.62 percentage points. Among the 22 Jev-accepted changed
questions, the control scored 11/22 and the candidate scored 21/22, with ten
wins, zero losses, and twelve ties. The five abstention controls stayed 5/5 in
both arms; the 99 ordinary temporal questions improved from 45 to 55 correct.

The ten gains covered elapsed-since-query and elapsed-between questions such as
receiving a chandelier, Maundy Thursday attendance, racket delivery, sculpting
classes, a networking event, a photography workshop, a 5K run, reading a
magazine issue, a house search, and ordering a birthday gift. The sole incorrect
accepted-hint answer was also incorrect in the control: both arms used January
19 to April 10 and answered about 11.6 weeks where the benchmark reference says
15 weeks.

Exactly 22 contexts changed and 82 remained byte-identical. Packing dropped 22
uncited records in total, at most two for any question. Aggregate memory context
fell from 413,367 to 412,535 tokens while retaining all cited records.

## Independent source-session audit

After judging completed, a separate audit mapped every cited node back to the
benchmark's gold source sessions:

| Set | Questions with only gold citations | Gold sessions cited |
|---|---:|---:|
| All validated relations | 35/35 | 56/68 |
| Jev-accepted relations | 22/22 | 36/39 |

This supports the alignment precision of the accepted relations. It does not
prove that every relation or benchmark reference is semantically correct.
Alignment audit identity:
`b7628189942318ec1d4f11fab1f1fe9681f94b57de1c9b1ff7d26936ad6d4b94`.

## Artifact verification

- Registration SHA-256: `cde14d90c05741a09a65beb6f7e4a76e6af44102f092bf2cff340df9372fc7b7`
- Resolver result identity: `1e8d4ec2a1acf17fff6e473b8ec2102820782cbb0cf6df44e80037fd8db5f878`
- Jev result identity: `577f96ceb2961c71f34f8b94ee287b5980f3548e7b9326f43eda755c53ff261b`
- Paired-input identity: `ab2ecae6109cccac6b2934f3064a5ee36b1f4a58d59716e6db181269571be869`
- Reader result identity: `8c0cd7998e19255aa921c7824ae687b1cbb34c8c62eb22c741579eb59d4162d7`
- Execution SHA-256: `193c5c8838b75e7686e79d16dae518c8067eaa680965f62c3ffd4c5a0376dc9d`
- Final result identity: `ae34bd75b6ba8aee3cbf51ec1dbc312eb556a193a3590a6b1a6b6dbfee158d49`

Raw resolver, Jev, paired-context, reader, judge, and retry artifacts remain
under `data/benchmarks/longmemeval-s-temporal-relation-confirmation-v2/`.

## Claim boundary and implementation gate

The confirmation uses a custom calibrated judge, one hosted generation per
unique prompt, and a temporal subset from the same benchmark dataset as
development. Ollama cloud tags do not prove immutable remote weights. Version 2
adapted its schema-repair method after an answer-blind infrastructure failure,
though before Jev, answers, references, or judgments. These constraints prevent
a universal or leaderboard claim.

Implement the capability as a provider-agnostic optional temporal-relation
stage with strict typed output, exact provenance validation, deterministic
arithmetic, bounded schema repair, configurable semantic gating, and receipt
fields for every selection and computation. Preserve PRME's local-first default.
Before enabling it by default, run product-path parity tests, the frozen full
500-question answer evaluation, non-temporal regression checks, latency/cost
measurement, and an offline or local-provider fallback evaluation.
