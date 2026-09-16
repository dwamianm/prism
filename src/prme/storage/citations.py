"""Append-only answer citations bound to immutable retrieval context."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

import duckdb

from prme.models.relevance import AnswerCitationRecord, AnswerCitationSubmission
from prme.storage.relevance import RelevanceRepository


class CitationConflict(ValueError):
    """A citation retry identity was reused for different input."""


def citation_operation_id(user_id: str, citation_id: UUID) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps([
        "prme:answer-citations:v1", user_id, str(citation_id),
    ])))


class CitationRepository(RelevanceRepository):
    """Persist answer-time memory use without consulting mutable graph state."""

    @staticmethod
    def _read_citation(payload, *, user_id: str, citation_id: UUID | None = None
                       ) -> AnswerCitationRecord:
        payload = json.loads(payload) if isinstance(payload, str) else payload
        raw = payload["record"]
        if hashlib.sha256(raw.encode()).hexdigest() != payload.get("checksum"):
            raise ValueError("Answer citation record checksum mismatch")
        record = AnswerCitationRecord.model_validate_json(raw)
        if record.user_id != user_id or (
            citation_id is not None and record.citation_id != citation_id
        ):
            raise ValueError("Answer citation record identity mismatch")
        return record

    async def get(self, citation_id: str, *, user_id: str) -> AnswerCitationRecord | None:
        self._owner(user_id)
        identity = UUID(citation_id)
        rows = await self._query(
            "SELECT payload FROM operations WHERE id = $1 AND actor_id = $2 "
            "AND op_type = 'ANSWER_CITATIONS_RECORDED'",
            citation_operation_id(user_id, identity), user_id,
        )
        return self._read_citation(
            rows[0]["payload"], user_id=user_id, citation_id=identity,
        ) if rows else None

    async def record(self, submission: AnswerCitationSubmission, *,
                     user_id: str) -> AnswerCitationRecord:
        self._owner(user_id)
        # Freeze mutable caller containers before a database await.
        submission = AnswerCitationSubmission.model_validate_json(submission.model_dump_json())
        receipt = await self.get_receipt(str(submission.request_id), user_id=user_id)
        if receipt is None:
            raise ValueError("A saved retrieval receipt owned by this user is required")

        candidates = {candidate.node_id: candidate for candidate in receipt.candidates}
        for node_id in submission.cited_node_ids:
            candidate = candidates.get(node_id)
            if candidate is None:
                raise ValueError("Citations must refer to saved response candidates")
            if not candidate.in_context or not candidate.has_content:
                raise ValueError("Citations require content-bearing entries included in context")

        record = AnswerCitationRecord(
            **submission.model_dump(), user_id=user_id,
            recorded_at=datetime.now(timezone.utc), receipt_checksum=receipt.checksum,
            context_sha256=receipt.context_sha256,
        )
        raw = record.model_dump_json()
        payload = json.dumps({
            "record": raw,
            "checksum": hashlib.sha256(raw.encode()).hexdigest(),
        })
        for attempt in range(4):
            try:
                await self._query(
                    "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                    "VALUES ($1, 'ANSWER_CITATIONS_RECORDED', $2, $3, $4, $5) "
                    "ON CONFLICT (id) DO NOTHING",
                    citation_operation_id(user_id, record.citation_id),
                    str(record.citation_id), payload, user_id, record.recorded_at,
                )
                break
            except (duckdb.TransactionException, duckdb.ConstraintException):
                if attempt == 3:
                    raise
                await asyncio.sleep(.005 * (2 ** attempt))

        saved = await self.get(str(record.citation_id), user_id=user_id)
        if saved is None or not saved.matches(submission) or (
            saved.receipt_checksum != receipt.checksum
            or saved.context_sha256 != receipt.context_sha256
        ):
            raise CitationConflict(
                "citation_id already identifies a different answer citation record"
            )
        return saved

    async def list(self, *, user_id: str, limit: int = 100,
                   after_id: str | None = None) -> list[AnswerCitationRecord]:
        self._owner(user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        after = str(UUID(after_id)) if after_id is not None else ""
        rows = await self._query(
            "SELECT payload FROM operations WHERE op_type = 'ANSWER_CITATIONS_RECORDED' "
            "AND actor_id = $1 AND target_id > $2 ORDER BY target_id LIMIT $3",
            user_id, after, limit,
        )
        return [self._read_citation(row["payload"], user_id=user_id) for row in rows]
