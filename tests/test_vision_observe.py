"""observe(): photos and videos become observation events (RFC-0018, phase 1).

A fake vision provider reads the dominant color of each frame and answers a
canned facts-first description for it, so these tests need no model server.
They check the public contract: vision is off by default; a photo becomes
one event of role ``tool`` with the capture clock as ``event_time``, the
RFC-0018 metadata and a thumbnail in the pack's ``blobs`` table; a video
becomes one event per scene, with heartbeat repeats dropped by the
description filter and frame offsets added to the clock; a frame already
observed by the same model and prompt is skipped; a failed extraction is
retried before the next frame; thumbnails live inside a single-file pack and
survive ``prme migrate`` and ``prme rebuild``; and ``prme observe`` turns
vision on for its own run. Video tests need ``ffmpeg`` and are skipped
without it.
"""

from __future__ import annotations

import io
import json
import logging
import random
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import duckdb
import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from prme import MemoryClient, MemoryEngine, PRMEConfig
from prme.cli import build_parser, cmd_observe
from prme.client import config_from_directory, config_from_pack
from prme.config import VisionConfig
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.storage.blobs import blob_count, has_blobs_table, thumbnail_key
from prme.storage.pack_tools import migrate_directory_pack, open_pack_read_only
from prme.types import Scope, SourceType
from prme.vision import ObservationError, UnsupportedSourceError, VisionDisabledError, VisionError, observe_source
from prme.vision.observe import ADMITTED, ALREADY_OBSERVED, REPEAT
from tests.test_concurrency import MockExtractionProvider
from tests.test_durable_ingestion import MockEmbeddingProvider

USER = "alice"
START = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
SIZE = (1280, 720)  # the size real recordings and the spike corpus have; the thresholds were set there
RED, GREEN, BLUE = (220, 40, 40), (40, 160, 60), (40, 60, 200)
DESCRIPTIONS = {
    RED: 'This is a video frame of a red slide. The slide title is "Budget review". The budget is "$12,500".',
    GREEN: 'This is a video frame of a green slide. The slide title is "Budget review". The budget is "$14,000".',
    BLUE: 'This is a video frame of a blue slide. The slide says "Next sync: 21 October".',
}
NEEDS_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


LINES = {
    RED: ("Budget review", ["Launch date: 3 Nov", "Budget: $12,500", "Vendor: Northwind Sensors"]),
    GREEN: ("Budget review", ["Launch date: 10 Nov", "Budget: $14,000", "Owner: Priya"]),
    BLUE: ("Wrap up", ["Next sync: 21 October", "Demo for Acme on 28 October", "Thanks everyone"]),
}


def slide(color, text: str | None = None) -> Image.Image:
    """A 1280x720 slide like the spike corpus's: a *color* title bar and dark text on white.

    The design matters for the pixel-change filter, whose score is
    contrast-normalized: a frame with little contrast, such as pale text on
    a saturated background, reads its own camera shake as a change (scores
    of 0.57 to 0.67 were measured on such slides), while these high-contrast
    slides shake at 0.3 or less, as the spike corpus did.
    """
    title, bullets = LINES[color]
    image = Image.new("RGB", SIZE, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, SIZE[0], 110), fill=color)
    draw.text((60, 28), text or title, fill=(255, 255, 255), font=ImageFont.load_default(size=52))
    for line, words in enumerate(bullets):
        draw.ellipse((70, 186 + line * 72, 86, 202 + line * 72), fill=(30, 60, 160))
        draw.text((110, 170 + line * 72), words, fill=(20, 20, 20), font=ImageFont.load_default(size=40))
    return image


def jitter(image: Image.Image, rng: random.Random) -> Image.Image:
    dx, dy = rng.randint(-3, 3), rng.randint(-3, 3)
    canvas = Image.new("RGB", image.size, (0, 0, 0))
    canvas.paste(image, (dx, dy))
    return ImageEnhance.Brightness(canvas).enhance(rng.uniform(0.97, 1.03))


def dominant(image: bytes) -> tuple[int, int, int]:
    """The scene color of a frame: the mean color of its title bar (the top sixth)."""
    picture = Image.open(io.BytesIO(image)).convert("RGB")
    bar = picture.crop((0, 0, picture.width, max(1, picture.height // 6))).resize((8, 8))
    pixels = list(bar.getdata())
    mean = tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))
    return min(DESCRIPTIONS, key=lambda color: sum(abs(color[i] - mean[i]) for i in range(3)))


class FakeVision:
    """Describes a frame by its dominant color; counts calls and keeps the prompts it saw."""

    model_name = "fake/vision-1"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def describe(self, image: bytes, *, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        return DESCRIPTIONS[dominant(image)]


@pytest.fixture
def vision(monkeypatch) -> FakeVision:
    fake = FakeVision()
    monkeypatch.setattr("prme.vision.provider.create_vision_provider", lambda config: fake)
    return fake


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: MockExtractionProvider())
    monkeypatch.setenv("PRME_ORGANIZER_OPPORTUNISTIC_ENABLED", "false")
    for name in ("PRME_DATABASE_URL", "PRME_PACK_PATH", "PRME_ENCRYPTION_ENABLED", "PRME_ENCRYPTION_KEY",
                 "PRME_VISION__ENABLED", "PRME_VISION_ENABLED", "PRME_VISION_EXTRACT", "PRME_VISION__EXTRACT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def quiet_structlog():
    import structlog

    saved = structlog.get_config()
    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )
    yield
    structlog.configure(**saved)


def vision_config(**overrides) -> VisionConfig:
    settings = {"enabled": True, "extract": False, "max_gap_seconds": 3.0, "extraction_retry_delays": [0.0]}
    settings.update(overrides)
    return VisionConfig(**settings)


def directory_config(directory: Path, **vision_overrides) -> PRMEConfig:
    return config_from_directory(str(directory)).model_copy(update={"vision": vision_config(**vision_overrides)})


@pytest.fixture
def photo(tmp_path) -> Path:
    path = tmp_path / "receipt.jpg"
    slide(RED, "TOTAL $12,500").save(path, quality=90)
    return path


@pytest.fixture
def video(tmp_path) -> Path:
    """Three 8 second scenes at one frame per second, each frame shaken a little."""
    frames = tmp_path / "video_frames"
    frames.mkdir()
    rng = random.Random(5)
    index = 0
    for color in (RED, GREEN, BLUE):
        for _ in range(8):
            jitter(slide(color), rng).save(frames / f"{index:05d}.png")
            index += 1
    path = tmp_path / "meeting.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", "1", "-i", str(frames / "%05d.png"), "-r", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def tables(db_path: Path) -> set[str]:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        return {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    finally:
        conn.close()


# --- defaults and guards -----------------------------------------------------


def test_vision_is_off_by_default_and_loads_from_the_environment(monkeypatch):
    config = PRMEConfig()
    assert config.vision.enabled is False
    assert config.vision.provider == "ollama" and config.vision.extract is True
    assert config.vision.describe_prompt_version == 2 and config.vision.keep_thumbnails is True
    monkeypatch.setenv("PRME_VISION__ENABLED", "true")
    monkeypatch.setenv("PRME_VISION_MODEL", "other:1")
    monkeypatch.setenv("PRME_VISION__EXTRACT", "false")
    loaded = PRMEConfig()
    assert (loaded.vision.enabled, loaded.vision.model, loaded.vision.extract) == (True, "other:1", False)
    with pytest.raises(ValueError):
        VisionConfig(describe_prompt_version=1)
    with pytest.raises(ValueError):
        VisionConfig(extraction_retry_delays=[-1.0])


async def test_observe_refuses_while_vision_is_off_and_changes_nothing(tmp_path, photo, vision):
    config = config_from_directory(str(tmp_path / "pack"))
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(VisionDisabledError, match="PRME_VISION__ENABLED"):
            await engine.observe(photo, user_id=USER, captured_at=START)
        assert await engine.get_events(USER) == []
    assert vision.calls == 0
    assert "blobs" not in tables(tmp_path / "pack" / "memory.duckdb")


async def test_unsupported_and_missing_sources(tmp_path, vision):
    note = tmp_path / "note.txt"
    note.write_text("not an image")
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        with pytest.raises(UnsupportedSourceError, match="not an image or video"):
            await engine.observe(note, user_id=USER)
        with pytest.raises(UnsupportedSourceError):
            await engine.observe(b"plain bytes", user_id=USER)
        with pytest.raises(FileNotFoundError):
            await engine.observe(tmp_path / "missing.jpg", user_id=USER)
        # JPEG magic alone is not an image Pillow can read.
        with pytest.raises(UnsupportedSourceError, match="Pillow can read"):
            await engine.observe(b"\xff\xd8\xff" + b"\x00" * 16, user_id=USER)
        with pytest.raises(ValueError, match="timezone-aware"):
            await engine.observe(photo_bytes(), user_id=USER, captured_at=datetime(2026, 10, 14))
        # A HEIC still is a photo, not a video, and needs pillow-heif.
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32
        try:
            import pillow_heif  # noqa: F401
        except ImportError:
            with pytest.raises(UnsupportedSourceError, match="pillow-heif"):
                await engine.observe(heic, user_id=USER, source_name="IMG_0001.HEIC")
    assert vision.calls == 0


def photo_bytes() -> bytes:
    buffer = io.BytesIO()
    slide(RED).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


# --- photos ------------------------------------------------------------------


async def test_photo_becomes_one_tool_event_with_provenance_and_a_thumbnail(tmp_path, photo, vision):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        ids = await engine.observe(photo, user_id=USER, captured_at=START, metadata={"album": "errands"})
        assert len(ids) == 1 and vision.calls == 1
        assert "photo" in vision.prompts[0] and "Quote visible text" in vision.prompts[0]
        event = await engine.get_event(ids[0], user_id=USER)
        assert event.role == "tool"
        assert event.content == DESCRIPTIONS[RED]
        assert event.event_time == START
        assert event.session_id == "photo:receipt.jpg"
        frame_sha = event.metadata["frame_sha256"]
        assert event.metadata == {
            "album": "errands",
            "modality": "photo",
            "source_file": "receipt.jpg",
            "source_path": str(photo.resolve()),
            "source_sha256": frame_sha,
            "frame_index": 0,
            "frame_time_s": None,
            "frame_sha256": frame_sha,
            "thumbnail": thumbnail_key(frame_sha),
            "vision_model": "fake/vision-1",
            "describe_prompt_version": 2,
            "capture_clock": "given",
        }
        thumbnail = await engine.get_blob(event.metadata["thumbnail"])
        image = Image.open(io.BytesIO(thumbnail))
        assert image.format == "JPEG" and image.size == (320, 180)
        assert await engine.get_blob("frames/missing.jpg") is None
        nodes = await engine.get_event_nodes(ids[0], user_id=USER)
        assert len(nodes) == 1 and nodes[0].source_type == SourceType.TOOL_OUTPUT
        response = await engine.retrieve("what is the budget", user_id=USER, token_budget=1000)
        assert "$12,500" in response.bundle.render()


async def test_photo_bytes_use_the_given_name_and_no_path(tmp_path, photo, vision):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        ids = await engine.observe(photo.read_bytes(), user_id=USER, captured_at=START, source_name="scan.jpg",
                                   session_id="errands")
        event = await engine.get_event(ids[0], user_id=USER)
        assert event.session_id == "errands"
        assert event.metadata["source_file"] == "scan.jpg" and event.metadata["source_path"] is None
        unnamed = await engine.observe(photo.read_bytes(), user_id="bob")
        other = await engine.get_event(unnamed[0], user_id="bob")
        assert other.metadata["source_file"].startswith("bytes:")
        assert other.metadata["capture_clock"] == "now"


async def test_caller_metadata_cannot_override_observation_fields(tmp_path, photo, vision):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        ids = await engine.observe(photo, user_id=USER, captured_at=START,
                                   metadata={"modality": "forged", "vision_model": "forged", "note": "kept"})
        event = await engine.get_event(ids[0], user_id=USER)
        assert event.metadata["modality"] == "photo"
        assert event.metadata["vision_model"] == "fake/vision-1"
        assert event.metadata["note"] == "kept"


async def test_capture_clock_falls_back_to_exif_then_file_time(tmp_path, vision):
    with_exif = tmp_path / "exif.jpg"
    image = slide(RED)
    exif = image.getexif()
    exif[36867] = "2026:10:12 08:41:00"
    exif[36881] = "-07:00"
    image.save(with_exif, quality=90, exif=exif.tobytes())
    plain = tmp_path / "plain.jpg"
    slide(GREEN).save(plain, quality=90)
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        (exif_id,) = await engine.observe(with_exif, user_id=USER)
        exif_event = await engine.get_event(exif_id, user_id=USER)
        assert exif_event.metadata["capture_clock"] == "exif"
        assert exif_event.event_time == datetime(2026, 10, 12, 15, 41, tzinfo=timezone.utc)
        (plain_id,) = await engine.observe(plain, user_id=USER)
        plain_event = await engine.get_event(plain_id, user_id=USER)
        assert plain_event.metadata["capture_clock"] == "file"
        modified = datetime.fromtimestamp(plain.stat().st_mtime, tz=timezone.utc)
        assert abs(plain_event.event_time - modified) < timedelta(seconds=2)


async def test_a_frame_is_observed_once_per_owner_model_and_prompt(tmp_path, photo, vision):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        first = await engine.observe(photo, user_id=USER, captured_at=START)
        again = await observe_source(engine, photo, user_id=USER, captured_at=START)
        assert again.event_ids == [] and again.already_observed == 1 and again.admitted == 0
        assert again.observations[0].status == ALREADY_OBSERVED
        assert again.observations[0].event_id == first[0]
        assert vision.calls == 1
        # Another owner, or another model, is a new observation.
        assert len(await engine.observe(photo, user_id="bob", captured_at=START)) == 1
        vision.model_name = "fake/vision-2"
        assert len(await engine.observe(photo, user_id=USER, captured_at=START)) == 1
        assert vision.calls == 3
        assert len(await engine.get_events(USER)) == 2


async def test_keep_thumbnails_off_stores_no_blob(tmp_path, photo, vision):
    async with MemoryEngine.open(directory_config(tmp_path / "pack", keep_thumbnails=False)) as engine:
        (event_id,) = await engine.observe(photo, user_id=USER, captured_at=START)
        event = await engine.get_event(event_id, user_id=USER)
        assert event.metadata["thumbnail"] is None
    assert "blobs" not in tables(tmp_path / "pack" / "memory.duckdb")


# --- video -------------------------------------------------------------------


@NEEDS_FFMPEG
async def test_video_becomes_one_event_per_scene_with_frame_offsets(tmp_path, video, vision):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        report = await observe_source(engine, video, user_id=USER, captured_at=START)
        assert report.kind == "video_frame" and report.frames == 24
        kept = [entry for entry in report.keyframe_log if entry.kept]
        assert kept[0].reason == "first"
        # Scene changes start a keyframe on pixel change; the rest are heartbeats
        # every 3 seconds that the description filter then drops as repeats.
        changes = [entry.index for entry in kept if entry.reason == "change"]
        assert changes == [8, 16]
        assert all(entry.change >= 0.55 for entry in kept if entry.reason == "change")
        assert report.described == report.keyframes and report.failed == 0
        assert report.admitted == 3 and report.repeats_dropped == report.keyframes - 3
        assert [record.status for record in report.observations].count(REPEAT) == report.repeats_dropped
        assert all(record.similarity is not None and record.similarity >= 0.93
                   for record in report.observations if record.status == REPEAT)
        admitted = [record for record in report.observations if record.status == ADMITTED]
        assert [record.time_s for record in admitted] == [0.0, 8.0, 16.0]
        assert report.event_ids == [record.event_id for record in admitted]
        events = [await engine.get_event(event_id, user_id=USER) for event_id in report.event_ids]
        assert [event.event_time for event in events] == [START, START + timedelta(seconds=8),
                                                          START + timedelta(seconds=16)]
        assert [event.content for event in events] == [DESCRIPTIONS[RED], DESCRIPTIONS[GREEN], DESCRIPTIONS[BLUE]]
        assert {event.session_id for event in events} == {"video:meeting.mp4"}
        assert [event.metadata["frame_time_s"] for event in events] == [0.0, 8.0, 16.0]
        assert all(event.metadata["modality"] == "video_frame" for event in events)
        assert all(event.metadata["source_sha256"] == report.source_sha256 for event in events)
        assert len({event.metadata["frame_sha256"] for event in events}) == 3
        for event in events:
            assert (await engine.get_blob(event.metadata["thumbnail"])) is not None
        assert "video frame" in vision.prompts[0]
        assert report.summary()["event_ids"] == report.event_ids
        assert json.dumps(report.to_dict(), default=str)
        response = await engine.retrieve("what is the budget now", user_id=USER, token_budget=2000)
        assert "$14,000" in response.bundle.render()
        # A second run admits nothing: the admitted frames are found by source
        # hash and frame time, and the stored descriptions seed the repeat
        # filter so the heartbeat frames are dropped again.
        calls_before = vision.calls
        second = await observe_source(engine, video, user_id=USER, captured_at=START)
        assert (second.admitted, second.already_observed, second.failed) == (0, 3, 0)
        assert second.repeats_dropped == report.repeats_dropped
        assert vision.calls == calls_before + second.repeats_dropped
        assert len(await engine.get_events(USER)) == 3
        assert await engine.observe(video, user_id=USER, captured_at=START) == []
        # Another scope is another observation.
        assert len(await engine.observe(video, user_id=USER, captured_at=START, scope=Scope.PROJECT)) == 3
    conn = duckdb.connect(str(tmp_path / "pack" / "memory.duckdb"), read_only=True)
    try:
        assert blob_count(conn) == 3
    finally:
        conn.close()


@NEEDS_FFMPEG
async def test_vision_failure_mid_run_keeps_the_admitted_events_in_the_error(tmp_path, video, vision):
    original = vision.describe

    async def flaky(image: bytes, *, prompt: str) -> str:
        if vision.calls >= 2:
            raise VisionError("Ollama went away")
        return await original(image, prompt=prompt)

    vision.describe = flaky
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        with pytest.raises(ObservationError, match="Ollama went away") as failure:
            await observe_source(engine, video, user_id=USER, captured_at=START)
        partial = failure.value.report
        assert partial.admitted == 1 and partial.failed == 1
        assert len(partial.event_ids) == 1
        assert partial.observations[-1].status == "failed" and "Ollama went away" in partial.observations[-1].error
        assert (await engine.get_event(partial.event_ids[0], user_id=USER)).content == DESCRIPTIONS[RED]


async def test_observe_raises_when_a_frame_is_not_admitted(tmp_path, photo, vision, monkeypatch):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        async def refuse(*args, **kwargs):
            raise RuntimeError("write queue closed")

        monkeypatch.setattr(engine, "store", refuse)
        with pytest.raises(ObservationError, match="write queue closed") as failure:
            await engine.observe(photo, user_id=USER, captured_at=START)
        assert failure.value.report.failed == 1 and failure.value.report.event_ids == []
        report = await observe_source(engine, photo, user_id=USER, captured_at=START)
        assert report.failed == 1 and report.observations[0].status == "failed"


@NEEDS_FFMPEG
async def test_video_without_a_clock_uses_the_container_or_file_time(tmp_path, video, vision):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        report = await observe_source(engine, video, user_id=USER)
        assert report.capture_clock in {"container", "file"}
        assert report.captured_at.tzinfo is not None
        events = [await engine.get_event(event_id, user_id=USER) for event_id in report.event_ids]
        assert events[1].event_time - events[0].event_time == timedelta(seconds=8)


# --- extraction --------------------------------------------------------------


class FlakyExtraction(MockExtractionProvider):
    """Fails its first call, then extracts one fact from the description."""

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, content: str, **kwargs) -> ExtractionResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("vision description cut off")
        return ExtractionResult(
            entities=[ExtractedEntity(name="Budget review", entity_type="event")],
            facts=[ExtractedFact(subject="Budget review", predicate="budget", object="$12,500",
                                 evidence_quote='The budget is "$12,500".')],
        )


async def test_extraction_is_retried_before_the_next_frame_and_facts_cite_the_frame(tmp_path, photo, vision,
                                                                                    monkeypatch):
    extraction = FlakyExtraction()
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: extraction)
    async with MemoryEngine.open(directory_config(tmp_path / "pack", extract=True)) as engine:
        engine._pipeline._retry_delays = ()
        report = await observe_source(engine, photo, user_id=USER, captured_at=START)
        assert report.extract is True and report.admitted == 1 and report.failed == 0
        record = report.observations[0]
        assert record.status == ADMITTED and record.extraction == "complete"
        assert extraction.calls == 2
        status = await engine.extraction_status(record.event_id, user_id=USER)
        assert status.status == "complete"
        nodes = await engine.get_event_nodes(record.event_id, user_id=USER)
        facts = [node for node in nodes if node.node_type.value == "fact"]
        assert facts and "$12,500" in facts[0].content
        provenance = await engine.get_provenance(str(facts[0].id), user_id=USER)
        assert [str(event.id) for event in provenance.evidence_events] == [record.event_id]
        assert provenance.evidence_events[0].metadata["thumbnail"] == thumbnail_key(record.frame_sha256)
        assert facts[0].source_type == SourceType.TOOL_OUTPUT


async def test_extraction_failure_that_never_recovers_still_returns_the_event(tmp_path, photo, vision, monkeypatch):
    broken = MockExtractionProvider()
    broken.extract = AsyncMock(side_effect=RuntimeError("model unavailable"))
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: broken)
    async with MemoryEngine.open(directory_config(tmp_path / "pack", extract=True,
                                                  extraction_retry_delays=[0.0, 0.0])) as engine:
        engine._pipeline._retry_delays = ()
        report = await observe_source(engine, photo, user_id=USER, captured_at=START)
        record = report.observations[0]
        assert record.status == ADMITTED and record.extraction == "failed"
        assert report.event_ids == [record.event_id]
        assert (await engine.get_event(record.event_id, user_id=USER)).content == DESCRIPTIONS[RED]
        assert broken.extract.await_count == 3


# --- pack layouts and maintenance ------------------------------------------


async def test_thumbnails_live_inside_a_single_file_pack(tmp_path, photo, vision):
    pack = tmp_path / "memories.prme"
    config = config_from_pack(str(pack)).model_copy(update={"vision": vision_config()})
    async with MemoryEngine.open(config) as engine:
        (event_id,) = await engine.observe(photo, user_id=USER, captured_at=START)
        key = (await engine.get_event(event_id, user_id=USER)).metadata["thumbnail"]
    assert sorted(path.name for path in tmp_path.iterdir() if path.name.startswith("memories")) == ["memories.prme"]
    async with MemoryEngine.open(config) as engine:
        thumbnail = await engine.get_blob(key)
        assert Image.open(io.BytesIO(thumbnail)).size == (320, 180)
        assert "$12,500" in (await engine.retrieve("budget", user_id=USER, token_budget=1000)).bundle.render()
    with open_pack_read_only(pack) as conn:
        assert has_blobs_table(conn) and blob_count(conn) == 1


async def test_migrate_and_rebuild_keep_observations_and_thumbnails(tmp_path, photo, vision):
    directory = tmp_path / "pack"
    async with MemoryEngine.open(directory_config(directory)) as engine:
        (event_id,) = await engine.observe(photo, user_id=USER, captured_at=START)
        key = (await engine.get_event(event_id, user_id=USER)).metadata["thumbnail"]
        summary = await engine.rebuild_indexes()
        assert summary["nodes_indexed"] >= 1
        assert (await engine.get_blob(key)) is not None
        assert "$12,500" in (await engine.retrieve("budget", user_id=USER, token_budget=1000)).bundle.render()
    pack = tmp_path / "migrated.prme"
    result = await migrate_directory_pack(directory, pack)
    assert result["events"] == 1
    async with MemoryEngine.open(config_from_pack(str(pack))) as engine:
        assert (await engine.get_blob(key)) is not None
        assert (await engine.get_event(event_id, user_id=USER)).metadata["thumbnail"] == key


# --- client and CLI ----------------------------------------------------------


def test_client_observe_and_get_blob(tmp_path, photo, vision):
    with MemoryClient(config=directory_config(tmp_path / "pack")) as client:
        ids = client.observe(photo, user_id=USER, captured_at=START)
        assert len(ids) == 1
        event = client.get_event(ids[0], user_id=USER)
        assert Image.open(io.BytesIO(client.get_blob(event.metadata["thumbnail"]))).size == (320, 180)
        assert client.observe(photo, user_id=USER, captured_at=START) == []


def test_cli_observe_turns_vision_on_for_its_run(tmp_path, photo, vision, capsys, monkeypatch):
    directory = tmp_path / "pack"
    with MemoryClient(str(directory)):
        pass
    parser = build_parser()
    args = parser.parse_args(["observe", str(directory / "memory.duckdb"), str(photo), "--user-id", USER,
                              "--captured-at", "2026-10-14T10:00:00+00:00", "--no-extract", "--format", "json"])
    assert args.func is cmd_observe and args.extract is False

    import asyncio

    asyncio.run(cmd_observe(args))
    reports = json.loads(capsys.readouterr().out)
    assert len(reports) == 1
    assert reports[0]["admitted"] == 1 and reports[0]["extract"] is False
    assert reports[0]["keyframe_log"] == [{"index": 0, "time_s": 0.0, "change": None, "kept": True, "reason": "first"}]
    assert reports[0]["observations"][0]["status"] == "admitted"
    assert vision.calls == 1

    # The table format, a second run that observes nothing new, and the
    # pack is unchanged by the command's own vision setting afterwards.
    args = parser.parse_args(["observe", str(directory / "memory.duckdb"), str(photo), "--user-id", USER,
                              "--no-extract"])
    asyncio.run(cmd_observe(args))
    out = capsys.readouterr().out
    assert "Already observed: 1" in out and "Admitted:         0" in out
    assert vision.calls == 1
    with MemoryClient(str(directory)) as client:
        assert len(client.get_events(USER)) == 1
        with pytest.raises(VisionDisabledError):
            client.observe(photo, user_id=USER)


def test_cli_observe_reads_nested_vision_settings_from_the_environment(tmp_path, photo, vision, capsys,
                                                                        monkeypatch):
    directory = tmp_path / "pack"
    with MemoryClient(str(directory)):
        pass
    monkeypatch.setenv("PRME_VISION__EXTRACT", "false")
    monkeypatch.setenv("PRME_VISION__KEEP_THUMBNAILS", "false")
    monkeypatch.setenv("PRME_VISION_MAX_GAP_SECONDS", "7")
    args = build_parser().parse_args(["observe", str(directory / "memory.duckdb"), str(photo), "--user-id", USER,
                                      "--format", "json"])
    assert args.extract is None

    import asyncio

    asyncio.run(cmd_observe(args))
    report = json.loads(capsys.readouterr().out)[0]
    assert report["extract"] is False and report["admitted"] == 1
    with MemoryClient(str(directory)) as client:
        event = client.get_event(report["event_ids"][0], user_id=USER)
        assert event.metadata["thumbnail"] is None
    assert "blobs" not in tables(directory / "memory.duckdb")


def test_cli_observe_reports_a_bad_file_and_exits_nonzero(tmp_path, photo, vision, capsys):
    directory = tmp_path / "pack"
    with MemoryClient(str(directory)):
        pass
    note = tmp_path / "note.txt"
    note.write_text("x")
    args = build_parser().parse_args(["observe", str(directory / "memory.duckdb"), str(note), str(photo),
                                      "--user-id", USER, "--no-extract", "--format", "json"])
    import asyncio

    with pytest.raises(SystemExit) as exit_info:
        asyncio.run(cmd_observe(args))
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    reports = json.loads(captured.out)
    assert "UnsupportedSourceError" in reports[0]["error"] and reports[1]["admitted"] == 1
    assert "not an image or video" in captured.err
