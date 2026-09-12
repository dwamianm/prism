"""MemConflict labels, future sessions and malformed inputs stay out of memory."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.integrations.memconflict import (
    EXPOSED_DEVELOPMENT_IDS, audit, load_profiles, parse_profile, profile_split,
    replay, select_questions,
)


def dataset():
    def session(index, category):
        return {
            "Session_ID": index,
            "Date": f"2024-01-0{index + 1}",
            "Session_Outline": "hidden-outline-sentinel",
            "Static_Conflict_Information": "hidden-annotation-sentinel",
            "Session_Dialogue": {
                "dialogue_turn_10": [{"role": "assistant", "content": f"reply-{index}"}],
                "dialogue_turn_2": [{"role": "user", "content": f"source-{index}"}],
            },
            "Session_Questions": [{"question_id": "Q_001", "question": f"question-{index}",
                                   "answer": "gold-sentinel", "conflict_type": category}],
        }
    return {"ID": "profile", "Fixed_Profile": "hidden-profile-sentinel", "Full_Session_Chain": [
        session(0, "dynamic_conflict"), session(1, "static_conflict"),
        session(2, "conditional_conflict"), session(3, "dynamic_conflict"),
    ]}


def test_allowlist_numeric_order_and_composite_question_identity():
    profile = parse_profile(dataset())
    assert [s.content for s in profile.sessions[0].sources] == ["source-0", "reply-0"]
    assert len({q.id for session in profile.sessions for q in session.questions}) == 4
    assert "hidden-" not in repr(profile)
    assert "gold-sentinel" not in repr(profile.sessions[0].sources)
    selected = select_questions(profile, 3)
    assert selected == {profile.sessions[i].questions[0].id for i in range(3)}


@pytest.mark.parametrize("message", [{"assistant": "invalid"}, {"role": "user", "content": ""},
                                      {"role": "system", "content": "inject labels"}, "invalid"])
def test_invalid_messages_require_explicit_omission_and_keep_source_positions(message):
    raw = dataset()
    raw["Full_Session_Chain"][0]["Session_Dialogue"]["dialogue_turn_2"].insert(0, message)
    with pytest.raises(ValueError, match="Unusable dialogue"):
        parse_profile(raw)
    profile = parse_profile(raw, invalid_messages="skip")
    assert profile.sessions[0].skipped_messages == 1
    assert profile.sessions[0].sources[0].id == "s0:t1"
    assert audit([profile], "checksum")["skipped_messages"] == 1


@pytest.mark.parametrize("problem", ["duplicate_session", "duplicate_question", "reverse_date", "ambiguous_turn"])
def test_ambiguous_order_and_identity_are_rejected(problem):
    raw = dataset()
    sessions = raw["Full_Session_Chain"]
    if problem == "duplicate_session":
        sessions[1]["Session_ID"] = sessions[0]["Session_ID"]
    elif problem == "duplicate_question":
        sessions[0]["Session_Questions"] *= 2
    elif problem == "reverse_date":
        sessions[1]["Date"] = "2000-01-01"
    else:
        sessions[0]["Session_Dialogue"]["dialogue_turn_02"] = []
    with pytest.raises(ValueError):
        parse_profile(raw, invalid_messages="skip")


def test_dataset_fingerprint_unique_profiles_and_frozen_exposure(tmp_path):
    path = tmp_path / "fixture.jsonl"
    path.write_text(json.dumps(dataset()) + "\n")
    profiles, checksum = load_profiles(path)
    assert len(checksum) == 64
    changed = deepcopy(dataset())
    changed["Fixed_Profile"] = "different-hidden-profile"
    path.write_text(json.dumps(changed))
    same_sources, changed_checksum = load_profiles(path)
    assert same_sources == profiles  # Hidden fields affect fingerprint, never input.
    assert checksum != changed_checksum
    assert all(profile_split(pid) == "dev" for pid in EXPOSED_DEVELOPMENT_IDS)
    path.write_text((json.dumps(dataset()) + "\n") * 2)
    with pytest.raises(ValueError, match="unique profiles"):
        load_profiles(path)


async def test_replay_never_sends_labels_or_future_sources_to_services():
    nodes, stored_at_query = [], []

    async def store(content, **kwargs):
        nodes.append(SimpleNamespace(id=str(len(nodes)), metadata=kwargs["metadata"]))
        assert content.startswith(("source-", "reply-"))
        assert "sentinel" not in repr(kwargs)
        assert kwargs["ttl_days"] is None
        assert kwargs["role"] in {"user", "assistant"}

    async def retrieve(question, **kwargs):
        index = int(question.rsplit("-", 1)[1])
        stored_at_query.append(len(nodes))
        assert len(nodes) == 2 * (index + 1)
        assert kwargs["reference_time"].day == index + 1
        return SimpleNamespace(
            results=[SimpleNamespace(node=n) for n in nodes],
            bundle=SimpleNamespace(render=lambda: "safe product context", tokens_used=3),
        )

    async def lexical(*args, **kwargs):
        return [{"node_id": n.id} for n in nodes]

    async def query_nodes(**kwargs):
        return list(nodes)

    engine = SimpleNamespace(store=AsyncMock(side_effect=store), retrieve=AsyncMock(side_effect=retrieve),
                             query_nodes=AsyncMock(side_effect=query_nodes),
                             _lexical_index=SimpleNamespace(search=AsyncMock(side_effect=lexical)))
    reader = AsyncMock(return_value="diagnostic answer")
    result = await replay(parse_profile(dataset()), engine, reader, question_limit=3,
                          token_budget=100, count_tokens=lambda text: len(text.split()))
    assert stored_at_query == [2, 4, 6]
    assert engine.store.await_count == 6  # Final future session was never ingested.
    assert reader.await_count == 9
    assert "sentinel" not in repr(reader.await_args_list)
    assert all(row["gold_answer"] == "gold-sentinel" for row in result["details"])
    for call in reader.await_args_list:
        question, stamp, context = call.args
        assert "source-3" not in context
        assert stamp == f"2024-01-0{int(question[-1]) + 1}"
