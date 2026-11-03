"""Live frames: an observe session that keeps its state between frames (RFC-0018, phases 2 and 3).

``observe()`` takes one photo or one video file, and its keyframe policy and
repeat filter keep their state only inside that call, so separate camera
frames were never compared with each other (dwamianm/prism#390). A live
session is the engine's answer: the caller sends every sampled frame,
twice a second for the camera demo, with a session id, and the session
decides which to keep. :meth:`prme.storage.engine.MemoryEngine.observe_frame`
is the entry point; this module holds the decision and the sessions.

The decision (:class:`StreamPolicy`) is the camera demo's watch gate, built
on :class:`~prme.vision.keyframes.KeyframePolicy`, so the change score, the
noise-floor calibration and the heartbeat are the ones video files use. A
frame is kept when it changed from the last kept frame by the threshold or
more, and then:

- the picture has held still for ``vision.stream_settle_seconds`` (two
  frames in a row within the threshold of each other): ``"changed"``, or
  ``"first"`` for the session's first keep. A hand on its way is not kept
  mid-motion, but every pose held for a moment is, however recently the
  last frame was kept; or
- it has kept changing for ``vision.stream_settle_timeout_seconds`` without
  holding still (a person who never stops moving): ``"moving"``, at most
  once every ``vision.stream_moving_gap_seconds`` (``"cooldown"`` until
  then), so constant motion cannot flood the memory.

An unchanged picture is kept every ``vision.stream_heartbeat_seconds``
(``"heartbeat"``) and is otherwise ``"unchanged"``; a changed one that has
not held still yet is ``"settling"``. A frame the session would keep while
``vision.stream_max_pending`` of its kept frames wait to be described is
``"busy"`` and changes nothing, so the next frame can be kept instead.

A kept frame becomes an observation the way a photo does in ``observe()``:
the vision model describes it, the description repeat filter compares it
with the session's last admitted description (so a repeat across frames is
dropped), a thumbnail goes into the pack's blobs table, and the description
is admitted as an event of role ``tool`` with the frame's capture time as
``event_time`` and the session id as ``session_id``. Kept frames are
described one at a time and in the order they were kept, behind the
engine's observe lock, while the decision for the next frame is made at
once: a session is never blocked by a description in progress.

Memory is bounded: the engine keeps state for at most
``vision.stream_max_sessions`` sessions, forgets one that received no frame
for ``vision.stream_idle_seconds``, and holds at most
``vision.stream_max_pending`` frames per session for description.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from prme.storage._threading import run_to_completion
from prme.types import Scope
from prme.vision import frames as frames_module
from prme.vision.describe import describe_prompt
from prme.vision.errors import UnsupportedSourceError, VisionDisabledError, VisionError
from prme.vision.keyframes import (
    MIN_CHANGE_THRESHOLD,
    NOISE_MULTIPLIER,
    KeyframePolicy,
    frame_signature,
    thumbnail_bytes,
)
from prme.vision.observe import (
    ADMITTED,
    ALREADY_OBSERVED,
    FAILED,
    REPEAT,
    RESERVED_METADATA,
    ObservationRecord,
    _admit,
)
from prme.vision.repeat import DescriptionKey, is_repeat

if TYPE_CHECKING:
    from prme.config import VisionConfig
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)

# Decisions that keep the frame.
FIRST = "first"
CHANGED = "changed"
MOVING = "moving"
HEARTBEAT = "heartbeat"
# Decisions that do not.
SETTLING = "settling"
UNCHANGED = "unchanged"
COOLDOWN = "cooldown"
BUSY = "busy"
KEPT_DECISIONS = frozenset({FIRST, CHANGED, MOVING, HEARTBEAT})

PENDING = "pending"
"""The status of a kept frame's :class:`~prme.vision.observe.ObservationRecord` until it is described."""

STREAM_METADATA = ("kept_reason",)
"""Observation metadata keys a live session adds to a photo's; caller metadata cannot override them."""


@dataclass(frozen=True)
class StreamDecision:
    """What :class:`StreamPolicy` decided for one frame, with the scores behind it."""

    decision: str
    since_kept: float | None  # change from the last kept frame; None before the first
    since_previous: float | None  # change from the frame before; None for the session's first frame
    threshold: float  # the threshold both scores were compared with
    wait_s: float | None = None  # for "cooldown": seconds until a picture that keeps changing may be kept

    @property
    def kept(self) -> bool:
        return self.decision in KEPT_DECISIONS


class StreamPolicy:
    """The keep decision for one live session's frames, one frame at a time.

    It holds a :class:`~prme.vision.keyframes.KeyframePolicy` (the last kept
    frame, the heartbeat clock and, when calibrated, the noise floor), the
    previous frame's signature and the settle state. *now* is the session's
    clock in seconds; it must not go backwards.
    """

    def __init__(self, *, threshold: float, heartbeat: float, settle_seconds: float, settle_timeout: float,
                 moving_gap: float, calibrate: bool = True, min_threshold: float = MIN_CHANGE_THRESHOLD,
                 noise_multiplier: float = NOISE_MULTIPLIER) -> None:
        self.keyframes = KeyframePolicy(threshold=threshold, max_gap=heartbeat, calibrate=calibrate,
                                        min_threshold=min_threshold, noise_multiplier=noise_multiplier)
        self.settle_seconds = settle_seconds
        self.settle_timeout = settle_timeout
        self.moving_gap = moving_gap
        self._previous: np.ndarray | None = None
        self._still_since: float | None = None
        self._changed_since: float | None = None

    @classmethod
    def from_config(cls, config: VisionConfig) -> StreamPolicy:
        return cls(threshold=config.change_threshold, heartbeat=config.stream_heartbeat_seconds,
                   settle_seconds=config.stream_settle_seconds, settle_timeout=config.stream_settle_timeout_seconds,
                   moving_gap=config.stream_moving_gap_seconds, calibrate=config.calibrate_keyframes,
                   min_threshold=config.min_change_threshold, noise_multiplier=config.noise_multiplier)

    def decide(self, signature: np.ndarray, *, now: float, can_keep: bool = True) -> StreamDecision:
        """Keep or drop the frame with *signature*; *can_keep* false answers "busy" instead of keeping."""
        threshold = self.keyframes.threshold
        since_kept = self.keyframes.change(signature)
        since_previous = None if self._previous is None else self.keyframes.compare(self._previous, signature)
        self._previous = signature
        moved = since_previous is None or since_previous >= threshold
        if moved or self._still_since is None:
            self._still_since = now
        kept_at = self.keyframes.last_kept_time

        def result(decision: str, wait_s: float | None = None) -> StreamDecision:
            return StreamDecision(decision=decision, since_kept=since_kept, since_previous=since_previous,
                                  threshold=threshold, wait_s=wait_s)

        if since_kept is not None and since_kept < threshold:
            self._changed_since = None
            self.keyframes.still(since_kept)
            if kept_at is None or now - kept_at < self.keyframes.max_gap:
                return result(UNCHANGED)
            reason = HEARTBEAT
        else:
            if self._changed_since is None:
                self._changed_since = now
            reason = FIRST if since_kept is None else CHANGED
            settled = not moved and now - self._still_since >= self.settle_seconds
            if not settled:
                if now - self._changed_since < self.settle_timeout:
                    return result(SETTLING)
                if kept_at is not None and now - kept_at < self.moving_gap:
                    return result(COOLDOWN, round(self.moving_gap - (now - kept_at), 1))
                if since_kept is not None:
                    reason = MOVING
        if not can_keep:
            return result(BUSY)
        self.keyframes.keep(signature, time_s=now)
        self._changed_since = None
        return result(reason)


@dataclass
class FrameResult:
    """What a live session did with one frame (:meth:`MemoryEngine.observe_frame`).

    ``decision`` says whether the frame was kept and why (see
    :mod:`prme.vision.stream`). A kept frame has an ``observation`` record:
    its status is ``"pending"`` until the frame is described, then
    ``"admitted"`` (with ``event_id``), ``"repeat"``, ``"already_observed"``
    or ``"failed"`` (with ``error``). ``pending`` counts the session's kept
    frames waiting to be described, this one included.
    """

    session_id: str
    index: int | None  # the frame's number in the session, from 0; None when no session could take it
    captured_at: datetime
    decision: str
    since_kept: float | None
    since_previous: float | None
    threshold: float
    wait_s: float | None = None
    pending: int = 0
    observation: ObservationRecord | None = None
    _done: asyncio.Task[ObservationRecord] | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def kept(self) -> bool:
        return self.decision in KEPT_DECISIONS

    async def observed(self) -> ObservationRecord | None:
        """Wait until a kept frame is described and admitted or dropped; None for a frame that was not kept.

        Cancelling the wait does not cancel the description.
        """
        if self._done is None:
            return self.observation
        try:
            return await asyncio.shield(self._done)
        except asyncio.CancelledError:
            if self._done.cancelled():  # the engine closed first; the record says so
                return self.observation
            raise

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable account of the decision and, for a kept frame, its observation so far."""
        return {
            "session_id": self.session_id, "index": self.index, "captured_at": self.captured_at.isoformat(),
            "decision": self.decision, "kept": self.kept, "since_kept": self.since_kept,
            "since_previous": self.since_previous, "threshold": self.threshold, "wait_s": self.wait_s,
            "pending": self.pending,
            "observation": None if self.observation is None else self.observation.to_dict(),
        }


@dataclass
class _Session:
    session_id: str
    user_id: str
    scope: Scope
    policy: StreamPolicy
    seen: float  # the registry clock when the last frame arrived
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started: datetime | None = None  # the first frame's capture time; the session clock counts from it
    clock_s: float = 0.0
    frames: int = 0
    previous: DescriptionKey | None = None  # the last admitted description, for the repeat filter
    inflight: list[tuple[asyncio.Task[ObservationRecord], ObservationRecord]] = field(default_factory=list)

    @property
    def pending(self) -> int:
        return sum(1 for task, _ in self.inflight if not task.done())

    @property
    def idle(self) -> bool:
        """No decision in progress and no kept frame waiting: the session can be forgotten safely."""
        return not self.lock.locked() and self.pending == 0


SessionKey = tuple[str, str, str]  # (user_id, scope, session_id)


class ObserveSessions:
    """The live observe sessions of one engine, keyed by owner, scope and session id."""

    def __init__(self, engine: MemoryEngine, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._engine = engine
        self._sessions: OrderedDict[SessionKey, _Session] = OrderedDict()
        self.clock = clock
        self._closed = False

    def __len__(self) -> int:
        return len(self._sessions)

    def _session(self, key: SessionKey, config: VisionConfig) -> _Session | None:
        """The session for *key*, made if needed; None when the cap is reached and every session is busy."""
        now = self.clock()
        for stale in [name for name, session in self._sessions.items()
                      if session.idle and now - session.seen >= config.stream_idle_seconds]:
            del self._sessions[stale]
            logger.info("observe session %s was idle for %.0f s and is forgotten", stale[2],
                        config.stream_idle_seconds)
        session = self._sessions.get(key)
        if session is not None:
            self._sessions.move_to_end(key)
            return session
        while len(self._sessions) >= config.stream_max_sessions:
            oldest = next((name for name, candidate in self._sessions.items() if candidate.idle), None)
            if oldest is None:
                return None
            del self._sessions[oldest]
            logger.info("observe session %s is forgotten to make room for %s", oldest[2], key[2])
        session = self._sessions[key] = _Session(session_id=key[2], user_id=key[0], scope=Scope(key[1]),
                                                 policy=StreamPolicy.from_config(config), seen=now)
        return session

    async def observe_frame(
        self, frame: bytes | str | os.PathLike[str], *, user_id: str, session_id: str,
        captured_at: datetime | None, metadata: dict[str, Any] | None, scope: Scope, wait: bool,
    ) -> FrameResult:
        engine = self._engine
        config: VisionConfig = engine._config.vision
        if not config.enabled:
            raise VisionDisabledError(
                "Visual memory is off; set vision.enabled (PRME_VISION__ENABLED=true) to observe images and video"
            )
        if not user_id or not user_id.strip():
            raise ValueError("user_id is required")
        if not session_id or not session_id.strip():
            raise ValueError("session_id is required: a live session's frames are compared with each other")
        if captured_at is not None and (captured_at.tzinfo is None or captured_at.utcoffset() is None):
            raise ValueError("captured_at must be a timezone-aware datetime")
        if not engine.supports_blobs:
            raise VisionError(
                "observe_frame() needs a local DuckDB pack in this release; the PostgreSQL backend is not supported"
            )
        if self._closed:
            raise RuntimeError("The engine is closed")
        data = await _frame_bytes(frame)
        when, clock = (captured_at, "given") if captured_at is not None else (datetime.now().astimezone(), "now")
        key: SessionKey = (user_id, scope.value, session_id)
        session = self._session(key, config)
        if session is None:
            logger.info("observe session %s: all %d live sessions have frames waiting; busy", session_id,
                        config.stream_max_sessions)
            return FrameResult(session_id=session_id, index=None, captured_at=when, decision=BUSY, since_kept=None,
                               since_previous=None, threshold=config.change_threshold)
        # The lock is taken before anything is awaited, so one session's frames are decided in call order.
        async with session.lock:
            if self._closed:
                raise RuntimeError("The engine is closed")
            signature = await run_to_completion(_signature, data)
            if session.started is None:
                session.started = when
            # The session clock never goes backwards: a frame sent out of order counts as simultaneous.
            session.clock_s = max(session.clock_s, (when - session.started).total_seconds())
            session.seen = self.clock()
            index = session.frames
            session.frames += 1
            session.inflight = [entry for entry in session.inflight if not entry[0].done()]
            decision = await run_to_completion(session.policy.decide, signature, now=session.clock_s,
                                               can_keep=len(session.inflight) < config.stream_max_pending)
            result = FrameResult(session_id=session_id, index=index, captured_at=when, decision=decision.decision,
                                 since_kept=decision.since_kept, since_previous=decision.since_previous,
                                 threshold=decision.threshold, wait_s=decision.wait_s)
            if decision.kept:
                record = ObservationRecord(index=index, time_s=round(session.clock_s, 3),
                                           frame_sha256=hashlib.sha256(data).hexdigest(),
                                           change=decision.since_kept, kept_reason=decision.decision, status=PENDING)
                after = session.inflight[-1][0] if session.inflight else None
                task = asyncio.create_task(self._observe_kept(session, record, data, after=after, captured_at=when,
                                                              clock=clock, metadata=dict(metadata or {})))
                session.inflight.append((task, record))
                result.observation, result._done = record, task
            result.pending = session.pending
        logger.debug("observe session %s frame %d: %s since_kept=%s since_previous=%s threshold=%s", session_id,
                     index, decision.decision, decision.since_kept, decision.since_previous, decision.threshold)
        if wait:
            await result.observed()
        return result

    async def _observe_kept(self, session: _Session, record: ObservationRecord, data: bytes, *,
                            after: asyncio.Task[ObservationRecord] | None, captured_at: datetime, clock: str,
                            metadata: dict[str, Any] | None) -> ObservationRecord:
        """Describe and admit one kept frame, after the session's earlier kept frames."""
        try:
            if after is not None:
                await asyncio.wait([after])  # keep order; the earlier frame's outcome does not matter here
            async with self._engine._observe_lock:
                await self._describe_and_admit(session, record, data, captured_at=captured_at, clock=clock,
                                               metadata=metadata)
        except asyncio.CancelledError:
            record.status, record.error = FAILED, "cancelled: the engine closed before the frame was observed"
            raise
        except Exception as exc:  # noqa: BLE001 - the record says why; the session goes on with the next frame
            record.status, record.error = FAILED, f"{type(exc).__name__}: {exc}"
            logger.warning("observe session %s frame %d: %s", session.session_id, record.index, record.error)
        return record

    async def _describe_and_admit(self, session: _Session, record: ObservationRecord, data: bytes, *,
                                  captured_at: datetime, clock: str, metadata: dict[str, Any] | None) -> None:
        engine = self._engine
        config: VisionConfig = engine._config.vision
        provider = engine.vision_provider()
        user_id, scope = session.user_id, session.scope
        existing = await engine.find_observation(
            user_id=user_id, scope=scope, source_sha256=record.frame_sha256, frame_time_s=None,
            vision_model=provider.model_name, describe_prompt_version=config.describe_prompt_version,
        )
        if existing is not None:
            # As in observe(): the stored description seeds the repeat filter.
            record.status, record.event_id = ALREADY_OBSERVED, existing
            stored = await engine.get_event(existing, user_id=user_id)
            if stored is not None:
                record.description = stored.content
                session.previous = DescriptionKey.of(stored.content, (await engine.embed_texts([stored.content]))[0])
            return
        started = time.perf_counter()
        description = await provider.describe(data, prompt=describe_prompt(frames_module.PHOTO,
                                                                           config.describe_prompt_version))
        record.describe_seconds = round(time.perf_counter() - started, 3)
        record.description = description
        current = DescriptionKey.of(description, (await engine.embed_texts([description]))[0])
        repeat, similarity = is_repeat(session.previous, current, threshold=config.repeat_similarity)
        record.similarity = None if similarity is None else round(similarity, 4)
        if repeat:
            record.status = REPEAT
            logger.info("observe session %s frame %d: repeat of the last kept frame (similarity %.3f)",
                        session.session_id, record.index, similarity)
            return
        session.previous = current
        record.event_time = captured_at
        thumbnail = None
        if config.keep_thumbnails:
            from prme.storage.blobs import thumbnail_key

            thumbnail = thumbnail_key(record.frame_sha256)
            await engine.put_blob(thumbnail, "image/jpeg",
                                  await run_to_completion(thumbnail_bytes, data, width=config.thumbnail_width))
        reserved = (*RESERVED_METADATA, *STREAM_METADATA)
        event_metadata = {
            **{key: value for key, value in (metadata or {}).items() if key not in reserved},
            "modality": frames_module.PHOTO,
            "source_file": f"{session.session_id} frame {record.index}",
            "source_path": None,
            "source_sha256": record.frame_sha256,
            "frame_index": record.index,
            "frame_time_s": None,
            "frame_sha256": record.frame_sha256,
            "thumbnail": thumbnail,
            "vision_model": provider.model_name,
            "describe_prompt_version": config.describe_prompt_version,
            "capture_clock": clock,
            "kept_reason": record.kept_reason,
        }
        started = time.perf_counter()
        event_id, extraction = await _admit(
            engine, description, user_id=user_id, session_id=session.session_id, metadata=event_metadata,
            event_time=captured_at, scope=scope, extract=config.extract, retry_delays=config.extraction_retry_delays,
        )
        record.admit_seconds = round(time.perf_counter() - started, 3)
        record.status, record.event_id, record.extraction = ADMITTED, event_id, extraction
        logger.info("observe session %s frame %d: event %s (%s)", session.session_id, record.index, event_id,
                    extraction or "stored")

    async def end(self, key: SessionKey) -> list[ObservationRecord]:
        """Forget a session once its kept frames are observed; return the records of the frames it waited for."""
        session = self._sessions.pop(key, None)
        if session is None:
            return []
        waiting = [entry for entry in session.inflight if not entry[0].done()]
        if waiting:
            await asyncio.gather(*(task for task, _ in waiting), return_exceptions=True)
        return [record for _, record in waiting]

    async def close(self) -> None:
        """Cancel the kept frames still waiting to be described and forget every session."""
        self._closed = True
        tasks = [task for session in self._sessions.values() for task, _ in session.inflight if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.warning("The engine closed with %d kept live frames not yet observed", len(tasks))
        self._sessions.clear()


async def _frame_bytes(frame: bytes | str | os.PathLike[str]) -> bytes:
    """The bytes of one live frame, which must be an image."""
    if isinstance(frame, (bytes, bytearray)):
        data = bytes(frame)
        name = "the frame"
    else:
        path = Path(os.fspath(frame))
        if not path.is_file():
            raise FileNotFoundError(f"Frame not found: {path}")
        data = await run_to_completion(path.read_bytes)
        name = str(path)
    if not data:
        raise UnsupportedSourceError(f"{name} is empty")
    if frames_module.sniff_kind(data[:16]) != frames_module.PHOTO or data[4:8] == b"ftyp":
        raise UnsupportedSourceError(
            f"{name} is not a JPEG, PNG, WebP, GIF, BMP or TIFF image; a live frame is one image (use observe() "
            "for a video file)"
        )
    return data


def _signature(data: bytes) -> np.ndarray:
    try:
        return frame_signature(data)
    except Exception as exc:  # noqa: BLE001 - Pillow raises several types for an unreadable image
        raise UnsupportedSourceError(f"The frame is not an image Pillow can read: {exc}") from exc


__all__ = [
    "BUSY", "CHANGED", "COOLDOWN", "FIRST", "HEARTBEAT", "KEPT_DECISIONS", "MOVING", "PENDING", "SETTLING",
    "UNCHANGED", "FrameResult", "ObserveSessions", "StreamDecision", "StreamPolicy",
]
