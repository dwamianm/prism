# Reader context format: acceptance criteria (2026-11-04)

**Status:** Evidence for dwamianm/prism#79. Every acceptance criterion is met. The format shipped
in #109 (opt-in), speaker labels in #190, the default flip in #177 and the ordering in #187. This
record checks the first criterion again on today's `main` and points at the evidence for the others.
No model was called and no paid API was used.

## Criteria

| Criterion | Evidence | Met |
|---|---|---|
| Evidence gate at 4K, with the ordering chosen in #81: LoCoMo averages at least 70 records per context and memory text is at least 80% of context tokens | The gate at the current defaults (rank fusion, reader format, balanced order) on `60c37730`: LoCoMo 84.5 records per context and 96.6% memory text, below. Balanced order has been the default since #187, and score order, the other candidate #81 weighed, also passes (77.6 records and 97% in `BENCHMARKS.md`, "Offline evidence gate") | Yes |
| Receipts still record every packed identity and replay the ranking exactly | `tests/test_reader_context.py`: `test_reader_receipt_is_version_14_and_records_every_packed_identity`, `test_reader_receipt_with_rank_assignment_is_version_14` and `test_engine_retrieval_renders_reader_lines_and_persists_the_receipt` (#109), which write version 14 receipts under weighted scoring. Receipts of the current defaults (version 22, rank fusion, folded text) are covered by `tests/test_fold_repeated_text.py` (`test_version_22_records_folding_and_round_trips`, `test_version_22_admits_session_context_packing`) and the replay tests in `tests/test_graph_seed_count.py` (`test_a_receipt_records_the_list_and_replays_its_ranking`, `test_a_retrieval_ranks_the_records_that_connect_the_names_and_replays_its_receipt`) | Yes |
| Tests cover default states omitted, non-default states tagged, citation references on request, no repeated date or speaker, and no ingestion timestamp in reader text | `tests/test_reader_context.py`: `test_default_states_and_audit_fields_are_omitted`, `test_every_default_state_combination_is_untagged`, `test_non_default_states_are_tagged`, `test_citation_references_only_on_request`, `test_date_and_speaker_the_text_begins_with_are_not_repeated`, `test_no_admission_or_creation_timestamp_in_reader_text` and `test_supersedence_windows_do_not_show_admission_times` | Yes |
| The paired answer run shows a LoCoMo gain whose 95% interval excludes zero before the default changes | The DeepSeek-track variant `prme-reader-rrf-sd06-rec` passed the epic #77 default-change rule against `prme@46647825` before #177 made the reader format the default: LoCoMo +15.19 points (95% interval +12.99 to +17.40) in the first pair and +15.71 (+13.51 to +17.92) in the confirmation, with the conversation-level intervals (+13.63 to +16.69 and +13.95 to +17.48) also excluding zero; LongMemEval-S +1.0 (-1.6 to +3.6) and +0.2 (-2.6 to +3.0). The four lines are in `ollama-deepseek-v4.1-flash-cloud-pair-verdicts.jsonl` | Yes, on the DeepSeek track |

The issue asked for the paired run with the 2026-09-23 GPT-5.4 reader, judge and prompts. #117,
opened by the owner, names #79 among the tickets that stopped at a paid GPT-5.4 run and moves every
such run to `deepseek-v4.1-flash:cloud` through Ollama, with default changes "now backed by the
recorded DeepSeek paired run instead of a paid one". The epic's rules in `CLAUDE.md` record the same
decision and still rule out paid API runs, so the #177 pairs are the run that gated the flip. No GPT-5.4 pair was answered
with the reader format, and DeepSeek scores are never compared with GPT-5.4 scores.

## Gate at the current defaults

```sh
uv run python -m benchmarks.diagnostics.product_packing gate \
  --lme-captures data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline --output <defaults.json>
```

Run on commit `60c37730`, no `--set`, 3,996-token budget. The summary the gate wrote beside its JSON
report is `reader-format-gate-defaults-4k.md` in this folder.

| | LoCoMo, 2026-09-23 run | LoCoMo, current defaults | LongMemEval-S, 2026-09-23 run | LongMemEval-S, current defaults |
|---|---:|---:|---:|---:|
| Records per context | 25.2 | 84.5 | 23.9 | 39.9 |
| Memory-text share of context tokens | 29% | 96.6% | 32% | 82.6% |
| Records without memory text | 337 | 0 | 109 | 0 |
| All annotated evidence packed | 983/1,536 (64.0%) | 1,290/1,536 (84.0%) | 403/470 (85.7%) | 445/470 (94.7%) |
| Multi-hop with all evidence packed | 45/282 (16.0%) | 140/282 (49.6%) | | |
| Projected accuracy | 64.0% | 77.6% | 86.0% | 91.4% |

These match the "current defaults" columns of the table in `BENCHMARKS.md` ("Offline evidence
gate"), measured after #187, so the alpha defaults turned on since then leave the gate's `store()`
packs where they were. Projected accuracy is a planning estimate, not an answer score.

## Still open elsewhere

- #81 is still open. The first criterion holds under either ordering it weighed.
- #106: extracted facts and summaries show their processing time as the event date when the source
  has none.
- #107: summaries and temporal-relation guidance still write node IDs and admission times into
  reader contexts.
