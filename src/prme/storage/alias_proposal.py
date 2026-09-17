"""Atomic, journaled publication of unverified entity-alias proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import struct
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prme.models.derivation import canonical_hash, node_checksum
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.merge_policy import alias_pair_allowed
from prme.storage import _snapshot_json
from prme.storage._threading import run_to_completion
from prme.types import EdgeType, LifecycleState, NodeType

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore


AliasType = Literal["abbreviation", "case_variation", "semantic"]
_ACTIVE = (LifecycleState.TENTATIVE, LifecycleState.STABLE)
_POLICY: Literal["unverified_alias_proposals_v1"] = (
    "unverified_alias_proposals_v1"
)
_EVIDENCE_POLICY: Literal["unverified_alias_proposals_v2"] = (
    "unverified_alias_proposals_v2"
)


class AliasProposalConflict(ValueError):
    """A deterministic alias identity conflicts with durable state."""


class StaleAliasProposalEvidence(ValueError):
    """The assessed node snapshots changed before proposal publication."""


class AliasProposalEvidence(BaseModel):
    """Complete Jev evidence bound to two exact product-node snapshots."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    version: Literal[1] = 1
    kind: Literal["typesafe_jev_product_alignment_v1"] = (
        "typesafe_jev_product_alignment_v1"
    )
    provider: str = Field(min_length=1)
    protocol: str = Field(min_length=1)
    model: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    left_node_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    right_node_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_payload(self) -> "AliasProposalEvidence":
        try:
            observed = canonical_hash(self.payload)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError(
                "Alias proposal evidence must contain finite JSON values"
            ) from exc
        if observed != self.payload_sha256:
            raise ValueError("Alias proposal evidence payload checksum mismatch")
        if self.provider != "typesafe_jev" or set(self.payload) != {
            "node_bindings",
            "assessment",
        }:
            raise ValueError("Alias proposal evidence has an invalid Jev payload")
        bindings = self.payload["node_bindings"]
        assessment = self.payload["assessment"]
        if (
            not isinstance(bindings, list)
            or len(bindings) != 2
            or not isinstance(assessment, dict)
            or assessment.get("provider") != self.provider
            or assessment.get("protocol") != self.protocol
            or assessment.get("model") != self.model
            or assessment.get("request_sha256") != self.request_sha256
            or assessment.get("assessment_sha256") != self.assessment_sha256
            or assessment.get("proposal_recommended") is not True
            or assessment.get("automatic_merge_authorized") is not False
        ):
            raise ValueError("Alias proposal evidence has an invalid Jev assessment")
        try:
            node_ids = [str(UUID(binding["node_id"])) for binding in bindings]
            products = [binding["product"] for binding in bindings]
            if any(
                set(binding) != {"node_id", "product"}
                or not isinstance(binding["product"], dict)
                for binding in bindings
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Alias proposal evidence has invalid product bindings"
            ) from exc
        if len(set(node_ids)) != 2:
            raise ValueError("Alias proposal evidence requires two distinct nodes")
        if (
            canonical_hash(products[0]) != assessment.get("left_sha256")
            or canonical_hash(products[1]) != assessment.get("right_sha256")
        ):
            raise ValueError("Alias proposal product hashes do not match")
        expected_request = canonical_hash(
            {
                "configuration_sha256": assessment.get("configuration_sha256"),
                "model": self.model,
                "questions_sha256": assessment.get("questions_sha256"),
                "state": {"entity_a": products[0], "entity_b": products[1]},
            }
        )
        result_identity = {
            "compatible_price_probability": assessment.get(
                "compatible_price_probability"
            ),
            "confidence": assessment.get("confidence"),
            "probabilities": assessment.get("probabilities"),
            "proposal_recommended": True,
            "protocol": self.protocol,
            "request_sha256": self.request_sha256,
            "same_manufacturer_probability": assessment.get(
                "same_manufacturer_probability"
            ),
            "same_name_probability": assessment.get("same_name_probability"),
            "score": assessment.get("score"),
        }
        if expected_request != self.request_sha256:
            raise ValueError("Alias proposal Jev request checksum does not match")
        if canonical_hash(result_identity) != self.assessment_sha256:
            raise ValueError("Alias proposal Jev assessment checksum does not match")
        return self


def _evidence_node_ids(evidence: AliasProposalEvidence) -> list[str]:
    return sorted(
        str(UUID(binding["node_id"]))
        for binding in evidence.payload["node_bindings"]
    )


class AliasProposalRecord(BaseModel):
    """Complete inputs and output for one accepted unverified alias proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    policy: Literal["unverified_alias_proposals_v1"] = _POLICY
    operation_id: UUID
    alias_type: AliasType
    score: float
    left_before: MemoryNode
    right_before: MemoryNode
    edge: MemoryEdge


class AliasProposalRecordV2(BaseModel):
    """Alias proposal with complete external assessment evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[2] = 2
    policy: Literal["unverified_alias_proposals_v2"] = _EVIDENCE_POLICY
    operation_id: UUID
    alias_type: AliasType
    score: float
    left_before: MemoryNode
    right_before: MemoryNode
    evidence: AliasProposalEvidence
    edge: MemoryEdge


AliasProposalJournalRecord = AliasProposalRecord | AliasProposalRecordV2


@dataclass(frozen=True)
class AliasProposalResult:
    operation_id: str | None
    edge_id: str
    applied: bool
    evidence: AliasProposalEvidence | None = None


def _float32(value: float) -> float:
    """Match the REAL/FLOAT precision used by both edge tables."""
    return float(struct.unpack("!f", struct.pack("!f", value))[0])


def _request(
    a: str,
    b: str,
    user_id: str,
    alias_type: str,
    score: float,
    evidence: AliasProposalEvidence | dict[str, Any] | None = None,
) -> tuple[
    list[str], UUID, str, AliasType, float, AliasProposalEvidence | None
]:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Alias proposal requires an owner")
    if alias_type not in {"abbreviation", "case_variation", "semantic"}:
        raise ValueError("Alias proposal requires a supported alias type")
    if (
        type(score) not in (int, float)
        or not math.isfinite(score)
        or not 0 <= score <= 1
    ):
        raise ValueError("Alias proposal requires a finite score in [0, 1]")
    ids = sorted((str(UUID(a)), str(UUID(b))))
    if ids[0] == ids[1]:
        raise ValueError("Alias proposal requires two distinct nodes")
    operation_id = uuid5(
        UUID(ids[0]), f"prme:unverified-alias-proposal:v1:{ids[1]}"
    )
    frozen_evidence = (
        None
        if evidence is None
        else AliasProposalEvidence.model_validate(evidence).model_copy(deep=True)
    )
    return (
        ids,
        operation_id,
        user_id,
        cast(AliasType, alias_type),
        _float32(float(score)),
        frozen_evidence,
    )


def _payload(record: AliasProposalJournalRecord) -> str:
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    return json.dumps(
        {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )


def _evidence_metadata(evidence: AliasProposalEvidence) -> dict[str, Any]:
    return {
        "kind": evidence.kind,
        "provider": evidence.provider,
        "protocol": evidence.protocol,
        "model": evidence.model,
        "request_sha256": evidence.request_sha256,
        "assessment_sha256": evidence.assessment_sha256,
        "payload_sha256": evidence.payload_sha256,
    }


def read_record(payload: str | dict[str, Any]) -> AliasProposalJournalRecord:
    """Validate and decode a checksummed alias proposal journal payload."""
    value = json.loads(payload) if isinstance(payload, str) else payload
    try:
        raw = value["record"]
        if hashlib.sha256(raw.encode()).hexdigest() != value["sha256"]:
            raise ValueError("Alias proposal journal checksum mismatch")
        decoded = _snapshot_json.loads(raw)
        version = decoded.get("version") if isinstance(decoded, dict) else None
        if version == 1:
            record: AliasProposalJournalRecord = AliasProposalRecord.model_validate(
                decoded
            )
        elif version == 2:
            record = AliasProposalRecordV2.model_validate(decoded)
        else:
            raise ValueError("Unsupported alias proposal journal version")
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError("Malformed alias proposal journal") from exc

    left, right, edge = record.left_before, record.right_before, record.edge
    ids = sorted((str(left.id), str(right.id)))
    expected_operation = uuid5(
        UUID(ids[0]), f"prme:unverified-alias-proposal:v1:{ids[1]}"
    )
    expected_metadata: dict[str, Any] = {
        "relation": "alias",
        "alias_type": record.alias_type,
        "identity_verified": False,
        "alias_operation_id": str(record.operation_id),
    }
    if isinstance(record, AliasProposalRecordV2):
        expected_metadata["proposal_evidence"] = _evidence_metadata(record.evidence)
    if (
        str(left.id) != ids[0]
        or str(right.id) != ids[1]
        or left.id == right.id
        or record.operation_id != expected_operation
        or left.node_type != NodeType.ENTITY
        or right.node_type != NodeType.ENTITY
        or left.user_id != right.user_id
        or left.scope != right.scope
        or left.lifecycle_state not in _ACTIVE
        or right.lifecycle_state not in _ACTIVE
        or edge.id != uuid5(record.operation_id, "relates-to")
        or edge.source_id != left.id
        or edge.target_id != right.id
        or edge.edge_type != EdgeType.RELATES_TO
        or edge.user_id != left.user_id
        or edge.confidence != record.score
        or edge.valid_to is not None
        or edge.provenance_event_id is not None
        or edge.metadata != expected_metadata
        or edge.valid_from != edge.created_at
        or (
            isinstance(record, AliasProposalRecordV2)
            and (
                node_checksum(left) != record.evidence.left_node_sha256
                or node_checksum(right) != record.evidence.right_node_sha256
                or _evidence_node_ids(record.evidence) != ids
            )
        )
    ):
        raise ValueError("Alias proposal journal identity mismatch")
    return record


def _replayed(
    row: tuple[Any, Any, Any, Any, Any] | None,
    *,
    ids: list[str],
    operation_id: UUID,
    user_id: str,
    evidence: AliasProposalEvidence | None,
) -> AliasProposalResult | None:
    if row is None:
        return None
    op_type, target_id, payload, actor_id, namespace_id = row
    try:
        record = read_record(payload)
    except (ValueError, TypeError) as exc:
        raise AliasProposalConflict(
            "Alias proposal identity is already used by another operation"
        ) from exc
    if (
        op_type != "ALIAS_PROPOSED"
        or record.operation_id != operation_id
        or [str(record.left_before.id), str(record.right_before.id)] != ids
        or str(target_id) != ids[1]
        or actor_id != user_id
        or namespace_id != record.left_before.scope.value
    ):
        raise AliasProposalConflict(
            "Alias proposal identity is already used with different inputs"
        )
    if evidence is not None:
        if not isinstance(record, AliasProposalRecordV2):
            raise AliasProposalConflict(
                "Alias proposal already exists without external assessment evidence"
            )
        saved = record.evidence
        if (
            saved.provider != evidence.provider
            or saved.protocol != evidence.protocol
            or saved.model != evidence.model
            or saved.request_sha256 != evidence.request_sha256
            or saved.left_node_sha256 != evidence.left_node_sha256
            or saved.right_node_sha256 != evidence.right_node_sha256
        ):
            raise AliasProposalConflict(
                "Alias proposal already exists for a different external assessment request"
            )
        return AliasProposalResult(
            str(record.operation_id), str(record.edge.id), False, saved
        )
    return AliasProposalResult(str(record.operation_id), str(record.edge.id), False)


def _legacy_alias(
    edges: list[MemoryEdge], ids: list[str], user_id: str
) -> MemoryEdge | None:
    for edge in edges:
        metadata = edge.metadata or {}
        if (
            sorted((str(edge.source_id), str(edge.target_id))) == ids
            and edge.edge_type == EdgeType.RELATES_TO
            and edge.user_id == user_id
            and metadata.get("relation") == "alias"
            and metadata.get("identity_verified") is False
        ):
            return edge
    return None


def _prepare(
    nodes: dict[str, MemoryNode],
    edges: list[MemoryEdge],
    ids: list[str],
    operation_id: UUID,
    user_id: str,
    alias_type: AliasType,
    score: float,
    evidence: AliasProposalEvidence | None,
) -> AliasProposalJournalRecord | AliasProposalResult | None:
    if any(node_id not in nodes for node_id in ids):
        return None
    left, right = (nodes[node_id] for node_id in ids)
    if (
        left.user_id != user_id
        or right.user_id != user_id
        or left.lifecycle_state not in _ACTIVE
        or right.lifecycle_state not in _ACTIVE
        or not alias_pair_allowed(left, right)
    ):
        return None
    if evidence is not None and (
        node_checksum(left) != evidence.left_node_sha256
        or node_checksum(right) != evidence.right_node_sha256
        or _evidence_node_ids(evidence) != ids
    ):
        raise StaleAliasProposalEvidence(
            "Assessed alias nodes changed before proposal publication"
        )

    existing = _legacy_alias(edges, ids, user_id)
    expected_edge_id = uuid5(operation_id, "relates-to")
    if existing is not None:
        if existing.id == expected_edge_id:
            raise AliasProposalConflict(
                "Deterministic alias edge exists without its journal record"
            )
        if evidence is not None:
            raise AliasProposalConflict(
                "Legacy alias proposal cannot retain external assessment evidence"
            )
        # Older versions used random edge IDs and no journal. Preserve that
        # state without claiming it was atomically produced or duplicating it.
        return AliasProposalResult(None, str(existing.id), False)

    now = datetime.now(timezone.utc)
    metadata: dict[str, Any] = {
        "relation": "alias",
        "alias_type": alias_type,
        "identity_verified": False,
        "alias_operation_id": str(operation_id),
    }
    if evidence is not None:
        metadata["proposal_evidence"] = _evidence_metadata(evidence)
    edge = MemoryEdge(
        id=expected_edge_id,
        source_id=left.id,
        target_id=right.id,
        edge_type=EdgeType.RELATES_TO,
        user_id=user_id,
        confidence=score,
        valid_from=now,
        metadata=metadata,
        created_at=now,
    )
    if evidence is None:
        return AliasProposalRecord(
            operation_id=operation_id,
            alias_type=alias_type,
            score=score,
            left_before=left,
            right_before=right,
            edge=edge,
        )
    return AliasProposalRecordV2(
        operation_id=operation_id,
        alias_type=alias_type,
        score=score,
        left_before=left,
        right_before=right,
        evidence=evidence,
        edge=edge,
    )


def _checkpoint(stage: str) -> None:
    """Native transaction fault-injection boundary; no external work."""


async def propose_duckdb(
    store: "DuckPGQGraphStore",
    a: str,
    b: str,
    *,
    user_id: str,
    alias_type: str,
    score: float,
    evidence: AliasProposalEvidence | dict[str, Any] | None = None,
) -> AliasProposalResult | None:
    request = _request(a, b, user_id, alias_type, score, evidence)
    async with store._conn_lock:
        return await run_to_completion(_propose_duckdb, store, request)


def _propose_duckdb(
    store: "DuckPGQGraphStore",
    request: tuple[
        list[str], UUID, str, AliasType, float, AliasProposalEvidence | None
    ],
) -> AliasProposalResult | None:
    ids, operation_id, user_id, alias_type, score, evidence = request
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        saved = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=?",
            [str(operation_id)],
        ).fetchone()
        replayed = _replayed(
            saved,
            ids=ids,
            operation_id=operation_id,
            user_id=user_id,
            evidence=evidence,
        )
        if replayed is not None:
            conn.execute("COMMIT")
            return replayed
        nodes = {node_id: store._get_node_sync(node_id, True) for node_id in ids}
        present_nodes = {
            node_id: node for node_id, node in nodes.items() if node is not None
        }
        edges = store._get_edges_sync(
            None, None, ids, EdgeType.RELATES_TO, None, None
        )
        prepared = _prepare(
            present_nodes,
            edges,
            ids,
            operation_id,
            user_id,
            alias_type,
            score,
            evidence,
        )
        if prepared is None or isinstance(prepared, AliasProposalResult):
            conn.execute("COMMIT")
            return prepared
        _checkpoint("validated")
        store._create_edge_sync(prepared.edge)
        _checkpoint("edge")
        conn.execute(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES (?,'ALIAS_PROPOSED',?,?,?,?,?)""",
            [
                str(prepared.operation_id),
                ids[1],
                _payload(prepared),
                user_id,
                prepared.left_before.scope.value,
                prepared.edge.created_at,
            ],
        )
        _checkpoint("journal")
        conn.execute("COMMIT")
        return AliasProposalResult(
            str(prepared.operation_id),
            str(prepared.edge.id),
            True,
            prepared.evidence
            if isinstance(prepared, AliasProposalRecordV2)
            else None,
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def propose_postgres(
    store: "PgGraphStore",
    a: str,
    b: str,
    *,
    user_id: str,
    alias_type: str,
    score: float,
    evidence: AliasProposalEvidence | dict[str, Any] | None = None,
) -> AliasProposalResult | None:
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    ids, operation_id, user_id, alias_type, score, evidence = _request(
        a, b, user_id, alias_type, score, evidence
    )
    async with store._pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            f"SELECT {_NODE_COLUMNS} FROM nodes "
            "WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
            ids,
        )
        saved = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=$1",
            str(operation_id),
        )
        replayed = _replayed(
            tuple(saved) if saved is not None else None,
            ids=ids,
            operation_id=operation_id,
            user_id=user_id,
            evidence=evidence,
        )
        if replayed is not None:
            return replayed
        nodes = {str(row["id"]): store._record_to_node(row) for row in rows}
        edge_rows = await conn.fetch(
            """SELECT * FROM edges
            WHERE edge_type='relates_to'
              AND ((source_id=$1::uuid AND target_id=$2::uuid)
                OR (source_id=$2::uuid AND target_id=$1::uuid))
            ORDER BY id FOR UPDATE""",
            ids[0],
            ids[1],
        )
        edges = [store._record_to_edge(row) for row in edge_rows]
        prepared = _prepare(
            nodes,
            edges,
            ids,
            operation_id,
            user_id,
            alias_type,
            score,
            evidence,
        )
        if prepared is None or isinstance(prepared, AliasProposalResult):
            return prepared
        _checkpoint("validated")
        await store._create_edge_on_connection(conn, prepared.edge)
        _checkpoint("edge")
        inserted = await conn.fetchval(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES ($1,'ALIAS_PROPOSED',$2,$3::jsonb,$4,$5,$6)
            ON CONFLICT (id) DO NOTHING RETURNING id""",
            str(prepared.operation_id),
            ids[1],
            _payload(prepared),
            user_id,
            prepared.left_before.scope.value,
            prepared.edge.created_at,
        )
        if inserted is None:
            raise AliasProposalConflict(
                "Alias proposal identity is already used with different inputs"
            )
        _checkpoint("journal")
        return AliasProposalResult(
            str(prepared.operation_id),
            str(prepared.edge.id),
            True,
            prepared.evidence
            if isinstance(prepared, AliasProposalRecordV2)
            else None,
        )
