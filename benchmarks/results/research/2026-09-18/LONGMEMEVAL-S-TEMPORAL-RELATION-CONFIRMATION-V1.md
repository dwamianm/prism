# LongMemEval-S temporal relation confirmation v1

**Decision:** abort before Jev, reader, or judge. The answer-blind resolver
completed 99 of 104 questions with zero earlier failures, then returned one
response outside the frozen schema. This run provides no answer-quality
evidence and does not change the passing development result or PRME defaults.

## Failure

The terminal question was `gpt4_70e84552_abs`. DeepSeek completed the request,
but encoded a missing source as `evidence_id=unsupported` inside an `order`
operand. The registered schema requires a UUID for every operand and requires
unsupported cases to use the top-level `unsupported` operation, so the runner
failed closed. Five questions received no valid saved generation: the failed
question plus four that followed it.

The resolver never deserialized reference answers. Jev, paired packing, reader,
and judge did not start. Retrying inside this registration would violate its
zero-failure and no-selective-retry rules.

## Durable evidence

- Successful resolver generations: 99/104
- Terminal failed questions: 1
- Unattempted questions: 4
- Frozen resolver-state SHA-256: `91dfbbabfa6999eccc485bca8b78bb1a07e71726ab2603964fb4a21b689d0630`
- Frozen run-log SHA-256: `3399cdfd204d5aa766295d266da645c5373f4227bbc58d8c080ffeb078bdd4df`
- Aborted result identity: `a33cb85af78dc205e2e04b7f534005e0edebf30b34e6923a45d9f920980716fd`

The frozen external state and log remain under
`data/benchmarks/longmemeval-s-temporal-relation-confirmation-v1/`.

## Next experiment

A new registration may predeclare one generic, answer-blind schema-repair pass
and may import the 99 valid responses only when prompt, model digest, response
checksum, original registration, and frozen state all match. Every imported and
repair attempt must remain auditable. This change addresses provider protocol
reliability; it cannot inspect answer labels or reinterpret the completed Jev
development confirmation.
