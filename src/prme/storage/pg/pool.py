"""asyncpg connection pool factory for PostgreSQL backend."""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


async def create_pool(
    database_url: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create an asyncpg connection pool.

    Args:
        database_url: PostgreSQL connection string
            (e.g. ``postgresql://user:pass@host:5432/dbname``).
        min_size: Minimum number of connections to keep open.
        max_size: Maximum number of connections in the pool.

    Returns:
        An initialized asyncpg connection pool ready for use.
    """
    pool = await asyncpg.create_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
    )
    logger.info("PostgreSQL connection pool created (min=%d, max=%d)", min_size, max_size)
    return pool
