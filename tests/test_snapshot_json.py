"""Legacy special floats have explicit paths; finite journal bytes stay stable."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from uuid import UUID, uuid4

import pytest

from prme.storage import _snapshot_json as codec


def legacy_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError


def test_finite_snapshot_keeps_original_wire_bytes():
    value = {
        "metadata": {"unicode": "é", "zero": -0.0, "values": [None, True, 2, 1.25]},
        "time": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "id": uuid4(),
    }
    raw = json.dumps(value, default=legacy_default, allow_nan=False)
    assert codec.dumps(value) == raw
    assert codec.loads(raw) == json.loads(raw)


def test_special_float_paths_preserve_nulls_strings_and_tag_like_metadata():
    value = {
        "metadata": {
            "0": [
                float("nan"),
                float("inf"),
                float("-inf"),
                None,
                "NaN",
                {"_snapshot_encoding": codec.ENCODING, "value": None},
            ]
        }
    }
    raw = codec.dumps(value)
    json.loads(raw, parse_constant=lambda _: pytest.fail("Non-standard JSON emitted"))
    restored = codec.loads(raw)
    values = restored["metadata"]["0"]
    assert (
        math.isnan(values[0])
        and values[1] == float("inf")
        and values[2] == float("-inf")
    )
    assert values[3:] == value["metadata"]["0"][3:]
    assert codec.dumps(restored) == raw
    assert codec.dumps({"b": float("nan"), "a": 1}, sort_keys=True) == codec.dumps(
        {"a": 1, "b": float("nan")}, sort_keys=True
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "missing",
        "negative_index",
        "bool_index",
        "root",
        "kind",
        "duplicate",
        "not_null",
        "empty",
    ],
)
def test_invalid_special_float_document_is_rejected(mutation):
    document = json.loads(codec.dumps({"metadata": [float("nan"), 1]}))
    entry = document["nonfinite"][0]
    if mutation == "version":
        document["_snapshot_encoding"] = "unknown"
    elif mutation == "missing":
        entry["path"] = ["absent", 0]
    elif mutation == "negative_index":
        entry["path"][-1] = -1
    elif mutation == "bool_index":
        entry["path"][-1] = False
    elif mutation == "root":
        entry["path"] = []
    elif mutation == "kind":
        entry["kind"] = "unknown"
    elif mutation == "duplicate":
        document["nonfinite"].append(deepcopy(entry))
    elif mutation == "not_null":
        entry["path"][-1] = 1
    else:
        document["nonfinite"] = []
    with pytest.raises(ValueError, match="snapshot special-float"):
        codec.loads(json.dumps(document))


@pytest.mark.parametrize(
    "raw", ['{"value": NaN}', '{"value": Infinity}', '{"value": 1e999}']
)
def test_unencoded_special_floats_are_rejected(raw):
    with pytest.raises(ValueError, match="Unencoded nonfinite"):
        codec.loads(raw)


def test_all_snapshot_journal_writers_keep_finite_record_bytes():
    from prme.models.nodes import MemoryNode
    from prme.storage import lifecycle, reinforcement, organizer_merge
    from prme.types import NodeType, LifecycleState

    before = MemoryNode(
        user_id="owner",
        node_type=NodeType.FACT,
        content="A claim",
        metadata={"negative_zero": -0.0},
    )
    after = before.model_copy(update={"lifecycle_state": LifecycleState.STABLE})
    records = [
        (
            lifecycle,
            lifecycle.LifecycleRecord(
                operation_id=uuid4(), action="promote", before=before, after=after
            ),
        ),
        (
            reinforcement,
            reinforcement.ReinforcementRecord(
                operation_id=uuid4(), evidence_id=None, before=before, after=before
            ),
        ),
        (
            organizer_merge,
            organizer_merge.MergeRecord(
                operation_id=str(uuid4()),
                kind="duplicate",
                user_id="owner",
                score=1.0,
                canonical_before=before,
                retired_before=before,
                canonical_after=before,
                retired_after=before,
                original_edges=(),
                published_edges=(),
            ),
        ),
    ]
    for module, record in records:
        payload = json.loads(module._payload(record))
        assert payload["record"] == json.dumps(
            record.model_dump(mode="python"), default=legacy_default, allow_nan=False
        )
