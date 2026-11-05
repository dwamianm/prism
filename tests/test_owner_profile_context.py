"""The owner's profile for a first-person question (dwamianm/prism#398).

"Do you remember the names of my dependents?" names no entity and its words
match no claim, so retrieval missed the records that answer it. With
``enable_owner_profile_context`` (off by default) a first-person question from
a named owner gets the owner's knowledge profile right after the top result,
through a replayable ``owner_profile`` adjustment that the receipt records,
and the owner's profile is kept fresh after ingest and at the end of a
session. These tests cover the detector, the placement, the unchanged
retrievals, the receipt replay and the rebuilds, with no model call.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.client import config_from_directory
from prme.ingestion.owner_name import OWNER_NAME_KEY, owner_name_marker
from prme.models.nodes import MemoryNode
from prme.models.relevance import RetrievalReceipt
from prme.organizer.maintenance import MaintenanceRunner
from prme.retrieval.models import RetrievalCandidate, ScoreAdjustment
from prme.retrieval.owner_profile import (
    OWNER_PROFILE_PATH,
    OWNER_PROFILE_SCORE_STEP,
    add_owner_profile,
    profile_entity_name,
)
from prme.retrieval.query_analysis import is_first_person_question
from prme.types import NodeType, Scope
from tests import conftest
from tests.test_concurrency import MockExtractionProvider
from tests.test_durable_ingestion import MockEmbeddingProvider

USER = "member"
ASKED_AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
DEPENDENTS = "Do you remember the names of my dependents?"
THIRD_PERSON = "What does Maya's husband do?"
MEMORIES = [
    "Maya's husband is Theo.",
    "Maya's child is Nico.",
    "Maya was diagnosed with breast cancer in October.",
    "The plan covers two dental cleanings a year.",
    "Do you remember the names of my dependents?",
]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: MockExtractionProvider())
    monkeypatch.setenv("PRME_ORGANIZER_OPPORTUNISTIC_ENABLED", "false")
    monkeypatch.setenv("PRME_EXTRACTION_BATCH_TARGET_TOKENS", "0")
    for name in ("PRME_DATABASE_URL", "PRME_PACK_PATH", "PRME_ENCRYPTION_ENABLED", "PRME_ENCRYPTION_KEY"):
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


def config(pack: Path, on: bool) -> PRMEConfig:
    return config_from_directory(str(pack)).model_copy(update={"enable_owner_profile_context": on})


async def build(pack: Path, *, profile: bool = True, marker: bool = False) -> None:
    """Store the member's memories and, with *profile*, build Maya's profile."""
    async with MemoryEngine.open(config(pack, False)) as engine:
        for text in MEMORIES:
            await engine.store(text, user_id=USER)
        if marker:
            await mark_owner(engine, "Maya")
        if profile:
            assert await engine.consolidate_knowledge(user_id=USER, entity_names=["Maya"]) == 1


async def mark_owner(engine: MemoryEngine, name: str, scope: Scope = Scope.PERSONAL) -> None:
    """The person entity a user turn creates when it gives the owner's name."""
    await engine._graph_store.create_node(MemoryNode(
        user_id=USER, node_type=NodeType.ENTITY, scope=scope, content=name,
        metadata={OWNER_NAME_KEY: owner_name_marker("e1", "speaker")},
    ))


def receipt_bytes(receipt: RetrievalReceipt) -> dict:
    """A receipt without its request ID, which every retrieval draws anew."""
    data = receipt.model_dump(mode="json")
    data.pop("request_id")
    return data


async def ask(pack: Path, on: bool, question: str, **options):
    async with MemoryEngine.open(config(pack, on)) as engine:
        response = await engine.retrieve(question, user_id=USER, reference_time=ASKED_AT, **options)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
    return response, receipt


def is_profile(candidate: RetrievalCandidate) -> bool:
    return profile_entity_name(candidate.node) == "Maya"


# --- the question ---------------------------------------------------------------


@pytest.mark.parametrize("question", [
    DEPENDENTS,
    "What did I tell you about my health?",
    "I'm wondering what you know about me.",
    "Is this mine?",
    "Remind myself what the deductible is.",
])
def test_first_person_questions(question):
    assert is_first_person_question(question)


@pytest.mark.parametrize("question", [
    THIRD_PERSON,
    "What are the names of Maya's dependents?",
    "Did the team in Myanmar meet the deadline?",
    "What is the capital of France?",
    "Is it time to submit the claim?",
])
def test_other_questions(question):
    # Words that only contain the pronouns ("Myanmar", "time") do not count.
    assert not is_first_person_question(question)


def test_off_by_default_and_on_from_the_environment(monkeypatch):
    conftest.product_defaults(monkeypatch)
    assert PRMEConfig().enable_owner_profile_context is False
    monkeypatch.setenv("PRME_ENABLE_OWNER_PROFILE_CONTEXT", "true")
    assert PRMEConfig().enable_owner_profile_context is True


# --- the placement --------------------------------------------------------------


def candidate(number: int, score: float, *, node_type: NodeType = NodeType.NOTE, profile: bool = False):
    metadata = {"entity_profile": True, "entity_name": "Maya"} if profile else None
    node = MemoryNode(id=UUID(int=number), user_id=USER, node_type=node_type, content=f"record {number}",
                      metadata=metadata)
    return RetrievalCandidate(node=node, paths=["VECTOR"], path_count=1, composite_score=score)


def test_the_profile_follows_the_top_result():
    scored = [candidate(1, 0.9), candidate(2, 0.8), candidate(3, 0.7)]
    profile = candidate(9, 0.0, node_type=NodeType.SUMMARY, profile=True)
    results, changed, ranking = add_owner_profile(scored, profile.model_copy(update={"paths": [OWNER_PROFILE_PATH]}))
    assert (changed, ranking) == (True, True)
    assert [result.node.id.int for result in results] == [1, 9, 2, 3]
    assert results[1].paths == [OWNER_PROFILE_PATH]
    assert results[1].composite_score == pytest.approx(0.9 * (1 - OWNER_PROFILE_SCORE_STEP))


def test_a_ranked_profile_is_raised_and_a_top_profile_is_left_alone():
    profile = candidate(9, 0.5, node_type=NodeType.SUMMARY, profile=True)
    scored = [candidate(1, 0.9), candidate(2, 0.8), profile]
    results, changed, ranking = add_owner_profile(scored, profile)
    assert (changed, ranking) == (True, True)
    assert [result.node.id.int for result in results] == [1, 9, 2]
    assert results[1].paths == ["VECTOR", OWNER_PROFILE_PATH]
    top = candidate(9, 0.95, node_type=NodeType.SUMMARY, profile=True)
    scored = [top, candidate(1, 0.9)]
    assert add_owner_profile(scored, top) == (scored, False, False)
    assert add_owner_profile([], top) == ([], False, False)


def test_the_adjustment_is_bounded():
    with pytest.raises(ValueError):
        ScoreAdjustment(kind="owner_profile", coefficient=1.5, source_node_id=UUID(int=1))


# --- retrieval --------------------------------------------------------------------


async def test_a_named_owner_gets_the_profile_after_the_top_result(tmp_path):
    await build(tmp_path / "pack")
    off, off_receipt = await ask(tmp_path / "pack", False, DEPENDENTS, speaker="Maya")
    on, receipt = await ask(tmp_path / "pack", True, DEPENDENTS, speaker="Maya")
    assert not any(OWNER_PROFILE_PATH in result.paths for result in off.results)
    assert "owner_profile_context" not in off_receipt.execution.features
    assert is_profile(on.results[1]) and OWNER_PROFILE_PATH in on.results[1].paths
    assert on.results[0].node.id == off.results[0].node.id
    packed = [candidate.node.id for section in on.bundle.sections.values() for candidate in section]
    assert on.results[1].node.id in packed
    assert "Theo" in on.bundle.render() and "Nico" in on.bundle.render()
    # The receipt records the feature and replays the scores and the order.
    assert receipt.execution.features["owner_profile_context"] == {"version": 1}
    assert receipt.replay_ranking() == tuple(result.node.id for result in on.results)
    assert any(adjustment.kind == "owner_profile"
               for provenance in receipt.score_provenance.values() for adjustment in provenance.adjustments)
    # A receipt that drops the feature does not validate.
    data = receipt.model_dump(mode="json")
    data["execution"]["features"].pop("owner_profile_context")
    with pytest.raises(ValueError, match="owner_profile_context"):
        RetrievalReceipt.model_validate(data)
    # The speaker's name is matched without regard to case.
    lower, _ = await ask(tmp_path / "pack", True, DEPENDENTS, speaker="maya")
    assert is_profile(lower.results[1])


async def test_the_owner_name_marker_names_the_owner_without_a_speaker(tmp_path):
    await build(tmp_path / "pack", marker=True)
    on, receipt = await ask(tmp_path / "pack", True, DEPENDENTS)
    assert is_profile(on.results[1])
    assert "owner_profile_context" in receipt.execution.features
    # The caller's speaker wins over the marker: nobody named Sam has a profile.
    other, other_receipt = await ask(tmp_path / "pack", True, DEPENDENTS, speaker="Sam")
    assert not any(OWNER_PROFILE_PATH in result.paths for result in other.results)
    assert "owner_profile_context" not in other_receipt.execution.features


@pytest.mark.parametrize("question, speaker, profile", [
    (THIRD_PERSON, "Maya", True),
    (DEPENDENTS, None, True),
    (DEPENDENTS, "Maya", False),
])
async def test_other_retrievals_are_unchanged_byte_for_byte(tmp_path, question, speaker, profile):
    # A third-person question, an unnamed owner and an owner without a profile.
    await build(tmp_path / "pack", profile=profile)
    off, off_receipt = await ask(tmp_path / "pack", False, question, speaker=speaker)
    on, receipt = await ask(tmp_path / "pack", True, question, speaker=speaker)
    assert [result.node.id for result in on.results] == [result.node.id for result in off.results]
    assert on.bundle.render() == off.bundle.render()
    assert receipt_bytes(receipt) == receipt_bytes(off_receipt)


async def test_filters_apply_to_the_profile(tmp_path):
    await build(tmp_path / "pack")
    on, receipt = await ask(tmp_path / "pack", True, DEPENDENTS, speaker="Maya",
                            exclude_node_types=[NodeType.SUMMARY])
    assert not any(result.node.node_type == NodeType.SUMMARY for result in on.results)
    assert "owner_profile_context" not in receipt.execution.features
    project, _ = await ask(tmp_path / "pack", True, DEPENDENTS, speaker="Maya", scope=Scope.PROJECT)
    assert not any(OWNER_PROFILE_PATH in result.paths for result in project.results)


def test_speaker_is_validated():
    from prme.api.models import RetrieveRequest

    assert RetrieveRequest(query="q", speaker=" Maya ").speaker == "Maya"
    with pytest.raises(ValueError):
        RetrieveRequest(query="q", speaker="   ")


# --- freshness ----------------------------------------------------------------------


async def profiles(engine: MemoryEngine) -> list[MemoryNode]:
    nodes = await engine._graph_store.query_nodes(user_id=USER, node_type=NodeType.SUMMARY, limit=50)
    return [node for node in nodes if profile_entity_name(node) == "Maya"]


async def profile_text(engine: MemoryEngine) -> list[str]:
    return [node.content for node in await profiles(engine)]


async def test_the_opportunistic_pass_rebuilds_a_profile_after_ingest(tmp_path):
    await build(tmp_path / "pack")
    async with MemoryEngine.open(config(tmp_path / "pack", True)) as engine:
        organizer = engine._config.organizer.model_copy(update={"opportunistic_budget_ms": 60_000})
        runner = MaintenanceRunner(engine, organizer)
        # Nothing newer mentions Maya yet.
        assert (await runner._run_maintenance(user_id=USER)).profiles_rebuilt == []
        await engine.ingest("Maya started a new job at the library.", user_id=USER, wait_for_extraction=True)
        result = await runner._run_maintenance(user_id=USER)
        assert result.profiles_rebuilt == [{"entity_name": "Maya", "scope": "personal", "published": True}]
        [text] = await profile_text(engine)
        assert "new job at the library" in text
        # One rebuild per person: the next pass finds it fresh.
        assert (await runner._run_maintenance(user_id=USER)).profiles_rebuilt == []
        # Another owner's pass never touches Maya's profile.
        assert (await runner._run_maintenance(user_id="someone-else")).profiles_rebuilt == []


async def test_the_opportunistic_pass_leaves_profiles_alone_with_the_option_off(tmp_path):
    await build(tmp_path / "pack")
    async with MemoryEngine.open(config(tmp_path / "pack", False)) as engine:
        organizer = engine._config.organizer.model_copy(update={"opportunistic_budget_ms": 60_000})
        await engine.store("Maya started a new job at the library.", user_id=USER)
        result = await MaintenanceRunner(engine, organizer)._run_maintenance(user_id=USER)
        assert result.profiles_rebuilt == []
        [text] = await profile_text(engine)
        assert "library" not in text


async def test_end_session_builds_and_rebuilds_the_owners_profile(tmp_path):
    await build(tmp_path / "pack", profile=False, marker=True)
    async with MemoryEngine.open(config(tmp_path / "pack", True)) as engine:
        ended = await engine.end_session(user_id=USER)
        job = ended.per_job["owner_profile_refresh"]
        assert "owner_profile_refresh" in ended.jobs_run
        assert job.details["rebuilt"] == [{"entity_name": "Maya", "scope": "personal", "published": True}]
        assert len(await profile_text(engine)) == 1
        # Fresh: nothing to rebuild.
        again = await engine.end_session(user_id=USER)
        assert again.per_job["owner_profile_refresh"].details["rebuilt"] == []
        await engine.store("Maya moved to Denver.", user_id=USER)
        moved = await engine.end_session(user_id=USER)
        assert moved.per_job["owner_profile_refresh"].nodes_modified == 1
        # The rebuild counted the new record. consolidate_knowledge() keeps a
        # profile's oldest sources within its token budget, so a full profile
        # need not show it.
        [profile] = await profiles(engine)
        assert profile.metadata["source_count_available"] == 5
        # A profile that left a source out of its budget would be rebuilt with
        # the same text, so a newer record does not rebuild it.
        assert profile.metadata["source_count_included"] < 5
        await engine.store("Maya likes hiking.", user_id=USER)
        full = await engine.end_session(user_id=USER)
        assert full.per_job["owner_profile_refresh"].details["rebuilt"] == []
        # A source a correction retired does rebuild it, so the old fact leaves.
        [profile] = await profiles(engine)
        retired = profile.metadata["source_node_ids"][0]
        await engine.archive(retired, user_id=USER)
        corrected = await engine.end_session(user_id=USER)
        assert corrected.per_job["owner_profile_refresh"].nodes_modified == 1
        [profile] = await profiles(engine)
        assert retired not in profile.metadata["source_node_ids"]
    async with MemoryEngine.open(config(tmp_path / "pack", False)) as engine:
        assert "owner_profile_refresh" not in (await engine.end_session(user_id=USER)).per_job


async def test_end_session_refresh_failure_leaves_the_session_ending(tmp_path, monkeypatch):
    await build(tmp_path / "pack", profile=False, marker=True)

    async def broken(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr("prme.organizer.owner_profiles.carried_owner_entity", broken)
    async with MemoryEngine.open(config(tmp_path / "pack", True)) as engine:
        ended = await engine.end_session(user_id=USER)
        assert ended.per_job["owner_profile_refresh"].errors == 1
        assert ended.per_job["promote"].errors == 0


async def test_end_session_starts_no_build_after_its_budget(tmp_path, monkeypatch):
    await build(tmp_path / "pack", profile=False, marker=True)
    monkeypatch.setattr("prme.storage.engine.OWNER_PROFILE_REFRESH_BUDGET_S", 0.0)
    async with MemoryEngine.open(config(tmp_path / "pack", True)) as engine:
        ended = await engine.end_session(user_id=USER)
        assert ended.per_job["owner_profile_refresh"].details["rebuilt"] == []
        assert await profile_text(engine) == []


async def test_speaker_turns_are_profile_sources_only_on_request(tmp_path):
    async with MemoryEngine.open(config(tmp_path / "pack", False)) as engine:
        for text in ("My husband is Theo.", "My child is Nico."):
            await engine.store(text, user_id=USER, speaker="Maya")
        await engine.store("Maya's plan renews in January.", user_id=USER)
        # An explicit call matches the name in the text, as before: one record.
        assert await engine.consolidate_knowledge(user_id=USER, entity_names=["Maya"]) == 0
        assert await engine.consolidate_knowledge(
            user_id=USER, entity_names=["Maya"], include_speaker_turns=True,
        ) == 1
        [text] = await profile_text(engine)
        assert "My husband is Theo." in text and "renews in January" in text


async def test_several_owner_names_in_a_scope_name_no_owner(tmp_path):
    # Two people write under one account; the newest name need not be the asker.
    await build(tmp_path / "pack", marker=True)
    async with MemoryEngine.open(config(tmp_path / "pack", False)) as engine:
        await mark_owner(engine, "Sam")
    unnamed, receipt = await ask(tmp_path / "pack", True, DEPENDENTS)
    assert not any(OWNER_PROFILE_PATH in result.paths for result in unnamed.results)
    assert "owner_profile_context" not in receipt.execution.features
    named, _ = await ask(tmp_path / "pack", True, DEPENDENTS, speaker="Maya")
    assert is_profile(named.results[1])


async def test_end_session_without_a_named_owner_builds_nothing(tmp_path):
    await build(tmp_path / "pack", profile=False)
    async with MemoryEngine.open(config(tmp_path / "pack", True)) as engine:
        ended = await engine.end_session(user_id=USER)
        assert ended.per_job["owner_profile_refresh"].details["rebuilt"] == []
        assert await profile_text(engine) == []
