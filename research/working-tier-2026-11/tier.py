"""P6 working tier for writes (thesis section 5): accept in memory, drain to the durable store.

Research only. Nothing here runs unless a caller constructs it, and the engine's default path is untouched.

Accept, on the caller's thread:

1. Validate and normalize the caller's stated assertions exactly as ``ingest_batch(assertions=...)`` does
   (``checked_assertions``, then ``prepare_assertions``). An end the content does not name is looked up in the
   owner's memory, as the current path does; that lookup is the only store read and the caller pays for it.
2. Put the fact into the working table, keyed by its address (subject, subject type, relation or has:predicate)
   under the owner and scope together, so another owner or scope never matches it.
3. Append the source row to a memory-mapped working log, so a process that stops before the drain replays it.

No database statement, no index write, no model call and no syscall runs on the caller's thread. That is what keeps
accept off the GIL handoff and the DuckDB statement cost (``REPORT.md``, "Bar 1").

The drainer, an asyncio task on the engine's own loop:

1. Takes the oldest logged records.
2. Appends each source event exactly as ``IngestionPipeline._append_event`` does (``defer_materialization`` and
   ``defer_extraction``), so the durable ``event_extractions`` row is the restart queue.
3. Runs the engine's own unmodified derivation for those sources: ``process_pending`` for the raw notes, then
   ``process_extractions`` for the stated facts.
4. Drops each fact from the working table once its durable materialization and its extraction are both complete.

Restart: on open the tier reads every logged record. A record whose event is already stored is not appended again.
A ``running`` lease left by a killed process is returned to ``pending`` with a new generation, so its derivation
runs again instead of waiting out the 300 s lease. The replay also closes P5's crash gap between the graph commit
and the lexical commit, because the derivation decides completion, not the checkpoint.

Reads: ``lookup()`` and ``retrieve_with_working()`` run the current path's privacy check first
(``require_legacy_range``), then read the working table under the same owner and scope. A working fact replaces
only a stored fact at the same address, and never one it does not match by address.
"""
from __future__ import annotations

import asyncio
import json
import mmap
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from prme.ingestion.assertions import Assertion, attach_assertions, checked_assertions, prepare_assertions
from prme.models.events import Event
from prme.storage.metadata import snapshot_metadata
from prme.types import Scope

RECORD_MAGIC = 1
ID_BATCH = 20000
# Bytes one working fact holds beyond its text: the dataclass fields, the address keys and the dict entry.
FACT_OVERHEAD = 700


@dataclass
class WorkingFact:
    """One accepted fact that the drainer has not published yet."""

    sequence: int
    address: tuple[str, str, str]
    subject: str
    subject_type: str
    relation: str | None
    predicate: str | None
    object: str
    object_type: str | None
    status: str | None
    polarity: str
    content: str
    metadata: dict
    user_id: str
    scope: str
    role: str
    session_id: str | None
    event_time: datetime | None
    event_id: UUID
    accepted_at: float

    def as_assertion(self) -> Assertion:
        return Assertion(
            subject=self.subject, subject_type=self.subject_type, relation=self.relation,
            predicate=self.predicate, object=self.object, object_type=self.object_type,
            status=self.status, polarity=self.polarity,
        )


@dataclass
class DrainStats:
    appended: int = 0
    derived: int = 0
    dropped: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)


class WorkingLog:
    """An append-only log of accepted source rows in a memory-mapped file.

    A record is a four-byte little-endian length then that many bytes of JSON. The log survives ``os._exit``
    because the bytes are in the page cache. It does not survive a host power loss, and this prototype never
    calls ``msync``.
    """

    def __init__(self, path: str, *, capacity: int = 64 << 20) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        size = os.fstat(self._fd).st_size
        self._capacity = max(capacity, size, 1 << 20)
        os.ftruncate(self._fd, self._capacity)
        self._mapping = mmap.mmap(self._fd, self._capacity, access=mmap.ACCESS_WRITE)
        self._offsets: list[int] = []
        self._position = 0
        self._scan()

    def _scan(self) -> None:
        position, offsets = 0, []
        while position + 4 <= self._capacity:
            length = int.from_bytes(self._mapping[position:position + 4], "little")
            if length <= 0 or position + 4 + length > self._capacity:
                break
            offsets.append(position)
            position += 4 + length
        self._offsets, self._position = offsets, position

    def __len__(self) -> int:
        return len(self._offsets)

    def _grow(self, needed: int) -> None:
        capacity = self._capacity
        while capacity < self._position + needed:
            capacity *= 2
        self._mapping.close()
        os.ftruncate(self._fd, capacity)
        self._mapping = mmap.mmap(self._fd, capacity, access=mmap.ACCESS_WRITE)
        self._capacity = capacity

    def append(self, payload: bytes) -> int:
        """Write one record and return its sequence number. No syscall runs here."""
        with self._lock:
            if self._position + 4 + len(payload) > self._capacity:
                self._grow(4 + len(payload))
            position = self._position
            self._mapping[position:position + 4] = len(payload).to_bytes(4, "little")
            self._mapping[position + 4:position + 4 + len(payload)] = payload
            self._offsets.append(position)
            self._position = position + 4 + len(payload)
            return len(self._offsets) - 1

    def read(self, sequence: int) -> bytes:
        offset = self._offsets[sequence]
        length = int.from_bytes(self._mapping[offset:offset + 4], "little")
        return bytes(self._mapping[offset + 4:offset + 4 + length])

    def close(self) -> None:
        self._mapping.close()
        os.close(self._fd)


def _payload(message: dict, *, user_id: str, scope: Scope, role: str, session_id: str | None, speech: str | None,
             prepared: list[dict], checked: list[Assertion], content: str, event_id: UUID) -> bytes:
    from prme.models.owner_reference import attach_owner_reference
    from prme.models.speaker import attach_speaker

    metadata = attach_speaker(
        attach_owner_reference(message.get("metadata"), False, role=role, speaker=speech), speech)
    metadata = attach_assertions(metadata, prepared)
    record = {
        "magic": RECORD_MAGIC, "event_id": str(event_id), "user_id": user_id, "scope": Scope(scope).value,
        "content": content, "metadata": metadata, "role": role, "session_id": session_id,
        "event_time": (message["event_time"].isoformat() if message.get("event_time") else None),
        "accepted_at": time.perf_counter(),
        "assertions": [{**assertion.model_dump(mode="json", exclude_none=True), "found": item["found"]}
                       for assertion, item in zip(checked, prepared)],
    }
    return json.dumps(record, separators=(",", ":")).encode()


class WorkingTier:
    """Accept at memory speed, drain with the engine's own derivation."""

    def __init__(self, engine, directory: str, *, log_name: str = "working.log",
                 checkpoint_name: str = "working.checkpoint") -> None:
        self.engine = engine
        self.directory = directory
        self.log = WorkingLog(os.path.join(directory, log_name))
        os.makedirs(directory, exist_ok=True)
        self._checkpoint_path = os.path.join(directory, checkpoint_name)
        self._durable_through = self._read_checkpoint()
        self._ids: list[UUID] = []
        self._ids_lock = threading.Lock()
        self._lock = threading.Lock()
        # (user_id, scope) -> address -> facts, oldest first
        self._table: dict[tuple[str, str], dict[tuple[str, str, str], list[WorkingFact]]] = {}
        self.stats = DrainStats()
        self._drain_task: asyncio.Task | None = None
        self._stopping = False

    # --- restart -------------------------------------------------------------------------------------------

    def _read_checkpoint(self) -> int:
        try:
            with open(self._checkpoint_path) as handle:
                return int(handle.read().strip() or 0)
        except FileNotFoundError:
            return 0

    def _write_checkpoint(self, sequence: int) -> None:
        temporary = self._checkpoint_path + ".tmp"
        with open(temporary, "w") as handle:
            handle.write(str(sequence))
        os.replace(temporary, self._checkpoint_path)
        self._durable_through = sequence

    async def replay(self) -> dict:
        """Load every logged fact into the working table and reset stale leases for unfinished sources."""
        loaded = 0
        for sequence in range(len(self.log)):
            record = json.loads(self.log.read(sequence))
            if record.get("magic") != RECORD_MAGIC:
                continue
            for item in record["assertions"]:
                # ``found`` says which of the caller's claims were kept. It is not an input to validation, and
                # ``checked_assertions`` refuses unknown keys, so it is dropped before the record is revalidated.
                checked = checked_assertions([{key: value for key, value in item.items() if key != "found"}])[0]
                with self._lock:
                    self._remember(self._fact_from(record, sequence, checked))
                loaded += 1
        reset = await self._reset_stale_leases([fact.event_id for fact in self._pending()])
        return {"logged_facts": loaded, "checkpoint": self._durable_through, "leases_reset": reset}

    async def _reset_stale_leases(self, event_ids: list[UUID]) -> int:
        """Return this tier's unfinished jobs to ``pending`` whatever lease a dead process left behind.

        ``ExtractionWorkRepository.claim`` takes a ``running`` job only after its lease expires (300 s by default),
        so a process killed mid-derivation would otherwise re-derive nothing for five minutes.
        """
        if not event_ids:
            return 0
        from prme.storage._threading import run_to_completion

        def reset() -> int:
            conn = self.engine._event_store._conn
            changed = 0
            conn.execute("BEGIN TRANSACTION")
            try:
                for event_id in event_ids:
                    row = conn.execute("SELECT status FROM event_extractions WHERE event_id = ?",
                                       [str(event_id)]).fetchone()
                    if row is None:
                        continue
                    if row[0] == "complete":
                        continue
                    conn.execute(
                        "UPDATE event_extractions SET status = 'pending', generation = generation + 1, "
                        "lease_expires_at = NULL, plan_id = NULL WHERE event_id = ?", [str(event_id)],
                    )
                    changed += 1
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return changed

        return await run_to_completion(reset)

    # --- accept --------------------------------------------------------------------------------------------

    def _next_id(self) -> UUID:
        with self._ids_lock:
            if not self._ids:
                self._ids = [uuid4() for _ in range(ID_BATCH)]
            return self._ids.pop()

    def _fact_from(self, record: dict, sequence: int, assertion: Assertion) -> WorkingFact:
        return WorkingFact(
            sequence=sequence, address=assertion.address(), subject=assertion.subject,
            subject_type=assertion.subject_type, relation=assertion.relation, predicate=assertion.predicate,
            object=assertion.object, object_type=assertion.object_type, status=assertion.status,
            polarity=assertion.polarity, content=record["content"], metadata=record["metadata"],
            user_id=record["user_id"], scope=record["scope"], role=record["role"],
            session_id=record.get("session_id"),
            event_time=datetime.fromisoformat(record["event_time"]) if record.get("event_time") else None,
            event_id=UUID(record["event_id"]), accepted_at=record["accepted_at"],
        )

    def _remember(self, fact: WorkingFact) -> None:
        self._table.setdefault((fact.user_id, fact.scope), {}).setdefault(fact.address, []).append(fact)

    def _forget(self, fact: WorkingFact) -> None:
        table = self._table.get((fact.user_id, fact.scope))
        if not table or fact.address not in table:
            return
        table[fact.address] = [item for item in table[fact.address] if item.sequence != fact.sequence]
        if not table[fact.address]:
            del table[fact.address]

    async def accept(self, message: dict, *, user_id: str, session_id: str | None = None,
                     scope: Scope = Scope.PERSONAL, speaker: str | None = None) -> UUID:
        """Accept one stated fact and return the source event's id.

        The body is synchronous Python except for one ``await``: the same held-end lookup the current path makes,
        and only for an end the content does not name. That read is the caller's cost; everything after it is a
        dict put and a memory-mapped write.
        """
        from prme.ingestion.assertions import Held, needs_memory, read_held

        assertions = message.get("assertions")
        if not assertions:
            raise ValueError("a working-tier fact needs at least one assertion")
        checked = checked_assertions(assertions)
        content = message.get("content")
        held = Held()
        if content is not None and content.strip() and any(
                needs_memory(assertion, content, speaker) for assertion in checked):
            held = await read_held(checked, content, graph_store=self.engine._graph_store,
                                   entity_merger=self.engine._pipeline._entity_merger,
                                   user_id=user_id, scope=scope, speaker=speaker)
        content, prepared = prepare_assertions(content, checked, speaker=speaker, held=held)
        event_id = self._next_id()
        payload = _payload(message, user_id=user_id, scope=scope, role=message["role"], session_id=session_id,
                           speech=speaker, prepared=prepared, checked=checked, content=content, event_id=event_id)
        sequence = self.log.append(payload)
        record = json.loads(payload)
        accepted_at = record["accepted_at"]
        with self._lock:
            for assertion in checked:
                self._remember(WorkingFact(
                    sequence=sequence, address=assertion.address(), subject=assertion.subject,
                    subject_type=assertion.subject_type, relation=assertion.relation,
                    predicate=assertion.predicate, object=assertion.object,
                    object_type=assertion.object_type, status=assertion.status, polarity=assertion.polarity,
                    content=content, metadata=record["metadata"], user_id=user_id,
                    scope=Scope(scope).value, role=message["role"], session_id=session_id,
                    event_time=message.get("event_time"), event_id=event_id, accepted_at=accepted_at,
                ))
        return event_id

    # --- reads ---------------------------------------------------------------------------------------------

    def backlog(self, *, user_id: str | None = None, scope: Scope = Scope.PERSONAL) -> int:
        with self._lock:
            keys = [(user_id, Scope(scope).value)] if user_id is not None else list(self._table)
            return sum(len(facts) for key in keys for facts in self._table.get(key, {}).values())

    def memory_bytes(self, *, user_id: str | None = None) -> int:
        """Bytes the working table holds: its facts, plus the log's committed bytes."""
        with self._lock:
            keys = [(user_id, None)] if user_id is None else [k for k in self._table if k[0] == user_id]
            facts = [fact for key in keys for group in self._table.get(key, {}).items() for fact in group[1]]
        return sum(len(fact.content.encode()) + len(json.dumps(fact.metadata).encode()) + FACT_OVERHEAD
                   for fact in facts)

    async def lookup(self, assertion: Assertion, *, user_id: str, scope: Scope = Scope.PERSONAL) -> list[WorkingFact]:
        """Working facts at one address, refused wherever the current path refuses a stored read."""
        await self.engine._privacy.repository.require_legacy_range(user_id, Scope(scope).value)
        address = assertion.address()
        with self._lock:
            return list(self._table.get((user_id, Scope(scope).value), {}).get(address, ()))

    async def facts_by_address(self, *, user_id: str, scope: Scope = Scope.PERSONAL) -> dict:
        """Every working fact of one owner and scope, by address, refused wherever the current path refuses."""
        await self.engine._privacy.repository.require_legacy_range(user_id, Scope(scope).value)
        with self._lock:
            return {address: list(facts) for address, facts in
                    self._table.get((user_id, Scope(scope).value), {}).items()}

    async def retrieve_with_working(self, query: str, *, user_id: str, scope: Scope = Scope.PERSONAL,
                                    subject: str | None = None, relation: str | None = None, **kwargs):
        """``retrieve()`` plus the working facts at the query's address that are not stored yet.

        ``subject`` and ``relation`` name the address to merge. The stored side is the engine's own response,
        untouched. The working facts come back beside it, keyed by address, so a caller merges them by address and
        a working fact never reorders or replaces a stored fact it does not match.
        """
        response = await self.engine.retrieve(query, user_id=user_id, scope=scope, **kwargs)
        if subject is None or relation is None:
            return response, {}
        assertion = Assertion(subject=subject, subject_type="product", relation=relation, object="x",
                              object_type="location")
        working = await self.lookup(assertion, user_id=user_id, scope=scope)
        return response, ({assertion.address(): working} if working else {})

    # --- drain ---------------------------------------------------------------------------------------------

    def _pending(self, *, user_id: str | None = None, scope: Scope | None = None) -> list[WorkingFact]:
        with self._lock:
            keys = [(user_id, Scope(scope).value)] if user_id is not None and scope is not None else list(self._table)
            facts = [fact for key in keys for group in self._table.get(key, {}).values() for fact in group]
        facts.sort(key=lambda fact: fact.sequence)
        return facts

    async def drain_once(self, *, user_id: str, scope: Scope = Scope.PERSONAL, batch: int = 25,
                         budget_ms: float = 60_000) -> DrainStats:
        """Append, derive and drop one batch of logged facts."""
        started = time.perf_counter()
        stats = DrainStats()
        selected = self._pending(user_id=user_id, scope=scope)[:batch]
        if not selected:
            stats.seconds = time.perf_counter() - started
            return stats
        store = self.engine._event_store
        seen: set[str] = set()
        for fact in selected:
            if str(fact.event_id) in seen:
                continue
            seen.add(str(fact.event_id))
            if await store.get(str(fact.event_id)) is not None:
                continue
            event = Event(id=fact.event_id, content=fact.content, user_id=fact.user_id, role=fact.role,
                          session_id=fact.session_id, metadata=fact.metadata, event_time=fact.event_time,
                          scope=Scope(fact.scope))
            await self.engine._pipeline._write_queue.submit(
                lambda ev=event: store.append(ev, defer_materialization=True, defer_extraction=True),
                label=f"working.append:{event.id}",
            )
            stats.appended += 1
        await self.engine.process_pending(user_id=user_id, budget_ms=min(budget_ms, 5000))
        await self.engine.process_extractions(user_id=user_id, limit=len(seen), budget_ms=budget_ms)
        for fact in selected:
            status = await store.processing_status(str(fact.event_id), user_id=user_id)
            work = await store.extraction_work.status(str(fact.event_id), user_id=user_id)
            if status is not None and status.status == "complete" and work is not None and work.status == "complete":
                with self._lock:
                    self._forget(fact)
                stats.dropped += 1
            else:
                stats.failures.append(f"{fact.event_id}:{getattr(work, 'status', None)}")
        stats.derived = stats.dropped
        self._write_checkpoint(selected[-1].sequence + 1)
        stats.seconds = time.perf_counter() - started
        self.stats.appended += stats.appended
        self.stats.derived += stats.derived
        self.stats.dropped += stats.dropped
        self.stats.seconds += stats.seconds
        self.stats.failures.extend(stats.failures)
        return stats

    async def start_drainer(self, *, user_id: str, scope: Scope = Scope.PERSONAL, every_ms: float = 25.0,
                            batch: int = 25, step_budget_ms: float = 60_000, on_cycle=None) -> None:
        """Run the drainer on the engine's loop until ``stop_drainer``.

        ``step_budget_ms`` is the most one drain step may spend deriving. The loop is shared with callers, so a
        step that runs one whole batch without yielding holds a reader for the batch's full cost (bar 2 measured
        up to 3.9 s). A step budget keeps each wait short; the drain then takes more steps for the same facts.
        """
        self._stopping = False

        async def loop() -> None:
            while not self._stopping:
                stats = await self.drain_once(user_id=user_id, scope=scope, batch=batch,
                                              budget_ms=step_budget_ms)
                if on_cycle is not None:
                    on_cycle(stats)
                await asyncio.sleep(every_ms / 1000 if stats.appended == 0 else 0)

        self._drain_task = asyncio.create_task(loop())

    async def stop_drainer(self, *, drain: bool = False, user_id: str | None = None,
                           scope: Scope = Scope.PERSONAL) -> None:
        self._stopping = True
        if self._drain_task is not None:
            await self._drain_task
            self._drain_task = None
        if drain and user_id is not None:
            while self._pending(user_id=user_id, scope=scope):
                stats = await self.drain_once(user_id=user_id, scope=scope)
                if stats.appended == 0 and stats.dropped == 0:
                    break

    def close(self) -> None:
        self.log.close()
