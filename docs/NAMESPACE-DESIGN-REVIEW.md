# Named memory partitions: implementation review

Reviewed 2026-09-12 against the current code and primary documentation. This is
a design comparison, not an implemented namespace API or a new GSD milestone.

PRME's owner and scope filters cannot distinguish two named projects with the
same owner and `Scope.PROJECT`. A `project_id` in metadata is not a mandatory
boundary for entity resolution, organizer merges, receipts or recovery. Separate
packs remain the supported solution. The catalog collision fixed at `afc35ad`
also shows why adding a PostgreSQL search-path setting alone is insufficient.

[Hindsight's memory-bank API](https://hindsight.vectorize.io/developer/api/memory-banks)
describes a partition covering memories, documents, entities and relationships.
[LangChain's long-term-memory API](https://docs.langchain.com/oss/python/langchain/long-term-memory)
addresses stored items through namespaces and keys. These are useful developer
contracts to compare; they do not by themselves prove PRME's isolation or
establish a winning storage implementation.

| Approach | Useful properties | Costs and unresolved requirements |
|---|---|---|
| Bind one engine to one local pack or PostgreSQL schema | Naturally covers unscoped maintenance, receipts, recovery and all derived data in that engine; local indexes remain physically separate. | More engines/indexes/connections as partition count grows. PostgreSQL type/operator resolution and every pool connection need explicit handling. Copy/open must verify partition identity. |
| Add a partition ID throughout shared tables and indexes | Supports many partitions with shared pools and storage; a natural basis for cross-partition administrative operations. | Requires migrations and mandatory checks on every read, write, edge, queue, receipt and replay path. Approximate vector search must preserve authorized recall and avoid post-limit filtering. Owner and partition must remain distinct. |
| Filter a metadata key after retrieval | Small initial API change. | Rejected: ingestion and maintenance can combine projects before retrieval, and omitted filters expose mixed state. |

The next experiment should prototype engine-bound physical partitions and
measure cold open, steady memory, connection counts and retrieval across 1, 10
and 100 partitions with one shared caller-owned embedding provider. Compare the
cost with a shared-table prototype before choosing a scalable hosted default.
Do not introduce two incomplete production namespace implementations at once.

The PostgreSQL prototype must exclude fallback to a different schema when a
private relation is missing. Extension types and operators need explicit safe
resolution; arbitrary schema text must not be interpolated as SQL. PostgreSQL
documents both schema privileges and the trust implications of search paths in
its [schema documentation](https://www.postgresql.org/docs/current/ddl-schemas.html).
A schema boundary is not a substitute for application authorization or database
roles, and privileged direct database access remains privileged.

Before promotion, the same-owner/two-project workflow must preserve distinct
entities, contradictory claims, feedback receipts and pending work through
ingestion, retrieval, maintenance, restart, failure recovery and copying. Foreign
IDs must not reveal content or mutate another partition. Historical packs and
journal checksums must keep their existing interpretation. Tenant HTTP/MCP
credentials must bind allowed partitions explicitly; a request-supplied name is
not an access grant. The full RFC-0004 hierarchy and grant model remains separate
from this initial partition contract.
