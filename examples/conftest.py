"""Pytest fixtures for examples/integration_test.py.

Provides ``engine`` and ``log`` fixtures so the integration test functions
can be discovered and executed by pytest (issue #18).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio

from prme import MemoryEngine, PRMEConfig

from integration_test import TestLogger


@pytest_asyncio.fixture
async def engine(tmp_path: Path):
    """Create a MemoryEngine backed by a temporary directory.

    Yields the engine for test use, then closes it and cleans up.
    """
    lexical_dir = tmp_path / "lexical_index"
    lexical_dir.mkdir(parents=True, exist_ok=True)

    config = PRMEConfig(
        db_path=str(tmp_path / "memory.duckdb"),
        vector_path=str(tmp_path / "vectors.usearch"),
        lexical_path=str(lexical_dir),
    )

    eng = await MemoryEngine.create(config)
    yield eng
    await eng.close()


@pytest.fixture
def log(tmp_path: Path) -> TestLogger:
    """Create a TestLogger writing to a temporary log directory."""
    log_dir = tmp_path / "logs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    return TestLogger(log_dir)
