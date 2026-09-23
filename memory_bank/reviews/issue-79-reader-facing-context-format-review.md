# Code Review: issue-79-reader-facing-context-format

## Files Changed
- `src/prme/retrieval/config.py`: `PackingConfig.context_format` accepts `"reader"`; new
  `context_citations: bool = False`, rejected unless the format is `"reader"`.
- `src/prme/retrieval/models.py`: `MemoryBundle.context_format` accepts `"reader"`; new
  `MemoryBundle.ensure_citable()` raises for a nonempty reader bundle without references.
- `src/prme/retrieval/packing.py`: reader renderer (`_render_reader_entry`, `reader_text`,
  `_reader_time_label`, `_text_states_date` with a cached `_date_pattern`, `_reader_tags`), the
  reader header, reader packing limited to the stored text (FULL or the identical PROSE, nonblank
  content), and one `context_refs` mapping that replaces the separate citations flag.
- `src/prme/models/relevance.py`: receipt schema version 14 for reader retrievals. Versions below
  14 omit `context_citations` from their canonical JSON and mean it was off; version 14 must state
  it. Versions below 14 cannot claim the reader format. Ordering, guidance, format and episode
  settings are now required from the version that introduced them onward instead of taking current
  defaults. Error messages no longer end at version 12.
- `src/prme/retrieval/credit.py`: `ablate_context` re-renders in the bundle's own format and
  references (this also fixes compact bundles, which were re-rendered as auditable JSON).
- `src/prme/retrieval/context_formatter.py`, `src/prme/retrieval/pipeline.py`: temporal guidance for
  reader contexts names the bracketed date instead of the missing `event_time` field. Other formats
  keep their exact bytes.
- `src/prme/retrieval/answerability.py`, `src/prme/retrieval/claim_verification.py`: raise for an
  uncited reader bundle instead of silently abstaining or returning no evidence; answerability
  accepts `[m3]` as well as `m3` for reader bundles.
- `src/prme/mcp/server.py`: `memory_retrieve` with `include_context` also returns
  `context_references` when the bundle has any.
- `benchmarks/diagnostics/product_packing.py`: the gate's text-presence check also accepts the
  reader encoding.
- Tests: new `tests/test_reader_context.py`; one MCP test in `tests/test_mcp.py`.
- Docs: `docs/PACKING.md`, `docs/RFC-0006-Retrieval-Cost-and-Context-Efficiency.md`,
  `docs/INTEGRATION.md`, `docs/ANSWERABILITY.md`, `docs/CLAIM-VERIFICATION.md`, `docs/HTTP-API.md`,
  `docs/RFC-0005-Hybrid-Retrieval-Pipeline.md`, `documentation/configuration.md`, `AGENTS.md`,
  `CHANGELOG.md`.

## Approach Summary
The reader format is a third renderer behind the existing `PackingConfig.context_format` switch,
so it goes through the same budget accounting, ordering, receipts and temporal repack as the other
two formats. Each record becomes `- [m3] [2023-06-09 19:55] [superseded] "text"`: the reference
only with `context_citations=True`, the `event_time` in UTC (left out when the text already begins
with that date), a validity range only for a caller-supplied closed window, tags for every
non-default lifecycle and epistemic state, and the complete stored text as a JSON string with
U+0085, U+2028 and U+2029 also escaped. The renderer prints no ID, type, scope, source type or
representation; the full record stays in `MemoryBundle.sections` and the receipt. Defaults are
unchanged (`auditable`, `balanced`).

Alternatives considered:
- `format_for_llm` in `context_formatter.py`: rejected. It formats `response.results`, not the
  budgeted bundle; it falls back to `created_at` when `event_time` is missing
  (`context_formatter.py:806-808`); and it prints `source_type` (`_provenance_label`, 248-264).
  The issue forbids both.
- Dropping fields from `compact`: rejected. Its documented contract keeps every metadata field
  (`docs/PACKING.md`, RFC-0006 compact section), and v7 to v13 receipts already record compact
  contexts under that meaning.
- Unquoted, whitespace-collapsed text: rejected. It loses text, fails the gate's verbatim check
  (`product_packing.py:517-520`), and lets stored text forge a leading tag.
- A `"reader_cited"` format value instead of a boolean: considered (it avoids the receipt pop and
  default logic). Kept the boolean because the issue treats citations as a caller request and it
  reads better as an environment setting. Either way the reader format needs a new receipt version,
  as balanced (v5) and compact (v7) did.
- Speaker labels from `metadata["source_speaker"]`: rejected. That key is written by one benchmark
  adapter and is not a product contract. #84 adds the product speaker field and owns rendering it.

## Must Fix (all resolved)
- **Blank records packed as a text-free STRUCTURED line that prints the node type** (Agents 1, 3,
  4, 5). The skip tested the rendered text, and `"type: fact, content: "` is not blank. Resolved:
  the reader checks the node content and packs only FULL or PROSE; STRUCTURED is never shorter than
  FULL. Tested with the default `min_fidelity` and a roomy budget.
- **Unicode line separators could forge record lines** (Agent 2). `json.dumps(ensure_ascii=False)`
  leaves U+0085, U+2028 and U+2029 raw, and LongMemEval-S contains 108 U+2028 characters.
  Resolved: `reader_text` escapes them; the gate accepts that encoding. Tested per separator.

## Should Fix (all resolved)
- **Regex compiled per render** (Agents 1 to 5, 7): past 512 distinct dates Python's cache
  thrashed (13.8 s versus 2.5 s cached at 16K). Resolved with `lru_cache` on `_date_pattern`.
- **Validity range could show admission clocks** (Agents 1, 3, 7): write-time supersedence closes
  `valid_to` from effective times that fall back to `event.timestamp` (`storage/derivation.py:127-134`,
  `:205-212`). Resolved: ranges are shown only when `superseded_by` is unset, so only
  caller-supplied windows appear. The related event-time fallback is a follow-up (below).
- **Temporal guidance named `event_time`** (Agents 1, 6): reader guidance now names the bracketed
  date; auditable and compact guidance bytes are unchanged.
- **Context ablation re-rendered reader and compact bundles as auditable JSON** (Agents 1, 3, 4, 6,
  7): fixed for both formats; counterfactuals keep format and surviving references.
- **Silent abstention for uncited reader bundles** (Agents 3, 7): `ensure_citable()` raises in
  answerability and `bundle_evidence`; answerability accepts bracketed references for reader.
- **MCP returned `[m3]` without the mapping** (Agents 3, 6): `context_references` is returned with
  `include_context`.
- **Receipt checks that stopped at version 12** (Agents 1 to 4): ordering, guidance, format and
  episode settings are required from their introducing version on; stale messages updated. Every
  receipt `make_receipt` writes already includes these fields, and v1 to v13 bytes are unchanged.
- **Header claimed every line is a record** (Agent 3): it now says lines starting with `- ` are
  records.
- **Unhashable `condition_state` crashed the reader** (Agents 2, 3): parsed through `ConditionState`
  with a fallback to `unknown`.
- **Docs that enumerate formats or receipt versions** (Agents 4, 6): HTTP-API, AGENTS, RFC-0005,
  configuration, answerability and claim verification updated. RFC-0017's version log stops at 9
  and was not extended for 10 to 13 either, so it was left.

## Consider (not adopted, with reasons)
- One shared "carries text" predicate for receipts and the reader (Agents 4, 5): the reader now
  uses a narrower set than the receipt's `has_content`; #80 defines the cross-format rule.
- Consolidating the four condition-state parsers (Agents 4, 5): the reader now uses the enum; a
  repo-wide consolidation is outside this issue.
- The date de-duplication is shaped by the LoCoMo prefix (Agent 5): the issue requires it, and the
  pattern accepts ISO, day-month-year and month-day-year dates from any source.
- Large reference numbers such as `m195` (Agent 6): they come from the existing deterministic
  numbering over all candidates, shared with compact, which keeps costs stable during packing.
- Value bindings keep a UUID `context_ref` for uncited reader bundles (Agents 3, 6): bindings render
  in their own block, as in the auditable format.
- Benchmark adapters that only accept auditable or compact (Agent 6): they reject reader loudly;
  the owner-approved paired run will need them extended.
- Naive legacy `event_time` values are labeled UTC (Agent 1): new events are always aware.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs and responses | PASS | No logging added; reader lines show fewer fields than auditable. |
| Tenant isolation and references | PASS | References come only from packed sections of a single-owner retrieval; `make_receipt` still rejects other owners. |
| Forgery via control characters and quotes | PASS | JSON escaping keeps text inside its string. |
| Forgery via U+0085, U+2028, U+2029 | PASS (fixed) | Escaped by `reader_text`; tested. |
| Forged notice, guidance or header | PASS | System text only; line breaks in stored text are escaped. |
| ReDoS | PASS | Anchored, bounded patterns; linear on 1 MB hostile input. |
| Receipt deserialization and version gates | PASS | Pydantic only; crafted v13 reader, v14 without citations and compact with citations are all rejected. |

## Pattern Consistency Assessment
The receipt bump follows v12 and v13: new Literal value, a default for older versions in the
before-validator, a serializer pop below 14, an after-validator gate, an execution requirement in
`make_receipt`, and emitting 14 only when the feature is used (as 13 is only for rank
assignments). The new config field follows `exclusive_evidence_representation` for cross-field
validation. The renderer follows the compact renderer's dispatch and reference handling.

## Redundancy Check
No existing renderer or date helper fits: `context_formatter` falls back to `created_at` and prints
`source_type`; `calendar.month_name` is locale-dependent. The separate `citations` flag was removed
in favor of one `context_refs` mapping, and the tag tables were replaced by the two sets of
untagged defaults.

## Wiring Findings
Config reaches both engines, the pipeline, the temporal repack and receipts. HTTP returns the whole
bundle (`dict[str, Any]`), so `context_format` and `context_references` need no model change. CI
runs `tests/` on DuckDB and PostgreSQL, so the new test file runs on both backends.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Reader-format credit ablations compared 1K-token reader contexts
against 4K-token auditable JSON, and answerability gating abstained on every query."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | `ablate_context` re-renders reader bundles as auditable JSON | New (compact pre-existing) | Medium-High | Wrong credit tiers, silent | Fix now | Contained; fixed for reader and compact. |
| 2 | Answerability and claim verification silently abstain on uncited reader bundles | New | Medium | Every answer abstains | Fix now | Now raises `ValueError`; bracketed refs accepted. |
| 3a | Supersedence-closed windows show admission clocks as a validity range | New | Medium-Low | Wrong dates in prompt | Fix now | Bypassed the rule this issue enforces; ranges now require `superseded_by` unset. |
| 3b | Extracted facts without a source time use the ingestion time as `event_time` | Pre-existing | Medium-Low | Wrong dates in prompt | Follow-up | Needs a decision on what `ingest()` stores for unknown times (#106). |
| 4 | Organizer summary text embeds node IDs, source types and creation times | Pre-existing content | Medium-Low | IDs return through text | Follow-up | Changing summary content changes hashes; docs now qualify the claim (#107). |
| 5 | Regex compiled per render thrashes the cache past 512 dates | New | Medium | 0.3 to 1.1 s latency | Fix now | One-line cache. |
| 6 | Temporal-relation guidance writes `[evidence_id=<uuid>]` into reader contexts | New for reader | Low | UUIDs in prompt | Follow-up | Opt-in feature with registered studies; grouped with 4 (#107). |
| 7 | Rolling back after v14 receipts exist blocks learning snapshots | Pattern pre-existing | Low | Loud failure | Accept | Every receipt bump behaves this way; documented. |

Security review also found that auditable and compact contexts leave U+0085, U+2028 and U+2029
raw (pre-existing). Fixing it changes the default format's bytes, so it is a follow-up (#108).

## Follow-ups Raised
- #106: Extracted facts and organizer summaries use the ingestion or creation time as their event
  time when the source has none.
- #107: Reader contexts can still show node IDs and admission times written by organizer summaries
  and temporal-relation guidance.
- #108: Auditable and compact contexts leave Unicode line separators unescaped.

## Evidence Gate
Full offline gate on the final tree (no reader, judge or paid calls), 4K budget:

| Run | LoCoMo records | LoCoMo memory text | LoCoMo all evidence (multi-hop) | LoCoMo projected | LongMemEval-S records | LongMemEval-S memory text | LongMemEval-S all evidence | LongMemEval-S projected |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Defaults (auditable, balanced) | 25.2 | 28.8% | 64.0% (16.0%) | 64.0% | 23.9 | 32.0% | 85.7% | 86.0% |
| Reader, balanced | 101.7 | 96.2% | 69.5% (19.9%) | 67.8% | 83.1 | 69.0% | 87.4% | 87.0% |
| Reader, score order | 74.8 | 96.9% | 78.3% (36.5%) | 73.7% | 21.5 | 87.8% | 84.3% | 85.1% |

The default run reproduced all 1,540 and 500 saved contexts byte for byte, so the default
behavior is unchanged. With score order (the ordering #81 proposes), LoCoMo meets the first
acceptance criterion (at least 70 records and 80% memory text). On LongMemEval-S, score order packs
fewer, longer records than today and loses 1.5 points of all-evidence share (12 wins, 19 losses,
interval -4.0 to +0.6), while balanced order gains 1.7. That tradeoff belongs to #81. Projected
accuracy is a planning estimate, not an answer score.

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Blank records packed as STRUCTURED | Must Fix | Resolved |
| Unicode line separators | Must Fix | Resolved |
| Regex cache | Should Fix | Resolved |
| Admission-clock validity ranges | Should Fix | Resolved (event-time fallback: #106) |
| Temporal guidance wording | Should Fix | Resolved |
| Ablation format | Should Fix | Resolved |
| Silent abstention | Should Fix | Resolved |
| MCP references | Should Fix | Resolved |
| Receipt version rules and messages | Should Fix | Resolved |
| Header wording | Should Fix | Resolved |
| Unhashable condition state | Should Fix | Resolved |
| Docs | Should Fix | Resolved |
| Adversarial 1, 2, 3a, 5 | Fix now | Resolved |
| Adversarial 3b, 4, 6 | Follow-up | #106, #107 |
| Adversarial 7 | Accept | Recorded |
