# Stable tie break (2026-11-12)

Two builds of the same chat gave contexts whose tied lines came in a different order. Between two replay builds of
`origin/main` (task 19, labels `rc-M` and `rc-M2`), 1 of 18 chat-v1 contexts and 3 of 12 member-v1 contexts differed,
and 0 of 7 defects-v1 contexts. That noise blurs every probe comparison. This change adds one opt-in key that orders
tied records by what they hold, so two builds give the same context.

## Cause

Context records (session, episode and evidence context, owner profile, claim statements, current-state observations)
inherit the score of the record that brought them in, so equal scores are common. Packing ordered records by
`(tier, -value, str(node.id))`, and node ids are random UUIDs drawn at build time. Example, chat-v1 probe
`mel-news`: the fact "A girl named Ivy." (13:30) and the note "Congratulations, Aunt Dana!..." (13:32) both scored
0.521272727275763 and swapped places between the two main builds.

## Sorts whose last key is the node id

Found with `grep` over `src/prme/retrieval/` on `origin/main` e10ca299:

- `scoring.py:1055` `score_and_rank` (`-composite_score, -path_score, str(id)`).
- `session_context.py:150` and `:269`.
- `reranker.py:163` and `:209`.
- `owner_profile.py:76` (`_newest_first`), `:225` and `:245`.
- `episode_context.py:134`, `:142` and `:195`.
- `evidence_context.py:251` and `:361`.
- `claim_statements.py:125`.
- `current_state.py:74` and `:201`.
- `relation_walk.py:427` (cut at `ROUTE_MAX_CANDIDATES`).
- `packing.py:665` `priority`, which sets the order of the lines in the context.
- Insertion order, not a sort: `candidates.py:356` (graph post-filter by confidence, then `created_at`).

Receipt replay sorts by `str(node_id)` too: `models/relevance.py:546` (`replay_ranking`, checked by the validator at
`:487`), `models/privacy_receipts.py:82` and `retrieval/learning.py:63`. Changing the ranked result sorts would
need a new receipt field so replay can reproduce the order. Cycle 1 therefore changes only the packing priority,
which receipts do not replay. Cycle 2 (results-list sorts plus a receipt `tie_rank`) was planned only if bar (a)
failed. It passed, so cycle 2 was not needed.

## Change (commit 7c3938f4)

- `packing.stable_tie_break` (`PRME_PACKING__STABLE_TIE_BREAK`), off by default. It is omitted from dumps and
  receipts when off. It is not tagged `[HYPOTHESIS]`, because it has no tuned value.
- One helper, `tie_key(node, stable)` in `src/prme/retrieval/models.py`. Off, it returns `(str(node.id),)`, the old
  key. On, it returns newest stated time first (`event_time`, else `created_at`), then the text (NFKC, casefold,
  spaces collapsed), then `created_at`, then the node id. Every key but the id is the same in every build of the
  same memories. A time without a zone is read as UTC.
- `pack_context`'s `priority` ends with `*tie_key(candidate.node, config.stable_tie_break)`. Records of higher
  priority never move. Required records (`required_positions`) keep their own order.
- `tests/test_stable_tie_break.py`: 6 tests (off keeps id order, on gives one order whatever ids a build drew, the
  text and stored-time tie cuts, higher priority never moves, naive time read as UTC, off by default and omitted
  from dumps).
- `documentation/configuration.md` and `CHANGELOG.md` rows. `DERIVATION_VERSION` is unchanged: nothing derived
  changes.

## Bars

Setup: worktree `~/Sites/prism-tiebreak` on `origin/main` e10ca299 plus the change, after `uv sync --all-extras`.
Model `deepseek-v4.1-flash:cloud`, manifest digest `e04da138`, Ollama 0.40.16. All probe builds are replays
(`PRME_BENCH_CACHE_ONLY=1`): 0 model calls on every build. Providers blocked on every run.

**(a) Same context on two rebuilds, setting on.** Two fresh replay builds per probe with
`--set packing.stable_tie_break=true` (labels `tb-1`, `tb-2`; member with `--speaker --set
enable_speaker_grounding=true`). Met.

| Probe | Contexts that differ, tb-1 vs tb-2 | Main vs main (rc-M vs rc-M2) |
|---|---|---|
| defects-v1 | 0/7 | 0/7 |
| chat-v1 | 0/18 | 1/18 |
| member-v1 | 0/12 | 3/12 |

**(b) Top 3 and top 5 not below origin/main.** `chat_probe compare rc-M tb-1`. Met. Every probe answer rank is the
same on both sides, and so is the count of user claims attached to the named owner.

| Probe | Top 3, main / on | Top 5, main / on | Owner claims, main / on |
|---|---|---|---|
| defects-v1 | 7/7 / 7/7 | 7/7 / 7/7 | 12 / 12 |
| chat-v1 | 14/18 / 14/18 | 15/18 / 15/18 | 35 / 35 |
| member-v1 | 10/12 / 10/12 | 11/12 / 11/12 | 7 / 7 |

The setting moves tied lines in 4 chat-v1 contexts (`coffee`, `job-now`, `mel-news`, `niece-born`) and 4 member-v1
contexts (`coworker`, `dependents-first-person`, `diagnosis-first-person`, `diagnosis`) against `rc-M`. Each
difference is a tied line moved within its tier. No line is added or dropped.

**(c) Offline evidence gate.** `product_packing gate` with the regenerated LongMemEval-S controls and
`--set packing.stable_tie_break=true`, then `gate-compare` against the task 19 off side
(`data/read-cost-v1/gate/off.json`, origin/main e10ca299, current defaults). Met. Every row of the first table is
+0.0 pp, interval +0.0 to +0.0, 0 won and 0 lost (1,540 LoCoMo and 500 LongMemEval-S questions), category rows
included. No question lost or gained all its evidence among the candidates.

The context digest differs on 430 of 2,040 questions (LoCoMo 419, LongMemEval-S 11): tied records changed places.
On 6 LoCoMo questions the context sits at the 4,096-token budget edge, and the new order changes which last record
fits (`conv-26-q0125` and `conv-30-q0055` one record more, `conv-49-q0074` one less, three with the same count and
1 to 10 tokens more). Evidence and projected accuracy are the same on all 6. Every other field is the same on every
question except timing. The two sides ran at different times under different load (one-minute load average 20.7
to 22.6 before, 6.0 to 5.8 after), so the gate's latency is not a clean comparison.

**(d) Setting off gives the outputs of origin/main.** The `rc-M` packs were copied and measured once with clean
`origin/main` code (`sp-main`) and once with this branch, setting off (`sp-off`). Met.

- Contexts: 37/37 byte-identical (7, 18, 12).
- Stored receipts (request id masked): 37/37 identical once `execution.features.source_files_sha256` is masked.
  Unmasked, 0/37 match. The only keys that differ in that map are `config`, `models` and `packing`: the hashes of
  the three source files this change edits. Every receipt from any code change differs there. Log fields 37/37.

**(e) Tests.** Providers blocked. `test_stable_tie_break`, `test_determinism_rebuild`, `test_config_audit`,
`test_balanced_packing`, `test_packing_composition`, `test_packing_line_counts`, `test_packing_order_option`,
`test_packing_reader`, `test_packing_receipt_compatibility`, `test_packing_regression`, `test_privacy_receipts`,
`test_rank_fusion_session_context`, `test_session_context_packing`, `test_session_context`: 458 passed, 11 skipped
(PostgreSQL), 1 failed. The failure was
`test_privacy_receipts.py::test_CL06_component_minimized_receipt_labels_and_citations[directory]`. It passed on its
own once, and the whole file then passed three times in a row (2 passed, 1 skipped each time). It reads as a
flaky test under load, not this change. The change touches neither privacy receipts nor their replay. Met, with that
note. `ruff check` is clean on the changed files.

## Verdict

All five bars met in cycle 1. Two builds of one chat now give the same context when the setting is on. With it off,
nothing changes.

## Next step

On `address-first-memory`, turn the setting on with `memory_mode="address_first"`: add
`("stable_tie_break", True)` to the packing tuple in `PRMEConfig._apply_memory_mode` (next to
`("collapse_identical_text", True)`, `src/prme/config.py` about line 1804). Also name it in the `memory_mode`
description. Then rerun bar (a) there without `--set`.

Not done, and not needed for bar (a): the results-list sorts listed above still end with the node id. A tie there
can still swap two results in the ranked list (not in the context) between builds. Fixing that needs a receipt
field so replay reproduces the order (cycle 2 plan).

Reports (ignored): `data/chat-probe-v1/<conversation>/tb-1`, `tb-2`; `data/tie-break-v1/gate/` (`on`, `compare`).
The gate's off side is `data/read-cost-v1/gate/off.json` (task 19, origin/main e10ca299).
