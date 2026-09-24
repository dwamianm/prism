# Code Review: issue-80-exclude-text-free-packed-records

## Files Changed
- `src/prme/types.py`: `TEXT_REPRESENTATIONS` (FULL, PROSE, STRUCTURED) and `has_memory_text()`, the one
  definition of "shows memory text"; `RepresentationLevel` docstring now says it is ordered by fidelity.
- `src/prme/retrieval/packing.py`: `_text_levels()` and public `requires_memory_text()`; under the reader
  format or a text-bearing `min_fidelity`, blank records are excluded before any level is tried, fallback
  levels are filtered once per pack, a reserved (`_required`) text-free level is refused, and a blank record
  cannot take the balanced head. `_READER_REPRESENTATIONS` is derived from the shared set.
- `src/prme/retrieval/pipeline.py`: per-request `min_fidelity` is converted to `RepresentationLevel` (the
  `model_copy` override skipped validation); aggregation coverage no longer counts a blank record's exclusion
  as a `token_budget` limit when text is required.
- `src/prme/models/relevance.py`: receipt `has_content` uses the shared definition (no behavior change).
- `src/prme/retrieval/config.py`, `src/prme/retrieval/models.py`, `src/prme/api/models.py`,
  `src/prme/mcp/server.py`, `src/prme/storage/engine.py`: field descriptions and docstrings.
- Docs: `docs/PACKING.md`, `docs/RFC-0006-Retrieval-Cost-and-Context-Efficiency.md` (new clarification
  section), `docs/INTEGRATION.md`, `documentation/async-engine.md`, `documentation/configuration.md`,
  `BENCHMARKS.md`, `CHANGELOG.md`.
- Tests: new `tests/test_text_free_fallbacks.py` (45 tests plus 2 PostgreSQL variants skipped locally);
  `tests/test_product_packing_diagnostic.py` (gate accepts the floor override).

## Approach Summary
The packer falls back through representation levels down to `PackingConfig.min_fidelity`, whose default is
`reference`. `key_value` renders `id: <uuid>, type: fact, confidence: <value>` and `reference` renders
`fact:<uuid>`: no memory text. Only the reader format (#79) filtered them. Auditable and compact packed them
inside a full envelope, and packed blank records as `"text":""` at any floor.

The epic #77 rules keep production defaults unchanged until the owner approves a paid paired answer run, so
the default floor stays `reference` and the gate at defaults still reproduces every saved context. The opt-in
is the existing `min_fidelity` setting: a text-bearing floor already stopped the level slice above the pointer
levels, and this change closes the remaining hole (blank records, reserved levels) so the floor guarantees
memory text in every format. `full`, `prose` and `structured` pack the same records, because neither `prose`
nor `structured` is ever shorter than `full`; the docs recommend `full`, which does the least work.

Alternatives considered:
- Filter text-free levels in every format unconditionally, or change the `min_fidelity` default: both change
  default output (337 LoCoMo and 106 LongMemEval-S saved contexts would differ), which the epic rule forbids
  until the paired run. Left as the remaining acceptance criteria.
- A new boolean `PackingConfig` field: duplicates `min_fidelity`, which is already recorded in every receipt
  since version 1 and overridable per request through engine, client, HTTP and MCP. A new field would need
  `exclude_if` or a receipt schema bump (#79 and #82 precedent) to keep stored receipt bytes.
- Exclusion reason codes in the bundle or receipt: a contract and receipt schema change. Excluded records are
  already in `MemoryBundle.excluded_ids` and in the receipt as candidates with `in_context=False`.

## Must Fix (none)
No agent raised a Must Fix.

## Should Fix (all resolved)
- **The blank-text half of the rule was written twice** (Agent 4): `has_memory_text()` is shared by the packer,
  the pipeline and the receipt.
- **`_READER_REPRESENTATIONS` was a separate hand-kept set** (Agents 4, 5): now
  `TEXT_REPRESENTATIONS - {STRUCTURED}`.
- **Per-request descriptions not updated** (Agent 4): HTTP field, MCP tool docstring, engine and pipeline
  docstrings now say what a text-bearing floor does.
- **`docs/PACKING.md` said both pointer levels were "above" the floor** (Agents 3, 4, 5, 6): reworded; the
  section now follows the file's "To ..., set ...:" style.
- **In-loop comment was incomplete** (Agent 3): fallback levels are filtered once; the in-loop check only
  guards a reserved level, and says so.
- **A render assertion could not fail in compact or reader** (Agent 3): tests now parse each format's rendered
  record text and check it.
- **Aggregation coverage called blank exclusions a budget limit** (Agents 1, 3, 4, 7): fixed in the pipeline;
  see break scenario 1.
- **`docs/INTEGRATION.md` representation table contradicted the change** (Agent 6): rows now say which levels
  carry text.
- **New test file only intent-added** (Agent 6): staged with the commit.

## Consider
Adopted:
- Blank records no longer take the balanced reserved head under the text rule (Agent 1). Reader output changes
  only when a blank multi-path record would have been the head; the saved packs contain none.
- Plain-string per-request floor converted in the pipeline, with tests for the string, the environment
  setting, a `model_copy` string, and a bad value (Agents 1, 2, 3, 6).
- Test additions: reserved-level refusal in all three formats, a default-floor pointer check in the budget
  sweep, blank exclusion in monotonic compact, a gate override test (Agents 3, 4).
- Recommend `full` in the docs: same records as `structured`, fewer token counts (Agent 3 measured 25 to 27 ms
  against 39 to 41 ms for 300 candidates).
- "ID-only" wording replaced with "text-free", since `key_value` also shows type and confidence (Agent 3).
- `BENCHMARKS.md` now says `records_without_text` also counts blank records (Agent 6).
- CHANGELOG "Changed" entry for installs that already set a text-bearing floor (Agent 7).

Not adopted:
- The legacy `measure()` diagnostic and three other benchmark scripts spell out `{"full", "prose",
  "structured"}` (Agents 4, 5). The gate's `records_without_text` reads the receipt flag, which now uses the
  shared definition; the other scripts pin their own source hashes.
- `select_representation` in `packing.py` is dead (Agent 5). It was already unused before this change.
- `documentation/mcp-server.md`, `http-api.md` and `memory-client.md` never listed `min_fidelity` (Agents 4, 6).
  Pre-existing; `docs/PACKING.md` and `docs/INTEGRATION.md` document it.
- Content made only of invisible characters such as U+200B counts as text (Agent 2). The packer and receipt
  agree, and the text belongs to the same tenant.
- An explicit `PackingConfig` object passed to `PRMEConfig` ignores `PRME_PACKING__*` variables (Agent 6).
  Pre-existing pydantic-settings behavior for every packing field.
- Reusing `candidate()` from `tests/test_reader_context.py` (Agent 5): the local helper keeps the file
  standalone.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs and responses | PASS | No logging or new error text. `excluded_ids` holds the caller's own filtered candidates. |
| Authorization and tenant isolation | PASS | Owner is fixed before `min_fidelity` is read; packing sees only filtered candidates. |
| Filter bypass through `min_fidelity` | PASS | It does not affect candidate generation or filtering. |
| Input validation (HTTP, MCP, env, library) | PASS | Enum validation on HTTP, MCP and env; the library path now converts and fails before retrieval. |
| Prompt injection and forged records | PASS | Rendering is unchanged; stored `fact:<uuid>` text renders as quoted data at `full`. |
| Receipt loading | PASS | Same `has_content` semantics; no schema change. |
| Credentials in code or fixtures | PASS | None. |
| Denial of service | PASS | Pointer levels are skipped before any token counting. |
| Node IDs in the prompt | Reduced | Compact at a text floor puts no raw UUIDs in the prompt; reader never does. |

## Pattern Consistency Assessment
Follows #79 and #82: field description, `documentation/configuration.md` row, `docs/PACKING.md`, a dated
RFC-0006 section, `docs/INTEGRATION.md`, CHANGELOG, a dedicated test file reusing the
`tests/test_durable_ingestion` engine fixture, and a gate override test. No receipt schema bump is needed
because `min_fidelity` is already recorded and only ranking is replayed. No hypothesis-audit entry, because no
field was added.

## Redundancy Check
No new dependency, field or state. The shared constant and predicate replace inline copies in the packer and
the receipt. Test cases that overlap `tests/test_reader_context.py` for the reader format were kept on purpose
so one file states the cross-format rule.

## Wiring Findings
`PRME_PACKING__MIN_FIDELITY` loads and reaches `pack_context` through the engine; per-request values work
through engine, `MemoryClient`, HTTP and MCP; receipts record the floor and mark excluded records
`in_context=False`; the gate accepts `--set packing.min_fidelity=full`. `render_system_instructions`, value
bindings, ablation, monotonic compact and the temporal repack all read the packed sections. `format_for_llm`,
the LangChain and LlamaIndex retrievers, and MCP `results` use `response.results`, which the floor does not
govern; `docs/PACKING.md` now says so.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Agents with a text floor report every count question as
`context_limited: token_budget`, because blank tool-call turns are now excluded."

| # | Scenario | Introduced | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | Blank records (for example tool-call-only chat turns from the LangChain and LlamaIndex adapters) excluded under a text floor make aggregation coverage report `token_budget` / `context_limited`, persisted in receipts | New for auditable and compact; already true for reader | Medium (opt-in, but agent workloads create blank turns) | Wrong coverage metadata, silent | Fix now | Contained: the pipeline counts only exclusions of records that have text. Tested with blank turns; the test fails without the fix. |
| 2 | MCP `results`, the LangChain and LlamaIndex retrievers and `format_for_llm` ignore the floor, so blank records still reach those consumers | Pre-existing | Medium | Low (never text-free pointers, only blank records) | Fix now (docs) | `docs/PACKING.md` now says the floor governs the packed context only; `results` are the retrieval results by design. |
| 3 | Installs that already set `min_fidelity=full` (as the old async-engine docs advised) lose blank records from auditable and compact contexts on upgrade | New | Low to Medium | Low: the records carried no text; contexts differ from pre-upgrade replays | Fix now (CHANGELOG) | Stated under Changed. Alternatives re-checked: a separate opt-in field would spare these installs but duplicates `min_fidelity` and needs receipt schema work, and excluding text-free records is what the issue asks for. Receipts distinguish the two policies through the packing source hash. |
| 4 | The temporal relation repack rebuilds the bundle from packed records only, so the first pass's exclusions disappear from `excluded_ids` and later aggregation coverage | Pre-existing | Low (enricher is opt-in and calls a provider) | Low to Medium, silent | Follow-up #114 | A fix in `temporal_relations.py`, a subsystem this diff does not touch. The receipt stays correct. |
| 5 | A per-request `min_fidelity=reference` replaces a text floor the operator configured | Pre-existing | Low | Low (pointers waste space) | Accept | The configured value is a default, not an enforced policy; the floor used is recorded in the receipt. `docs/PACKING.md` states that a request replaces it. |

Attacks that failed (Agent 7): default drift (neither new check runs at the `reference` floor in auditable or
compact, and reader output is unchanged); pointers smuggled in through `_required` (refused); string-valued
floors (hash like the enum and are now converted); scale at 2,000 candidates and a 16K budget (the text floor
was faster than the default); receipts (same `has_content` semantics, same schema).

Agent 1 compared the branch packer against `main` on about 45,000 random inputs: no difference at the
`reference` or `key_value` floors in any format, reader output unchanged at every floor, and auditable or
compact at a text floor differing only for blank records or reserved pointer levels.

Tally: Fix now 3, Follow-up 1 (#114), Accept 1, Dismissed 0.

## Follow-ups Raised
- #114: the temporal relation repack drops earlier packing exclusions from `excluded_ids` (scenario 4).

## Evidence gate (offline, 4K)
Full runs on the final code: current defaults against `--set 'packing.min_fidelity="full"'`. An earlier
run with `structured` produced the same context for all 2,040 questions.

| Benchmark | Saved contexts reproduced | Records per context | Memory text share | Records without text | All evidence packed | All evidence packed with text |
|---|---|---|---|---|---|---|
| LoCoMo | 1540/1540 to 1203/1540 | 25.2 to 25.2 | 28.8% to 29.0% | 337 to 0 | 64.0% to 63.8% (0 wins, 3 losses) | 63.8% to 63.8% (no change) |
| LongMemEval-S | 500/500 to 394/500 | 23.9 to 23.9 | 32.0% to 32.2% | 109 to 0 | 85.7% to 85.7% | 85.7% to 85.7% |

The three LoCoMo losses were questions whose evidence was packed only as a pointer, which the headline metric
counts; evidence packed with its text is unchanged on both benchmarks, as are all multi-hop and LongMemEval-S
categories. Only the contexts that held a pointer changed (337 LoCoMo, 106 LongMemEval-S).

At defaults the final code still reproduces 1540/1540 LoCoMo and 500/500 LongMemEval-S saved contexts
byte for byte. Full test suite: 4003 passed, 852 skipped.

## Resolution Status
| Finding | Source | Status |
|---|---|---|
| Shared blank-text predicate | Agent 4 | Resolved |
| Reader level set derived from the shared set | Agents 4, 5 | Resolved |
| Per-request descriptions | Agent 4 | Resolved |
| PACKING.md wording and style | Agents 3, 4, 5, 6 | Resolved |
| In-loop comment | Agent 3 | Resolved |
| Vacuous render assertion | Agent 3 | Resolved |
| Aggregation coverage mislabel | Agents 1, 3, 4, 7 | Resolved (scenario 1) |
| INTEGRATION.md representation table | Agent 6 | Resolved |
| Test file staged | Agent 6 | Resolved |
| Balanced head given to a blank record | Agent 1 | Resolved |
| Plain-string per-request floor | Agents 1, 2, 3, 6 | Resolved |
| Test gaps (env, reserved levels, sweep, monotonic, gate) | Agents 3, 4 | Resolved |
| Bundle-bypassing consumers | Agent 7 | Resolved in docs (scenario 2) |
| Upgrade note for existing text floors | Agent 7 | Resolved in CHANGELOG (scenario 3) |
| Temporal repack drops exclusions | Agents 2, 7 | Follow-up #114 |
| Per-request floor replaces the configured one | Agent 7 | Accepted, documented |
