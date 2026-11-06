# Review: issue #446, relation routing on the mid tier

## Review Scope

- Base: `main` at `41005d66`. Reviewed snapshot: `fcebd9c00658a4db4ad72c1b6c8ea59e8ac2dc0f36af7891cfea4147b5241d9a`
  (the first version of the section and the first four result files).
- Tier M from `review-plan`: 7 files, no SECURITY, DATA, INFRA, PERSISTED_FIELD or NEW_SYMBOLS predicate. Semantic
  check: a benchmark record and committed result JSON, no code, so no risk flag applies.
- Roster: core reviewer (Sonnet; checklists 1 correctness, 3 quality and 4 patterns merged) and adversarial
  reviewer (Opus; checklist 8). Not applicable: 2 security, 5 redundancy, 6 wiring, 7 data safety.
- Both reports completed with `Status: COMPLETE` against the snapshot above.
- After the review I changed the record in response to it (below). The revised section and the new result files were
  checked by me against the raw gate rows; no second review round was run.

## Files Changed

- `BENCHMARKS.md`: new section "Relation routing, mid tier (#446)".
- `benchmarks/results/research/2026-11-05/mid-tier/`: the three run reports (`baseline-41005d66`,
  `routing-41005d66`, `defaults-on-routing-packs`) and the two comparisons (`routing-same-packs`,
  `routing-cross-build`), each as JSON and Markdown, unedited harness output.

## Approach Summary

The mid tier ran with the defaults at `41005d66` and with `enable_relation_routing=true`. The harness refused
`--reuse-build` for the option, so the routing side built its own packs from the extraction cache (0 live calls).
After the review, the defaults were gated over the routing side's packs, which gives a same-pack comparison; that is
the result the record reports, and the cross-build comparison is kept as the drift record.

## Must Fix

- M1 (core): "283 routed and 137 not routed" mixed the context count with the rank count. Resolved: the section now
  reports the same-pack pair, where ranks changed on 248 routed questions and no unrouted one.

## Should Fix

- S1 (core): the routed-question changes were attributed to routing while the cross-build pair carries drift.
  Resolved by the same-pack pair (unrouted questions: 0 contexts and 0 ranks differ).
- S2 (core): drift was asserted, not measured, and the contrast with the 2026-10-19 exact replay was not noted.
  Resolved: the same-pack gate measures it, and the section notes the contrast.
- S3 (core): the verdict needed to be limited to evidence coverage and to mention the top-25 lean. Resolved.

## Consider

- C1 (core): the routing report prints "40 min 60 s". Noted in the section; the formatter bug is in follow-up #456.
- C2 (core): load averages came from samples, not the reports. The section says "sampled".
- C3 (core): "about eight times" is 7.7 to 9.3. Changed to "roughly eight to nine times".
- C4 (core): 955 is of 1,133 questions with evidence. Changed.
- C5 (core): the harness refusal deserves a tracking issue. Folded into follow-up #456.

## Security Audit Results

Not applicable: no code, configuration or credentials change. The committed reports carry no absolute or scratch
paths (checked with grep) and only public benchmark question identifiers.

## Pattern Consistency Assessment

The section follows the neighboring Mid tier section and the other records in `BENCHMARKS.md`: plain US English,
tables, commands, committed result paths. The result files mirror `benchmarks/results/research/2026-10-19/mid-tier/`.

## Redundancy Check

Not applicable (no new symbols).

## Wiring Findings

Not applicable (no runtime change).

## Data Safety and Read/Write Proof

Not applicable (no storage or persisted field change). The builds live in the ignored `data/mid-tier-v1/builds/`.

## Break Scenarios (adversarial)

Pre-mortem headline (verbatim): "Routing changed almost nothing" was true of the 4K packed bundle on third-person
LoCoMo. But the record left out that routed questions lost their raw evidence turn from the top 5 on 52 questions
and gained it on none, and that the first-person, owner-seeded shape the hosted service uses was tested on 4
questions at ceiling.

| # | Scenario | Likelihood / impact | Label | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | The verdict leaves out a one-way loss at the top of the ranking on routed questions | Medium / medium | Newly introduced (omission in the record) | Fix now | Added the top 1, 3, 5, 10 and 25 counts on the same packs, said the rank measures count only source turns, and limited the verdict to the 4K packed context. The claim-aware rank measure goes to follow-up #457. |
| 2 | The sample does not exercise first-person, owner-seeded routing as the hosted service uses it | Medium / medium to high | Newly introduced (omission in the record) | Fix now (caveat), Follow-up (measurement) | Added "What this sample does not test" with the verified numbers (0 of 1,540 first person, 106 of 405 with a people word, 609 of 3,912 claims mapped). The measurement is follow-up #457. |
| 3 | `CLAUDE.md` promises `--reuse-build` for retrieval-only options, the harness refuses them | High / low to medium | Pre-existing | Follow-up | Changing the harness is a code change this issue rules out. Follow-up #456. |
| 4 | Drift on routed questions is not bounded by the unrouted estimate (the walk breaks ties by node id) | Low / low | Newly introduced (measurement limit) | Accept | The same-pack pair now removes build drift from the result. What remains is that the routed effects carry this build's tie-breaks, which the section states. A second build would cost another 90 minutes for a low-impact question. |

Attacks that failed (from the adversarial report): routing does not act at build time; the source changes since the
tag do not touch the routing path; build drift does not explain the five packing gains.

## Follow-ups Raised

- #456: the mid tier refuses `--reuse-build` for retrieval-only top-level options (and prints "40 min 60 s").
- #457: relation routing's effect on first-person questions and at the top of the ranking is not measured at scale.

## Resolution Status

| Finding | Status |
|---|---|
| M1 | Resolved |
| S1, S2, S3 | Resolved |
| C1 to C5 | Resolved or folded into #456 |
| Adversarial 1, 2 | Fixed in the record; measurement in #457 |
| Adversarial 3 | Follow-up #456 |
| Adversarial 4 | Accepted, stated in the record |
