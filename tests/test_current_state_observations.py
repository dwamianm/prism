"""Current-state questions get the newest observations (dwamianm/prism#387).

Retrieval ranks by text match, so a camera question about now ("How many
fingers is the man holding up?") found older frames that used its words and
none of the newest frames. ``packing.current_state_observations`` (on in
PRMEConfig by the owner's decision of 2026-11-02, off in a PackingConfig built
in code) recognizes current-state questions, adds the newest observations of
the most recent observation session after the top result, and stops reading
such a question as an aggregation. These tests cover the detector, the
candidate stage, the packing reservation, the context type, the receipt rule
and the defaults; the engine-level tests over observed photos are in
``tests/test_vision_observe.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from prme import PRMEConfig
from prme.models.nodes import MemoryNode
from prme.retrieval.config import PackingConfig, default_packing_config
from prme.retrieval.context_formatter import _detect_context_type, format_for_llm
from prme.retrieval.current_state import (
    CURRENT_STATE_OBSERVATION_COUNT,
    CURRENT_STATE_PATH,
    CURRENT_STATE_SCORE_STEP,
    add_current_state_observations,
    latest_session_observations,
)
from prme.retrieval.models import QueryAnalysis, RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.query_analysis import is_current_state_question
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.config import default_scoring_weights
from prme.types import NodeType, Scope

START = datetime(2026, 11, 2, 20, 31, tzinfo=timezone.utc)

CURRENT_STATE_QUESTIONS = [
    "How many fingers is the man holding up?",
    "How many fingers is he holding up?",
    "What is he holding now?",
    "What is on the desk right now?",
    "What is the man currently wearing?",
    "What was the last thing the camera saw?",
    "What did the camera see last?",
    "Describe the latest frame.",
    "What is the most recent observation?",
    "Is the light on now?",
    "What's the man holding?",
    "How many people are sitting at the table?",
    "What is she doing?",
    "Who is speaking at the moment?",
]

OTHER_QUESTIONS = [
    "How many times did he hold up four fingers?",
    "How many fingers did he hold up before the fist?",
    "What was the first thing he held up?",
    "What did I eat last week?",
    "What did we discuss in the last three days?",
    "Where did we go on vacation last summer?",
    "What did I order last time?",
    "What is my last name?",
    "When did I last see Bob?",
    "How long have I lived here now?",
    "How many days ago did I start the job?",
    "What have I told you so far?",
    "List all the cities I visited.",
    "What is the total I spent?",
    "How often do I run?",
    "Can you remind me what you recommended in our previous chat?",
    "What is the capital of France?",
    "How many books did I read this year?",
    "What is my favorite color?",
    "What is the morning meeting about?",
    "Is the wedding ring gold?",
    "What inspired Caroline's painting for the art show?",
]


@pytest.mark.parametrize("question", CURRENT_STATE_QUESTIONS)
def test_questions_about_now_are_current_state_questions(question):
    assert is_current_state_question(question)


@pytest.mark.parametrize("question", OTHER_QUESTIONS)
def test_history_count_and_ordinary_questions_are_not(question):
    assert not is_current_state_question(question)


# --- defaults -----------------------------------------------------------------


def test_on_in_prmeconfig_off_in_code_and_omitted_when_off(monkeypatch):
    monkeypatch.delenv("PRME_PACKING__CURRENT_STATE_OBSERVATIONS", raising=False)
    assert PRMEConfig().packing.current_state_observations is True
    assert default_packing_config().current_state_observations is True
    # A PackingConfig built in code keeps the field default, so stored receipts
    # and configurations, which omit it, keep their meaning and their bytes.
    assert PackingConfig().current_state_observations is False
    assert "current_state_observations" not in PackingConfig().model_dump()
    assert PRMEConfig().packing.model_dump()["current_state_observations"] is True
    monkeypatch.setenv("PRME_PACKING__CURRENT_STATE_OBSERVATIONS", "false")
    restored = PRMEConfig()
    assert restored.packing.current_state_observations is False
    assert "current_state_observations" not in restored.model_dump()["packing"]


# --- the candidate stage --------------------------------------------------------


def node(number: int, text: str, *, seconds: float = 0, session: str | None = "camera:1",
         modality: str | None = "photo", scope: Scope = Scope.PERSONAL) -> MemoryNode:
    at = START + timedelta(seconds=seconds)
    metadata = {"modality": modality} if modality else None
    return MemoryNode(id=UUID(int=number), user_id="u", content=text, node_type=NodeType.NOTE,
                      scope=scope, session_id=session, event_time=at, created_at=at, updated_at=at,
                      last_reinforced_at=at, metadata=metadata)


def scored_pool(*nodes: MemoryNode) -> list[RetrievalCandidate]:
    """Rank-fused candidates on the lexical channel, strongest first."""
    candidates = [
        RetrievalCandidate(node=item, paths=["VECTOR", "LEXICAL"], path_count=2,
                           semantic_score=0.9 - index / 100, lexical_score=1.0 - index / 10)
        for index, item in enumerate(nodes)
    ]
    scored, _ = score_and_rank(candidates, default_scoring_weights(), now=START + timedelta(minutes=5))
    return scored


def observation_candidates(*nodes: MemoryNode) -> list[RetrievalCandidate]:
    return [RetrievalCandidate(node=item, paths=[CURRENT_STATE_PATH], path_count=1) for item in nodes]


FOUR = node(1, "The man holds up four fingers.", seconds=0)
OLDER = node(2, "Earlier the man held up three fingers.", seconds=-600, session="camera:0")
FIVE = node(3, "The man raises his hand with five fingers extended.", seconds=3)
FIST = node(4, "The man raises a clenched fist.", seconds=6)
LAP = node(5, "His hands rest in his lap.", seconds=9)


def test_the_newest_observations_follow_the_top_result_newest_first_and_replay():
    scored = scored_pool(OLDER, FOUR, FIVE)
    top = scored[0]
    results, changed = add_current_state_observations(scored, observation_candidates(LAP, FIST, FIVE))
    assert changed is True
    assert [item.node.id for item in results[:4]] == [top.node.id, LAP.id, FIST.id, FIVE.id]
    for place, item in enumerate(results[1:4]):
        coefficient = 1.0 - (place + 1) * CURRENT_STATE_SCORE_STEP
        assert item.composite_score == pytest.approx(top.composite_score * coefficient, rel=1e-12)
        assert item.score_provenance.replay_score() == item.composite_score
        adjustment = item.score_provenance.adjustments[-1]
        assert (adjustment.kind, adjustment.coefficient, adjustment.source_node_id) == (
            "current_observation", coefficient, top.node.id)
        assert CURRENT_STATE_PATH in item.paths
    # A frame already found keeps its own paths beside the new one.
    assert results[3].paths == ["VECTOR", "LEXICAL", CURRENT_STATE_PATH]
    # A new frame is not on any search channel; it shares the top result's relevance.
    assert results[1].paths == [CURRENT_STATE_PATH]
    assert results[1].semantic_relevance is None and results[1].context_relevance == top.semantic_score


def test_an_observation_that_is_already_the_top_result_keeps_its_place():
    scored = scored_pool(LAP, OLDER, FOUR)
    assert scored[0].node.id == LAP.id
    score = scored[0].composite_score
    results, changed = add_current_state_observations(scored, observation_candidates(LAP, FIST))
    assert changed is True
    assert [item.node.id for item in results[:2]] == [LAP.id, FIST.id]
    assert results[0].composite_score == score
    assert results[0].score_provenance.adjustments == ()


def test_nothing_changes_without_observations_or_results():
    scored = scored_pool(OLDER, FOUR)
    assert add_current_state_observations(scored, []) == (scored, False)
    assert add_current_state_observations([], observation_candidates(LAP)) == ([], False)


class FakeGraph:
    """query_nodes over a fixed list: newest created first, like the stores."""

    def __init__(self, nodes: list[MemoryNode]) -> None:
        self.nodes = nodes
        self.calls: list[dict] = []

    async def query_nodes(self, *, user_id=None, scopes=None, session_ids=None, created_before=None,
                          limit=100, **_):
        self.calls.append({"scopes": scopes, "session_ids": session_ids, "limit": limit})
        found = [
            item for item in self.nodes
            if item.user_id == user_id
            and (scopes is None or item.scope in scopes)
            and (session_ids is None or item.session_id in session_ids)
            and (created_before is None or item.created_at <= created_before)
        ]
        found.sort(key=lambda item: item.created_at, reverse=True)
        return found[:limit]


async def test_the_most_recent_session_is_the_one_with_the_newest_observation():
    chat = node(9, "I like jazz.", seconds=60, session="chat", modality=None)
    no_session = node(10, "A frame without a session.", seconds=30, session=None)
    other_scope = node(11, "A project frame.", seconds=8, session="camera:1", scope=Scope.PROJECT)
    graph = FakeGraph([OLDER, FOUR, FIVE, FIST, LAP, chat, no_session, other_scope])
    found = await latest_session_observations(
        graph, user_id="u", scopes=None, created_before=None, eligible=lambda items: items,
    )
    # Newest first; the chat note, the frame without a session and the other
    # scope's frame of the same session name are not part of it.
    assert [item.node.id for item in found] == [LAP.id, FIST.id, FIVE.id]
    assert len(found) == CURRENT_STATE_OBSERVATION_COUNT
    assert graph.calls[1]["session_ids"] == ["camera:1"] and graph.calls[1]["scopes"] == [Scope.PERSONAL]


async def test_filters_apply_before_the_session_is_chosen():
    graph = FakeGraph([OLDER, FOUR, FIVE, FIST, LAP])
    # The request may not see camera:1, so the older session is the most recent one it can see.
    found = await latest_session_observations(
        graph, user_id="u", scopes=None, created_before=None,
        eligible=lambda items: [item for item in items if item.node.session_id == "camera:0"],
    )
    assert [item.node.id for item in found] == [OLDER.id]
    # A knowledge cutoff reads only records created by then.
    found = await latest_session_observations(
        graph, user_id="u", scopes=None, created_before=START + timedelta(seconds=4),
        eligible=lambda items: items,
    )
    assert [item.node.id for item in found] == [FIVE.id, FOUR.id]


async def test_a_store_without_observations_returns_none_and_reads_once():
    chat = [node(20 + index, f"Note {index}", seconds=index, session="chat", modality=None) for index in range(5)]
    graph = FakeGraph(chat)
    assert await latest_session_observations(
        graph, user_id="u", scopes=None, created_before=None, eligible=lambda items: items) == []
    assert len(graph.calls) == 1


# --- packing and the context type -------------------------------------------------


def test_packing_reserves_the_added_observations_before_the_multi_path_tier():
    text = " ".join(["word"] * 40)
    strong = [
        RetrievalCandidate(node=node(30 + index, f"{text} {index}", seconds=index, modality=None),
                           paths=["VECTOR", "LEXICAL"], path_count=2, composite_score=0.9 - index / 100)
        for index in range(4)
    ]
    added = RetrievalCandidate(node=LAP, paths=[CURRENT_STATE_PATH], path_count=1, composite_score=0.5)
    config = default_packing_config()
    one_record = pack_context([strong[0]], config=config.model_copy(update={"token_budget": 10_000}))
    budget = one_record.tokens_used + config.overhead_tokens + 20
    bundle = pack_context([*strong, added], config=config.model_copy(update={"token_budget": budget}))
    packed = [item.node.id for group in bundle.sections.values() for item in group]
    assert packed == [LAP.id]
    # Without the path, the same record waits behind the multi-path tier.
    plain = added.model_copy(update={"paths": ["VECTOR"]})
    bundle = pack_context([*strong, plain], config=config.model_copy(update={"token_budget": budget}))
    assert LAP.id not in [item.node.id for group in bundle.sections.values() for item in group]


def test_a_current_state_question_is_not_an_aggregation():
    question = "How many fingers is the man holding up?"
    counted = QueryAnalysis(query=question, intent="factual", is_aggregation=True)
    current = QueryAnalysis(query=question, intent="factual", is_current_state=True)
    assert _detect_context_type(question, counted) == "aggregation"
    assert _detect_context_type(question, current) == "knowledge_update"
    results = [RetrievalCandidate(node=FIST, composite_score=1.0), RetrievalCandidate(node=FOUR, composite_score=0.9)]
    assert "AGGREGATION TASK" in format_for_llm(results, question, query_analysis=counted, include_profile=False)
    rendered = format_for_llm(results, question, query_analysis=current, include_profile=False)
    assert "AGGREGATION TASK" not in rendered and "chronological order" in rendered
    assert "[MOST RECENT]" in rendered.splitlines()[-1] and "clenched fist" in rendered.splitlines()[-1]
