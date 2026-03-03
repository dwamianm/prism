"""PostgreSQL storage backends for PRME.

Alternative to the file-based DuckDB/USearch/Tantivy stack. All four
storage layers (events, graph, vector, lexical) are backed by a single
PostgreSQL instance using pgvector for embeddings and tsvector for
full-text search.

Activated by setting ``database_url`` in PRMEConfig (or the
``PRME_DATABASE_URL`` environment variable).
"""

from prme.storage.pg.event_store import PgEventStore
from prme.storage.pg.graph_store import PgGraphStore
from prme.storage.pg.lexical_index import PgLexicalIndex
from prme.storage.pg.pool import create_pool
from prme.storage.pg.schema import initialize_pg_database
from prme.storage.pg.vector_index import PgVectorIndex

__all__ = [
    "PgEventStore",
    "PgGraphStore",
    "PgLexicalIndex",
    "PgVectorIndex",
    "create_pool",
    "initialize_pg_database",
]
