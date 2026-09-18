# LongMemEval-S compact packing v1

**Decision:** reject direct compact packing as a default. It fit substantially
more records and improved aggregate source coverage, but failed the registered
no-loss confirmation gate.

## Protocol

The trial reopened clone-on-write copies of the 500 frozen PRME baseline packs.
For every question it required the current product path to reproduce the saved
auditable context byte for byte and the saved candidate IDs and scores exactly.
It then changed only `PackingConfig.context_format` from `auditable` to
`compact`, retained the 4,096-token budget and balanced ordering, and measured
source retention after both contexts were finalized.

The ten-question pilot and 119-question development split were used to select
the arm. The separately registered 381-question split was bound to the passing
development-result hash before evaluation. These partitions have appeared in
earlier PRME studies, so this is confirmation within the frozen LongMemEval-S
workload rather than an independent benchmark holdout.

## Development result

The candidate passed all registered development gates over 111 answerable
questions:

- complete labeled-turn coverage: 102 control, 103 compact;
- mean labeled-turn recall: 0.95195 control, 0.95646 compact;
- per-question turn-recall losses: 0;
- complete required-session coverage: 109 in both arms;
- median packed records: 24 control, 38 compact;
- replay failures and budget violations: 0.

Result identity:
`08a6b5f97b61e3d92afe206c0e94c817504cc93c621929b96051ddc6499d8d10`.

## Confirmation result

The candidate failed the registered confirmation gates over 359 answerable
questions:

- complete labeled-turn coverage: 301 control, 306 compact;
- mean labeled-turn recall: 0.90311 control, 0.90255 compact;
- turn-recall wins/losses/ties: 13 / 6 / 340;
- complete required-session coverage: 328 control, 337 compact;
- session-recall wins/losses/ties: 11 / 0 / 348;
- median packed records: 24 control, 38 compact;
- replay failures and budget violations: 0.

Four losses were single-session assistant questions, one was a multi-session
question, and one was a single-session user question. Their compact bundles
contained 13 to 19 more records while omitting one or two records retained by
the control. Compact serialization changes per-record token cost, which also
changes balanced density ordering and admission. More records therefore did not
guarantee a superset of the control evidence.

Result identity:
`12fe4077bca87ed993959c08d12e0e1597f0fa2c73f7ded4880a18701b3bcde2`.

## Next experiment

Test a monotonic compact policy that first preserves every memory selected by
the auditable control and then spends recovered serialization space on
additional candidates. Require exact control-record inclusion, exact budget
adherence, zero per-question source losses, and a strict coverage gain before
running answer-quality evaluation. Direct compact packing remains opt-in and
must not become the default from these results.
