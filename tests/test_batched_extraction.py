"""Batched extraction: several turns of a session per model call (#254)."""

import asyncio
import json
import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.errors import ExtractionError
from prme.ingestion.extraction import (
    BATCH_EXTRACTION_SYSTEM_PROMPT,
    BatchTurn,
    InstructorExtractionProvider,
    batch_request_messages,
    batch_request_sha256,
    batch_response_fields,
    validate_batch_output,
)
from prme.ingestion.pipeline import IngestionPipeline
from prme.ingestion.schema import ExtractionResult
from prme.types import LifecycleState, NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2023, 5, 8, 13, 56, tzinfo=timezone.utc)
# The claim the scripted model finds in each known sentence.
CLAIMS = {
    "Caroline paints sunsets over the lake.": ("Caroline", "paints", "sunsets"),
    "Melanie adopted a puppy named Oscar.": ("Melanie", "adopted", "a puppy named Oscar"),
    "Jordan moved to Denver last spring.": ("Jordan", "moved_to", "Denver"),
}
# The record a turn extracted on its own has always had; batching adds nothing to it.
RECORD_KEYS = {"schema_version", "grounding_policy", "event_id", "user_id", "scope", "content_hash",
               "provider", "model", "result", "created_at"}


def claim_entry(index, text):
    subject, predicate, obj = CLAIMS[text]
    return {"entities": [{"name": subject, "entity_type": "person"}],
            "facts": [{"subject": subject, "subject_entity_type": "person", "predicate": predicate,
                       "object": obj, "polarity": "positive", "epistemic_type": "observed",
                       "evidence_quote": text}]}


class ScriptedBatches:
    """A batched model that cites each known sentence's claim from the turn that holds it."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def extract_batch(self, turns):
        self.calls.append([(turn.index, turn.role, turn.speaker, turn.content) for turn in turns])
        reason = self.fail(turns) if self.fail else None
        if reason:
            raise ExtractionError("scripted failure", reason_code=reason)
        entries = []
        for turn in turns:
            entry = {"turn": turn.index, "entities": [], "facts": [], "relationships": []}
            for text in CLAIMS:
                if text in turn.content:
                    entry.update(claim_entry(turn.index, text))
            entries.append(entry)
        return {"turns": entries}

    def contents(self):
        return [[content for *_, content in call] for call in self.calls]


class SlowBatches(ScriptedBatches):
    """A batched model that takes ``delay`` seconds to answer each request."""

    def __init__(self, delay, **kwargs):
        super().__init__(**kwargs)
        self.delay = delay
        self.started = []

    async def extract_batch(self, turns):
        self.started.append([turn.content for turn in turns])
        await asyncio.sleep(self.delay)
        return await super().extract_batch(turns)


def batched(base, tokens=512):
    return base.model_copy(update={"extraction_batch_target_tokens": tokens})


def install(engine, model, *, flush_seconds=3600.0):
    pipeline = engine._pipeline
    pipeline._extraction_provider.extract_batch = model.extract_batch
    pipeline._extraction_provider.extract = AsyncMock(side_effect=AssertionError("one call per turn"))
    pipeline._batch_flush_seconds = flush_seconds
    return pipeline


def message(text, *, role="user", speaker=None, minutes=0):
    return {"content": text, "role": role, "speaker": speaker, "event_time": T0 + timedelta(minutes=minutes)}


async def claims_by_event(engine, user):
    facts = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
    return {str(ref): fact.content for fact in facts for ref in fact.evidence_refs}


async def statuses(engine, user, event_ids):
    return [(await engine.extraction_status(event_id, user_id=user)).status for event_id in event_ids]


async def batch_operations(engine, user):
    sql = ("SELECT count(*) FROM operations o JOIN events e ON o.target_id = e.id::{} "
           "WHERE o.op_type = 'EXTRACTION_BATCH_RECORDED' AND e.user_id = {}")
    if engine._conn is not None:
        return engine._conn.execute(sql.format("VARCHAR", "?"), [user]).fetchone()[0]
    async with engine._pool.acquire() as conn:
        return await conn.fetchval(sql.format("text", "$1"), user)


async def drain(pipeline):
    while pipeline._background_tasks or any(not task.done() for task in pipeline._flush_timers.values()):
        await asyncio.gather(*pipeline._background_tasks, *pipeline._flush_timers.values(),
                             return_exceptions=True)
        await asyncio.sleep(0)


class TestTheDefault:
    async def test_each_turn_is_extracted_alone_and_its_record_is_unchanged(self, config, user):
        calls = []

        async def extract(content, *, role="user"):
            calls.append(content)
            return ExtractionResult()

        async with MemoryEngine.open(config) as engine:
            engine._pipeline._extraction_provider.extract = extract
            engine._pipeline._extraction_provider.extract_batch = AsyncMock(side_effect=AssertionError("batched"))
            event_ids = await engine.ingest_batch([message("First."), message("Second.", minutes=1)],
                                                  user_id=user, session_id="s1", wait_for_extraction=True)
            assert calls == ["First.", "Second."]
            for event_id in event_ids:
                record = await engine.get_extraction(event_id, user_id=user)
                assert record.batch_id is None and record.grounding_policy == "speech_act_v14"
                assert set(json.loads(record.model_dump_json())) == RECORD_KEYS
            assert await batch_operations(engine, user) == 0

    def test_the_default_configuration_keeps_its_identity(self):
        from benchmarks.diagnostics.product_packing import gate_config
        from benchmarks.integrations.gpt54_baselines import _check_defaults_build, variant_settings
        from benchmarks.retrieval_eval import provenance

        assert "extraction_batch_target_tokens" not in PRMEConfig(_env_file=None).model_dump(mode="json")
        # What an ingest() build records as its engine configuration.
        defaults = provenance(gate_config(Path("{pack}")))["engine_config"]
        assert "extraction_batch_target_tokens" not in defaults
        assert PRMEConfig(_env_file=None, extraction_batch_target_tokens=0).model_dump() == \
            PRMEConfig(_env_file=None).model_dump()
        assert gate_config(Path("{pack}"), {"extraction_batch_target_tokens": 512}).model_dump(
            mode="json")["extraction_batch_target_tokens"] == 512
        # The answer track sees no new setting in the defaults, and the one a batched build sets.
        assert variant_settings({}) == {}
        assert variant_settings({"extraction_batch_target_tokens": 512}) == {"extraction_batch_target_tokens": 512}
        # An ingest() baseline built before the option existed still holds this commit's defaults.
        _check_defaults_build(Path("built-before"), {"provenance": {"engine_config": defaults}})
        with pytest.raises(ValueError, match="extraction_batch_target_tokens"):
            _check_defaults_build(Path("batched"), {"provenance": {"engine_config": {
                **defaults, "extraction_batch_target_tokens": 512}}})

    def test_the_single_turn_request_and_record_keep_their_bytes(self):
        import hashlib
        from uuid import UUID

        from prme.ingestion.extraction import _CitedExtractionResult, _extraction_prompt_for_role
        from prme.models.extraction import ExtractionRecord

        # Digests of the request a turn has always been extracted with, taken before #254.
        prompts = {role: hashlib.sha256(_extraction_prompt_for_role(role).encode()).hexdigest()
                   for role in ("user", "participant", "assistant")}
        assert prompts == {
            "user": "8aa7deee60dd41e4e44e6397e0dc54836199fb2d43a1b23d5139a3a70bf7e27d",
            "participant": "3ff6eab556b708f3eb8a45c2ed4d36df26ab86e8923bc6ea1ff3a45f9fb5aab8",
            "assistant": "a417c9c095da96bbc5cdd786d095fcb29298ef480971bcf5688c78e76a89a0ca",
        }
        schema = json.dumps(_CitedExtractionResult.model_json_schema(), sort_keys=True)
        assert hashlib.sha256(schema.encode()).hexdigest() == \
            "97a82e96af1fea0a61bc366b9defe781ca5f9f456f4d593a0fef05ff882b2f4f"
        record = ExtractionRecord(
            event_id=UUID("00000000-0000-4000-8000-000000000254"), user_id="u", scope="personal",
            content_hash="a" * 64, provider="ollama", model="ollama/m", grounding_policy="speech_act_v14",
            result={"entities": [], "facts": [], "relationships": [], "summary": None},
            created_at=datetime(2026, 10, 15, tzinfo=timezone.utc))
        assert record.model_dump_json() == (
            '{"schema_version":1,"grounding_policy":"speech_act_v14",'
            '"event_id":"00000000-0000-4000-8000-000000000254","user_id":"u","scope":"personal",'
            '"content_hash":"' + "a" * 64 + '","provider":"ollama","model":"ollama/m",'
            '"result":{"entities":[],"facts":[],"relationships":[],"summary":null},'
            '"created_at":"2026-10-15T00:00:00Z"}')
        assert ExtractionRecord.model_validate_json(record.model_dump_json()) == record

    def test_batching_takes_a_nonnegative_target_and_neither_the_window_nor_resolution(self):
        with pytest.raises(ValidationError):
            PRMEConfig(_env_file=None, extraction_batch_target_tokens=-1)
        for other in ({"enable_windowed_extraction": True}, {"enable_fact_text_resolution": True}):
            with pytest.raises(ValidationError, match="cannot be combined"):
                PRMEConfig(_env_file=None, extraction_batch_target_tokens=512, **other)
        # A window of no turns is no window.
        PRMEConfig(_env_file=None, extraction_batch_target_tokens=512, enable_windowed_extraction=True,
                   extraction_window_turns=0)
        with pytest.raises(ValueError, match="cannot be combined"):
            IngestionPipeline(None, None, None, None, ScriptedBatches(), None, extraction_window_turns=4,
                              extraction_batch_target_tokens=512)
        with pytest.raises(ValueError, match="extract_batch"):
            IngestionPipeline(None, None, None, None, object(), None, extraction_batch_target_tokens=512)


class TestOneCallPerBatch:
    async def test_a_session_is_extracted_in_one_call_and_each_turn_keeps_its_record(self, config, user):
        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            event_ids = await engine.ingest_batch([
                message("Caroline paints sunsets over the lake.", speaker="Caroline"),
                message("Melanie adopted a puppy named Oscar.", role="participant", speaker="Melanie", minutes=1),
                message("Thanks, that sounds fun.", speaker="Caroline", minutes=2),
            ], user_id=user, session_id="s1", wait_for_extraction=True)
            assert model.calls == [[
                (1, "user", "Caroline", "Caroline paints sunsets over the lake."),
                (2, "participant", "Melanie", "Melanie adopted a puppy named Oscar."),
                (3, "user", "Caroline", "Thanks, that sounds fun."),
            ]]
            assert await statuses(engine, user, event_ids) == ["complete"] * 3
            records = [await engine.get_extraction(event_id, user_id=user) for event_id in event_ids]
            assert len({record.batch_id for record in records}) == 1 and records[0].batch_id is not None
            assert {record.grounding_policy for record in records} == {"speech_act_v14"}
            assert [len(record.result["facts"]) for record in records] == [1, 1, 0]
            # Each claim belongs to the turn that states it, as if each were extracted alone.
            assert await claims_by_event(engine, user) == {
                event_ids[0]: "Caroline paints sunsets over the lake.",
                event_ids[1]: "Melanie adopted a puppy named Oscar.",
            }
            batch = await engine._event_store.get_extraction_batch(str(records[0].batch_id), user_id=user)
            assert [(turn.index, str(turn.event_id), turn.speaker) for turn in batch.turns] == [
                (1, event_ids[0], "Caroline"), (2, event_ids[1], "Melanie"), (3, event_ids[2], "Caroline")]
            assert batch.head_event_id == records[0].event_id and batch.session_id == "s1"
            assert [entry["turn"] for entry in batch.response["turns"]] == [1, 2, 3]
            assert batch.request_sha256 == batch_request_sha256([BatchTurn(
                index=turn.index, role=turn.role, content=content, speaker=turn.speaker, time=turn.event_time,
            ) for turn, (*_, content) in zip(batch.turns, model.calls[0])])
            assert await engine._event_store.get_extraction_batch(str(records[0].batch_id), user_id="other") is None

    async def test_every_claim_cites_a_turn_of_its_batch_and_quotes_that_turn(self, config, user):
        first, second = "Caroline paints sunsets over the lake.", "Melanie adopted a puppy named Oscar."

        async def extract_batch(turns):
            return {"turns": [
                # Turn 1's entry quotes turn 2: grounding rejects it, as for a single turn.
                {"turn": 1, **claim_entry(1, second)},
                {"turn": 2, **claim_entry(2, second)},
                # No turn 9 in this batch.
                {"turn": 9, **claim_entry(9, first)},
            ]}

        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, ScriptedBatches())
            engine._pipeline._extraction_provider.extract_batch = extract_batch
            event_ids = await engine.ingest_batch([message(first), message(second, minutes=1)], user_id=user,
                                                  session_id="s1", wait_for_extraction=True)
            assert await claims_by_event(engine, user) == {event_ids[1]: second}
            assert [(await engine.get_extraction(event_id, user_id=user)).result["facts"] != []
                    for event_id in event_ids] == [False, True]

    async def test_mixed_roles_follow_each_turns_own_policy(self, config, user):
        async def extract_batch(turns):
            return {"turns": [{"turn": turn.index, "entities": [], "facts": []} for turn in turns]}

        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, ScriptedBatches())
            engine._pipeline._extraction_provider.extract_batch = extract_batch
            event_ids = await engine.ingest_batch([
                message("My final score was 3."), message("My final score was 3.", role="assistant", minutes=1),
            ], user_id=user, session_id="s1", wait_for_extraction=True)
            records = [await engine.get_extraction(event_id, user_id=user) for event_id in event_ids]
            # The user-only literal recovery runs for the user's turn and not for the assistant's.
            assert [len(record.result["facts"]) for record in records] == [1, 0]

    async def test_a_turn_larger_than_the_target_goes_alone(self, config, user):
        model = ScriptedBatches()
        long = "Jordan moved to Denver last spring. " + "The weather there was mild and sunny. " * 8
        async with MemoryEngine.open(batched(config, tokens=24)) as engine:
            install(engine, model)
            await engine.ingest_batch([message("Short one."), message(long, minutes=1),
                                       message("Short two.", minutes=2), message("Short three.", minutes=3)],
                                      user_id=user, session_id="s1", wait_for_extraction=True)
        assert model.contents() == [["Short one."], [long], ["Short two.", "Short three."]]

    async def test_a_turn_without_a_session_goes_alone(self, config, user):
        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            await engine.ingest_batch([message("One."), message("Two.", minutes=1)], user_id=user,
                                      wait_for_extraction=True)
        assert model.contents() == [["One."], ["Two."]]


class TestSessions:
    async def test_batches_never_cross_a_session(self, config, user):
        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            # Two conversations interleaved in one owner's queue.
            for minutes, (text, session) in enumerate([("A1.", "a"), ("B1.", "b"), ("A2.", "a"), ("B2.", "b")]):
                await engine.ingest(text, user_id=user, session_id=session, event_time=T0 + timedelta(minutes=minutes))
            assert model.calls == []
            result = await engine.process_extractions(user_id=user)
            assert (result.processed, result.pending, result.failed) == (4, 0, 0)
        assert model.contents() == [["A1.", "A2."], ["B1.", "B2."]]

    async def test_a_waiting_ingest_extracts_the_queue_through_itself(self, config, user):
        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            for minutes, text in enumerate(["One.", "Two."]):
                await engine.ingest(text, user_id=user, session_id="s1", event_time=T0 + timedelta(minutes=minutes))
            event_id = await engine.ingest("Three.", user_id=user, session_id="s1",
                                           event_time=T0 + timedelta(minutes=2), wait_for_extraction=True)
            assert (await engine.extraction_status(event_id, user_id=user)).status == "complete"
        assert model.contents() == [["One.", "Two.", "Three."]]

    async def test_queued_turns_are_extracted_after_the_flush_delay(self, config, user):
        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            pipeline = install(engine, model, flush_seconds=0.05)
            event_ids = [await engine.ingest(text, user_id=user, session_id="s1",
                                             event_time=T0 + timedelta(minutes=minutes))
                         for minutes, text in enumerate(["One.", "Two.", "Three."])]
            assert model.calls == []
            await asyncio.sleep(0.1)
            await drain(pipeline)
            assert model.contents() == [["One.", "Two.", "Three."]]
            assert await statuses(engine, user, event_ids) == ["complete"] * 3

    async def test_closing_the_engine_extracts_the_queued_turns(self, config, user):
        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            event_ids = [await engine.ingest(text, user_id=user, session_id="s1",
                                             event_time=T0 + timedelta(minutes=minutes))
                         for minutes, text in enumerate(["One.", "Two."])]
            assert model.calls == []
        assert model.contents() == [["One.", "Two."]]
        async with MemoryEngine.open(batched(config)) as engine:
            assert await statuses(engine, user, event_ids) == ["complete"] * 2


def scaled_close(pipeline, monkeypatch, *, timeout, cap=None, drain=0.05):
    """The close's usual drain, the extraction timeout and the close's cap, scaled down from 10, 30 and 300 seconds."""
    shutdown = pipeline.shutdown
    monkeypatch.setattr(pipeline, "shutdown", lambda: shutdown(drain_timeout=drain))
    pipeline._extraction_timeout = timeout
    if cap is not None:
        pipeline._batch_close_seconds = cap
    return pipeline


class TestClosing:
    """The engine's close waits for the batches of queued turns, within a cap (#258)."""

    TEXTS = ["Caroline paints sunsets over the lake.", "Two."]

    async def ingest_queued(self, engine, user):
        return [await engine.ingest(text, user_id=user, session_id="s1", event_time=T0 + timedelta(minutes=minutes))
                for minutes, text in enumerate(self.TEXTS)]

    async def test_a_close_waits_for_a_batch_that_takes_longer_than_the_usual_drain(self, config, user,
                                                                                     monkeypatch):
        # The request takes six times the usual drain, and less than its own timeout.
        model = SlowBatches(delay=0.3)
        async with MemoryEngine.open(batched(config)) as engine:
            scaled_close(install(engine, model), monkeypatch, timeout=5.0)
            event_ids = await self.ingest_queued(engine, user)
            assert model.calls == []
        assert model.contents() == [self.TEXTS]
        async with MemoryEngine.open(batched(config)) as engine:
            assert await statuses(engine, user, event_ids) == ["complete"] * 2
            assert await claims_by_event(engine, user) == {event_ids[0]: self.TEXTS[0]}

    async def test_a_close_also_waits_for_a_flush_already_running(self, config, user, monkeypatch):
        model = SlowBatches(delay=0.3)
        async with MemoryEngine.open(batched(config)) as engine:
            scaled_close(install(engine, model), monkeypatch, timeout=5.0)
            # ingest_batch() without waiting starts its flush at once.
            event_ids = await engine.ingest_batch([message(text, minutes=n) for n, text in enumerate(self.TEXTS)],
                                                  user_id=user, session_id="s1")
        assert model.contents() == [self.TEXTS]
        async with MemoryEngine.open(batched(config)) as engine:
            assert await statuses(engine, user, event_ids) == ["complete"] * 2

    async def test_a_close_waits_no_longer_than_its_cap(self, config, user, monkeypatch):
        # A provider that does not answer within the test: the cap ends the wait.
        model = SlowBatches(delay=60.0)
        async with MemoryEngine.open(batched(config)) as engine:
            scaled_close(install(engine, model), monkeypatch, timeout=5.0, cap=0.3)
            event_ids = await self.ingest_queued(engine, user)
            started = time.monotonic()
        assert time.monotonic() - started < 5.0
        assert model.started == [self.TEXTS] and model.calls == []
        async with MemoryEngine.open(batched(config)) as engine:
            # The turns stay queued, durably, for the next flush of their owner and scope.
            assert await statuses(engine, user, event_ids) == ["pending"] * 2
            install(engine, ScriptedBatches())
            assert (await engine.process_extractions(user_id=user)).processed == 2

    async def test_the_close_allows_the_timeout_for_each_queued_request(self, config, user):
        model = ScriptedBatches()
        long = "Jordan moved to Denver last spring. " + "The weather there was mild and sunny. " * 8
        queue = [("Short one.", "a"), (long, "a"), ("Short two.", "a"), ("B one.", "b"), ("Short three.", "a"),
                 ("Alone.", None)]
        async with MemoryEngine.open(batched(config, tokens=24)) as engine:
            pipeline = install(engine, model)
            for minutes, (text, session) in enumerate(queue):
                await engine.ingest(text, user_id=user, session_id=session, event_time=T0 + timedelta(minutes=minutes))
            key = (user, Scope.PERSONAL)
            # As the flush composes them: the long turn goes alone and ends the batch before it,
            # the next short turn of its session starts another, and a turn without a session goes alone.
            assert await pipeline._queued_requests(user, Scope.PERSONAL, limit=100) == 5
            assert await pipeline._queued_requests(user, Scope.PERSONAL, limit=2) == 2
            pipeline._extraction_timeout = 30.0
            assert await pipeline._drain_seconds(10.0, [key, key]) == 10.0 + 5 * 30.0
            pipeline._batch_close_seconds = 100.0
            assert await pipeline._drain_seconds(10.0, [key]) == 100.0
            # The usual drain, never less, and nothing longer without a flush to wait for.
            assert await pipeline._drain_seconds(120.0, [key]) == 120.0
            assert await pipeline._drain_seconds(10.0, []) == 10.0
            assert (await engine.process_extractions(user_id=user)).processed == len(queue)
            assert model.contents() == [["Short one."], [long], ["Short two.", "Short three."], ["B one."],
                                        ["Alone."]]
            assert await pipeline._drain_seconds(10.0, [key]) == 10.0

    async def test_without_batching_the_close_keeps_the_usual_drain(self, config, user):
        async with MemoryEngine.open(config) as engine:
            assert await engine._pipeline._drain_seconds(10.0, [(user, Scope.PERSONAL)]) == 10.0

    async def test_the_flushes_run_while_their_requests_are_counted(self, config, user, monkeypatch):
        model, happened = SlowBatches(delay=0.1), []
        answer = model.extract_batch

        async def extract_batch(turns):
            happened.append("request sent")
            return await answer(turns)

        model.extract_batch = extract_batch
        async with MemoryEngine.open(batched(config)) as engine:
            pipeline = scaled_close(install(engine, model), monkeypatch, timeout=5.0)
            count = pipeline._drain_seconds

            async def slow_count(*args):
                await asyncio.sleep(0.5)
                happened.append("count done")
                return await count(*args)

            monkeypatch.setattr(pipeline, "_drain_seconds", slow_count)
            event_ids = await self.ingest_queued(engine, user)
        assert happened == ["request sent", "count done"] and model.contents() == [self.TEXTS]
        async with MemoryEngine.open(batched(config)) as engine:
            assert await statuses(engine, user, event_ids) == ["complete"] * 2

    async def test_counting_is_part_of_the_wait_and_never_passes_the_cap(self, config, user, monkeypatch):
        model = SlowBatches(delay=60.0)
        with capture_logs() as logs:
            async with MemoryEngine.open(batched(config)) as engine:
                pipeline = scaled_close(install(engine, model), monkeypatch, timeout=5.0, cap=0.3)

                async def endless_count(*args):
                    await asyncio.sleep(60.0)

                monkeypatch.setattr(pipeline, "_drain_seconds", endless_count)
                event_ids = await self.ingest_queued(engine, user)
                started = time.monotonic()
            assert time.monotonic() - started < 5.0
        assert [entry["log_level"] for entry in logs
                if entry["event"] == "ingestion.extraction_close_count_timed_out"] == ["warning"]
        async with MemoryEngine.open(batched(config)) as engine:
            assert await statuses(engine, user, event_ids) == ["pending"] * 2

    async def test_a_retry_that_wakes_after_the_close_starts_nothing(self, config, user):
        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            pipeline = install(engine, model)
            pipeline._retry_delays = (0.3,)
            event_ids = await self.ingest_queued(engine, user)
            head = await engine._event_store.get(event_ids[0])
            pipeline._schedule_retry(head, event_ids[0], attempt=1, scope=Scope.PERSONAL)
            # As if a later retry of the same turn had replaced it: the close does not cancel it.
            stray = pipeline._retry_tasks.pop(event_ids[0])
        assert model.contents() == [self.TEXTS]
        await asyncio.wait_for(stray, timeout=5.0)
        assert not pipeline._flushing and not pipeline._background_tasks
        assert model.contents() == [self.TEXTS]

    async def test_a_retry_flush_running_at_close_is_waited_for(self, config, user, monkeypatch):
        model = SlowBatches(delay=0.3)
        answer, attempts = model.extract_batch, []

        async def fail_once_then_answer_slowly(turns):
            attempts.append([turn.content for turn in turns])
            if len(attempts) == 1:
                raise ExtractionError("scripted failure", reason_code="RateLimitError")
            return await answer(turns)

        model.extract_batch = fail_once_then_answer_slowly
        async with MemoryEngine.open(batched(config)) as engine:
            pipeline = scaled_close(install(engine, model), monkeypatch, timeout=5.0)
            pipeline._retry_delays = (0.2, 0.2, 0.2)
            event_ids = await engine.ingest_batch([message(text, minutes=n) for n, text in enumerate(self.TEXTS)],
                                                  user_id=user, session_id="s1")
            # Close once the retry's flush has sent its request.
            for _ in range(300):
                if model.started:
                    break
                await asyncio.sleep(0.01)
            assert len(attempts) == 2 and model.started == [self.TEXTS]
        # The retry's request was answered, not cancelled and sent again by the close's own flush.
        assert model.started == [self.TEXTS] and model.contents() == [self.TEXTS]
        async with MemoryEngine.open(batched(config)) as engine:
            assert await statuses(engine, user, event_ids) == ["complete"] * 2


def test_a_client_close_waits_for_the_batched_close(config, user, monkeypatch):
    from prme.client import MemoryClient

    # Scaled down from 30 seconds and the five-minute cap.
    monkeypatch.setattr("prme.client._CLOSE_SECONDS", 0.05)
    monkeypatch.setattr("prme.ingestion.pipeline.BATCH_CLOSE_SECONDS", 5.0)
    model = SlowBatches(delay=0.3)
    client = MemoryClient(config=batched(config))
    install(client._engine, model)
    event_id = client._run(client._engine.ingest(TestClosing.TEXTS[0], user_id=user, session_id="s1",
                                                 event_time=T0))
    client.close()
    assert model.contents() == [TestClosing.TEXTS[:1]]

    async def status():
        async with MemoryEngine.open(batched(config)) as engine:
            return await statuses(engine, user, [event_id])

    assert asyncio.run(status()) == ["complete"]


class TestFailures:
    async def test_a_batch_that_fails_validation_is_asked_again_in_halves(self, config, user):
        def broken(turns):
            return "ValidationError" if any(turn.content == "Broken." for turn in turns) else None

        model = ScriptedBatches(fail=broken)
        texts = ["Caroline paints sunsets over the lake.", "Two.", "Three.", "Broken."]
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)._retry_delays = ()
            with pytest.raises(ExtractionError) as failure:
                await engine.ingest_batch([message(text, minutes=n) for n, text in enumerate(texts)],
                                          user_id=user, session_id="s1", wait_for_extraction=True)
            event_ids = [event.id for event in sorted(await engine.get_events(user), key=lambda e: e.event_time)]
            event_ids = [str(event_id) for event_id in event_ids]
            assert failure.value.event_id == event_ids[3] and failure.value.reason_code == "ValidationError"
            # One bad turn fails only itself: the halves without it are saved and published.
            assert model.contents() == [texts, texts[:2], texts[2:], ["Three."], ["Broken."], ["Broken."]]
            assert await statuses(engine, user, event_ids) == ["complete", "complete", "complete", "failed"]
            assert await claims_by_event(engine, user) == {event_ids[0]: texts[0]}
            assert await batch_operations(engine, user) == 2

    @pytest.mark.parametrize("reason", ["APIConnectionError", "RateLimitError", "InternalServerError"])
    async def test_a_transport_failure_is_not_split(self, config, user, reason):
        model = ScriptedBatches(fail=lambda turns: reason)
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)._retry_delays = ()
            with pytest.raises(ExtractionError) as failure:
                await engine.ingest_batch([message("One."), message("Two.", minutes=1)], user_id=user,
                                          session_id="s1", wait_for_extraction=True)
            assert failure.value.reason_code == reason
            assert model.contents() == [["One.", "Two."]]
        # The turn left behind the failed one is tried again when the engine closes.
        assert model.contents() == [["One.", "Two."], ["Two."]]

    async def test_a_restart_publishes_saved_batch_records_without_a_model_call(self, config, user):
        model = ScriptedBatches()
        texts = ["Caroline paints sunsets over the lake.", "Melanie adopted a puppy named Oscar.", "Three."]
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            event_ids = [await engine.ingest(text, user_id=user, session_id="s1",
                                             event_time=T0 + timedelta(minutes=minutes))
                         for minutes, text in enumerate(texts)]
            # One job runs: its request saves every turn's record, and only it is published.
            assert (await engine.process_extractions(user_id=user, limit=1)).processed == 1
            assert await statuses(engine, user, event_ids) == ["complete", "pending", "pending"]
            assert all([await engine.get_extraction(event_id, user_id=user) for event_id in event_ids])
            # Stop here, as a process that exits would, before the queue's flush publishes the rest.
            for timer in engine._pipeline._flush_timers.values():
                timer.cancel()
            await asyncio.gather(*engine._pipeline._flush_timers.values(), return_exceptions=True)
            engine._pipeline._flush_timers.clear()
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, ScriptedBatches(fail=lambda turns: pytest.fail("the model was asked again")))
            result = await engine.process_extractions(user_id=user)
            assert (result.processed, result.pending, result.failed) == (2, 0, 0)
            assert await claims_by_event(engine, user) == {event_ids[0]: texts[0], event_ids[1]: texts[1]}
        assert len(model.calls) == 1


def named(logs, event):
    return [entry for entry in logs if entry["event"] == event]


def leaving_out(model, text, *, times=None):
    """``model``, leaving out of its responses the entry of the turn ``text``, ``times`` times or always."""
    answer, left_out = model.extract_batch, []

    async def extract_batch(turns):
        response = await answer(turns)
        indices = {turn.index for turn in turns if turn.content == text}
        if indices and (times is None or len(left_out) < times):
            left_out.append(indices)
            response["turns"] = [entry for entry in response["turns"] if entry["turn"] not in indices]
        return response

    model.extract_batch = extract_batch
    return model


class TestTurnsLeftOut:
    """A turn a response gives no entry is asked for again, alone, before it is saved (#260)."""

    TEXTS = ["Caroline paints sunsets over the lake.", "Melanie adopted a puppy named Oscar.", "Thanks, that sounds fun."]

    async def ingest(self, engine, user):
        return await engine.ingest_batch([message(text, minutes=n) for n, text in enumerate(self.TEXTS)],
                                         user_id=user, session_id="s1", wait_for_extraction=True)

    async def batches(self, engine, user, event_ids):
        records = [await engine.get_extraction(event_id, user_id=user) for event_id in event_ids]
        return records, {record.event_id: await engine._event_store.get_extraction_batch(
            str(record.batch_id), user_id=user) for record in records}

    async def test_one_more_request_asks_for_the_turn_and_its_claims_are_saved_under_it(self, config, user):
        model = leaving_out(ScriptedBatches(), self.TEXTS[1], times=1)
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            with capture_logs() as logs:
                event_ids = await self.ingest(engine, user)
            # Exactly one more request, for the turn left out, alone.
            assert model.contents() == [self.TEXTS, [self.TEXTS[1]]]
            # The turn is reported as asked again, not as saved stating nothing.
            assert [entry["turns"] for entry in named(logs, "ingestion.extraction_batch_turns_asked_again")] == [1]
            assert named(logs, "ingestion.extraction_batch_turns_missing") == []
            assert await statuses(engine, user, event_ids) == ["complete"] * 3
            assert await claims_by_event(engine, user) == {event_ids[0]: self.TEXTS[0], event_ids[1]: self.TEXTS[1]}
            records, batches = await self.batches(engine, user, event_ids)
            first, again = batches[records[0].event_id], batches[records[1].event_id]
            # The first request's record names the turn asked again and saves only the others' records.
            assert first.turns_asked_again == (2,)
            assert [str(turn.event_id) for turn in first.saved_turns] == [event_ids[0], event_ids[2]]
            assert records[0].batch_id == records[2].batch_id == first.id != records[1].batch_id == again.id
            assert [(turn.index, str(turn.event_id)) for turn in again.turns] == [(1, event_ids[1])]
            assert again.turns_asked_again == () and [entry["turn"] for entry in again.response["turns"]] == [1]
            assert again.head_event_id == first.head_event_id == records[0].event_id
            assert [len(record.result["facts"]) for record in records] == [1, 1, 0]
            assert await batch_operations(engine, user) == 2
            # The record of a request that asked no turn again keeps the bytes it had before.
            assert "turns_asked_again" in first.model_dump_json()
            assert "turns_asked_again" not in again.model_dump_json()

    async def test_a_turn_left_out_again_is_saved_as_stating_nothing(self, config, user):
        model = leaving_out(ScriptedBatches(), self.TEXTS[1])
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            with capture_logs() as logs:
                event_ids = await self.ingest(engine, user)
            # The request for the turn is not followed by another.
            assert model.contents() == [self.TEXTS, [self.TEXTS[1]]]
            assert [entry["turns"] for entry in named(logs, "ingestion.extraction_batch_turns_missing")] == [[1]]
            assert await statuses(engine, user, event_ids) == ["complete"] * 3
            assert await claims_by_event(engine, user) == {event_ids[0]: self.TEXTS[0]}
            records, batches = await self.batches(engine, user, event_ids)
            assert records[1].result["facts"] == [] and batches[records[1].event_id].response == {"turns": []}

    async def test_a_response_that_leaves_out_every_turn_is_not_asked_again(self, config, user):
        async def nothing(turns):
            model.calls.append([(turn.index, turn.role, turn.speaker, turn.content) for turn in turns])
            return {"turns": []}

        model = ScriptedBatches()
        model.extract_batch = nothing
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            with capture_logs() as logs:
                event_ids = await self.ingest(engine, user)
            # Asking for every turn again would send the same request.
            assert model.contents() == [self.TEXTS]
            assert named(logs, "ingestion.extraction_batch_turns_asked_again") == []
            assert [entry["turns"] for entry in named(logs, "ingestion.extraction_batch_turns_missing")] == [[1, 2, 3]]
            records, batches = await self.batches(engine, user, event_ids)
            assert {record.batch_id for record in records} == {records[0].batch_id}
            assert batches[records[0].event_id].turns_asked_again == ()
            assert all(record.result["facts"] == [] for record in records)

    async def test_a_failed_request_for_the_turn_keeps_the_others_and_the_turn_asks_later(self, config, user):
        def fail_the_request_for_the_turn(turns):
            asked = [turn.content for turn in turns]
            return "RateLimitError" if asked == [self.TEXTS[1]] and model.contents().count(asked) == 1 else None

        model = leaving_out(ScriptedBatches(fail=fail_the_request_for_the_turn), self.TEXTS[1], times=1)
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            event_ids = await self.ingest(engine, user)
            # The other turns' records were saved first; the turn's own job then asked for it.
            assert model.contents() == [self.TEXTS, [self.TEXTS[1]], [self.TEXTS[1]]]
            assert await statuses(engine, user, event_ids) == ["complete"] * 3
            assert await claims_by_event(engine, user) == {event_ids[0]: self.TEXTS[0], event_ids[1]: self.TEXTS[1]}
            records, batches = await self.batches(engine, user, event_ids)
            assert batches[records[0].event_id].turns_asked_again == (2,)
            assert batches[records[1].event_id].head_event_id == records[1].event_id

    async def test_a_turn_left_out_of_one_half_of_a_split_request_is_asked_again(self, config, user):
        texts = [*self.TEXTS, "Jordan moved to Denver last spring."]
        model = leaving_out(ScriptedBatches(fail=lambda turns: "ValidationError" if len(turns) == 4 else None),
                            texts[3], times=1)
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            event_ids = await engine.ingest_batch([message(text, minutes=n) for n, text in enumerate(texts)],
                                                  user_id=user, session_id="s1", wait_for_extraction=True)
            assert model.contents() == [texts, texts[:2], texts[2:], [texts[3]]]
            assert await statuses(engine, user, event_ids) == ["complete"] * 4
            assert set((await claims_by_event(engine, user)).values()) == {texts[0], texts[1], texts[3]}


def directory_pack(tmp_path, monkeypatch):
    """A batched directory pack, as `prme rebuild` reads one."""
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider",
                        lambda _: test_durable_ingestion.MockEmbeddingProvider())
    (tmp_path / "lexical_index").mkdir()
    return PRMEConfig(_env_file=None, db_path=str(tmp_path / "memory.duckdb"),
                      vector_path=str(tmp_path / "vectors.usearch"), lexical_path=str(tmp_path / "lexical_index"),
                      organizer={"opportunistic_enabled": False}, extraction_batch_target_tokens=512)


async def snapshot(engine):
    nodes = await engine.query_nodes(user_id="u", lifecycle_states=list(LifecycleState), limit=1000)
    retrieved = await engine.retrieve("Who adopted a puppy?", user_id="u")
    return (sorted((str(node.id), node.node_type.value, node.content, node.lifecycle_state.value,
                    [str(ref) for ref in node.evidence_refs]) for node in nodes),
            [(str(result.node.id), round(result.composite_score, 9)) for result in retrieved.results])


def forbid_model_calls(monkeypatch):
    for name in ("extract", "extract_batch"):
        monkeypatch.setattr(InstructorExtractionProvider, name,
                            AsyncMock(side_effect=AssertionError("the model was called")))


async def test_rebuild_of_a_batched_pack_reproduces_it_without_a_model_call(tmp_path, monkeypatch):
    from prme.cli import cmd_rebuild

    pack = directory_pack(tmp_path, monkeypatch)
    async with MemoryEngine.open(pack) as engine:
        install(engine, ScriptedBatches())
        await engine.ingest_batch([message("Caroline paints sunsets over the lake.", speaker="Caroline"),
                                   message("Melanie adopted a puppy named Oscar.", role="participant",
                                           speaker="Melanie", minutes=1)],
                                  user_id="u", session_id="s1", wait_for_extraction=True)
        await engine.ingest_batch([message("Jordan moved to Denver last spring.", minutes=60)],
                                  user_id="u", session_id="s2", wait_for_extraction=True)
        await engine.process_pending(user_id="u", budget_ms=60_000)
        before = await snapshot(engine)
    assert len([node for node in before[0] if node[1] == "fact"]) == 3

    forbid_model_calls(monkeypatch)
    await cmd_rebuild(Namespace(db_path=pack.db_path, batch_size=2, format="json"))
    async with MemoryEngine.open(pack) as engine:
        assert await snapshot(engine) == before
        # Replaying a turn's extraction reads its saved record.
        for event in await engine.get_events("u"):
            replayed = await engine._pipeline._extract_or_load(event)
            assert replayed.model_dump(mode="json") == (await engine.get_extraction(
                str(event.id), user_id="u")).result


async def test_a_turn_a_pack_from_before_260_saved_as_stating_nothing_is_never_asked_again(tmp_path, monkeypatch):
    from prme.cli import cmd_rebuild

    pack = directory_pack(tmp_path, monkeypatch)
    texts = TestTurnsLeftOut.TEXTS
    with monkeypatch.context() as before_260:
        # #254 saved a turn its response left out as stating nothing, and asked for no turn again.
        before_260.setattr("prme.ingestion.extraction.batch_turns_without_entry", lambda raw, turns: [])
        async with MemoryEngine.open(pack) as engine:
            pipeline = install(engine, leaving_out(ScriptedBatches(), texts[1]))
            event_ids = [await engine.ingest(text, user_id="u", session_id="s1",
                                             event_time=T0 + timedelta(minutes=minutes))
                         for minutes, text in enumerate(texts)]
            # The first job's request saves every turn's record, and the process stops before the rest publish.
            assert (await engine.process_extractions(user_id="u", limit=1)).processed == 1
            for timer in pipeline._flush_timers.values():
                timer.cancel()
            await asyncio.gather(*pipeline._flush_timers.values(), return_exceptions=True)
            pipeline._flush_timers.clear()

    forbid_model_calls(monkeypatch)
    async with MemoryEngine.open(pack) as engine:
        record = await engine.get_extraction(event_ids[1], user_id="u")
        batch = await engine._event_store.get_extraction_batch(str(record.batch_id), user_id="u")
        assert batch.turns_asked_again == () and [entry["turn"] for entry in batch.response["turns"]] == [1, 3]
        # This release publishes the saved records as they are.
        result = await engine.process_extractions(user_id="u")
        assert (result.processed, result.pending, result.failed) == (2, 0, 0)
        assert await engine.get_extraction(event_ids[1], user_id="u") == record and record.result["facts"] == []
        assert await claims_by_event(engine, "u") == {event_ids[0]: texts[0]}
        await engine.process_pending(user_id="u", budget_ms=60_000)
        before = await snapshot(engine)
    await cmd_rebuild(Namespace(db_path=pack.db_path, batch_size=2, format="json"))
    async with MemoryEngine.open(pack) as engine:
        assert await snapshot(engine) == before


class TestTheRequest:
    TURNS = [BatchTurn(1, "user", "I paint.", speaker='Caro "C" line', time=T0),
             BatchTurn(2, "assistant", "Nice!", time=T0 + timedelta(minutes=1))]

    def test_each_turn_carries_its_index_role_time_and_speaker(self):
        system, turns = batch_request_messages(self.TURNS)
        assert system == {"role": "system", "content": BATCH_EXTRACTION_SYSTEM_PROMPT}
        assert turns["role"] == "user"
        assert '<turn index="1" role="user" time="2023-05-08T13:56+00:00" speaker="Caro &quot;C&quot; line">\n' \
               'I paint.\n</turn>' in turns["content"]
        assert '<turn index="2" role="assistant" time="2023-05-08T13:57+00:00">\nNice!\n</turn>' in turns["content"]
        assert batch_request_sha256(self.TURNS) == batch_request_sha256(list(self.TURNS))

    def test_the_prompt_states_every_roles_policy_and_the_grounding_rule(self):
        for text in ("user turns:", "assistant turns: Admit only", "system turns:", "tool turns:",
                     "any other role, such as participant:", "do not extract a claim that only another turn",
                     "never from another turn"):
            assert text in BATCH_EXTRACTION_SYSTEM_PROMPT
        assert BATCH_EXTRACTION_SYSTEM_PROMPT.startswith("You are a knowledge extraction system.")

    def test_a_response_without_turn_entries_fails_as_a_whole(self):
        for raw in ({}, {"facts": []}, {"turns": [{"facts": []}]}, {"turns": "none"}, ["turns"]):
            with pytest.raises(ExtractionError) as failure:
                validate_batch_output(raw, self.TURNS)
            assert failure.value.reason_code == "ValidationError"

    def test_entries_are_joined_by_turn_and_malformed_claims_dropped_alone(self):
        turns = [BatchTurn(1, "user", "Jordan moved to Denver last spring.")]
        entry = claim_entry(1, "Jordan moved to Denver last spring.")
        results = validate_batch_output({"turns": [
            {"turn": 1, "entities": entry["entities"] + [{"name": "broken"}], "facts": [{"subject": "x"}]},
            {"turn": "1", "facts": entry["facts"]},
        ]}, turns)
        assert [(fact.subject, fact.object) for fact in results[1].facts] == [("Jordan", "Denver")]
        assert [entity.name for entity in results[1].entities] == ["Jordan"]

    def test_speaker_grounding_applies_turn_by_turn(self):
        turns = [BatchTurn(1, "participant", "I live in Denver.", speaker="Dana")]
        raw = {"turns": [{"turn": 1, "entities": [{"name": "Dana", "entity_type": "person"},
                                                  {"name": "Denver", "entity_type": "location"}],
                          "facts": [{"subject": "Dana", "predicate": "lives_in", "object": "Denver",
                                     "polarity": "positive", "epistemic_type": "observed",
                                     "evidence_quote": "I live in Denver."}]}]}
        assert validate_batch_output(raw, turns)[1].facts == []
        assert [fact.subject for fact in validate_batch_output(raw, turns, speakers={1: "Dana"})[1].facts] == [
            "Dana"]

    def test_only_the_checked_fields_of_a_response_are_kept(self):
        raw = {"reasoning": "first I read the turns", "turns": [
            {"turn": 1, "facts": [], "note": "nothing here"}, "not an entry", {"turn": 2, "entities": []}]}
        assert batch_response_fields(raw) == {"turns": [{"turn": 1, "facts": []}, {"turn": 2, "entities": []}]}
        assert batch_response_fields({"turns": "none"}) == {"turns": []}


class TestTheProvider:
    async def test_a_batch_is_one_request_whose_parsed_output_is_returned(self):
        from unittest.mock import patch

        from prme.ingestion.extraction import _RAW_BATCH_OUTPUT, _BatchExtractionResult

        provider = InstructorExtractionProvider("ollama/qwen3.5:9b", max_retries=3)
        raw = {"turns": [{"turn": 1, "entities": [], "facts": [], "relationships": [], "note": "kept as sent"}]}

        async def create(**kwargs):
            # As Instructor does: parse the model's JSON into the response model.
            return kwargs["response_model"].model_validate_json(json.dumps(raw))

        client = AsyncMock()
        client.create = AsyncMock(side_effect=create)
        turns = TestTheRequest.TURNS
        with patch.object(provider, "_ensure_client", return_value=client):
            assert await provider.extract_batch(turns) == raw
            kwargs = client.create.await_args.kwargs
            assert kwargs["response_model"] is _BatchExtractionResult
            assert kwargs["messages"] == batch_request_messages(turns)
            # Several turns are asked once, since a failure is asked again in halves.
            assert (kwargs["max_retries"], kwargs["temperature"], kwargs["reasoning_effort"], kwargs["model"]) == (
                1, 0.0, "none", "qwen3.5:9b")
            # No validation context: Instructor would apply it to the messages as a template.
            assert "context" not in kwargs and "validation_context" not in kwargs
            await provider.extract_batch(turns[:1])
            assert client.create.await_args.kwargs["max_retries"] == 3
            assert _RAW_BATCH_OUTPUT.get() is None
            client.create = AsyncMock(return_value=None)
            with pytest.raises(ExtractionError) as failure:
                await provider.extract_batch(turns)
            assert failure.value.reason_code == "ValidationError"
            client.create = AsyncMock(side_effect=asyncio.TimeoutError())
            with pytest.raises(ExtractionError) as failure:
                await provider.extract_batch(turns)
            assert failure.value.reason_code == "TimeoutError"
        with pytest.raises(ValueError):
            await provider.extract_batch([])


class TestTheBatchRecord:
    async def test_it_is_saved_whole_and_only_under_the_senders_live_claim(self, config, user):
        from uuid import uuid4

        from prme.models import Event
        from prme.models.extraction import ExtractionBatchRecord, ExtractionBatchTurn, ExtractionRecord
        from prme.models.extraction_work import StaleExtractionClaimError

        async with MemoryEngine.open(batched(config)) as engine:
            store, work = engine._event_store, engine._event_store.extraction_work
            events = [Event(content=text, user_id=user, role="user", session_id="s1",
                            event_time=T0 + timedelta(minutes=n)) for n, text in enumerate(["One.", "Two."])]
            for event in events:
                await store.append(event, defer_extraction=True)
            claim = await work.claim(user_id=user, event_id=str(events[0].id))

            def batch_of(head=events[0], response=None):
                return ExtractionBatchRecord(
                    user_id=user, scope=events[0].scope, session_id="s1", head_event_id=head.id, provider="p",
                    model="m", request_sha256="0" * 64, response=response or {"turns": []},
                    turns=tuple(ExtractionBatchTurn(index=n, event_id=event.id, role="user",
                                                    event_time=event.event_time, content_hash=event.content_hash)
                                for n, event in enumerate(events, start=1)))

            def records(batch, **change):
                return [ExtractionRecord(**{
                    "event_id": event.id, "user_id": user, "scope": event.scope, "content_hash": event.content_hash,
                    "provider": "p", "model": "m", "grounding_policy": "speech_act_v14",
                    "result": ExtractionResult().model_dump(mode="json"), "batch_id": batch.id, **change})
                    for event in events]

            batch = batch_of()
            for wrong in (records(batch)[:1], records(batch, batch_id=uuid4()), records(batch, content_hash="f" * 64)):
                with pytest.raises(ValueError):
                    await store.record_extraction_batch(batch, wrong, claim=claim)
            # A stale generation writes nothing, and nor does a write without any claim.
            for stale in (claim.model_copy(update={"generation": claim.generation + 1}), None):
                with pytest.raises(StaleExtractionClaimError):
                    await store.record_extraction_batch(batch, records(batch), claim=stale)
            # Nor may a batch name another owner's source as the job that sent it.
            foreign = Event(content="Not yours.", user_id=user + "-other", role="user")
            await store.append(foreign)
            stray = batch_of(head=foreign)
            with pytest.raises(ValueError, match="job that sent it"):
                await store.record_extraction_batch(stray, records(stray), claim=None)
            assert await store.get_extraction_batch(str(batch.id), user_id=user) is None
            assert [await store.get_extraction(str(event.id), user_id=user) for event in events] == [None, None]

            await store.record_extraction_batch(batch, records(batch), claim=claim)
            saved = [await store.get_extraction(str(event.id), user_id=user) for event in events]
            assert {record.batch_id for record in saved} == {batch.id}
            # Saving the same request again changes nothing: the first output stays.
            again = batch_of(response={"turns": [{"turn": 1}]})
            assert again.id == batch.id
            await store.record_extraction_batch(again, records(again), claim=claim)
            assert (await store.get_extraction_batch(str(batch.id), user_id=user)).response == {"turns": []}
            assert [await store.get_extraction(str(event.id), user_id=user) for event in events] == saved

    async def test_a_turn_asked_again_gets_no_record_from_it(self, config, user):
        from prme.models import Event
        from prme.models.extraction import ExtractionBatchRecord, ExtractionBatchTurn, ExtractionRecord

        async with MemoryEngine.open(batched(config)) as engine:
            store, work = engine._event_store, engine._event_store.extraction_work
            events = [Event(content=text, user_id=user, role="user", session_id="s1",
                            event_time=T0 + timedelta(minutes=n)) for n, text in enumerate(["One.", "Two."])]
            for event in events:
                await store.append(event, defer_extraction=True)
            claim = await work.claim(user_id=user, event_id=str(events[0].id))
            batch = ExtractionBatchRecord(
                user_id=user, scope=events[0].scope, session_id="s1", head_event_id=events[0].id, provider="p",
                model="m", request_sha256="0" * 64, response={"turns": [{"turn": 1}]}, turns_asked_again=(2,),
                turns=tuple(ExtractionBatchTurn(index=n, event_id=event.id, role="user", event_time=event.event_time,
                                                content_hash=event.content_hash)
                            for n, event in enumerate(events, start=1)))
            records = [ExtractionRecord(
                event_id=event.id, user_id=user, scope=event.scope, content_hash=event.content_hash, provider="p",
                model="m", grounding_policy="speech_act_v14", result=ExtractionResult().model_dump(mode="json"),
                batch_id=batch.id) for event in events]
            with pytest.raises(ValueError, match="not asked again"):
                await store.record_extraction_batch(batch, records, claim=claim)
            await store.record_extraction_batch(batch, records[:1], claim=claim)
            assert (await store.get_extraction_batch(str(batch.id), user_id=user)).turns_asked_again == (2,)
            assert await store.get_extraction(str(events[1].id), user_id=user) is None

    def test_turns_asked_again_are_some_of_its_turns_and_left_out_when_none(self):
        from uuid import UUID

        from prme.models.extraction import ExtractionBatchRecord, ExtractionBatchTurn

        turns = tuple(ExtractionBatchTurn(index=n, event_id=UUID(f"00000000-0000-4000-8000-00000000026{n}"),
                                          role="user", event_time=T0, content_hash="a" * 64) for n in (1, 2, 3))
        fields = dict(user_id="u", scope="personal", head_event_id=turns[0].event_id, provider="p", model="m",
                      request_sha256="0" * 64, turns=turns, response={"turns": []},
                      created_at=datetime(2026, 10, 15, tzinfo=timezone.utc))
        # A batch record as #254 wrote it keeps its bytes and identity: these are the bytes
        # and the identity #254's own model gives this record.
        written_by_254 = (
            '{"schema_version":1,"user_id":"u","scope":"personal","session_id":null,'
            '"head_event_id":"00000000-0000-4000-8000-000000000261","provider":"p","model":"m",'
            '"request_sha256":"' + "0" * 64 + '","turns":['
            + ",".join('{"index":%d,"event_id":"00000000-0000-4000-8000-00000000026%d","role":"user",'
                       '"speaker":null,"event_time":"2023-05-08T13:56:00Z","content_hash":"%s"}' % (n, n, "a" * 64)
                       for n in (1, 2, 3))
            + '],"response":{"turns":[]},"created_at":"2026-10-15T00:00:00Z"}')
        before = ExtractionBatchRecord(**fields)
        assert before.model_dump_json() == written_by_254 and before.turns_asked_again == ()
        assert str(before.id) == "6d19f9b1-6b59-5c5a-8115-4cc1a9dc4f32"
        assert ExtractionBatchRecord.model_validate_json(written_by_254) == before
        assert before.saved_turns == turns
        asked = ExtractionBatchRecord(**fields, turns_asked_again=(1, 3))
        assert asked.id == before.id and [turn.index for turn in asked.saved_turns] == [2]
        assert json.loads(asked.model_dump_json())["turns_asked_again"] == [1, 3]
        for wrong in ((1, 2, 3), (4,), (3, 1), (2, 2)):
            with pytest.raises(ValidationError):
                ExtractionBatchRecord(**fields, turns_asked_again=wrong)
        # An index names one turn: turns are numbered 1, 2, 3 and on, in order.
        for order in ((2, 1, 3), (1, 1, 3), (1, 2, 4)):
            renumbered = tuple(turn.model_copy(update={"index": index}) for turn, index in zip(turns, order))
            with pytest.raises(ValidationError):
                ExtractionBatchRecord(**{**fields, "turns": renumbered})


class TestIsolationAndLiveness:
    async def test_owners_and_scopes_never_share_a_batch(self, config, user):
        from prme.types import Scope

        model = ScriptedBatches()
        other = user + "-other"
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            for minutes, (owner, scope, text) in enumerate([
                    (user, Scope.PERSONAL, "Mine one."), (other, Scope.PERSONAL, "Theirs one."),
                    (user, Scope.PROJECT, "Project one."), (user, Scope.PERSONAL, "Mine two."),
                    (other, Scope.PERSONAL, "Theirs two."), (user, Scope.PROJECT, "Project two.")]):
                await engine.ingest(text, user_id=owner, session_id="shared", scope=scope,
                                    event_time=T0 + timedelta(minutes=minutes))
            assert (await engine.process_extractions(user_id=user)).processed == 4
            assert (await engine.process_extractions(user_id=other)).processed == 2
        assert sorted(model.contents()) == [["Mine one.", "Mine two."], ["Project one.", "Project two."],
                                            ["Theirs one.", "Theirs two."]]

    async def test_a_flush_left_behind_another_workers_job_arms_the_next(self, config, user):
        from prme.models import Event
        from prme.types import Scope

        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            pipeline = install(engine, model, flush_seconds=0.05)
            store, work = engine._event_store, engine._event_store.extraction_work
            events = [Event(content=text, user_id=user, role="user", session_id="s1",
                            event_time=T0 + timedelta(minutes=n)) for n, text in enumerate(["One.", "Two."])]
            for event in events:
                await store.append(event, defer_extraction=True)
            held = await work.claim(user_id=user, event_id=str(events[0].id), lease_seconds=60)
            await pipeline._flush(user, Scope.PERSONAL)
            assert model.calls == [] and (user, Scope.PERSONAL) in pipeline._flush_timers
            # The other worker gives its job back; the armed flush extracts both turns.
            assert await work.fail(held, error="Released", retry_after=0)
            await asyncio.sleep(0.1)
            await drain(pipeline)
            assert model.contents() == [["One.", "Two."]]
            assert await statuses(engine, user, [str(event.id) for event in events]) == ["complete"] * 2

    async def test_messages_saved_before_a_failed_one_are_still_extracted(self, config, user, monkeypatch):
        model = ScriptedBatches()
        async with MemoryEngine.open(batched(config)) as engine:
            pipeline = install(engine, model, flush_seconds=0.05)
            append, appended = pipeline._append_event, []

            async def fail_the_second(*args, **kwargs):
                appended.append(args[0])
                if len(appended) == 2:
                    raise RuntimeError("injected append failure")
                return await append(*args, **kwargs)

            monkeypatch.setattr(pipeline, "_append_event", fail_the_second)
            with pytest.raises(RuntimeError):
                await engine.ingest_batch([message("One."), message("Two.", minutes=1)], user_id=user,
                                          session_id="s1", wait_for_extraction=True)
            await asyncio.sleep(0.1)
            await drain(pipeline)
        assert model.contents() == [["One."]]

    async def test_a_batch_that_times_out_is_asked_again_in_halves(self, config, user):
        model = ScriptedBatches(fail=lambda turns: "TimeoutError" if len(turns) > 1 else None)
        texts = ["Caroline paints sunsets over the lake.", "Two.", "Three."]
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)
            event_ids = await engine.ingest_batch([message(text, minutes=n) for n, text in enumerate(texts)],
                                                  user_id=user, session_id="s1", wait_for_extraction=True)
            assert await statuses(engine, user, event_ids) == ["complete"] * 3
        assert model.contents() == [texts, texts[:1], texts[1:], ["Two."], ["Three."]]

    async def test_a_provider_that_does_not_answer_stops_the_split(self, config, user):
        model = ScriptedBatches(fail=lambda turns: "TimeoutError")
        texts = ["One.", "Two.", "Three.", "Four."]
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)._retry_delays = ()
            with pytest.raises(ExtractionError) as failure:
                await engine.ingest_batch([message(text, minutes=n) for n, text in enumerate(texts)],
                                          user_id=user, session_id="s1", wait_for_extraction=True)
            assert failure.value.reason_code == "TimeoutError"
            # A single turn that times out stops the split: three requests, not seven.
            assert model.contents() == [texts, texts[:2], texts[:1]]

    async def test_a_waiting_flush_that_fails_still_arms_the_next(self, config, user):
        model = ScriptedBatches(fail=lambda turns: "ValidationError" if turns[0].content == "Broken." else None)
        async with MemoryEngine.open(batched(config)) as engine:
            pipeline = install(engine, model, flush_seconds=0.05)
            pipeline._retry_delays = ()
            with pytest.raises(ExtractionError):
                await engine.ingest_batch([message("Broken."), message("Two.", minutes=1)], user_id=user,
                                          session_id="s1", wait_for_extraction=True)
            event_ids = [str(event.id) for event in sorted(await engine.get_events(user), key=lambda e: e.event_time)]
            # The first turn failed for good; the flush armed after it publishes the second.
            await asyncio.sleep(0.1)
            await drain(pipeline)
            assert await statuses(engine, user, event_ids) == ["failed", "complete"]
        assert model.contents() == [["Broken.", "Two."], ["Broken."], ["Two."]]

    async def test_the_senders_half_failing_fails_it_and_keeps_the_other_half(self, config, user):
        def broken(turns):
            return "ValidationError" if any(turn.content == "Broken." for turn in turns) else None

        model = ScriptedBatches(fail=broken)
        texts = ["Broken.", "Two.", "Caroline paints sunsets over the lake."]
        async with MemoryEngine.open(batched(config)) as engine:
            install(engine, model)._retry_delays = ()
            with pytest.raises(ExtractionError) as failure:
                await engine.ingest_batch([message(text, minutes=n) for n, text in enumerate(texts)],
                                          user_id=user, session_id="s1", wait_for_extraction=True)
            event_ids = [str(event.id) for event in sorted(await engine.get_events(user), key=lambda e: e.event_time)]
            assert failure.value.event_id == event_ids[0]
            assert model.contents() == [texts, ["Broken."], texts[1:]]
            # The other half's records are saved, and its turns wait behind the failed one.
            assert await statuses(engine, user, event_ids) == ["failed", "pending", "pending"]
            assert all([await engine.get_extraction(event_id, user_id=user) for event_id in event_ids[1:]])
            model.fail = None
            await engine.retry_extraction(event_ids[0], user_id=user)
            assert (await engine.process_extractions(user_id=user)).processed == 3
            assert model.contents()[3:] == [["Broken."]]
            assert await claims_by_event(engine, user) == {event_ids[2]: texts[2]}
