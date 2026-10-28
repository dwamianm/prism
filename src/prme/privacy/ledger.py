"""Explicit external-to-pack SQLite ledger with durable intent-before-ack.

A local file is independent of a pack restore, not of the OS user who controls
both files. Deployments requiring adversarial rollback detection must inject an
external current authority implementing ErasureLedger.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
import time
from uuid import UUID, uuid4

from filelock import FileLock, Timeout

from prme.privacy.erasure import ErasureIntent, LedgerEntry, LedgerHead
from prme.privacy.policy import PrivacyDenied
from prme.storage._threading import run_to_completion


class LocalErasureLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        self._lock = FileLock(str(self.path) + ".lock", thread_local=False)
        self._async_lock = asyncio.Lock()
        with self._connect() as conn:
            row = conn.execute("SELECT ledger_id FROM identity WHERE version=1").fetchone()
            if row is None:
                raise PrivacyDenied("privacy_ledger_unavailable")
            self.ledger_id = UUID(row[0])

    @classmethod
    def create(cls, path: str | Path) -> LocalErasureLedger:
        target = Path(path).absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL ensures creation never adopts an old or missing ledger identity.
        with FileLock(str(target) + ".lock", timeout=10):
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            try:
                with sqlite3.connect(target) as conn:
                    conn.execute("PRAGMA synchronous=FULL")
                    conn.execute("CREATE TABLE identity(version INTEGER PRIMARY KEY,ledger_id TEXT NOT NULL)")
                    conn.execute("INSERT INTO identity VALUES(1,?)", (str(uuid4()),))
                    conn.execute("CREATE TABLE stores(store_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,sequence INTEGER NOT NULL,token TEXT NOT NULL)")
                    conn.execute("CREATE TABLE entries(store_id TEXT NOT NULL,sequence INTEGER NOT NULL,request_id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(store_id,sequence),UNIQUE(store_id,request_id))")
                if os.name != "nt":
                    parent = os.open(target.parent, os.O_RDONLY)
                    try:
                        os.fsync(parent)
                    finally:
                        os.close(parent)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        return cls(target)

    def _connect(self):
        if not self.path.is_file() or self.path.is_symlink():
            raise PrivacyDenied("privacy_ledger_unavailable")
        try:
            conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True)
            conn.execute("PRAGMA synchronous=FULL")
            return conn
        except sqlite3.Error:
            raise PrivacyDenied("privacy_ledger_unavailable") from None

    @asynccontextmanager
    async def guard(self):
        # Nonblocking acquisition avoids an abandoned background acquire after
        # cancellation. Lock ownership spans every awaited SQL/native effect.
        async with self._async_lock:
            deadline = time.monotonic() + 10
            while True:
                try:
                    self._lock.acquire(timeout=0)
                    break
                except Timeout:
                    if time.monotonic() >= deadline:
                        raise PrivacyDenied("privacy_ledger_unavailable") from None
                    await asyncio.sleep(0.01)
            try:
                with self._connect() as conn:
                    row = conn.execute("SELECT ledger_id FROM identity WHERE version=1").fetchone()
                    if row != (str(self.ledger_id),):
                        raise PrivacyDenied("privacy_ledger_unavailable")
                yield
            finally:
                self._lock.release()

    def _held(self):
        if not self._lock.is_locked:
            raise PrivacyDenied("privacy_ledger_unavailable")

    async def register_store(self, store_id: UUID, project_id: UUID) -> LedgerHead:
        self._held()
        def register():
            head = LedgerHead(ledger_id=self.ledger_id, store_id=store_id, sequence=0, token=uuid4())
            with self._connect() as conn:
                try:
                    conn.execute("INSERT INTO stores VALUES(?,?,0,?)", (str(store_id), str(project_id), str(head.token)))
                except sqlite3.IntegrityError:
                    raise PrivacyDenied("privacy_ledger_unavailable") from None
            return head
        return await run_to_completion(register)

    def _head(self, conn, store_id):
        row = conn.execute("SELECT sequence,token FROM stores WHERE store_id=?", (str(store_id),)).fetchone()
        if row is None:
            raise PrivacyDenied("privacy_ledger_unavailable")
        return LedgerHead(ledger_id=self.ledger_id, store_id=store_id, sequence=row[0], token=UUID(row[1]))

    async def current_head(self, store_id: UUID) -> LedgerHead:
        self._held()
        def read():
            with self._connect() as conn:
                return self._head(conn, store_id)
        return await run_to_completion(read)

    async def registration_head(self, store_id: UUID) -> LedgerHead:
        """The store's head at registration, before any intent.

        Restore replays from here, so no artifact-declared checkpoint can skip
        an earlier erasure. The first entry links to the registration token.
        """
        self._held()
        def read():
            with self._connect() as conn:
                head = self._head(conn, store_id)
                row = conn.execute("SELECT body FROM entries WHERE store_id=? AND sequence=1", (str(store_id),)).fetchone()
                if row is None:
                    if head.sequence != 0:
                        raise PrivacyDenied("privacy_ledger_stale")
                    return head
                first = LedgerEntry.model_validate_json(row[0])
                return head.model_copy(update={"sequence": 0, "token": first.previous_token})
        return await run_to_completion(read)

    async def entries_since(self, checkpoint: LedgerHead) -> tuple[LedgerEntry, ...]:
        self._held()
        def read():
            with self._connect() as conn:
                head = self._head(conn, checkpoint.store_id)
                if checkpoint.ledger_id != self.ledger_id or checkpoint.sequence > head.sequence:
                    raise PrivacyDenied("privacy_ledger_stale")
                rows = conn.execute("SELECT body FROM entries WHERE store_id=? AND sequence>? ORDER BY sequence", (str(checkpoint.store_id), checkpoint.sequence)).fetchall()
                entries = tuple(LedgerEntry.model_validate_json(row[0]) for row in rows)
                previous = checkpoint
                for entry in entries:
                    if (entry.head.sequence != previous.sequence + 1 or entry.previous_token != previous.token
                            or entry.head.store_id != checkpoint.store_id or entry.head.ledger_id != self.ledger_id):
                        raise PrivacyDenied("privacy_ledger_stale")
                    previous = entry.head
                if previous != head:
                    raise PrivacyDenied("privacy_ledger_stale")
                return entries
        return await run_to_completion(read)

    async def append_intent(self, intent: ErasureIntent) -> LedgerEntry:
        self._held()
        def append():
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                head = self._head(conn, intent.store_id)
                project = conn.execute("SELECT project_id FROM stores WHERE store_id=?", (str(intent.store_id),)).fetchone()[0]
                if project != str(intent.project_id):
                    raise PrivacyDenied("privacy_boundary_mismatch")
                row = conn.execute("SELECT body FROM entries WHERE store_id=? AND request_id=?", (str(intent.store_id), str(intent.request.request_id))).fetchone()
                if row:
                    entry = LedgerEntry.model_validate_json(row[0])
                    if entry.intent.request != intent.request or entry.intent.owner_id != intent.owner_id:
                        raise PrivacyDenied("privacy_erasure_conflict")
                    return entry
                next_head = head.model_copy(update={"sequence": head.sequence + 1, "token": uuid4()})
                entry = LedgerEntry(head=next_head, previous_token=head.token, intent=intent)
                conn.execute("INSERT INTO entries VALUES(?,?,?,?)", (str(intent.store_id), next_head.sequence, str(intent.request.request_id), entry.model_dump_json()))
                conn.execute("UPDATE stores SET sequence=?,token=? WHERE store_id=?", (next_head.sequence, str(next_head.token), str(intent.store_id)))
                return entry
        return await run_to_completion(append)
