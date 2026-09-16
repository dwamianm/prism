"""Append-only relevance records bound to immutable, owner-scoped retrievals."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json

import duckdb

from uuid import NAMESPACE_URL, UUID, uuid5

from prme.models.relevance import RelevanceRecord, RelevanceSubmission, RetrievalReceipt
from prme.storage._threading import run_to_completion


def relevance_operation_id(user_id: str, feedback_id: UUID) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps(["prme:relevance:v1", user_id, str(feedback_id)])))


class RelevanceRepository:
    def __init__(self, *, conn=None, conn_lock=None, pool=None):
        self.conn, self.lock, self.pool = conn, conn_lock or asyncio.Lock(), pool

    async def _query(self, sql, *args):
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                return [dict(row) for row in await conn.fetch(sql, *args)]
        async with self.lock:
            def query():
                cursor = self.conn.execute(sql, list(args))
                names = [column[0] for column in cursor.description]
                return [dict(zip(names, row)) for row in cursor.fetchall()]
            return await run_to_completion(query)

    @staticmethod
    def _owner(user_id):
        if not user_id or not user_id.strip():
            raise ValueError("Relevance operations require user_id")

    async def get_receipt(self, request_id: str, *, user_id: str) -> RetrievalReceipt | None:
        self._owner(user_id)
        request_id = str(UUID(request_id))
        rows = await self._query(
            "SELECT payload FROM operations WHERE op_type = 'RETRIEVAL_REQUEST' "
            "AND target_id = $1 AND actor_id = $2 LIMIT 2", request_id, user_id,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("Ambiguous retrieval receipt")
        return self._read_receipt(rows[0]["payload"], request_id=request_id, user_id=user_id)

    async def get_receipts(
        self, request_ids: list[UUID], *, user_id: str,
    ) -> list[RetrievalReceipt]:
        """Resolve a bounded receipt set in batches without silent omissions."""
        self._owner(user_id)
        if not request_ids or len(request_ids) > 20000:
            raise ValueError("request_ids must contain from 1 to 20000 UUIDs")
        identities = [str(UUID(str(request_id))) for request_id in request_ids]
        if len(identities) != len(set(identities)):
            raise ValueError("request_ids must be unique")
        receipts = {}
        for offset in range(0, len(identities), 200):
            chunk = identities[offset:offset + 200]
            placeholders = ",".join(f"${index + 2}" for index in range(len(chunk)))
            rows = await self._query(
                "SELECT target_id,payload FROM operations WHERE op_type='RETRIEVAL_REQUEST' "
                f"AND actor_id=$1 AND target_id IN ({placeholders})",
                user_id, *chunk,
            )
            for row in rows:
                request_id = str(row["target_id"])
                if request_id in receipts:
                    raise ValueError("Ambiguous retrieval receipt")
                receipt = self._read_receipt(
                    row["payload"], request_id=request_id, user_id=user_id,
                )
                if receipt is None:
                    raise ValueError("Full retrieval trial references a legacy operation")
                receipts[request_id] = receipt
        if set(receipts) != set(identities):
            raise ValueError("Full retrieval trial references a missing receipt")
        return [receipts[request_id] for request_id in identities]

    @staticmethod
    def _read_receipt(payload, *, request_id, user_id):
        payload = json.loads(payload) if isinstance(payload, str) else payload
        if "receipt" not in payload:
            return None
        receipt = RetrievalReceipt.model_validate_json(payload["receipt"])
        if (receipt.user_id != user_id or str(receipt.request_id) != request_id
                or receipt.checksum != payload.get("receipt_checksum")):
            raise ValueError("Retrieval receipt identity or checksum mismatch")
        return receipt

    async def learning_snapshot(self, *, user_id: str, max_records: int = 10000
                                ) -> tuple[list[RetrievalReceipt], list[RelevanceRecord]]:
        """Capture the feedback set in one query, then resolve immutable receipts.

        Concurrently admitted feedback is outside this cut. Missing/corrupt
        referenced receipts fail the snapshot. Never silently truncate training.
        """
        self._owner(user_id)
        if isinstance(max_records, bool) or not isinstance(max_records, int) or not 1 <= max_records <= 100000:
            raise ValueError("max_records must be an integer from 1 to 100000")
        rows = await self._query(
            "SELECT payload FROM operations WHERE op_type = 'RELEVANCE_RECORDED' "
            "AND actor_id = $1 ORDER BY id LIMIT $2", user_id, max_records + 1)
        if len(rows) > max_records:
            raise ValueError("Feedback exceeds max_records; increase the bound or export a deliberate dataset")
        records = [self._read_record(row["payload"], user_id=user_id) for row in rows]
        ids = sorted({str(record.request_id) for record in records})
        receipts = {}
        for offset in range(0, len(ids), 200):
            chunk = ids[offset:offset + 200]
            placeholders = ",".join(f"${i + 2}" for i in range(len(chunk)))
            receipt_rows = await self._query(
                "SELECT target_id, payload FROM operations WHERE op_type = 'RETRIEVAL_REQUEST' "
                f"AND actor_id = $1 AND target_id IN ({placeholders})", user_id, *chunk)
            for row in receipt_rows:
                request_id = str(row["target_id"])
                if request_id in receipts:
                    raise ValueError("Ambiguous retrieval receipt")
                receipt = self._read_receipt(row["payload"], request_id=request_id, user_id=user_id)
                if receipt is None:
                    raise ValueError("Feedback references a missing retrieval receipt")
                receipts[request_id] = receipt
        if set(receipts) != set(ids):
            raise ValueError("Feedback references a missing retrieval receipt")
        return [receipts[request_id] for request_id in ids], records

    @staticmethod
    def _read_record(payload, *, user_id, feedback_id=None):
        payload = json.loads(payload) if isinstance(payload, str) else payload
        raw = payload["record"]
        if hashlib.sha256(raw.encode()).hexdigest() != payload.get("checksum"):
            raise ValueError("Relevance record checksum mismatch")
        record = RelevanceRecord.model_validate_json(raw)
        if record.user_id != user_id or (feedback_id is not None and record.feedback_id != feedback_id):
            raise ValueError("Relevance record identity mismatch")
        return record

    async def get(self, feedback_id: str, *, user_id: str) -> RelevanceRecord | None:
        self._owner(user_id)
        identity = UUID(feedback_id)
        rows = await self._query(
            "SELECT payload FROM operations WHERE id = $1 AND actor_id = $2 "
            "AND op_type = 'RELEVANCE_RECORDED'", relevance_operation_id(user_id, identity), user_id,
        )
        return self._read_record(rows[0]["payload"], user_id=user_id, feedback_id=identity) if rows else None

    async def record(self, submission: RelevanceSubmission, *, user_id: str) -> RelevanceRecord:
        self._owner(user_id)
        # Isolate mutable caller dictionaries before any provider/database await.
        submission = RelevanceSubmission.model_validate_json(submission.model_dump_json())
        receipt = await self.get_receipt(str(submission.request_id), user_id=user_id)
        if receipt is None:
            raise ValueError("A saved retrieval receipt owned by this user is required")
        candidates = {candidate.node_id: candidate for candidate in receipt.candidates}
        for node_id, label in submission.labels.items():
            candidate = candidates.get(node_id)
            if candidate is None:
                raise ValueError("Relevance labels must refer to saved response candidates")
            if submission.surface == "context" and (not candidate.in_context or (label and not candidate.has_content)):
                raise ValueError("Context labels require included entries; positive labels require source content")
        record = RelevanceRecord(**submission.model_dump(), user_id=user_id,
                                 recorded_at=datetime.now(timezone.utc), receipt_checksum=receipt.checksum)
        raw = record.model_dump_json()
        payload = json.dumps({"record": raw, "checksum": hashlib.sha256(raw.encode()).hexdigest()})
        for attempt in range(4):
            try:
                await self._query(
                    "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                    "VALUES ($1, 'RELEVANCE_RECORDED', $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING",
                    relevance_operation_id(user_id, record.feedback_id), str(record.feedback_id), payload,
                    user_id, record.recorded_at,
                )
                break
            except (duckdb.TransactionException, duckdb.ConstraintException):
                # DuckDB can report a statement/commit-time unique conflict even with
                # ON CONFLICT when independent connections insert the same ID.
                # Retry only this autocommit statement with the same identity;
                # the read below still rejects different judgment content.
                if attempt == 3:
                    raise
                await asyncio.sleep(.005 * (2 ** attempt))
        saved = await self.get(str(record.feedback_id), user_id=user_id)
        if saved is None or not saved.matches(submission) or saved.receipt_checksum != receipt.checksum:
            raise ValueError("feedback_id already identifies a different relevance judgment")
        return saved

    async def list(self, *, user_id: str, limit: int = 100, after_id: str | None = None) -> list[RelevanceRecord]:
        self._owner(user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        after = str(UUID(after_id)) if after_id is not None else ""
        rows = await self._query(
            "SELECT payload FROM operations WHERE op_type = 'RELEVANCE_RECORDED' AND actor_id = $1 "
            "AND target_id > $2 ORDER BY target_id LIMIT $3", user_id, after, limit,
        )
        return [self._read_record(row["payload"], user_id=user_id) for row in rows]
