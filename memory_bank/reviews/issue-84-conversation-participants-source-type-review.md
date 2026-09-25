# Code Review: issue-84-conversation-participants-source-type

## Files Changed
- `src/prme/models/speaker.py` (new): the reserved `prme_speaker_v1` metadata key,
  `SpeakerError`, `normalize_speaker` (strip; non-empty; at most 200 characters; no
  control characters, line or paragraph separators or bidirectional controls),
  `attach_speaker`, `metadata_speaker` (lenient read, cached), `text_states_speaker` and
  `speaker_labeled`.
- `src/prme/epistemic/inference.py`: `OWNER_ROLES`, `PARTICIPANT_ROLE`, `FIRST_PARTY_ROLES`;
  `participant` maps to `USER_STATED`; stale docstring corrected.
- `src/prme/storage/engine.py`: `speaker` on `store`, `store_with_receipt`, `ingest`,
  `ingest_batch` (per message, all checked before the first admission, one read of the
  input), `ingest_fast`, `ingest_fast_many`. Q-A pairing keys on (role, speaker) and names
  each half's speaker in the merged text. Instruction reinforcement uses `OWNER_ROLES`.
- `src/prme/models/processing.py`: `FastIngestItem.speaker` with `validate_speaker`.
- `src/prme/client.py`: sync wrappers pass `speaker`.
- `src/prme/api/models.py`, `src/prme/api/routes.py`: `speaker` on `StoreRequest` and
  `IngestRequest`, validated in the request models with the reserved key rejected in
  `metadata`; `/v1/ingest` maps only `SpeakerError` to 422.
- `src/prme/mcp/server.py`: `speaker` on `memory_store` (after the existing parameters) and
  `memory_ingest`; `memory_ingest` returns only `SpeakerError` text.
- `src/prme/retrieval/packing.py`: reader label `"Speaker": "text"`, a separate header used
  only when a packed line shows a speaker (it also says speaker names are source data),
  `reader_shows_speaker`, `_render_sections(speaker_header=...)`, and a `speaker` key in the
  auditable format for records stored with one.
- `src/prme/retrieval/credit.py`: ablation keeps the baseline's reader header.
- `src/prme/retrieval/config.py`, `src/prme/config.py`, `src/prme/models/events.py`,
  `src/prme/ingestion/pipeline.py`: descriptions and docstrings.
- `src/prme/integrations/llamaindex.py`: a stored role LlamaIndex has no value for reads as a
  user turn.
- `benchmarks/locomo.py` (legacy, unregistered harness): both people stored as
  `participant` with their names.
- Tests: new `tests/test_participants.py`; `tests/test_instruction_corroboration.py`,
  `tests/test_integrations_llamaindex.py`, `tests/typing/public_api.py`.
- Docs: `docs/HTTP-API.md`, `docs/INTEGRATION.md`, `docs/PACKING.md`,
  `docs/RFC-0001-Core-Data-Model.md`, `docs/RFC-0003-Epistemic-State-Model.md`,
  `docs/RFC-0006-Retrieval-Cost-and-Context-Efficiency.md`, `documentation/` (async engine,
  memory client, HTTP API, MCP server), `CHANGELOG.md`.

## Approach Summary
A named speaker is caller-supplied provenance kept under a reserved metadata key on the
source event and on the node built from it (the direct-store node or the raw note), the same
pattern as `prme_value_bindings_v1`. The new `participant` role marks a human who is not the
memory's owner: first-party (`USER_STATED`) like `user`, but excluded from owner-only paths.
The reader format prints the speaker; contexts for records without a speaker are
byte-identical to `main`, which the saved benchmark contexts, receipts and the DeepSeek
baseline depend on. No default changes.

Plan self-check (auto-approved): concrete approach, bounded files, no schema change or
migration, opt-in only, core actionable now. Left open on #84 (the PR says `Part of #84`):
- A lexical speaker field. Tantivy refuses to open an existing index whose schema changed
  (checked locally with tantivy 0.25: "Schema error: An index exists but the schema does not
  match"), and PostgreSQL `lexical_documents` would need `ALTER TABLE`
  (`src/prme/storage/pg/schema.py:130`). This needs a rebuild or migration decision.
- The registered LoCoMo adapter `benchmarks/integrations/run_gpt54_comparison.py`
  (`source_turns`) is in `FROZEN_SOURCES` (`benchmarks/integrations/gpt54_baselines.py:191`)
  and its digest is checked (`gpt54_baselines.py:316-318`); changing it would stop the
  DeepSeek track. New packs with participants need #102.
- The per-speaker evidence gate report, before and after: the after side needs fresh packs
  (#102).
- Any default change: the paired answer run under the epic #77 rule.

Alternatives considered:
- Reuse `role="human"`: rejected. It already counts as the owner for instruction
  reinforcement (`src/prme/storage/engine.py`, `_check_instruction_reinforcement`), which a
  second person must not.
- A speaker column on events and nodes: rejected. DuckDB and PostgreSQL schema changes for
  data that fits the existing metadata precedent (`src/prme/models/value_bindings.py:22`).
- A plain `metadata["speaker"]` key: rejected. Callers may already use it; they would be
  rejected or suddenly get reader labels.
- A speaker field in the lexical index now: see above.

## Must Fix
None found by any agent.

## Should Fix (all resolved)
- **`ingest_batch` drained a one-shot iterable in the pre-check and ingested nothing**
  (Agent 1 S1). Resolved: the input is read once into a list; tested with a generator.
- **Ablation changed the reader header as well as the removed record** (Agent 1 S2, Agent 3
  Q4, Agent 7 #8). Resolved: `_render_sections(speaker_header=...)`, and `ablate_context`
  passes the baseline's choice; tested.
- **`/v1/ingest` and MCP `memory_ingest` echoed any `ValueError`, including pydantic and
  asyncpg internals** (Agent 2 L1). Resolved: the request models validate `speaker` and reject
  the reserved key; the route and tool catch only `SpeakerError`.
- **The name rule rejected real names (no-break space, U+3000, ZWNJ, ZWJ) and depended on the
  Python Unicode version** (Agent 3 Q1, Agent 4 L7, Agent 1 C2, Agent 2 I3, Agent 7 #3).
  Resolved: only control characters (Cc, Cs), line and paragraph separators (Zl, Zp) and the
  fixed list of bidirectional controls are rejected; tests pin both sides.
- **HTTP speaker validation differed by endpoint** (Agent 4 M2). Resolved: the same validator
  on `StoreRequest`, `IngestRequest` and `FastIngestItem`; descriptions use
  `MAX_SPEAKER_LENGTH`.
- **The speaker appeared only in the reader format** (Agent 4 M3, Agent 6 L5). Resolved for
  the auditable format (a `speaker` key only on records stored with one); the compact
  format's fixed positional fields do not include it, documented in `docs/PACKING.md` and
  RFC-0006.
- **The reader spec and config description were stale** (Agent 6 M2, Agent 4 L3). Resolved:
  `PackingConfig.context_format` description and RFC-0006 "Reader rendering extension".
- **The only behavior test file was untracked** (Agent 6 M1, Agent 7 #2). Resolved: committed.
- **`participant` is an unknown role to the extractor** (Agent 4 M1, Agent 6 L4, Agent 3 Q7).
  Documented now (INTEGRATION.md, RFC-0003); the extraction change is a follow-up (below).

## Consider
- Resolved: nested bracketed dates such as `[2023/06/09 (Fri) 19:55] Caroline:` now suppress
  the label (Agent 1 C1, Agent 3 Q3, Agent 4 L6); the batch error names the message index
  (Agent 1 C7); non-mapping metadata with a speaker is rejected (Agent 1 C8); `memory_store`
  keeps its parameter order (Agent 1 C9, Agent 3 N7); the legacy harness tolerates a null or
  padded speaker (Agent 1 C9, Agent 3 N9); the reader header says speaker names are source
  data (Agent 2 L2); `speaker` is normalized once in `store()` (Agent 3 N1, Agent 5 L2); the
  batch comprehension is simplified (Agent 3 N2, Agent 5 L1); role constants moved to
  `prme.epistemic.inference` and reused by instruction reinforcement (Agent 4 L1, Agent 5 I4);
  role lists, RFC-0001 `USER_STATED` definition, RFC-0003 section 9 and the `documentation/`
  pages updated (Agent 3 Q6, Agent 4 L2, Agent 6 L2, L3); docs wording fixes (Agent 3 N5, N6);
  the hot-loop cost is reduced by caching the stored-name check and comparing only a prefix
  (Agent 3 Q2, Agent 5 L4).
- Accepted: the owner repeating a participant's instruction can reinforce the participant's
  node (Agent 1 C3); the docs now say only that a participant's statements do not reinforce
  the owner's instructions. A participant without a speaker reads like the owner (Agent 2 L3);
  the docs call the speaker an unverified caller assertion. No request-level length cap before
  strip (Agent 2 I7), consistent with `content`.
- Not taken: `attach_speaker` stays positional and does not snapshot (Agent 4 L4; `store()`
  and `ingest_fast_many` snapshot around it, and the ingest paths snapshot at admission);
  `event_time` is still checked per message in `ingest_batch` (Agent 4 L5, Agent 5 I1; the
  docs promise the speaker check only); helpers are not re-exported from `prme` (Agent 3 N10;
  matches value bindings).

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets/PII in logs | PASS | The speaker is never logged. |
| Secrets/PII in responses | PASS | Returned only in the owner's own metadata and context. |
| Tenant isolation | PASS | Ownership never depends on the speaker. |
| Owner-only behavior | PASS | Instruction reinforcement uses `OWNER_ROLES`; user-only extraction recovery unchanged. |
| Control characters, separators, bidi | PASS | Rejected by `normalize_speaker`; JSON quoting in the reader line. |
| Very long names | PASS | 200 characters after strip; counted in the token budget. |
| Reader line forgery | PASS | Speaker JSON-quoted; header says speaker names are data. |
| Reserved metadata key | PASS | Rejected on every engine, HTTP and MCP write path; lenient read. |
| Error text exposure | PASS (after fix) | Only `SpeakerError` messages are returned by `/v1/ingest` and `memory_ingest`. |
| Unsafe deserialization, binds, credentials | PASS / N/A | None introduced. |

## Pattern Consistency Assessment
Follows the value-binding precedent for a reserved metadata key, the `tool` role commit
(`f33d3fe4`) for a new role (inference, events, pipeline docstring, RFC-0003 section 9,
INTEGRATION.md, tests), and the existing threading of store parameters across engine, client,
HTTP, MCP and `FastIngestItem`. The reader header variant follows the `{reference}` pattern but
keeps the speakerless bytes identical.

## Redundancy Check
No dead code or new dependencies. Double normalization in `store()` and the batch key filter
were removed. The explicit `participant` entry in `infer_source_type` has no runtime effect
(unknown roles already fell back to `USER_STATED`) and is kept to state intent.

## Wiring Findings
Wired end to end on every engine, client, HTTP and MCP write surface; kept through restart
recovery, `prme rebuild` and PostgreSQL JSONB; covered by the fast-ingest idempotency hash.
The frozen harness files and the evidence gate code are untouched. Framework adapters do not
map a message name to a speaker (follow-up below).

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Six months later, participants were still being quoted as the
owner. The raw notes were labeled correctly; the misattribution sat in the extracted claims,
summaries and Q-A pairs, which the speaker never reached."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | `participant` with `ingest()`: extracted claims, summaries and verifier evidence carry no speaker and read like the owner's | newly exposed | Medium | High | Follow-up (#188), docs caveat now | Changing claim metadata touches derivation plans and the extraction prompt, which needs an evaluated run; #91 reworks the extractor. Docs now say claims do not carry the speaker yet. |
| 2 | Test file untracked | new (process) | Medium | Medium | Fix now | Committed. |
| 3 | Real names with no-break space, U+3000, ZWNJ or ZWJ rejected; batch fails as a unit | new | Low to Medium | Medium | Fix now | Contained rule change; tests pin it. |
| 4 | LlamaIndex chat store cannot read or clear a session with a `participant` turn | pre-existing, newly exposed | Low | Medium | Fix now | One-line fallback now that the role is documented; tested. |
| 5 | Opt-in Q-A pairing merges two people's turns without names | new | Low | Medium | Fix now | Each half names its speaker unless its text already does; tested. |
| 6 | Read-modify-write copies of metadata fail on the reserved key | new | Low to Medium | Low | Accept, doc note | Loud 422 or `ValueError`; only data written with a speaker carries the key, so no existing round trip changes. Re-checked accepting the key when it equals `speaker`: rejected to keep one input path, like `prme_value_bindings_v1` (`value_bindings.py:160-163`). |
| 7 | Direct `IngestionPipeline` use bypasses the reserved-key guard | new | Low | Low | Accept | The engine is the documented write surface, and the read validates every stored value. |
| 8 | Ablation changes the header | new | Low | Low | Fix now | Header choice passed from the baseline; tested. |

Also grouped into #188: opt-in store-time supersedence and re-mention reinforcement
compare content across speakers (Agent 6 L7). Framework adapters mapping a message name to a
speaker (Agent 6 L1) is #189.

## Follow-ups Raised
- #188: speaker attribution is lost outside the raw note (extracted claims, summaries,
  consolidation and profile inputs, claim-verification evidence, extraction guidance for
  participants, cross-speaker supersedence and reinforcement).
- #189: the LangChain and LlamaIndex adapters cannot record a second human as a
  participant with a speaker.

## Evidence gate (offline, final tree)
Run on the final tree with `benchmarks.diagnostics.product_packing gate`, no paid calls.
The saved packs carry no speakers, so this change must leave every context unchanged, and it
does:

| Run | LoCoMo | LongMemEval-S |
|---|---|---|
| Current defaults, `main` (a475147f) vs this branch: identical `context_sha256` | 1,540/1,540 | 500/500 |
| Current defaults, all evidence packed (before = after) | 1,305/1,536 (85.0%) | 409/470 (87.0%) |
| Current defaults, projected accuracy (before = after) | 78.3% | 86.8% |
| `gate-compare` wins / losses / ties | 0 / 0 / 1,536 | 0 / 0 / 470 |
| Previous defaults (`fusion=weighted`, `auditable`, `balanced`): saved contexts reproduced | 1,540/1,540 | 500/500 |

The gate cannot see the new behavior itself (speaker labels, participant source type): that
needs packs stored with speakers (#102). Simulations: 74/74 under current and previous
defaults.

## Resolution Status
| Finding | Status |
|---|---|
| Must Fix | none |
| Should Fix (9) | resolved (8 in code or docs; extraction guidance documented and followed up) |
| Consider | resolved, accepted or not taken as listed above |
| Adversarial #1 | Follow-up #188 |
| Adversarial #2, #3, #4, #5, #8 | fixed |
| Adversarial #6, #7 | accepted |
