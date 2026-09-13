"""Strict JSON snapshots that preserve special floats in legacy metadata.

Finite records retain their existing JSON bytes. Only records with nonfinite
values use the checksummed, versioned path encoding; metadata dictionaries
cannot collide with tags because tags are outside the typed record root.
"""

from datetime import datetime
import json
import math
from uuid import UUID

ENCODING = "prme-special-floats-v1"
_KINDS = {
    "nan": float("nan"),
    "positive_infinity": float("inf"),
    "negative_infinity": float("-inf"),
}


def _default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError("Snapshot value cannot be represented as JSON")


def dumps(value: dict, *, sort_keys: bool = False) -> str:
    try:
        return json.dumps(value, default=_default, allow_nan=False, sort_keys=sort_keys)
    except ValueError:
        # Normalize dates/UUIDs and keys exactly as the original serializer did.
        tree = json.loads(
            json.dumps(value, default=_default, allow_nan=True, sort_keys=sort_keys)
        )
    nonfinite = []

    def visit(item, path):
        if isinstance(item, float) and not math.isfinite(item):
            kind = (
                "nan"
                if math.isnan(item)
                else "positive_infinity"
                if item > 0
                else "negative_infinity"
            )
            nonfinite.append({"path": path, "kind": kind})
            return None
        if isinstance(item, dict):
            return {key: visit(child, [*path, key]) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child, [*path, index]) for index, child in enumerate(item)]
        return item

    tree = visit(tree, [])
    return json.dumps(
        {"_snapshot_encoding": ENCODING, "value": tree, "nonfinite": nonfinite},
        allow_nan=False,
        sort_keys=sort_keys,
    )


def _reject_constant(_):
    raise ValueError("Unencoded nonfinite snapshot value")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("Unencoded nonfinite snapshot value")
    return value


def _child(parent, key):
    if isinstance(parent, dict) and type(key) is str and key in parent:
        return parent[key]
    if isinstance(parent, list) and type(key) is int and 0 <= key < len(parent):
        return parent[key]
    raise ValueError("Invalid snapshot special-float path")


def loads(raw: str) -> dict:
    document = json.loads(
        raw, parse_constant=_reject_constant, parse_float=_finite_float
    )
    if not isinstance(document, dict):
        raise ValueError("Snapshot must contain a record")
    if "_snapshot_encoding" not in document:
        return document
    if (
        set(document) != {"_snapshot_encoding", "value", "nonfinite"}
        or document["_snapshot_encoding"] != ENCODING
        or not isinstance(document["value"], dict)
        or not isinstance(document["nonfinite"], list)
        or not document["nonfinite"]
    ):
        raise ValueError("Invalid snapshot special-float encoding")
    result = document["value"]
    for entry in document["nonfinite"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "kind"}
            or not isinstance(entry["path"], list)
            or not entry["path"]
            or not isinstance(entry["kind"], str)
            or entry["kind"] not in _KINDS
        ):
            raise ValueError("Invalid snapshot special-float entry")
        parent = result
        for key in entry["path"][:-1]:
            parent = _child(parent, key)
        key = entry["path"][-1]
        if _child(parent, key) is not None:
            raise ValueError("Invalid snapshot special-float target")
        parent[key] = _KINDS[entry["kind"]]
    return result
