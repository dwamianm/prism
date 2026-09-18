# Identity, relationship preservation and maintenance boundaries

The PersonaMem annotated-reader diagnostic exposed third-party attribution
errors on raw histories. A separate code audit then reproduced ingestion and
organizer defects. These fixes address demonstrated storage behavior; they do
not establish an improvement on that reader benchmark.

## Ingestion and merge admission

At `fbac34a`, 26 corrected authored tests failed across DuckDB and PostgreSQL:
matching text or vectors could merge different types, source roles, episodes,
claim metadata or validity starts. Another reproduction failed eight personal
reference tests while six explicit non-personal name controls passed. Literal
`I`, `we` and `they` could be reused globally within an owner/scope.

`b21f099` adds event-local unresolved references and conservative merge admission.
New plans identify the policy as `event_local_references_v4`; historical plan
policies, identities and checksums retain their original meaning. Organizer
application checks durable text, provenance, type and validity, independently of
a caller's similarity score or candidate label. A semantic alias score alone
creates an unverified link and preserves both entities.

The installed real-BGE workflow embeds two long authored policies with opposite
final approval rules. Their vectors are exactly equal because the qualifiers
fall beyond the encoder's window. The baseline organizer merged them and left
one active claim. The fixed workflow retains both original claims and source
events after reopening. This is a component reproduction using explicitly
authored graph inputs, not extraction quality, full rebuild or QA evaluation.

An initial test fixture incorrectly tried to mutate immutable `valid_from`.
Those failures are retained but excluded from the corrected 26-case reproduction.
Another existing tenant-isolation test expected independently timed observations
to merge; its fixture now uses genuine copies with the same validity start.
The frozen `b21f099` full run completed with that one failure, 2,718 passes and
81 skips. A preceding run interrupted for test-database setup is also retained.

## Explicit operator control of legacy feedback

At `b21f099`, ten new boundary tests failed and two explicit operator controls
passed. Ordinary `organize()` and `end_session(user_id=...)` could consume
anonymous signals and change engine-global weights. A scoped call only logged a
warning before applying those changes to every user.

`e385702` separates the available job registry from default maintenance jobs.
Default organization excludes `feedback_apply`, session completion runs promotion
only, and a scoped explicit feedback request raises before any pending-work drain
or job executes. Pending signals remain intact. Explicit unscoped operator use
remains available. The focused Python/HTTP/MCP boundary suite passed 162 tests;
installed Python 3.13 combined maintenance/identity checks passed 247 with two
backend-specific skips. This contains legacy behavior; evaluated scoped profile
activation remains unimplemented.

## PostgreSQL catalog resolution

Two live database tests reproduced unrelated schemas suppressing the selected
table's embedding column or HNSW index. `afc35ad` replaces database-wide name
checks with native DDL against the selected relation. PostgreSQL creates an
index in its parent table's schema and supports `IF NOT EXISTS` for installed
index methods. [PostgreSQL CREATE INDEX documentation](https://www.postgresql.org/docs/current/sql-createindex.html)
describes that behavior and its limit: an existing same-name relation is not
proof of an equivalent index definition.

Seven source and seven installed initialization/vector integration tests passed.
The full `afc35ad` collection passed 2,733 tests with 81 skips in 368.96 seconds,
including live PostgreSQL, research and examples. This catalog fix does not add
a named-project API, permission grants or database row-level security.

## Relationship copying and retries

The next audit found that both duplicate and alias merges copied relationship
endpoints but discarded validity windows, provenance and original assertion time.
They swallowed edge-copy failures and could still retire the source. Retries
appended new random-ID copies of already transferred relationships.

The corrected pre-fix reproduction at `afc35ad` failed eight tests and passed two
acknowledgment-loss controls. Source comparisons use durable original values,
including the backends' float32 confidence representation. An earlier test
compared against pre-storage Python floats; its two residual failures are retained
and are not counted as a production confidence defect.

`05e6654` copies complete relationship values with deterministic transfer IDs.
Existing copies must match exactly; failures keep the source active. Concurrent
identical transfers converge, lost acknowledgments are verified against stored
values, and an explicit self-relationship becomes one canonical self-relationship.
The source suite passed 130 tests and the installed Python 3.13 suite passed the
same 130 tests. All 118 installed package source files match the frozen commit.

The whole merge is still not atomic: partial copies may remain visible after
failure, and unrelated concurrent merges do not yet share a transactional
publication protocol. Named entity matching still cannot distinguish equal-name
homonyms. The personal-reference guard is English-specific and does not resolve
multiple quoted speakers inside one source event. Existing bad identities are
not automatically split. These limitations remain implementation work.

The final frozen `05e6654` full collection completed with native exit 0:
**2,745 passed, 81 skipped in 354.97 seconds**, including live PostgreSQL,
research and examples. Both final installed real-BGE workflows also passed.
The [JSON record](identity-and-maintenance-05e6654.json) contains completed run
summaries, source identities and artifact/log hashes. No new memory-QA score or
comparative leadership claim follows from these checks.

An explicit root `.env` service recheck at 22:12 UTC returned HTTP 429 for both
configured OpenAI models, with no recognized quota/rate-limit subcode. The
[sanitized record](openai-file-health-recheck-221224.json) retains no credential,
provider endpoint or response body. Local-model work remains available.
