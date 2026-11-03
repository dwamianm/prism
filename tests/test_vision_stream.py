"""A live observe session: camera frames one at a time (dwamianm/prism#390, RFC-0018 phases 2 and 3).

The scenes are ported from the Nenavi camera demo's watch tests
(``apps/vision-demo/server/tests/test_watch.py`` in dwamianm/prism-monorepo),
the cases earlier versions of the demo's gate missed: the first frame, an
object put on the desk, a person sitting down, a person who never stops
moving, and a hand that holds up one more finger every two seconds. Sensor
noise, exposure steps and a few pixels of shake must not count as a change.
Frames are 640x360 JPEG samples half a second apart, as the demo's page
sends them, and the session runs on the default ``VisionConfig``: the
calibrated threshold, a 0.5 second settle, a 6 second settle timeout and a
10 second gap between keeps of a picture that keeps changing.

The first half checks :class:`StreamPolicy`, the decision alone; the second
replays the same scenes through :meth:`MemoryEngine.observe_frame` with fake
vision providers that read the finger count off the frame, so no model
server is needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from datetime import datetime, timedelta

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance

from prme import MemoryEngine, PRMEConfig
from prme.config import VisionConfig
from prme.storage.blobs import thumbnail_key
from prme.vision import UnsupportedSourceError, VisionDisabledError, VisionError
from prme.vision.describe import stated_counts
from prme.vision.keyframes import frame_signature
from prme.vision.observe import ADMITTED, ALREADY_OBSERVED, FAILED, REPEAT
from prme.vision.repeat import DescriptionKey, is_repeat
from prme.vision.stream import (
    BUSY,
    CHANGED,
    COOLDOWN,
    FIRST,
    HEARTBEAT,
    MOVING,
    PENDING,
    SETTLING,
    UNCHANGED,
    FrameResult,
    StreamPolicy,
)
from tests.test_vision_observe import (  # noqa: F401  (isolated and quiet_structlog are autouse fixtures)
    START,
    USER,
    directory_config,
    isolated,
    quiet_structlog,
)

WIDTH, HEIGHT = 640, 360  # the demo's sample size
STEP = 0.5  # the demo's page samples twice a second
SKIN = (205, 160, 135)
SEEDS = range(5)


def desk(*, phone: bool = False, person_x: int | None = None, fingers: int | None = None) -> Image.Image:
    """The demo's desk: a shaded wall, a laptop, a red ball and a white note."""
    shade = np.linspace(90, 170, WIDTH)[None, :].repeat(HEIGHT, 0)
    image = Image.fromarray(shade.astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 190, 260, 350), fill=(60, 60, 70))
    draw.ellipse((450, 210, 520, 280), fill=(200, 60, 50))
    draw.rectangle((300, 60, 550, 165), fill=(235, 235, 230))
    if phone:
        draw.rounded_rectangle((300, 225, 350, 320), 6, fill=(20, 20, 20))
    if person_x is not None:
        draw.ellipse((person_x, 75, person_x + 130, 215), fill=(180, 140, 120))
        draw.rectangle((person_x - 30, 215, person_x + 160, HEIGHT), fill=(40, 70, 120))
    if fingers is not None:
        draw.rounded_rectangle((330, 230, 430, 330), 12, fill=SKIN)  # the palm, held up
        for finger in range(fingers):
            left = 332 + finger * 20
            draw.rounded_rectangle((left, 160, left + 15, 232), 7, fill=SKIN)
    return image


def camera(image: Image.Image, rng: np.random.Generator, *, shift: tuple[int, int] = (0, 0),
           exposure: float = 1.0) -> bytes:
    """What the demo's page posts: the scene, moved by *shift*, at *exposure*, with sensor noise, as a JPEG."""
    canvas = Image.new("RGB", image.size)
    canvas.paste(image, shift)
    canvas = ImageEnhance.Brightness(canvas).enhance(exposure)
    noisy = np.asarray(canvas, dtype=np.float32) + rng.normal(0, 4.0, (HEIGHT, WIDTH, 3))
    buffer = io.BytesIO()
    Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8)).save(buffer, format="JPEG")
    return buffer.getvalue()


def held(image: Image.Image, seconds: float, rng: np.random.Generator) -> list[bytes]:
    """The samples of a scene held for *seconds*, each with its own sensor noise."""
    return [camera(image, rng) for _ in range(round(seconds / STEP))]


def finger_frames(rng: np.random.Generator, *, counts=range(1, 6), seconds: float = 2.0) -> list[bytes]:
    """The owner's test of 2026-11-02: one finger more every two seconds."""
    return [frame for count in counts for frame in held(desk(fingers=count), seconds, rng)]


SHAKES = [(3, 2), (-3, 2), (2, 3), (1, 1), (4, 4), (6, -4)]


def nuisance_frames(rng: np.random.Generator) -> list[bytes]:
    """A still desk, then exposure steps of 12% each way and shakes of 1 to 6 pixels."""
    still = desk()
    frames = held(still, 1.5, rng)
    frames += [camera(still, rng, exposure=1.12), camera(still, rng), camera(still, rng, exposure=0.88)]
    frames += [camera(still, rng, shift=shift) for shift in SHAKES]
    return frames + [camera(still, rng)]


def walk(index: int, speed: int = 14) -> int:
    """Where a person walking *speed* px a sample, back and forth across the desk, is at sample *index*."""
    travel = (speed * index) % 900
    return 20 + (travel if travel <= 450 else 900 - travel)


def policy(**overrides) -> StreamPolicy:
    return StreamPolicy.from_config(VisionConfig(**overrides))


def run(gate: StreamPolicy, frames: list[bytes], *, start: float = 0.0) -> list[str]:
    return [gate.decide(frame_signature(frame), now=start + index * STEP).decision
            for index, frame in enumerate(frames)]


def kept(decisions: list[str]) -> list[int]:
    return [index for index, decision in enumerate(decisions) if decision in (FIRST, CHANGED, MOVING, HEARTBEAT)]


# --- the decision ----------------------------------------------------------------


class TestStreamPolicy:
    def test_defaults_are_the_demo_gate(self):
        vision = VisionConfig()
        assert (vision.stream_settle_seconds, vision.stream_settle_timeout_seconds,
                vision.stream_moving_gap_seconds, vision.stream_heartbeat_seconds) == (0.5, 6.0, 10.0, 60.0)
        assert (vision.stream_max_pending, vision.stream_max_sessions, vision.stream_idle_seconds) == (10, 16, 300.0)

    def test_the_first_frame_is_kept_once_the_picture_has_held_still_for_half_a_second(self):
        assert run(policy(), held(desk(), 1.5, np.random.default_rng(0))) == [SETTLING, FIRST, UNCHANGED]

    @pytest.mark.parametrize("seed", SEEDS)
    def test_noise_exposure_and_shake_are_not_changes(self, seed):
        decisions = run(policy(), nuisance_frames(np.random.default_rng(seed)))
        assert decisions == [SETTLING, FIRST] + [UNCHANGED] * 11

    @pytest.mark.parametrize("seed", SEEDS)
    def test_every_finger_count_held_for_two_seconds_is_kept_once(self, seed):
        decisions = run(policy(), finger_frames(np.random.default_rng(seed)))
        # Each count is kept once it has held still for half a second: the second of its four samples.
        assert kept(decisions) == [1, 5, 9, 13, 17], decisions
        assert [decisions[index] for index in kept(decisions)] == [FIRST, CHANGED, CHANGED, CHANGED, CHANGED]
        assert decisions[4::4] == [SETTLING] * 4  # the first sample of each new count is still moving

    def test_without_calibration_the_clip_threshold_keeps_no_finger_count(self):
        # Why calibration matters here: the strongest block change of one more finger stays under 0.55.
        frames = finger_frames(np.random.default_rng(0))
        assert kept(run(policy(calibrate_keyframes=False), frames)) == [1]

    def test_a_longer_settle_time_keeps_each_count_one_sample_later(self):
        decisions = run(policy(stream_settle_seconds=1.0), finger_frames(np.random.default_rng(0)))
        assert kept(decisions) == [2, 6, 10, 14, 18], decisions

    def test_an_object_put_down_is_kept_right_after_the_last_keep(self):
        rng = np.random.default_rng(2)
        frames = [*held(desk(), 1.5, rng), *held(desk(phone=True), 1.5, rng)]
        assert run(policy(), frames) == [SETTLING, FIRST, UNCHANGED, SETTLING, CHANGED, UNCHANGED]

    def test_a_person_sitting_down_is_kept_once_they_hold_still(self):
        rng = np.random.default_rng(3)
        frames = [*held(desk(), 1.5, rng), camera(desk(person_x=60), rng), camera(desk(person_x=110), rng),
                  *held(desk(person_x=150), 2.0, rng)]
        decisions = run(policy(), frames)
        assert decisions == [SETTLING, FIRST, UNCHANGED, SETTLING, SETTLING, SETTLING, CHANGED, UNCHANGED,
                             UNCHANGED], decisions

    @staticmethod
    def walking(gate: StreamPolicy, *, speed: int, seed: int = 4):
        rng = np.random.default_rng(seed)
        return [gate.decide(frame_signature(camera(desk(person_x=walk(index, speed)), rng)), now=index * STEP)
                for index in range(34)]  # 17 seconds

    def test_the_demo_walk_is_kept_after_the_settle_timeout_and_at_most_once_a_gap(self):
        # The demo's scene and score: a walk of 14 px a sample, every pair of samples over 0.55 under the
        # 0.16.1 score, so nothing counts as a pause.
        results = self.walking(policy(calibrate_keyframes=False), speed=14)
        assert [(index * STEP, result.decision) for index, result in enumerate(results) if result.kept] == [
            (6.0, FIRST), (16.0, MOVING)]
        cooldown = [result for result in results if result.decision == COOLDOWN]
        assert cooldown and cooldown[0].wait_s == 3.5 and cooldown[-1].wait_s == 0.5
        assert all(result.since_previous >= result.threshold for result in results[1:])

    @pytest.mark.parametrize("seed", SEEDS)
    def test_a_person_who_keeps_moving_is_kept_after_the_settle_timeout_and_at_most_once_a_gap(self, seed):
        # The calibrated score lines up part of a slower walk (14 px a sample scored 0.51 to 0.53 between
        # some samples), which then counts as a pause; at 35 px every pair of samples is clear motion.
        results = self.walking(policy(), speed=35, seed=seed)
        assert all(result.since_previous >= result.threshold for result in results[1:])
        assert [(index * STEP, result.decision) for index, result in enumerate(results) if result.kept] == [
            (6.0, FIRST), (16.0, MOVING)]
        assert [result.decision for result in results[13:32]] == [SETTLING] * 12 + [COOLDOWN] * 7

    def test_a_shorter_gap_keeps_a_moving_picture_more_often(self):
        results = self.walking(policy(stream_moving_gap_seconds=4.0), speed=35)
        assert [index * STEP for index, result in enumerate(results) if result.kept] == [6.0, 12.5]

    def test_an_unchanged_picture_is_kept_every_heartbeat(self):
        decisions = run(policy(stream_heartbeat_seconds=5.0), held(desk(), 12.0, np.random.default_rng(5)))
        assert [(index * STEP, decisions[index]) for index in kept(decisions)] == [
            (0.5, FIRST), (5.5, HEARTBEAT), (10.5, HEARTBEAT)]

    def test_busy_keeps_nothing_until_there_is_room(self):
        gate = policy()
        frames = held(desk(), 2.0, np.random.default_rng(6))
        decisions = [gate.decide(frame_signature(frame), now=index * STEP, can_keep=index >= 3).decision
                     for index, frame in enumerate(frames)]
        assert decisions == [SETTLING, BUSY, BUSY, FIRST]

    def test_scores_and_threshold_are_reported(self):
        gate = policy()
        frames = finger_frames(np.random.default_rng(7))
        results = [gate.decide(frame_signature(frame), now=index * STEP) for index, frame in enumerate(frames)]
        assert results[0].since_kept is None and results[0].since_previous is None
        assert results[0].threshold == 0.55  # the ceiling until the session has seen a still frame
        assert results[3].threshold == 0.30  # calibrated on a fixed camera
        assert 0.30 <= results[5].since_kept < 0.55  # one more finger


# --- the repeat filter and counts ----------------------------------------------------


class TestStatedCounts:
    def test_counts_are_read_outside_quotes_as_digits(self):
        assert stated_counts("A hand is held up with three fingers raised.") == frozenset({"3"})
        assert stated_counts('Two people sit at a desk with 3 cups. The note says "4 pm".') == frozenset({"2", "3"})
        assert stated_counts("A desk with a laptop and one cup.") == frozenset()  # "one" is often a pronoun
        assert stated_counts("The total is 1,250 items.") == frozenset({"1250"})

    def test_a_changed_count_is_never_a_repeat(self):
        previous = DescriptionKey.of("A hand is held up with two fingers raised.", [1.0, 0.0])
        current = DescriptionKey.of("A hand is held up with three fingers raised.", [1.0, 0.0])
        assert is_repeat(previous, current, threshold=0.0) == (False, None)
        same = DescriptionKey.of("A hand is held up, two fingers raised.", [1.0, 0.0])
        assert is_repeat(previous, same, threshold=0.93) == (True, 1.0)

    def test_prompt_version_3_counts_in_words_and_numerals_compare_as_digits(self):
        # Version 3 (dwamianm/prism#388) states counts; qwen3.5:9b wrote them in words, gemma4:26b in numerals.
        words = "Each hand shows five fingers fully extended, ten fingers total when counting both hands together."
        numerals = "Each hand shows 5 raised fingers, 10 raised fingers in total."
        assert stated_counts(words) == stated_counts(numerals) == frozenset({"5", "10"})
        nine = "The left hand shows 5 raised fingers and the right hand 4, 9 raised fingers in total."
        assert is_repeat(DescriptionKey.of(words, [1.0, 0.0]), DescriptionKey.of(nine, [1.0, 0.0]),
                         threshold=0.0) == (False, None)
        assert is_repeat(DescriptionKey.of(words, [1.0, 0.0]), DescriptionKey.of(numerals, [1.0, 0.0]),
                         threshold=0.93)[0]


# --- through the engine ----------------------------------------------------------------


def fingers_in(image: bytes) -> int:
    """Raised fingers in a desk frame: skin-colored runs along a row through the fingers."""
    picture = Image.open(io.BytesIO(image)).convert("RGB").resize((WIDTH, HEIGHT))
    row = np.asarray(picture, dtype=np.float32)[190:200, 320:440].mean(axis=0)
    skin = (row[:, 0] > 175) & (row[:, 0] - row[:, 2] > 40)
    return int(np.count_nonzero(skin[1:] & ~skin[:-1]) + skin[0])


NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


class CountingVision:
    """Reads the finger count off the frame and states it, unquoted by default as a model often does."""

    model_name = "fake/counting-1"

    def __init__(self, *, quoted: bool = False, numerals: bool = False) -> None:
        self.quoted = quoted
        self.numerals = numerals  # prompt version 3 asks for each count as a numeral
        self.counts: list[int] = []
        self.gate: asyncio.Event | None = None
        self.fail: str | None = None

    async def describe(self, image: bytes, *, prompt: str) -> str:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise VisionError(self.fail)
        count = fingers_in(image)
        self.counts.append(count)
        if self.numerals:
            return (f"This is a photo of a desk with a laptop, a red ball and a white note, and a hand held up in "
                    f"front of it. The hand shows {count} raised fingers.")
        stated = f'"{count} fingers"' if self.quoted else f"{NUMBERS[count]} fingers"
        return (f"This is a photo of a desk with a laptop, a red ball and a white note. A hand is held up in front "
                f"of the desk with {stated} raised.")


class WordEmbedding:
    """A bag of words, so two descriptions that differ in one word score about as a real model does (0.95+)."""

    model_name = "words"
    model_version = "1"
    dimension = 384

    async def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for word in re.findall(r"[a-z0-9]+", text.casefold()):
                vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimension] += 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def counting(monkeypatch) -> CountingVision:
    fake = CountingVision()
    monkeypatch.setattr("prme.vision.provider.create_vision_provider", lambda config: fake)
    return fake


@pytest.fixture
def words(monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: WordEmbedding())


def at(index: int) -> datetime:
    return START + timedelta(seconds=index * STEP)


async def replay(engine: MemoryEngine, frames: list[bytes], *, session: str = "camera:desk", first: int = 0,
                 **options) -> list[FrameResult]:
    return [await engine.observe_frame(frame, user_id=USER, session_id=session, captured_at=at(first + index),
                                       **options)
            for index, frame in enumerate(frames)]


def decisions_of(results: list[FrameResult]) -> list[str]:
    return [result.decision for result in results]


async def test_every_finger_count_becomes_one_observation_with_a_thumbnail(tmp_path, counting, words):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        results = await replay(engine, finger_frames(np.random.default_rng(0)), metadata={"capture": "watch"})
        assert kept(decisions_of(results)) == [1, 5, 9, 13, 17]
        assert counting.counts == [1, 2, 3, 4, 5]
        observations = [result.observation for result in results if result.kept]
        assert [record.status for record in observations] == [ADMITTED] * 5
        # Unquoted counts in otherwise equal descriptions: the count gate keeps each one.
        assert all(record.similarity is None for record in observations)
        assert all(result.observation is None and result.pending == 0 for result in results if not result.kept)
        for result in (results[1], results[9]):
            event = await engine.get_event(result.observation.event_id, user_id=USER)
            assert event.role == "tool" and event.session_id == "camera:desk"
            assert event.event_time == result.captured_at
            meta = event.metadata
            assert (meta["modality"], meta["capture"], meta["frame_index"], meta["kept_reason"]) == (
                "photo", "watch", result.index, result.decision)
            assert meta["thumbnail"] == thumbnail_key(meta["frame_sha256"]) == thumbnail_key(
                result.observation.frame_sha256)
            assert (await engine.get_blob(meta["thumbnail"]))[:3] == b"\xff\xd8\xff"
        assert results[5].to_dict()["observation"]["status"] == ADMITTED
        assert results[5].to_dict()["decision"] == CHANGED and results[5].to_dict()["kept"] is True


async def test_noise_exposure_and_shake_keep_only_the_first_frame(tmp_path, counting):
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        results = await replay(engine, nuisance_frames(np.random.default_rng(1)))
    assert kept(decisions_of(results)) == [1]
    assert counting.counts == [0]


@pytest.mark.parametrize("numerals", [False, True], ids=["words", "numerals"])
async def test_counts_that_differ_only_in_the_number_are_both_kept(tmp_path, counting, words, numerals):
    counting.numerals = numerals
    rng = np.random.default_rng(2)
    two, three = held(desk(fingers=2), 2.0, rng), held(desk(fingers=3), 2.0, rng)
    embedding = WordEmbedding()
    first, second = await embedding.embed([await counting.describe(two[0], prompt=""),
                                           await counting.describe(three[0], prompt="")])
    counting.counts.clear()
    similarity = float(np.dot(first, second) / (np.linalg.norm(first) * np.linalg.norm(second)))
    assert similarity >= VisionConfig().repeat_similarity  # without the count gate, a repeat
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        results = await replay(engine, two + three)
    assert [(result.decision, result.observation.status) for result in results if result.kept] == [
        (FIRST, ADMITTED), (CHANGED, ADMITTED)]
    assert counting.counts == [2, 3]


async def test_a_repeated_description_is_dropped_across_frames(tmp_path, counting):
    # A still hand kept by the heartbeat: the same description as the last kept frame is a repeat.
    config = directory_config(tmp_path / "pack", stream_heartbeat_seconds=2.0)
    async with MemoryEngine.open(config) as engine:
        results = await replay(engine, held(desk(fingers=2), 6.0, np.random.default_rng(3)))
        observed = [(result.decision, result.observation.status) for result in results if result.kept]
        assert observed == [(FIRST, ADMITTED), (HEARTBEAT, REPEAT), (HEARTBEAT, REPEAT)]
        assert results[5].observation.similarity == 1.0
        events = await engine.get_events(USER, limit=10)
    assert len(events) == 1


async def test_without_wait_frames_are_decided_while_earlier_ones_are_described(tmp_path, counting, words):
    counting.gate = asyncio.Event()
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        results = await replay(engine, finger_frames(np.random.default_rng(4), counts=range(1, 4)), wait=False)
        assert kept(decisions_of(results)) == [1, 5, 9]
        assert [results[index].pending for index in (1, 5, 9)] == [1, 2, 3]
        assert all(results[index].observation.status == PENDING for index in (1, 5, 9))
        assert counting.counts == []
        counting.gate.set()
        records = await engine.end_observe_session("camera:desk", user_id=USER)
        assert [(record.index, record.status) for record in records] == [(1, ADMITTED), (5, ADMITTED),
                                                                         (9, ADMITTED)]
        assert counting.counts == [1, 2, 3]  # described in the order they were kept
        assert await results[5].observed() is results[5].observation
        assert await engine.end_observe_session("camera:desk", user_id=USER) == []


async def test_a_session_with_too_many_frames_waiting_is_busy_until_there_is_room(tmp_path, counting):
    counting.gate = asyncio.Event()
    config = directory_config(tmp_path / "pack", stream_max_pending=1)
    async with MemoryEngine.open(config) as engine:
        rng = np.random.default_rng(5)
        results = await replay(engine, finger_frames(rng, counts=range(1, 3)), wait=False)
        assert decisions_of(results) == [SETTLING, FIRST, UNCHANGED, UNCHANGED, SETTLING, BUSY, BUSY, BUSY]
        counting.gate.set()
        await results[1].observed()
        later = await replay(engine, held(desk(fingers=2), 1.0, rng), first=8)
        assert decisions_of(later) == [CHANGED, UNCHANGED]
        assert later[0].observation.status == ADMITTED and counting.counts == [1, 2]


async def test_sessions_are_separate_and_safe_to_drive_concurrently(tmp_path, counting, words):
    frames = {name: finger_frames(np.random.default_rng(seed), counts=range(1, 4))
              for seed, name in enumerate(("a", "b", "c"))}
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        left, right = await asyncio.gather(replay(engine, frames["a"], session="camera:a"),
                                           replay(engine, frames["b"], session="camera:b"))
        assert kept(decisions_of(left)) == kept(decisions_of(right)) == [1, 5, 9]
        # One session's frames sent all at once are decided in the order they were sent.
        burst = await asyncio.gather(*(
            engine.observe_frame(frame, user_id=USER, session_id="camera:c", captured_at=at(index), wait=False)
            for index, frame in enumerate(frames["c"])))
        assert [result.index for result in burst] == list(range(len(frames["c"])))
        assert kept(decisions_of(burst)) == [1, 5, 9]
        await engine.end_observe_session("camera:c", user_id=USER)
        # The same session id for another owner is another session.
        other = await engine.observe_frame(frames["a"][0], user_id="bob", session_id="camera:a",
                                           captured_at=at(0))
        assert (other.index, other.decision) == (0, SETTLING)
    assert counting.counts.count(2) == 3


async def test_a_frame_already_observed_is_not_described_again(tmp_path, counting):
    frames = finger_frames(np.random.default_rng(14), counts=range(1, 3))
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        await replay(engine, frames, session="first")
        again = await replay(engine, frames, session="again")
        assert [result.observation.status for result in again if result.kept] == [ALREADY_OBSERVED] * 2
        assert len(await engine.get_events(USER, limit=10)) == 2
    assert counting.counts == [1, 2]


async def test_the_session_cap_forgets_the_least_recently_used_idle_session(tmp_path, counting):
    still = held(desk(), 0.5, np.random.default_rng(7))[0]
    async with MemoryEngine.open(directory_config(tmp_path / "pack", stream_max_sessions=2)) as engine:
        for name in ("a", "b", "c"):
            await engine.observe_frame(still, user_id=USER, session_id=name, captured_at=at(0))
        assert len(engine._observe_sessions) == 2
        again = await engine.observe_frame(still, user_id=USER, session_id="a", captured_at=at(1))
        assert again.index == 0  # forgotten, so it starts again
        assert (await engine.observe_frame(still, user_id=USER, session_id="c", captured_at=at(1))).index == 1


async def test_a_new_session_is_busy_when_every_live_session_has_frames_waiting(tmp_path, counting):
    counting.gate = asyncio.Event()
    frames = held(desk(fingers=1), 1.0, np.random.default_rng(8))
    async with MemoryEngine.open(directory_config(tmp_path / "pack", stream_max_sessions=1)) as engine:
        assert decisions_of(await replay(engine, frames, session="a", wait=False)) == [SETTLING, FIRST]
        refused = await engine.observe_frame(frames[0], user_id=USER, session_id="b", captured_at=at(0))
        assert (refused.decision, refused.index, refused.kept) == (BUSY, None, False)
        counting.gate.set()
        await engine.end_observe_session("a", user_id=USER)
        accepted = await engine.observe_frame(frames[0], user_id=USER, session_id="b", captured_at=at(0))
        assert (accepted.decision, accepted.index) == (SETTLING, 0)


async def test_an_idle_session_is_forgotten(tmp_path, counting):
    frames = held(desk(), 1.5, np.random.default_rng(9))
    async with MemoryEngine.open(directory_config(tmp_path / "pack", stream_idle_seconds=10.0)) as engine:
        clock = [0.0]
        assert decisions_of(await replay(engine, frames[:2])) == [SETTLING, FIRST]
        engine._observe_sessions.clock = lambda: clock[0]
        assert (await engine.observe_frame(frames[2], user_id=USER, session_id="camera:desk",
                                           captured_at=at(2))).decision == UNCHANGED
        clock[0] = 9.0
        assert (await engine.observe_frame(frames[0], user_id=USER, session_id="camera:desk",
                                           captured_at=at(3))).index == 3
        clock[0] = 19.5
        restarted = await engine.observe_frame(frames[1], user_id=USER, session_id="camera:desk", captured_at=at(4))
        assert (restarted.index, restarted.decision) == (0, SETTLING)


async def test_a_frame_sent_out_of_order_counts_as_simultaneous(tmp_path, counting):
    frames = held(desk(), 1.5, np.random.default_rng(10))
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        first = await engine.observe_frame(frames[0], user_id=USER, session_id="s", captured_at=at(4))
        late = await engine.observe_frame(frames[1], user_id=USER, session_id="s", captured_at=at(2))
        assert (first.decision, late.decision) == (SETTLING, SETTLING)  # no time passed on the session clock
        assert (await engine.observe_frame(frames[2], user_id=USER, session_id="s",
                                           captured_at=at(5))).decision == FIRST


async def test_a_failed_description_is_recorded_and_the_session_goes_on(tmp_path, counting):
    counting.fail = "model not reachable"
    rng = np.random.default_rng(11)
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        results = await replay(engine, held(desk(fingers=1), 2.0, rng))
        record = results[1].observation
        assert record.status == FAILED and "model not reachable" in record.error
        counting.fail = None
        later = await replay(engine, held(desk(fingers=2), 1.0, rng), first=4)
        assert [(result.decision, result.observation.status) for result in later if result.kept] == [
            (CHANGED, ADMITTED)]


async def test_close_cancels_frames_not_yet_described(tmp_path, counting):
    counting.gate = asyncio.Event()
    async with MemoryEngine.open(directory_config(tmp_path / "pack")) as engine:
        results = await replay(engine, held(desk(), 1.0, np.random.default_rng(12)), wait=False)
    record = await results[1].observed()
    assert record.status == FAILED and "cancelled" in record.error
    with pytest.raises(RuntimeError):
        await engine.observe_frame(b"", user_id=USER, session_id="s")


async def test_frames_are_checked_before_a_session_sees_them(tmp_path, counting):
    frame = held(desk(), 0.5, np.random.default_rng(13))[0]
    config = directory_config(tmp_path / "pack")
    async with MemoryEngine.open(config.model_copy(update={"vision": config.vision.model_copy(
            update={"enabled": False})})) as engine:
        with pytest.raises(VisionDisabledError):
            await engine.observe_frame(frame, user_id=USER, session_id="s")
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(UnsupportedSourceError):
            await engine.observe_frame(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64, user_id=USER, session_id="s")
        with pytest.raises(UnsupportedSourceError):
            await engine.observe_frame(b"\xff\xd8\xff" + b"\x00" * 64, user_id=USER, session_id="s")
        with pytest.raises(ValueError, match="session_id"):
            await engine.observe_frame(frame, user_id=USER, session_id=" ")
        with pytest.raises(ValueError, match="timezone"):
            await engine.observe_frame(frame, user_id=USER, session_id="s", captured_at=datetime(2026, 11, 2))
        path = tmp_path / "frame.jpg"
        path.write_bytes(frame)
        result = await engine.observe_frame(path, user_id=USER, session_id="s")
        assert (result.decision, result.index) == (SETTLING, 0)  # the unreadable frame changed nothing
        assert result.captured_at.tzinfo is not None
        assert await engine.end_observe_session("unknown", user_id=USER) == []


def test_stream_settings_load_from_the_environment(monkeypatch):
    monkeypatch.setenv("PRME_VISION__STREAM_SETTLE_SECONDS", "1.0")
    monkeypatch.setenv("PRME_VISION__STREAM_HEARTBEAT_SECONDS", "120")
    monkeypatch.setenv("PRME_VISION__STREAM_MAX_SESSIONS", "4")
    vision = PRMEConfig().vision
    assert (vision.stream_settle_seconds, vision.stream_heartbeat_seconds, vision.stream_max_sessions) == (
        1.0, 120.0, 4)
    with pytest.raises(ValueError):
        VisionConfig(stream_max_sessions=0)
    with pytest.raises(ValueError):
        VisionConfig(stream_heartbeat_seconds=0)
