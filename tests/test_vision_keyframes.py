"""Keyframe policy, thumbnails, quoted text and the repeat filter (RFC-0018, phase 1).

These tests run the pure parts of ``prme.vision`` over synthetic slides drawn
with Pillow's bundled font, so they need no model, no ffmpeg and no corpus.
The scores asserted here were measured on these slides: camera shake and
exposure drift stay well under the 0.55 threshold, a different slide on the
same layout scores above it, and a plain scene change scores above 2.3. A
same-layout change of a few characters scores only about 0.2 with this thin
font (the spike's whiteboard change, in a heavier marker font with a
strikethrough, scored about 0.7), which is the weak point RFC-0018 names:
the heartbeat catches it within one gap and the repeat filter drops the
duplicates.
"""

from __future__ import annotations

import io
import random

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from prme.vision.describe import DESCRIBE_PROMPT_VERSION, describe_prompt, normalize_quote, quoted_text
from prme.vision.keyframes import (
    KeyframeDecision,
    frame_change,
    frame_signature,
    load_image,
    select_keyframes,
    thumbnail_bytes,
)
from prme.vision.repeat import DescriptionKey, is_repeat, repeat_similarity

SIZE = (1280, 720)
WHITE = (255, 255, 255)


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def slide(title: str, bullets: list[str], background=WHITE) -> Image.Image:
    image = Image.new("RGB", SIZE, background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, SIZE[0], 110), fill=(24, 48, 96))
    draw.text((60, 28), title, fill=WHITE, font=font(52))
    y = 170
    for bullet in bullets:
        draw.ellipse((70, y + 16, 86, y + 32), fill=(30, 60, 160))
        draw.text((110, y), bullet, fill=(20, 20, 20), font=font(40))
        y += 72
    return image


def jitter(image: Image.Image, rng: random.Random) -> Image.Image:
    """A few pixels of camera shake and a little exposure drift, as the spike corpus has."""
    dx, dy = rng.randint(-4, 4), rng.randint(-4, 4)
    canvas = Image.new("RGB", image.size, (0, 0, 0))
    canvas.paste(image, (dx, dy))
    return ImageEnhance.Brightness(canvas).enhance(rng.uniform(0.96, 1.04))


LAUNCH = slide("Falcon launch", ["Launch date: 3 Nov", "Budget: $12,500", "Vendor: Northwind"])
LAUNCH_MOVED = slide("Falcon launch", ["Launch date: 10 Nov", "Budget: $14,000", "Vendor: Northwind"])
ACTIONS = slide("Action items", ["Dmitri orders 40 sensors", "Mei books the venue", "Sam writes the plan"])
FIELD = Image.new("RGB", SIZE, (30, 120, 60))


class TestFrameChange:
    def test_shake_and_exposure_drift_stay_under_threshold(self):
        rng = random.Random(7)
        reference = frame_signature(LAUNCH)
        scores = [frame_change(reference, frame_signature(jitter(LAUNCH, rng))) for _ in range(6)]
        assert max(scores) < 0.4

    def test_other_slide_on_same_layout_reaches_threshold(self):
        assert frame_change(frame_signature(LAUNCH), frame_signature(ACTIONS)) >= 0.55

    def test_scene_change_scores_high(self):
        assert frame_change(frame_signature(LAUNCH), frame_signature(FIELD)) > 2.3

    def test_small_text_change_on_same_layout_is_below_threshold(self):
        # The weak point RFC-0018 names: the heartbeat catches it instead.
        score = frame_change(frame_signature(LAUNCH), frame_signature(LAUNCH_MOVED))
        assert 0.1 < score < 0.55

    def test_signature_shape_and_dtype(self):
        signature = frame_signature(LAUNCH)
        assert signature.shape == (180, 320)
        assert signature.dtype == np.float32

    def test_signature_accepts_bytes_and_applies_orientation(self):
        buffer = io.BytesIO()
        LAUNCH.save(buffer, format="JPEG")
        assert frame_signature(buffer.getvalue()).shape == (180, 320)
        assert load_image(buffer.getvalue()).size == SIZE


class TestSelectKeyframes:
    def test_first_change_and_heartbeat_reasons(self):
        rng = random.Random(3)
        frames = [jitter(LAUNCH, rng) for _ in range(5)] + [jitter(ACTIONS, rng) for _ in range(40)]
        decisions = select_keyframes(frames, fps=1.0, threshold=0.55, max_gap=30.0)
        assert [d.index for d in decisions] == list(range(45))
        kept = [d for d in decisions if d.kept]
        assert kept[0].reason == "first" and kept[0].change is None
        assert kept[1] == KeyframeDecision(index=5, time_s=5.0, change=kept[1].change, kept=True, reason="change")
        assert kept[1].change >= 0.55
        assert [d.reason for d in kept[2:]] == ["heartbeat"]
        assert kept[2].index == 35  # 30 seconds after the last kept frame
        assert all(not d.kept for d in decisions if d.index not in (0, 5, 35))

    def test_small_text_change_is_caught_by_the_heartbeat(self):
        frames = [LAUNCH] * 4 + [LAUNCH_MOVED] * 4
        decisions = select_keyframes(frames, fps=1.0, threshold=0.55, max_gap=4.0)
        assert [(d.index, d.reason) for d in decisions if d.kept] == [(0, "first"), (4, "heartbeat")]

    def test_fps_scales_time(self):
        decisions = select_keyframes([LAUNCH, LAUNCH, LAUNCH], fps=2.0, threshold=0.55, max_gap=1.0)
        assert [d.time_s for d in decisions] == [0.0, 0.5, 1.0]
        assert [d.kept for d in decisions] == [True, False, True]

    def test_rejects_non_positive_fps(self):
        with pytest.raises(ValueError):
            select_keyframes([LAUNCH], fps=0, threshold=0.55, max_gap=30.0)

    def test_empty_input(self):
        assert select_keyframes([], fps=1.0, threshold=0.55, max_gap=30.0) == []

    def test_decision_is_json_friendly(self):
        decision = select_keyframes([LAUNCH], fps=1.0, threshold=0.55, max_gap=30.0)[0]
        assert decision.to_dict() == {"index": 0, "time_s": 0.0, "change": None, "kept": True, "reason": "first"}


class TestThumbnail:
    def test_thumbnail_is_a_small_jpeg(self):
        data = thumbnail_bytes(LAUNCH, width=320)
        image = Image.open(io.BytesIO(data))
        assert image.format == "JPEG"
        assert image.size == (320, 180)
        assert len(data) < 40_000

    def test_portrait_thumbnail_is_bounded_by_width_on_both_sides(self):
        portrait = Image.new("RGB", (600, 900), (200, 200, 200))
        image = Image.open(io.BytesIO(thumbnail_bytes(portrait, width=320)))
        assert image.size == (213, 320)


class TestDescriptionPrompt:
    def test_prompt_names_the_source_kind(self):
        assert "video frame" in describe_prompt("video_frame")
        assert "photo" in describe_prompt("photo")
        assert "Quote visible text exactly" in describe_prompt("photo")

    def test_only_version_two_exists(self):
        assert DESCRIBE_PROMPT_VERSION == 2
        with pytest.raises(ValueError):
            describe_prompt("photo", version=1)
        with pytest.raises(ValueError):
            describe_prompt("screen_frame")


class TestQuotedText:
    def test_quotes_are_read_and_normalized(self):
        description = ('The whiteboard says "Launch date: 3-Nov" and "Budget: $12,500". '
                       'The owner is "Priya".')
        assert quoted_text(description) == frozenset({"launch date: 3 nov", "budget: $12,500", "priya"})

    def test_normalization_ignores_case_dashes_and_edge_punctuation(self):
        assert normalize_quote("3-Nov") == normalize_quote("3 Nov") == normalize_quote("3  nov.")
        assert normalize_quote("12,500") != normalize_quote("14,000")

    def test_no_quotes_is_an_empty_set(self):
        assert quoted_text("A lake seen from the air.") == frozenset()

    def test_curly_quotes_count_as_quotes(self):
        assert quoted_text("The note says \u201cDentist 3pm\u201d and \"Dr. Okafor\".") == frozenset(
            {"dentist 3pm", "dr. okafor"})


class TestRepeatFilter:
    def key(self, text: str, vector) -> DescriptionKey:
        return DescriptionKey.build(quoted_text(text), vector)

    def test_same_quotes_and_similar_vector_is_a_repeat(self):
        previous = self.key('The budget is "$12,500".', [1.0, 0.0, 0.0])
        current = self.key('The slide states the budget as "$12,500".', [0.98, 0.1, 0.0])
        repeat, similarity = is_repeat(previous, current, threshold=0.93)
        assert repeat and similarity == pytest.approx(0.9948, abs=1e-3)

    def test_changed_quote_is_never_a_repeat(self):
        previous = self.key('The budget is "$12,500".', [1.0, 0.0, 0.0])
        current = self.key('The budget is "$14,000".', [1.0, 0.0, 0.0])
        assert repeat_similarity(previous, current) is None
        assert is_repeat(previous, current, threshold=0.0) == (False, None)

    def test_same_quotes_but_new_words_below_threshold_is_kept(self):
        previous = self.key('A lake "Tahoe" from the air.', [1.0, 0.0, 0.0])
        current = self.key('A forested shore at "Tahoe".', [0.6, 0.8, 0.0])
        repeat, similarity = is_repeat(previous, current, threshold=0.93)
        assert not repeat and similarity == pytest.approx(0.6, abs=1e-3)

    def test_first_frame_has_nothing_to_repeat(self):
        assert is_repeat(None, self.key("x", [1.0, 0.0]), threshold=0.93) == (False, None)

    def test_vectors_are_unit_length(self):
        key = self.key("x", [3.0, 4.0])
        assert np.allclose(key.vector, [0.6, 0.8])
