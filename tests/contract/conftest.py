"""The contract scorecard's visible set. See README.md here, and harness.py for how product defaults are kept."""

from pathlib import Path

import pytest

from tests.contract import expected, world
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
    summarize(terminalreporter, "tests/contract/", by_group=True)
