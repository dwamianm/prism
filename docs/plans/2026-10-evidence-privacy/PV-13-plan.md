# PV-13: shared local sanitizer implementation

Issue: #322. Target branch: `main`. Local implementation is authorized on
provisional prerequisite `b84a866cd3b46db8c018ca608bb5079e96f90fd9` (#320).
Dependency delivery, final reconciliation, push and PR creation remain held by
the orchestrator. This plan reuses the accepted preparation and its source
reconciliations; it does not begin a new investigation or evaluation cohort.

## Original acceptance criteria

- [ ] Run the effective policy before content logging, storage, queueing,
      embedding and provider calls for sync/async store/ingest, fast/batch,
      HTTP/MCP and framework/import adapters. Check content metadata and generated
      claims/summaries too. Pre-sanitization originals are never persisted in v1.
- [ ] Implement supported credential blocking and category-specific allow/redact/
      pseudonymize/block rules, versioned presets, supported languages and bounded
      safe custom patterns. Errors/timeouts/unsupported configurations fail closed;
      mandatory hosted restrictions are expressed as reusable engine policy.
- [ ] Optional placeholders preserve within-owner relationships without global
      identity hashes or plaintext reverse maps. Scope tokens by project/owner/
      scope, prevent token spoofing and include mapping state in erasure coverage.
      Do not claim that pseudonymized facts are anonymous.
- [ ] Extract and validate verbatim evidence against the sanitized source's own
      identity/checksum. Retain policy/detector version and non-content action
      counts. Preserve conditions/negation or reject ambiguous claims; never keep
      removed values or raw-value hashes in transformation reports.
- [ ] Strict admission permits only approved fields and validated values. Policy
      inspection reports detector/language coverage and may expose only sanitized
      preview output; no inference that a clean scan proves absence of secrets.
- [ ] Add twelve fixed scenarios: recognized credential, contact identifier,
      placeholder continuity, cross-owner/project isolation, configured sensitive
      topic, condition/negation preservation, unsupported language, supported
      obfuscation, detector failure, timeout, pre-write side effects, and generated
      output reintroduction. Include benign controls and known unsupported cases.

Done means those criteria have actual evidence, not only a functioning component.
A prerequisite-only checkpoint must retain every pending criterion, use a
non-closing issue reference and avoid claiming unsupported routes as successes.

## Implementation and ownership

The gap is an intentionally absent production implementation behind #320's
`Sanitizer.admit(AdmissionContent)` capability. Reuse `AdmissionCoordinator`,
immutable `PolicySnapshot`, canonical `fields_json`, and `OperationBudget`.
No second admission/policy stack or custody implementation is introduced.

New runtime modules under `src/prme/privacy/`:

- `sanitizer.py`: bounded nested traversal, aggregate operation accounting,
  overlap/action resolution, sanitized identity/checksum and trusted provenance.
- `recognizers.py`: frozen credential, obfuscated-email and bounded custom rules.
- `local_analyzer.py`: explicit offline Presidio/spaCy registry, artifact/version
  checks, timeout completeness and supervision.
- `pseudonyms.py`: domain-separated exact-value HMAC and authenticated token use.
- `strict_schema.py`: immutable closed schemas and typed value validation.

Approved shared wiring: `privacy/{__init__,capabilities,admission,records}.py`,
`storage/engine.py`, `ingestion/{extraction,grounding,pipeline,planning}.py`,
`retrieval/pipeline.py`, `organizer/{summarization,consolidation}.py`,
`pyproject.toml`, `uv.lock`, and `.github/workflows/ci.yml`. Only necessary
sanitizer-specific changes belong here. #321 owns `privacy/keys.py`, cryptography
and custody. #323 owns physical `retrieval/candidates.py` changes and guarded
read/projection routing. #324 owns historical journal/work lifecycle. Coordinate
shared interfaces without copying sibling implementations.

New tests: `tests/test_privacy_{recognizers,sanitizer,sanitizer_scenarios,
sanitizer_interfaces}.py`, `tests/privacy_sanitizer_support.py`,
`tests/fixtures/privacy/{sanitizer-v1,sanitizer-v1-artifacts}.json`, and public
typing assertions. Reuse `tests/privacy_fakes.py` and its private PostgreSQL
schema fixture. Documentation includes `docs/SANITIZATION.md`, this plan, a
content-free `PV-13-results.json`, and the exact-revision review report.
Update inventory anchors for actual new copies; never weaken frozen obligations.

## Fixed security and behavior contract

- Pin `presidio-analyzer==2.2.364`, `spacy==3.8.11`, and
  `en_core_web_sm==3.8.0`. English is an explicit declaration. Explicit
  recognizers only; no default registry, remote engine, runtime model download,
  classifier training or extraction/answer model call.
- Offline email suffix validation uses a local `TLDExtract(cache_dir=None,
  suffix_list_urls=(), fallback_to_snapshot=True,
  include_psl_private_domains=False)`. Assert no network/cache effects.
- Handle Presidio's swallowed per-pattern regex timeout: convert it to a fixed
  failure before the library can skip it. A complete result attestation and
  outer bounded supervisor are both required. An injected early timeout must
  deny even if other recognizers succeed; discard late/partial results.
- Preserve the frozen 65,536 UTF-8 byte aggregate, 64 rules, 256 bytes per
  literal, 8,192 total custom bytes, metadata depth 8/leaves 1,024 and 2,000 ms
  cumulative detector budget. Ingress, generated bundles and batch members
  share accounting. Persist only authenticated non-content continuation
  accounting for resumed work, never arbitrary `OperationBudget.state`.
  Provider waiting does not replenish or consume local detector execution time.
- Traverse metadata keys and values, source/retrieval content, speaker and value
  bindings. Reject duplicate/normalized/transformed keys, non-finite input,
  malformed offsets and partial results. Credential key/value assignments remain
  associated. No prefix truncation or raw-value/report hash.
- Mandatory credential block cannot be relaxed. Resolve actions from effective
  policy, not preset names. Safe custom rules are bounded exact literals or
  allowlisted typed recognizers; never caller regex/callables. Strict schema
  references remain effective when composition changes the preset to custom.
- Escape every source reserved token prefix. Output preserves only authenticated
  source-issued tokens in the same project/owner/scope/epoch. #321 supplies exact
  purpose-bound keys; missing/foreign/retired keys deny without reprovision.
  No reverse map or global identity digest; pseudonyms are not anonymity.
- Bind verbatim evidence to sanitized source IDs and private canonical checksum.
  Keep old event/plan checksums unchanged. Retain bounded detector versions/counts
  beyond processing completion, including capture-none.
- Conditions, negation, attribution, time and exact quantities survive or the
  claim rejects. SN12 source-only classification comes from trusted engine/schema
  context and is recorded in the fixture; it is not a semantic detector or
  prohibited-marker shortcut.
- Providers must guard every actual send/retry and avoid intermediate generated
  content in logs/retry requests before admission. Final generated checks
  precede embeddings, plans, indexes, profile/summary preparation and publication.
  Preserve #320 planning/reingest checks and protected group refusal, including
  the source-text LRU/post-plan group paths. Global defaults remain unchanged.

## Finite validation

Freeze all synthetic labels/bytes/artifact references before real local detector
inference. Exactly 69 detector rows: credential 20 (13 supported positive,
3 benign, 4 unsupported); email 9 (5 positive, 2 benign, 2 unsupported); phone,
person, location, US SSN, payment card, IP address, custom identifier and topic
5 each (2 positive, 2 benign, 1 unsupported). Preserve misses/false positives;
unsupported rows remain separate and never inflate true negatives.

Exactly the existing twelve scenario IDs on DuckDB and PostgreSQL (24
executions), with directory/single-file and adapter subchecks inside them:

| Scenario | Actual effects/assertions |
| --- | --- |
| SN01 credential | Body and metadata assignment blocking before every sink; benign password text allowed. |
| SN02 contact | Supported email/US/GB phone actions and benign numeric controls; copied-content scans and metrics. |
| SN03 continuity | Same-boundary/epoch token continuity, inert source spoof, lost key refusal; no identity collapse. |
| SN04 isolation | Project/owner/scope separation, foreign token refusal, no global/reverse mapping. |
| SN05 custom | Exact configured topic/identifier rules, malicious/unbounded rule rejection, opaque inspection. |
| SN06 qualifiers | Negated conditional attribution/time and exact sanitized evidence, closed typed schema, ambiguity refusal. |
| SN07 language | Missing/unsupported declarations refuse; misdeclared/mixed-language limits are measured/reported. |
| SN08 obfuscation | All four declared at/dot forms and original offsets; encoded/homoglyph examples stay unsupported. |
| SN09 failure | Real adapter error, absent model/recognizer and invalid offsets deny with content-free codes and zero effects. |
| SN10 bounds | Controlled clock/stall/early internal timeout; aggregate exact/+1 boundaries, no partial or late publication. |
| SN11 side effects | Actual supported public adapters, query metadata and imports; atomic later-item batch failure; logs/queues/storage/provider/index sinks. Reuse real #323 query integration when available. |
| SN12 generated output | Prohibited value, classified source-only passage, foreign token and strict extra field withheld at actual plan/index/profile/summary boundaries; successful benign extracted/generated controls. |

Targeted development tests use local recognition and deterministic fake
extraction/embedding. No benchmark or additional cohort. Preserve each failed
log and command/exit/revision. Stop a strategy after two unsuccessful
fix-and-validation cycles for the same behavior; no third without user approval.

Use only the assigned worktree `.venv`, explicit `UV_PROJECT_ENVIRONMENT`, and
the task's dedicated `privacy_engine_322` PostgreSQL database. Never mutate
another ticket environment/database, the container lifecycle or global installs.

Final required full engine suite and project checks run once on the final
reconciled revision, after actual dependency delivery: Ruff, strict public API
typing, contract structure/anchors, wheel/source build, installed-wheel check,
and full pytest with required extras and the assigned live PostgreSQL service.
Root schedules independent reviews. No push/PR/rebase/publication is authorized
until root releases final reconciliation.

## Explicit remaining integration gates

Real #321 custody and #323/#324 query/generated/profile/source/copy integration
are dependencies, not successful fake-provider evidence. Positive query,
profile and summary controls remain required and pending until the corresponding
actual route exists. Unavailable routes cannot manufacture an SN pass. The
fixed 69-row report and actual local recognition are separate from the combined
protected-product gate in #327. No anonymity, leak-proof, universal detection or
semantic entailment claim is made.
