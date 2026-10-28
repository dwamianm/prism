"""Backend-neutral node and edge row decoding.

PostgreSQL graph reads and the protected export, restore and migration code
share these. They need no database driver, so a DuckDB-only install can use
them; importing ``prme.storage.pg`` requires asyncpg.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import DecayProfile, EdgeType, LifecycleState, NodeType, Scope

# Explicit column list for SELECT * replacement (positional independence).
NODE_COLUMNS = (
    "id, node_type, user_id, session_id, scope, content, "
    "metadata, confidence, salience, lifecycle_state, "
    "valid_from, valid_to, superseded_by, evidence_refs, "
    "created_at, updated_at, epistemic_type, source_type, "
    "decay_profile, last_reinforced_at, reinforcement_boost, "
    "salience_base, confidence_base, pinned, event_time, ttl_days"
)

EDGE_COLUMNS = (
    "id, source_id, target_id, edge_type, user_id, "
    "confidence, valid_from, valid_to, provenance_event_id, "
    "metadata, created_at"
)


def record_to_node(row: Mapping[str, Any]) -> MemoryNode:
    """Convert a node row (asyncpg Record or mapping) to a MemoryNode."""
    raw_id = row["id"]
    node_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))

    raw_evidence = row["evidence_refs"]
    evidence_refs: list[UUID] = []
    if raw_evidence is not None:
        if isinstance(raw_evidence, str):
            evidence_refs = [UUID(ref) for ref in json.loads(raw_evidence)]
        elif isinstance(raw_evidence, list):
            evidence_refs = [UUID(str(ref)) for ref in raw_evidence]

    raw_metadata = row["metadata"]
    if isinstance(raw_metadata, str):
        metadata = json.loads(raw_metadata)
    else:
        metadata = raw_metadata

    raw_superseded = row["superseded_by"]
    superseded_by = None
    if raw_superseded is not None:
        superseded_by = (
            raw_superseded
            if isinstance(raw_superseded, UUID)
            else UUID(str(raw_superseded))
        )

    def ensure_tz(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if isinstance(dt, datetime) and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    # Decay/reinforcement fields (RFC-0015) -- graceful fallback
    raw_decay_profile = row.get("decay_profile", "medium") if hasattr(row, "get") else "medium"
    raw_last_reinforced = row.get("last_reinforced_at") if hasattr(row, "get") else None
    raw_reinforcement_boost = row.get("reinforcement_boost", 0.0) if hasattr(row, "get") else 0.0
    raw_salience_base = row.get("salience_base", row["salience"]) if hasattr(row, "get") else row["salience"]
    raw_confidence_base = row.get("confidence_base", row["confidence"]) if hasattr(row, "get") else row["confidence"]
    raw_pinned = row.get("pinned", False) if hasattr(row, "get") else False

    return MemoryNode(
        id=node_id,
        node_type=NodeType(row["node_type"]),
        user_id=row["user_id"],
        session_id=row["session_id"],
        scope=Scope(row["scope"]),
        content=row["content"],
        metadata=metadata,
        confidence=row["confidence"],
        salience=row["salience"],
        lifecycle_state=LifecycleState(row["lifecycle_state"]),
        valid_from=ensure_tz(row["valid_from"]),
        valid_to=ensure_tz(row["valid_to"]),
        superseded_by=superseded_by,
        evidence_refs=evidence_refs,
        created_at=ensure_tz(row["created_at"]),
        updated_at=ensure_tz(row["updated_at"]),
        epistemic_type=row["epistemic_type"],
        source_type=row["source_type"],
        decay_profile=DecayProfile(raw_decay_profile) if raw_decay_profile else DecayProfile.MEDIUM,
        last_reinforced_at=ensure_tz(raw_last_reinforced) or datetime.now(timezone.utc),
        reinforcement_boost=raw_reinforcement_boost or 0.0,
        salience_base=raw_salience_base if raw_salience_base is not None else 0.5,
        confidence_base=raw_confidence_base if raw_confidence_base is not None else 0.5,
        pinned=bool(raw_pinned),
        event_time=ensure_tz(row.get("event_time")),
        ttl_days=row.get("ttl_days"),
    )


def record_to_edge(row: Mapping[str, Any]) -> MemoryEdge:
    """Convert an edge row (asyncpg Record or mapping) to a MemoryEdge."""

    def to_uuid(val: object) -> UUID:
        return val if isinstance(val, UUID) else UUID(str(val))

    def ensure_tz(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if isinstance(dt, datetime) and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    raw_provenance = row["provenance_event_id"]
    provenance_event_id = (
        to_uuid(raw_provenance) if raw_provenance is not None else None
    )

    raw_metadata = row["metadata"]
    if isinstance(raw_metadata, str):
        metadata = json.loads(raw_metadata)
    else:
        metadata = raw_metadata

    return MemoryEdge(
        id=to_uuid(row["id"]),
        source_id=to_uuid(row["source_id"]),
        target_id=to_uuid(row["target_id"]),
        edge_type=EdgeType(row["edge_type"]),
        user_id=row["user_id"],
        confidence=row["confidence"],
        valid_from=ensure_tz(row["valid_from"]),
        valid_to=ensure_tz(row["valid_to"]),
        provenance_event_id=provenance_event_id,
        metadata=metadata,
        created_at=ensure_tz(row["created_at"]),
    )
