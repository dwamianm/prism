"""Direct writes preserve explicit real-world validity independently of source time."""

from datetime import datetime, timedelta, timezone

import pytest

from prme import AssertionStateQuery, MemoryClient, MemoryEngine
from prme.types import NodeType, Scope
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user

EVENT_TIME = datetime(2024, 12, 15, 9, tzinfo=timezone.utc)
VALID_FROM = datetime(2025, 1, 1, tzinfo=timezone.utc)
VALID_TO = datetime(2025, 7, 1, tzinfo=timezone.utc)


def _metadata() -> dict[str, str]:
    return {
        "subject": "Alice",
        "predicate": "office",
        "object": "Chicago",
        "polarity": "positive",
    }


def _state_query(valid_at: datetime) -> AssertionStateQuery:
    return AssertionStateQuery(
        subject="Alice",
        predicate="office",
        scope=Scope.PROJECT,
        valid_at=valid_at,
    )


async def test_direct_store_preserves_half_open_validity_window(config, user):
    async with MemoryEngine.open(config) as engine:
        receipt = await engine.store_with_receipt(
            "Alice's office is Chicago.",
            user_id=user,
            node_type=NodeType.FACT,
            scope=Scope.PROJECT,
            event_time=EVENT_TIME,
            valid_from=VALID_FROM,
            valid_to=VALID_TO,
            metadata=_metadata(),
        )
        event = await engine.get_event(str(receipt.event_id), user_id=user)
        node = await engine.get_node(str(receipt.node_id), user_id=user)
        assert event.event_time == EVENT_TIME
        assert node == receipt.node
        assert node.event_time == EVENT_TIME
        assert node.valid_from == VALID_FROM
        assert node.valid_to == VALID_TO

        expected = [
            (VALID_FROM - timedelta(microseconds=1), "unknown"),
            (VALID_FROM, "single"),
            (VALID_TO - timedelta(microseconds=1), "single"),
            (VALID_TO, "unknown"),
        ]
        for valid_at, status in expected:
            state = await engine.get_assertion_state(
                _state_query(valid_at), user_id=user
            )
            assert state.status == status
            assert state.timeline[0].valid_from == VALID_FROM
            assert state.timeline[0].valid_to == VALID_TO


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"valid_from": datetime(2025, 1, 1)}, "valid_from must be a timezone-aware datetime"),
        (
            {"valid_from": VALID_FROM, "valid_to": datetime(2025, 7, 1)},
            "valid_to must be a timezone-aware datetime",
        ),
        ({"valid_to": VALID_TO}, "valid_to requires an explicit valid_from"),
        (
            {"valid_from": VALID_FROM, "valid_to": VALID_FROM},
            "valid_to must be after valid_from",
        ),
        (
            {"valid_from": VALID_TO, "valid_to": VALID_FROM},
            "valid_to must be after valid_from",
        ),
    ],
)
async def test_invalid_validity_fails_before_source_admission(
    config, user, kwargs, message
):
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match=message):
            await engine.store("Invalid interval", user_id=user, **kwargs)
        assert await engine.get_events(user) == []


def test_sync_client_preserves_explicit_validity(config, user):
    with MemoryClient(config=config) as client:
        receipt = client.store_with_receipt(
            "Alice's office is Chicago.",
            user_id=user,
            node_type=NodeType.FACT,
            scope=Scope.PROJECT,
            event_time=EVENT_TIME,
            valid_from=VALID_FROM,
            valid_to=VALID_TO,
            metadata=_metadata(),
        )
        assert receipt.node.event_time == EVENT_TIME
        assert receipt.node.valid_from == VALID_FROM
        assert receipt.node.valid_to == VALID_TO
        assert client.get_assertion_state(
            _state_query(VALID_TO), user_id=user
        ).status == "unknown"
