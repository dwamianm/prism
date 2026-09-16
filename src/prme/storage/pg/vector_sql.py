"""Resolve pgvector symbols without adding its schema to the table search path."""

from dataclasses import dataclass

import asyncpg


def quote_identifier(value: str) -> str:
    """Quote an identifier, including names read from PostgreSQL's catalog."""
    if not value or "\x00" in value:
        raise ValueError("PostgreSQL identifiers must be nonempty and contain no NUL")
    return '"' + value.replace('"', '""') + '"'


@dataclass(frozen=True)
class VectorSQL:
    schema: str

    @property
    def type(self) -> str:
        return f'{quote_identifier(self.schema)}.vector'

    @property
    def cosine(self) -> str:
        return f'OPERATOR({quote_identifier(self.schema)}.<=>)'

    @property
    def dimensions(self) -> str:
        return f'{quote_identifier(self.schema)}.vector_dims'

    @property
    def cosine_ops(self) -> str:
        return f'{quote_identifier(self.schema)}.vector_cosine_ops'


async def resolve_vector_sql(conn: asyncpg.Connection) -> VectorSQL:
    schema = await conn.fetchval(
        "SELECT n.nspname FROM pg_catalog.pg_extension e "
        "JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'vector'"
    )
    if schema is None:
        raise RuntimeError("PostgreSQL vector extension must be installed before opening memory storage")
    return VectorSQL(schema)
