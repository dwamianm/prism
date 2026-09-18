# MemoryArena travel development trial: native Ollama transport

Date: 2026-09-17

## Decision

The registered PRME raw-trace arm failed both development noninferiority gates.
Execution itself passed: all 156 traveler runs completed with zero agent failures
and no terminal transport error. The result rejects the current adapter and
storage representation for bounded-context agent trajectories.

| Strict metric | Native full history | PRME raw trace | PRME minus native |
|---|---:|---:|---:|
| Person success (PS) | 15.38 | 0.00 | -15.38 |
| Constraint-slot success (SPS) | 74.48 | 32.72 | -41.76 |
| Group success (SR) | 0.00 | 0.00 | 0.00 |

The unchanged upstream scorer also favored native history: PS was 32.05 versus
1.28 and SPS was 87.08 versus 36.25. Strict scoring remains primary because it
requires complete cohort coverage and exact full-field equality.

The cohort contained 12 complete groups and 78 people per arm, stratified by
group size. Arm order was counterbalanced within groups. Both arms used
`deepseek-v4.1-flash:cloud` through Ollama's native `/api/chat` endpoint with
thinking disabled, temperature 0, seed 17, a 180-second request timeout and two
transport attempts. The model alias and local manifest are pinned; the remote
weights are not.

## Failure mechanism

The benchmark supplies each completed experience as structured JSON containing
the traveler query, a large scratchpad with tool results, and the final plan.
Observed PRME entries were commonly 30–40 KB. Direct `store()` used that entire
source as one graph node, one vector document and one lexical document.

Under the registered 4,096-token memory budget, PRME usually packed one full
record—typically the base traveler's plan—and represented later candidates as
`key_value` references containing only an ID, type and confidence. Those
references carry no source text. For example, the final query in group 203 had
one full base record and six `key_value` references; none of the six prior
traveler names appeared in the rendered memory. The model explicitly reported
that only the base plan was available and tried to reconstruct missing plans
from tool searches.

This is primarily a representation failure rather than a transport or candidate
generation failure. A memory engine should preserve a full source trajectory for
audit and replay while indexing and presenting a compact task-relevant view. In
the current direct-store contract, event content and node content must be
identical, so callers must either discard the raw trace or make the retrieval
unit too large to use.

## Next change

Add a durable source/presentation split to direct storage:

- keep the caller's exact source in the immutable event log;
- journal a separately supplied retrieval/presentation string in a versioned
  direct-store record;
- index and pack that compact representation while retaining the event as its
  evidence reference;
- expose the option consistently in async Python, the synchronous client, HTTP
  and MCP;
- preserve version-1 record bytes and checksums, and prove restart recovery for
  version-2 projected records on DuckDB and PostgreSQL;
- update the MemoryArena adapter to project structured traces to the traveler
  identity and final plan while preserving the raw JSON source.

The same 12 groups are now an examined development cohort. They can be used to
diagnose and tune the new representation, but any acceptance claim requires a
separately registered untouched cohort.

## Artifacts

- Registration: `memoryarena-travel-deepseek-dev12-v5-registration.json`
- Canonical result: `memoryarena-travel-deepseek-dev12-v5-result.json`
- Registration SHA-256: `b96e02c411fb16a5cd4c2144130919f57816939dd29fe43fdf1e00b3dd733bc4`
- Result SHA-256: `beeb44f8d5ecd90318b615fec25b9ed3ceadceb69c1dbe400195ce2be01a450f`
- Executed PRME revision: `5f80929678e6633771ef9b333e26a3f090bcf8a3`
- Pinned upstream revision: `6cd9de14b71915e39ac742a20dc33785e14b6aab`

## Limits

This is a development cohort, one generation per arm, with a cloud-routed model
whose remote weights are not pinned. Strict string equality can reject
semantically equivalent itinerary text and is not a semantic validity judge.
Neither arm achieved group-level success. This trial establishes a real product
failure and a reproducible repair target; it does not rank PRME against other
memory packages.
