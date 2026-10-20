"""observe(): a photo or a saved video becomes observation events (RFC-0018, phase 1).

A frame becomes memory the way a message does. The source is decoded into
frames, the keyframe policy keeps the frames that changed, a vision model
writes a facts-first description of each kept frame, descriptions that
repeat the previous kept frame's are dropped, and each remaining
description is admitted as an ordinary event: role ``tool``, the capture
clock as ``event_time``, and metadata that names the source, the frame
time, the frame's SHA-256, the thumbnail, the vision model and the prompt
version. Extraction, the graph, supersedence, retrieval, packing and
receipts then work unchanged, because an observation is text with
provenance. A thumbnail of each admitted keyframe stays in the pack's blobs
table; raw media stays outside, referenced by path and hash.

Everything here runs once, at admission. The vision model is never called
again by a rebuild or a replay, and a frame already observed by the same
model and prompt for the same owner and scope is skipped, so running
``observe()`` twice over the same files adds nothing: the repeat filter is
seeded from the stored description, so the heartbeat frames the first run
dropped are dropped again.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prme.ingestion.errors import ExtractionError
from prme.storage._threading import run_to_completion
from prme.types import Scope
from prme.vision import frames as frames_module
from prme.vision.describe import DESCRIBE_PROMPT_VERSION, describe_prompt, quoted_text
from prme.vision.errors import ObservationError, VisionDisabledError, VisionError
from prme.vision.keyframes import KeyframeDecision, select_keyframes, thumbnail_bytes
from prme.vision.repeat import DescriptionKey, is_repeat

if TYPE_CHECKING:
    import os

    from prme.config import VisionConfig
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)

ADMITTED = "admitted"
REPEAT = "repeat"
ALREADY_OBSERVED = "already_observed"
FAILED = "failed"

RESERVED_METADATA = (
    "modality", "source_file", "source_path", "source_sha256", "frame_index", "frame_time_s",
    "frame_sha256", "thumbnail", "vision_model", "describe_prompt_version", "capture_clock",
)
"""Observation metadata keys that observe() sets; caller metadata cannot override them."""


@dataclass
class ObservationRecord:
    """One kept keyframe: what the model said and what became of it."""

    index: int
    time_s: float | None
    frame_sha256: str
    change: float | None
    kept_reason: str | None
    status: str  # ADMITTED, REPEAT, ALREADY_OBSERVED or FAILED
    similarity: float | None = None
    event_id: str | None = None
    event_time: datetime | None = None
    extraction: str | None = None  # the extraction status when admitted with extraction
    error: str | None = None
    description: str | None = None
    describe_seconds: float | None = None
    admit_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_time"] = self.event_time.isoformat() if self.event_time else None
        return data


@dataclass
class ObservationReport:
    """Everything one observe() run decided, with the scores behind each decision."""

    source: str
    kind: str
    source_path: str | None
    source_sha256: str
    captured_at: datetime
    capture_clock: str
    vision_model: str
    describe_prompt_version: int
    extract: bool
    frames: int = 0
    keyframes: int = 0
    described: int = 0
    repeats_dropped: int = 0
    already_observed: int = 0
    admitted: int = 0
    failed: int = 0
    keyframe_log: list[KeyframeDecision] = field(default_factory=list)
    observations: list[ObservationRecord] = field(default_factory=list)

    @property
    def event_ids(self) -> list[str]:
        """The ids of the events this run admitted, in frame order."""
        return [record.event_id for record in self.observations
                if record.status == ADMITTED and record.event_id is not None]

    def summary(self) -> dict[str, Any]:
        """A JSON-serializable account of the run without the per-frame log."""
        return {
            "source": self.source, "kind": self.kind, "source_path": self.source_path,
            "source_sha256": self.source_sha256, "captured_at": self.captured_at.isoformat(),
            "capture_clock": self.capture_clock, "vision_model": self.vision_model,
            "describe_prompt_version": self.describe_prompt_version, "extract": self.extract,
            "frames": self.frames, "keyframes": self.keyframes, "described": self.described,
            "repeats_dropped": self.repeats_dropped, "already_observed": self.already_observed,
            "admitted": self.admitted, "failed": self.failed, "event_ids": self.event_ids,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "keyframe_log": [entry.to_dict() for entry in self.keyframe_log],
                "observations": [record.to_dict() for record in self.observations]}


def session_for(kind: str, name: str) -> str:
    """The default session id of a source: ``video:<file>`` or ``photo:<file>``."""
    return f"{'video' if kind == frames_module.VIDEO_FRAME else 'photo'}:{name}"


async def observe_source(
    engine: MemoryEngine,
    source: str | os.PathLike[str] | bytes,
    *,
    user_id: str,
    captured_at: datetime | None = None,
    source_name: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    scope: Scope = Scope.PERSONAL,
    policy=None,
) -> ObservationReport:
    """Observe one image or video through *engine* and report every decision.

    See :meth:`prme.storage.engine.MemoryEngine.observe` for the arguments.
    A keyframe whose admission fails is recorded as failed and the run goes
    on with the next; a vision model that cannot describe a frame, or a pack
    that cannot store one, stops the run with
    :class:`~prme.vision.errors.ObservationError`, whose ``report`` lists
    the events admitted before it. Calls on one engine run one at a time.
    """
    if engine._privacy.selected(policy):
        from prme.privacy.policy import PrivacyDenied
        raise PrivacyDenied("privacy_unsupported_input")
    config: VisionConfig = engine._config.vision
    if not config.enabled:
        raise VisionDisabledError(
            "Visual memory is off; set vision.enabled (PRME_VISION__ENABLED=true) to observe images and video"
        )
    if not user_id or not user_id.strip():
        raise ValueError("user_id is required")
    if not engine.supports_blobs:
        raise VisionError("observe() needs a local DuckDB pack in this release; the PostgreSQL backend is not supported")
    provider = engine.vision_provider()
    async with engine._observe_lock:
        with frames_module.new_workdir() as workdir_name:
            return await _observe(engine, provider, config, source, Path(workdir_name), user_id=user_id,
                                  captured_at=captured_at, source_name=source_name, session_id=session_id,
                                  metadata=metadata, scope=scope)


async def _observe(
    engine: MemoryEngine, provider: Any, config: VisionConfig, source: str | os.PathLike[str] | bytes,
    workdir: Path, *, user_id: str, captured_at: datetime | None, source_name: str | None,
    session_id: str | None, metadata: dict[str, Any] | None, scope: Scope,
) -> ObservationReport:
    # Blocking work (hashing, ffmpeg, the keyframe scores) runs on a worker
    # thread and is awaited to completion even when the caller is cancelled,
    # so the temporary directory is never removed under a running decoder.
    resolved = await run_to_completion(frames_module.resolve_source, source, name=source_name, workdir=workdir)
    start, clock = await run_to_completion(frames_module.capture_clock, resolved, captured_at)
    report = ObservationReport(
        source=resolved.name, kind=resolved.kind, source_path=resolved.given_path,
        source_sha256=resolved.sha256, captured_at=start, capture_clock=clock,
        vision_model=provider.model_name, describe_prompt_version=config.describe_prompt_version,
        extract=config.extract,
    )
    if resolved.kind == frames_module.VIDEO_FRAME:
        frame_paths = await run_to_completion(
            frames_module.decode_video_frames, resolved.path, workdir / "frames",
            fps=config.fps, width=config.frame_width,
        )
        decisions = await run_to_completion(
            select_keyframes, frame_paths, fps=config.fps, threshold=config.change_threshold,
            max_gap=config.max_gap_seconds,
        )
    else:
        frame_paths = [resolved.path]
        decisions = [KeyframeDecision(index=0, time_s=0.0, change=None, kept=True, reason="first")]
    report.frames = len(frame_paths)
    report.keyframe_log = decisions
    report.keyframes = sum(1 for decision in decisions if decision.kept)
    for decision in decisions:
        logger.debug("keyframe %s t=%.1fs change=%s kept=%s reason=%s", resolved.name, decision.time_s,
                     decision.change, decision.kept, decision.reason)
    prompt = describe_prompt(resolved.kind, config.describe_prompt_version)
    session = session_id or session_for(resolved.kind, resolved.name)
    previous: DescriptionKey | None = None
    for decision in decisions:
        if not decision.kept:
            continue
        frame_path = frame_paths[decision.index]
        if resolved.kind == frames_module.VIDEO_FRAME:
            frame_bytes = await run_to_completion(frame_path.read_bytes)
        else:
            frame_bytes = await run_to_completion(frames_module.photo_frame_bytes, frame_path)
        frame_sha256 = hashlib.sha256(frame_bytes).hexdigest()
        time_s = decision.time_s if resolved.kind == frames_module.VIDEO_FRAME else None
        record = ObservationRecord(index=decision.index, time_s=time_s, frame_sha256=frame_sha256,
                                   change=decision.change, kept_reason=decision.reason, status=FAILED)
        report.observations.append(record)
        existing = await engine.find_observation(
            user_id=user_id, scope=scope, source_sha256=resolved.sha256, frame_time_s=time_s,
            vision_model=provider.model_name, describe_prompt_version=config.describe_prompt_version,
        )
        if existing is not None:
            # The stored description seeds the repeat filter, so the frames
            # the earlier run dropped as repeats of this one are dropped again.
            record.status, record.event_id = ALREADY_OBSERVED, existing
            report.already_observed += 1
            stored = await engine.get_event(existing, user_id=user_id)
            if stored is not None:
                record.description = stored.content
                previous = await _description_key(engine, stored.content, report)
            logger.info("observe %s t=%s: frame already observed as event %s", resolved.name, time_s, existing)
            continue
        started = time.perf_counter()
        try:
            description = await provider.describe(frame_bytes, prompt=prompt)
        except VisionError as exc:
            record.error = str(exc)
            report.failed += 1
            logger.warning("observe %s t=%s: description failed: %s", resolved.name, time_s, exc)
            raise ObservationError(
                f"The vision model failed on frame {decision.index} of {resolved.name}: {exc}; "
                f"{report.admitted} keyframes were admitted before it", report=report,
            ) from exc
        record.describe_seconds = round(time.perf_counter() - started, 3)
        record.description = description
        report.described += 1
        current = await _description_key(engine, description, report)
        repeat, similarity = is_repeat(previous, current, threshold=config.repeat_similarity)
        record.similarity = None if similarity is None else round(similarity, 4)
        if repeat:
            record.status = REPEAT
            report.repeats_dropped += 1
            logger.info("observe %s t=%s: repeat of the previous keyframe (similarity %.3f)", resolved.name,
                        time_s, similarity)
            continue
        previous = current
        event_time = start + timedelta(seconds=time_s) if time_s is not None else start
        record.event_time = event_time
        thumbnail = None
        if config.keep_thumbnails:
            from prme.storage.blobs import thumbnail_key

            thumbnail = thumbnail_key(frame_sha256)
            try:
                data = await run_to_completion(thumbnail_bytes, frame_bytes, width=config.thumbnail_width)
                await engine.put_blob(thumbnail, "image/jpeg", data)
            except Exception as exc:  # noqa: BLE001 - a pack that cannot store a thumbnail stops the run
                record.error = f"{type(exc).__name__}: {exc}"
                report.failed += 1
                raise ObservationError(
                    f"The pack could not store the thumbnail of frame {decision.index} of {resolved.name}: "
                    f"{exc}; {report.admitted} keyframes were admitted before it", report=report,
                ) from exc
        event_metadata = {
            **{key: value for key, value in (metadata or {}).items() if key not in RESERVED_METADATA},
            "modality": resolved.kind,
            "source_file": resolved.name,
            "source_path": resolved.given_path,
            "source_sha256": resolved.sha256,
            "frame_index": decision.index,
            "frame_time_s": time_s,
            "frame_sha256": frame_sha256,
            "thumbnail": thumbnail,
            "vision_model": provider.model_name,
            "describe_prompt_version": config.describe_prompt_version,
            "capture_clock": clock,
        }
        started = time.perf_counter()
        try:
            event_id, extraction = await _admit(
                engine, description, user_id=user_id, session_id=session, metadata=event_metadata,
                event_time=event_time, scope=scope, extract=config.extract,
                retry_delays=config.extraction_retry_delays,
            )
        except Exception as exc:  # noqa: BLE001 - the record says why; the run goes on with the next frame
            record.error = f"{type(exc).__name__}: {exc}"
            record.admit_seconds = round(time.perf_counter() - started, 3)
            report.failed += 1
            logger.warning("observe %s t=%s: admission failed: %s", resolved.name, time_s, record.error)
            continue
        record.admit_seconds = round(time.perf_counter() - started, 3)
        record.status, record.event_id, record.extraction = ADMITTED, event_id, extraction
        report.admitted += 1
        logger.info("observe %s t=%s: event %s (%s)", resolved.name, time_s, event_id, extraction or "stored")
    return report


async def _description_key(engine: MemoryEngine, description: str, report: ObservationReport) -> DescriptionKey:
    """Embed a description for the repeat filter; a pack that cannot embed stops the run."""
    try:
        vector = (await engine.embed_texts([description]))[0]
    except Exception as exc:  # noqa: BLE001
        raise ObservationError(
            f"The pack could not embed a description of {report.source}: {exc}; "
            f"{report.admitted} keyframes were admitted before it", report=report,
        ) from exc
    return DescriptionKey.build(quoted_text(description), vector)


async def _admit(
    engine: MemoryEngine, content: str, *, user_id: str, session_id: str, metadata: dict[str, Any],
    event_time: datetime, scope: Scope, extract: bool, retry_delays: list[float],
) -> tuple[str, str | None]:
    """Admit one description; return its event id and, with extraction, the extraction status."""
    if not extract:
        event_id = await engine.store(content, user_id=user_id, role="tool", session_id=session_id,
                                      metadata=metadata, event_time=event_time, scope=scope)
        return str(event_id), None
    try:
        event_id = await engine.ingest(content, user_id=user_id, role="tool", session_id=session_id,
                                       metadata=metadata, event_time=event_time, wait_for_extraction=True,
                                       scope=scope)
    except ExtractionError as exc:
        # The event is saved before extraction runs. One failed extraction
        # leaves every later one in the session pending behind it, so it is
        # retried here, as the extracted-pack builder retries, before the
        # next frame is admitted.
        if exc.event_id is None:
            raise
        status = await _retry_extraction(engine, str(exc.event_id), user_id=user_id, delays=retry_delays)
        return str(exc.event_id), status
    status = await engine.extraction_status(str(event_id), user_id=user_id)
    return str(event_id), "complete" if status is None else status.status


async def _retry_extraction(engine: MemoryEngine, event_id: str, *, user_id: str, delays: list[float]) -> str:
    """Retry one event's extraction after each delay in *delays*; return its final status.

    Extraction work runs in order, so the owner's older pending work, which
    this event waits behind, is processed first by the same calls.
    """
    status = await engine.extraction_status(event_id, user_id=user_id)
    for delay in delays:
        if status is not None and status.status == "complete":
            break
        if delay:
            await asyncio.sleep(delay)
        await engine.retry_extraction(event_id, user_id=user_id)
        await engine.process_extractions(user_id=user_id, limit=10, budget_ms=10 ** 9)
        status = await engine.extraction_status(event_id, user_id=user_id)
    return "missing" if status is None else status.status


__all__ = [
    "ADMITTED", "ALREADY_OBSERVED", "DESCRIBE_PROMPT_VERSION", "FAILED", "REPEAT", "RESERVED_METADATA",
    "ObservationRecord", "ObservationReport", "observe_source", "session_for",
]
