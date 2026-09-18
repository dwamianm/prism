"""Validate pairwise lifecycle evidence inside the caller's transaction."""

from uuid import UUID

EVIDENCE_ERROR = "Evidence event is not available in the node owner and scope"


def _identity(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError(EVIDENCE_ERROR)
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(EVIDENCE_ERROR) from None


def validate_duckdb(conn, evidence_id, owner: str, scope: str) -> UUID | None:
    evidence = _identity(evidence_id)
    if (
        evidence is not None
        and conn.execute(
            "SELECT id FROM events WHERE id=? AND user_id=? AND scope=?",
            [str(evidence), owner, scope],
        ).fetchone()
        is None
    ):
        raise ValueError(EVIDENCE_ERROR)
    return evidence


async def validate_postgres(conn, evidence_id, owner: str, scope: str) -> UUID | None:
    evidence = _identity(evidence_id)
    if (
        evidence is not None
        and await conn.fetchval(
            "SELECT id FROM events WHERE id=$1 AND user_id=$2 AND scope=$3 FOR KEY SHARE",
            str(evidence),
            owner,
            scope,
        )
        is None
    ):
        raise ValueError(EVIDENCE_ERROR)
    return evidence
