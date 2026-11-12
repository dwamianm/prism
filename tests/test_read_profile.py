"""The robot read profile (``read_profile="robot"``) and the default result limit (``result_limit``).

The profile sets existing settings only, each unless the configuration sets it itself: fewer vector, lexical and
graph candidates, two graph hops, and a default result limit of 40. Off, nothing changes, the configuration dump
included. On, a read still replays from its stored receipt.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.config import ROBOT_READ_PACKING, ROBOT_READ_RESULT_LIMIT
from prme.retrieval.config import PackingConfig

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)
USER = "robot-1"


@pytest.fixture(autouse=True)
def _no_profile_environment(monkeypatch):
    # These tests set the profile themselves, so a PRME_READ_PROFILE in the environment must not change them.
    monkeypatch.delenv("PRME_READ_PROFILE", raising=False)
    monkeypatch.delenv("PRME_RESULT_LIMIT", raising=False)


def settings(**fields) -> PRMEConfig:
    return PRMEConfig(_env_file=None, **fields)


def profile_options(config: PRMEConfig) -> dict:
    return {**{name: getattr(config.packing, name) for name in ROBOT_READ_PACKING},
            "result_limit": config.result_limit}


ROBOT = {**ROBOT_READ_PACKING, "result_limit": ROBOT_READ_RESULT_LIMIT}


def test_the_default_changes_nothing():
    assert settings(read_profile="default").model_dump(mode="json") == settings().model_dump(mode="json")
    defaults = PackingConfig()
    assert profile_options(settings()) == {
        **{name: getattr(defaults, name) for name in ROBOT_READ_PACKING}, "result_limit": None}


def test_the_new_fields_are_left_out_of_a_default_dump():
    dump = settings().model_dump(mode="json")
    assert "read_profile" not in dump and "result_limit" not in dump


def test_robot_sets_each_option():
    assert profile_options(settings(read_profile="robot")) == ROBOT
    assert settings(read_profile="robot").model_dump(mode="json")["read_profile"] == "robot"


def test_robot_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("PRME_READ_PROFILE", "robot")
    assert profile_options(settings()) == ROBOT


@pytest.mark.parametrize("fields, kept", [
    ({"result_limit": 25}, {"result_limit": 25}),
    ({"packing": {"vector_k": 200}}, {"vector_k": 200}),
    ({"packing": {"graph_max_hops": 3}}, {"graph_max_hops": 3}),
])
def test_an_option_the_configuration_sets_itself_is_kept(fields, kept):
    assert profile_options(settings(read_profile="robot", **fields)) == {**ROBOT, **kept}


def test_robot_does_not_change_the_memory_mode():
    on = settings(read_profile="robot", memory_mode="address_first")
    assert on.memory_mode == "address_first" and on.enable_belief_state and profile_options(on) == ROBOT


def test_an_unknown_profile_and_a_negative_limit_are_refused():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        settings(read_profile="sideways")
    with pytest.raises(ValidationError):
        settings(result_limit=-1)


# --- reads ----------------------------------------------------------------------------------------------------------


class HashEmbedding:
    model_name = "read-profile-test"
    model_version = "1"
    dimension = 64

    async def embed(self, texts):
        return [[hashlib.sha256(text.encode()).digest()[i % 32] / 255 for i in range(self.dimension)]
                for text in texts]


@pytest.fixture
def make_config(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: HashEmbedding())

    def build(name: str, **fields) -> PRMEConfig:
        folder = tmp_path / name
        (folder / "lexical").mkdir(parents=True)
        return settings(db_path=str(folder / "memory.duckdb"), lexical_path=str(folder / "lexical"),
                        vector_path=str(folder / "vectors.usearch"), organizer={"opportunistic_enabled": False},
                        **fields)
    return build


async def stored(engine: MemoryEngine) -> None:
    for number in range(60):
        await engine.store(f"The box number {number} sits on shelf {number % 7} in the store room.",
                           user_id=USER, event_time=NOW - timedelta(minutes=60 - number))


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_mode", ["classic", "address_first"])
async def test_a_robot_read_returns_at_most_the_limit_and_replays(make_config, memory_mode):
    async with MemoryEngine.open(make_config("on", read_profile="robot", memory_mode=memory_mode)) as engine:
        await stored(engine)
        response = await engine.retrieve("Which box sits on shelf 3?", user_id=USER, reference_time=NOW)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
    assert 0 < len(response.results) <= ROBOT_READ_RESULT_LIMIT
    assert receipt.result_limit == ROBOT_READ_RESULT_LIMIT
    assert receipt.packing.graph_max_hops == 2 and receipt.packing.vector_k == 60
    assert receipt.replay_ranking() == tuple(candidate.node.id for candidate in response.results)


@pytest.mark.asyncio
async def test_a_limit_the_caller_passes_wins(make_config):
    async with MemoryEngine.open(make_config("on", result_limit=5)) as engine:
        await stored(engine)
        default = await engine.retrieve("Which box sits on shelf 3?", user_id=USER, reference_time=NOW)
        wider = await engine.retrieve("Which box sits on shelf 3?", user_id=USER, reference_time=NOW, limit=12)
    assert len(default.results) == 5 and len(wider.results) == 12
    assert [c.node.id for c in wider.results][:5] == [c.node.id for c in default.results]


@pytest.mark.asyncio
async def test_the_default_result_limit_matches_the_same_limit_passed_per_read(make_config):
    question = "Which box sits on shelf 3?"
    async with MemoryEngine.open(make_config("config", result_limit=7)) as engine:
        await stored(engine)
        from_config = await engine.retrieve(question, user_id=USER, reference_time=NOW)
    async with MemoryEngine.open(make_config("call")) as engine:
        await stored(engine)
        passed = await engine.retrieve(question, user_id=USER, reference_time=NOW, limit=7)
    assert [(c.node.content, c.composite_score) for c in from_config.results] == [
        (c.node.content, c.composite_score) for c in passed.results]
    assert from_config.bundle.render() == passed.bundle.render()
