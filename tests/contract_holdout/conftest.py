"""The contract scorecard's hidden set: the visible set's tests on different data.

Kept from the person working on the engine so the visible set cannot be tuned to it. See tests/contract/README.md.
"""

from pathlib import Path

import pytest

from tests.contract.harness import (  # noqa: F401 (fixtures)
    RECORDING_NAME,
    _no_network,
    _one_extraction_call_per_turn,
    add_memory_mode_option,
    chat,
    check_expected,
    embedding_provider,
    ingest_chat,
    memory_mode,
    parametrize,
    robot,
    structure_options_off,
    summarize,
)
from tests.contract_holdout import expected, world

check_expected(world.ROBOT, world.CHAT, expected.XFAIL, expected.XFAIL_INGEST, expected.XFAIL_INGEST_ADDRESS_FIRST)


@pytest.fixture(scope="session")
def robot_world():
    return world.ROBOT


@pytest.fixture(scope="session")
def chat_world():
    return world.CHAT


def pytest_generate_tests(metafunc):
    parametrize(metafunc, world.ROBOT, world.CHAT, expected.XFAIL, expected.XFAIL_INGEST,
                expected.XFAIL_INGEST_ADDRESS_FIRST)


@pytest.fixture(scope="session")
def chat_recording():
    return Path(__file__).parent / "fixtures" / RECORDING_NAME


def pytest_addoption(parser):
    add_memory_mode_option(parser)


def pytest_terminal_summary(terminalreporter):
    # Totals only: the hidden set's per-group results stay out of sight of whoever works against the visible set.
    summarize(terminalreporter, "tests/contract_holdout/", by_group=False)
