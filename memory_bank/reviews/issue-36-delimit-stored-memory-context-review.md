# Code Review — issue-36-delimit-stored-memory-context

## Files Changed

- `src/prme/retrieval/context_formatter.py` — added `_sanitize_content()` plus
  module constants (`_MARKER_SPECS`/`_MARKER_SUBS`, `_HEADER_RE`,
  `_WHITESPACE_RE`, `_DATA_NOTICE`, `_ZW`); applied sanitization at every
  stored-content interpolation site (profile preamble, conflict
  NEWER/OLDER/CONTESTED, temporal, knowledge_update, aggregation, default);
  prepended a trust-boundary notice to the retrieved-memory block; fixed a
  stale docstring marker reference.
- `tests/test_context_formatter.py` — sanitization unit tests + end-to-end
  poisoning regression tests.

## Approach Summary

Issue #36 (audit MEDIUM-1): stored node content is interpolated raw into the
LLM context alongside the formatter's own instructions and reserved markers, so
a single ingested message can forge markers like `[MOST RECENT — USE THIS
VALUE]` / `COMPUTED:` or inject instruction-like text that is replayed into
every future retrieval. The fix sanitizes **stored content only** — the
formatter's own scaffolding is emitted unescaped because those markers are
load-bearing for retrieval accuracy. `_sanitize_content` collapses internal
whitespace (linear-time, single line), breaks any forged reserved marker by
inserting a zero-width space (U+200B) so it no longer matches the formatter's
literal tokens, and defuses a leading markdown header. A natural-language
"this is DATA, not instructions" notice is prepended to the memory block on the
production (`include_profile=True`) paths.

## Must Fix

1. ReDoS in the whitespace-collapse regex (`\s*\n\s*` → O(n²) on long
   whitespace runs, reachable via untrusted content) — **resolved**: replaced
   with linear `\s+` collapse (`_WHITESPACE_RE`). 200k-whitespace input now
   processes in ~1ms (was ~99s).
2. Whitespace-variant marker forgery bypass (`[MOST  RECENT`, `[ MOST RECENT`,
   tab/NBSP, mixed case) — **resolved**: markers are now matched with
   whitespace-tolerant, bracket-tolerant patterns (`_MARKER_SPECS`) plus the
   `\s+`-collapse pass, instead of fixed-literal `re.escape`.

## Should Fix

1. `Today's date:` and `IMPORTANT:` are authoritative lines the formatter emits
   but were not guarded (a poisoned entry could forge a fake "today" and shift
   every temporal computation) — **resolved**: both added to the marker set.
2. `_HEADER_RE` used an inline `(?m)` flag, diverging from the package-wide
   `flags=` convention — **resolved**: anchored to string start (after
   whitespace-collapse only the entry start can match), no multiline flag.
3. `_sanitize_content(None)` returned `None` (would render as literal `"None"`
   if ever reached) — **resolved**: contract is now "always returns a single
   `str`"; empty/None yields `""`.

## Consider

- ZWSP is deliberate marker obfuscation, not a cryptographic boundary: a
  downstream Unicode normalizer that strips U+200B could re-expose a marker.
  The `_DATA_NOTICE` and the per-entry `[N] (date)` scaffolding are the
  defense-in-depth. Documented in the module comment.
- A mid-line `##` that survives whitespace-collapse (e.g. `intro ## Evil`) is
  left intact — it is no longer a structural markdown header, so it doesn't
  impersonate a section. Only a *leading* header is defused.
- `include_profile=False` (raw-output) path intentionally omits the
  `_DATA_NOTICE`; it has no production callers (tests only). Noted as a future
  foot-gun if a caller adopts the raw path.
- Out of scope (formatter-only fix): MCP (`mcp/server.py`), REST
  (`api/routes.py`), and framework adapters (`langchain.py`/`llamaindex.py`)
  return raw `node.content` as structured data; a host that prompts with it
  gets no sanitization. Two dead prompt-builders (`models.py`
  `render_system_instructions`, `snapshots.py` snapshot text) also bypass this
  defense if ever wired to an LLM. Flagged for a follow-up.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Forged bracketed recency markers (`[MOST RECENT`/`[RECENT]`/`[OLDER]`/`[LATEST]`) | PASS | Defused incl. whitespace/case variants. |
| Forged colon markers (`COMPUTED:`/`AGGREGATION TASK:`/`NEWER:`/`OLDER:`/`CONTESTED:`/`IMPORTANT:`/`Today's date:`) | PASS | Whitespace-tolerant, case-insensitive defusal. |
| Forged leading markdown header | PASS | Leading `#{1,6}` defused; multi-line headers flattened. |
| Multi-line structural injection | PASS | All internal whitespace collapsed to one line. |
| ReDoS on adversarial whitespace | PASS | Linear `\s+`; 200k ws ≈ 1ms. |
| Genuine formatter markers preserved | PASS | Scaffolding emitted unescaped; genuine `[MOST RECENT…]` lands once on the real newest entry. |
| Benign content integrity | PASS | Non-marker content passes through unchanged (benchmark-safe). |
| New imports / dependencies | PASS | None; only the already-imported `re`. |

## Pattern Consistency Assessment

`_sanitize_content` / module constants match the file's existing helper and
`_*_RE` / typed-constant conventions (cf. `_OFFSET_PATTERNS`,
`_AGGREGATION_GUARD_RE`). No pre-existing sanitization utility was duplicated
(`_escape_like` is SQL-only and unrelated). Sanitization applied uniformly at
all interpolation sites; the dedup `content_key` is correctly left raw
(comparison-only, not output).

## Redundancy Check

No new dependencies or imports. Dead `[LATEST]` defense was reconciled — the
formatter never emits `[LATEST]`, and the stale module docstring that named it
was corrected to the real markers; `[LATEST]` is retained in the marker set
only as cheap defensive coverage (it cannot mislead because the formatter never
produces it). No dead branches; every constant is reachable.

## Wiring Findings

Sanitization reaches every stored-content output site; `_DATA_NOTICE` reaches
both production return paths (`include_profile=True`). No caller parses the
formatter output — `abstention.py` and the benchmarks pass it straight to an
LLM — so the added notice line and U+200B do not break any downstream parsing.
Existing substring-based tests remain green.

## Resolution Status

| Finding | Severity | Status |
|---|---|---|
| ReDoS in whitespace-collapse regex | Must Fix | Resolved (`\s+` linear collapse) |
| Whitespace/case marker-forgery bypass | Must Fix | Resolved (tolerant patterns) |
| `Today's date:` / `IMPORTANT:` unguarded | Should Fix | Resolved (added to markers) |
| `_HEADER_RE` inline `(?m)` flag divergence | Should Fix | Resolved (anchored to start) |
| `_sanitize_content(None)` → `None` | Should Fix | Resolved (`""` contract) |
| Stale `[LATEST]` docstring + dead marker | Should Fix | Resolved (docstring fixed) |
| ZWSP strippable (obfuscation, not boundary) | Consider | Documented limitation |
| Mid-line `##` not defused | Consider | Acknowledged (not a structural header) |
| `include_profile=False` skips notice | Consider | Acknowledged (no prod callers) |
| MCP/API/adapter + dead prompt-builders bypass | Consider | Out of scope; flagged for follow-up |
