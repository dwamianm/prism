# Local content sanitization

`LocalSanitizer` is an explicit implementation of the privacy admission
capability. It applies to both standard and sealed policies. It is a prerequisite
for the protected product, not a completed privacy mode: query/read integration,
profile/summary publication, native key custody and durable replay accounting
still have separate gates. No global setting changes when this extra is installed.

Install the pinned local recognizers in the application's environment:

```sh
uv sync --extra privacy
```

The extra pins `presidio-analyzer==2.2.364`, `spacy==3.8.11`, and
`en_core_web_sm==3.8.0`, with dependency markers for Python 3.11–3.14. The local
installation and recognition evidence uses Python 3.11.6. Initialization checks the exact
versions and model file digest. Install artifacts before starting the service.
Recognition uses a separate local process with network connections disabled;
there is no default remote detector or runtime model download. Email suffix
validation uses the bundled suffix snapshot with network URLs and disk caching
disabled. The owner must call `LocalSanitizer.close()` at shutdown.

Pass a `LocalSanitizer` to an explicitly constructed `PrivacyRuntime`, alongside
the authority, protector and embedding capabilities required by
[privacy admission](PRIVACY-POLICIES.md). A configured extraction provider can
be adapted explicitly with `InstructorExtractionProvider.privacy_adapter()`.
The adapter supports OpenAI-compatible OpenAI/Ollama and Anthropic clients,
guards each actual transmission, disables SDK/transport retries and redirects,
and returns unvalidated output to admission before model validation or repair.
The tests use synthetic HTTP responses; no remote model result is claimed.
Other provider capabilities must satisfy the same per-transmission contract.
Protected owner/group linking is unsupported and requires the explicit
`enable_owner_group_links=False` option.

## Coverage and decisions

Policies must declare `language="en"`. This is a declaration, not reliable
language identification: mixed or misdeclared text can contain undetected
values. The frozen registry has explicit recognizers for email, US/GB phone,
person, location, US SSN, payment card and IP address, plus bounded credential
forms and configured exact literals. Names and locations use a small English
model and are heuristic. The four supported lowercase email obfuscations are
listed in the [sanitizer contract](plans/2026-10-evidence-privacy/sanitizer-contract.json).
Homoglyphs, arbitrary encodings, other languages and unlisted secret formats
remain outside the declared detection scope. A clean scan does not prove the
absence of secrets.

The effective category action is `allow`, `redact`, `pseudonymize` or `block`.
Credential blocking is mandatory. Overlapping redactions cover their union;
ambiguous overlapping pseudonyms and identity-collapsing person redactions
reject the operation. Body, retrieval text, speaker, structured value bindings,
and nested metadata keys and values pass through the same operation. Invalid or
colliding keys, malformed offsets, incomplete recognizer results, unsupported
configurations, detector errors and timeouts deny before publication.

Custom rules are supplied by a trusted `RuleProvider`, resolved from opaque
policy references. `CustomRule` supports exact `literal_identifier`,
`literal_topic` and allowlisted `typed_identifier` forms. It does not accept
arbitrary regular expressions or caller classifiers. Private literal material
does not appear in policy inspection.

One operation shares a 65,536 UTF-8 byte input budget, a 2,000 ms cumulative
detector budget, depth 8 and 1,024 leaves. Custom configuration permits at most
64 rules, 256 bytes per literal and 8,192 literal bytes total. Synchronous batch
members and their generated representations share accounting. Detector startup
occurs before sending caller content; provider wait time is separate. Early
per-pattern timeouts propagate rather than becoming silently skipped matches;
a supervisor kills timed-out workers and discards their results. Pathological
custom-rule output exceeding 65,536 spans rejects without truncation.

## Pseudonyms and strict schemas

Pseudonyms use a separately provisioned 256-bit key for the exact
project/owner/scope/epoch and a domain-separated HMAC over the category and exact
value. `LocalSanitizer(key_provider=..., pseudonym_epoch=...)` consumes the
purpose-bound key handle; it does not provision keys. Missing, foreign or
unavailable epochs deny without replacement. Key inventory and erasure belong
to the key custodian. There is no plaintext reverse map or retained raw-value
hash. Pseudonyms preserve limited relationships and are not anonymity.

Source text containing the reserved token prefix is escaped before recognition.
Only coordinator-supplied, authenticated source tokens may recur in generated
output. Public metadata cannot authorize a placeholder. A generated new identity
cannot create an additional token under an existing source's provenance.

A trusted `SchemaProvider` resolves an opaque strict schema reference to
`ClosedSchema`. Each field has a `ValueValidator`: boolean, bounded integer,
opaque UUID, issued scoped pseudonym, exact decimal string, timezone-aware
timestamp or finite enum. Unknown fields and invalid values reject. Arbitrary
free text is not a strict-schema value type. The strict ingress representation
is a closed JSON object in the content body with empty auxiliary content fields;
ordinary free-form extraction is not automatically a compatible strict schema.

## Evidence and current limits

New sanitized sources have opaque source/representation IDs and a private,
domain-separated checksum covering the admitted fields, source role and event
time. Existing event and plan checksum formats remain unchanged. Grounding checks
verbatim sanitized-source paragraphs, endpoints, polarity, conditions and exact
quantities before planning. Unsupported ambiguity rejects. Declared temporal
references and explicit date/relative-time cues currently reject extraction
until their effective-time resolution can be validated; an explicit caller
event clock is preserved. The closed statement
projection preserves admitted claims and truth clocks, strips source quotes and
entity descriptions from ordinary plan/index text, and runs a final generated
bundle check before embedding. These checks are conservative and do not prove
general semantic entailment.

An optional trusted `SourceClassifier` can mark exact sanitized source regions
as source-only. Generated copies of those regions reject. The frozen SN12 fixture
classifies its entire synthetic paragraph through that trusted capability;
this is not an arbitrary source-only language detector. General read separation
has its own integration gate.

Durable reports contain fixed sanitizer/registry/detector versions and bounded
category/action counts, without removed values, spans or raw-value hashes.
Ingress reports survive processing cleanup, including capture-none. Generated
reports accompany admitted plan nodes; complete no-node/generated-job report
retention remains part of the pending journal integration.

The typed continuation carries cumulative accounting only inside authenticated
processing input and requires a live work claim. Retry/concurrent consumption
and multi-item asynchronous continuation still require the durable work
integration. Multi-item asynchronous extraction and repeated real-sanitizer
attempts currently deny; those refusals are not successful route coverage.
Protected queries, summary/profile publication, lossless framework history and
other unavailable routes retain their explicit denial until their real
integration is delivered.

The frozen local detector report has 69 synthetic rows: 34 supported positive
spans detected, 21 benign rows without a target-category false positive, and
14 separately labelled unsupported examples (1 detected, 13 missed). It is a
small declared fixture result, not a general precision/recall guarantee. The
12 scenario matrix on DuckDB and PostgreSQL remains **20 passed / 4 pending**;
SN11 query and SN12 profile/summary success gates remain open. See the
[implementation plan](plans/2026-10-evidence-privacy/PV-13-plan.md) and
[results](plans/2026-10-evidence-privacy/PV-13-results.json). Native custody,
combined product behavior and lifecycle erasure need their own evidence.
