"""The extractor reads the turns before the new one in its session (#91)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from prme import MemoryEngine
from prme.ingestion.extraction import _extraction_prompt_for_role, _window_message
from prme.ingestion.schema import ExtractionResult
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 3, 2, 9, tzinfo=timezone.utc)


def recorder(calls):
    """An extractor that records what it was shown and finds nothing."""

    async def extract(content, *, role=None, context=()):
        calls.append({"content": content, "context": list(context)})
        return ExtractionResult.model_validate({"entities": [], "facts": []})

    return AsyncMock(side_effect=extract)


async def say(engine, user, text, *, session="s1", minutes=0):
    return await engine.ingest(text, user_id=user, session_id=session, role="user",
                               event_time=T0 + timedelta(minutes=minutes), wait_for_extraction=True)


def windowed(base, turns=4):
    return base.model_copy(update={"enable_windowed_extraction": True,
                                   "extraction_window_turns": turns})


class TestWindowContents:
    async def test_earlier_turns_are_shown_oldest_first(self, config, user):
        calls = []
        async with MemoryEngine.open(windowed(config)) as engine:
            engine._pipeline._extraction_provider.extract = recorder(calls)
            for n, text in enumerate(["First.", "Second.", "Third."]):
                await say(engine, user, text, minutes=n)
        assert [call["context"] for call in calls] == [[], ["First."], ["First.", "Second."]]

    async def test_a_turn_is_not_in_its_own_window(self, config, user):
        calls = []
        async with MemoryEngine.open(windowed(config)) as engine:
            engine._pipeline._extraction_provider.extract = recorder(calls)
            await say(engine, user, "Only turn.")
        assert calls[-1]["content"] == "Only turn." and calls[-1]["context"] == []

    async def test_the_window_is_bounded(self, config, user):
        calls = []
        async with MemoryEngine.open(windowed(config, turns=2)) as engine:
            engine._pipeline._extraction_provider.extract = recorder(calls)
            for n in range(5):
                await say(engine, user, f"Turn {n}.", minutes=n)
        assert calls[-1]["context"] == ["Turn 2.", "Turn 3."]

    async def test_another_session_is_not_in_the_window(self, config, user):
        calls = []
        async with MemoryEngine.open(windowed(config)) as engine:
            engine._pipeline._extraction_provider.extract = recorder(calls)
            await say(engine, user, "In one session.", session="a", minutes=0)
            await say(engine, user, "In another.", session="b", minutes=1)
        assert calls[-1]["context"] == []


class TestWhenThereIsNoWindow:
    async def test_the_option_is_off_by_default(self, config, user):
        calls = []
        async with MemoryEngine.open(config) as engine:
            engine._pipeline._extraction_provider.extract = recorder(calls)
            await say(engine, user, "First.", minutes=0)
            await say(engine, user, "Second.", minutes=1)
        assert [call["context"] for call in calls] == [[], []]

    async def test_zero_turns_disables_it_without_changing_the_flag(self, config, user):
        calls = []
        async with MemoryEngine.open(windowed(config, turns=0)) as engine:
            engine._pipeline._extraction_provider.extract = recorder(calls)
            await say(engine, user, "First.", minutes=0)
            await say(engine, user, "Second.", minutes=1)
        assert [call["context"] for call in calls] == [[], []]

    async def test_a_turn_without_a_session_has_no_window(self, config, user):
        calls = []
        async with MemoryEngine.open(windowed(config)) as engine:
            engine._pipeline._extraction_provider.extract = recorder(calls)
            await engine.ingest("No session.", user_id=user, role="user", wait_for_extraction=True)
        assert calls[-1]["context"] == []


class TestWhatTheModelIsTold:
    def test_the_window_is_labeled_as_reference_only(self):
        message = _window_message(["Ann: hello", "Bob: hi"])
        assert "reference only" in message
        assert "[earlier turn 1] Ann: hello" in message
        assert "[earlier turn 2] Bob: hi" in message

    def test_the_prompt_says_not_to_extract_from_the_window(self):
        prompt = _extraction_prompt_for_role("user", windowed=True)
        assert "Do not extract a claim that only an earlier turn supports." in prompt

    def test_a_prompt_without_a_window_is_unchanged(self):
        assert _extraction_prompt_for_role("user") == _extraction_prompt_for_role("user", windowed=False)
        assert "EARLIER TURNS" not in _extraction_prompt_for_role("user")

    def test_the_assistant_policy_still_applies_with_a_window(self):
        prompt = _extraction_prompt_for_role("assistant", windowed=True)
        assert "EARLIER TURNS" in prompt
        assert prompt.startswith(_extraction_prompt_for_role("assistant"))
