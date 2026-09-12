"""Bind fresh local packs and verify their identity before normal startup."""

from uuid import UUID

import duckdb


class NamespaceIdentityError(ValueError):
    """A pack does not have the identity the caller expects."""


def bind_namespace(conn: duckdb.DuckDBPyConnection, expected: UUID, *, fresh: bool) -> None:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main' "
        "AND table_name = 'prme_namespace_identity'"
    ).fetchone()
    assert row is not None
    exists = row[0]
    if not exists:
        if not fresh:
            raise NamespaceIdentityError("Existing pack has no namespace identity; explicit import is required")
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("CREATE TABLE prme_namespace_identity (singleton BOOLEAN PRIMARY KEY CHECK(singleton), "
                         "version INTEGER NOT NULL CHECK(version = 1), namespace_id UUID NOT NULL)")
            conn.execute("INSERT INTO prme_namespace_identity VALUES (true, 1, ?)", [str(expected)])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    rows = conn.execute("SELECT version, namespace_id FROM prme_namespace_identity").fetchall()
    if rows != [(1, expected)]:
        raise NamespaceIdentityError("Pack namespace identity does not match the requested namespace")
