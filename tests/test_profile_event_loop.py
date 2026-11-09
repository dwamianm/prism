"""A slow profile build must not stall other coroutines on the caller's event loop (#405)."""
import asyncio
import random
import re
import threading
import time

import pytest

from prme import MemoryEngine
from prme.organizer import profiles
from prme.organizer.profiles import mentions_entity, run_profile_work
from prme.types import NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

# The issue's bound on one stretch of blocked loop time.
MAX_GAP = 0.1


async def longest_gap(build):
    """Run ``build`` while a ticker on the same loop records its longest wait."""
    gaps: list[float] = []
    done = asyncio.Event()

    async def ticker():
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    started = time.perf_counter()
    try:
        result = await build
    finally:
        done.set()
        await task
    return result, time.perf_counter() - started, max(gaps)


def measurable(elapsed):
    """Skip when the build was too quick for a blocked loop to show as a gap over MAX_GAP."""
    if elapsed <= MAX_GAP:
        pytest.skip(f"build finished in {elapsed:.3f} s, too fast to tell a blocked loop from a free one")


async def test_long_run_of_one_character_keeps_the_loop_running(config, user):
    # The tokenizer's cost grows with the square of a run of one character,
    # and each scope is built separately: one scope holds the long runs and
    # the other the short mentions that make it publish a profile.
    async with MemoryEngine.open(config) as engine:
        for _ in range(3):
            await engine.store("Aurora " + "a" * 80_000, user_id=user, scope=Scope.PERSONAL)
        await engine.store("Aurora personal note", user_id=user, scope=Scope.PERSONAL)
        await engine.store("Aurora personal plan", user_id=user, scope=Scope.PERSONAL)
        for i in range(2):
            await engine.store(f"Aurora team note {i}", user_id=user, scope=Scope.PROJECT)
        built, elapsed, gap = await longest_gap(engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"]))
        assert built == 2
        # Blocking the loop for this whole build would show as one long gap.
        measurable(elapsed)
        assert gap < MAX_GAP, f"event loop blocked for {gap:.3f} s of a {elapsed:.3f} s build"
        nodes = await engine.query_nodes(user_id=user, node_type=NodeType.SUMMARY)
        personal = [node for node in nodes if node.scope == Scope.PERSONAL and (node.metadata or {}).get("entity_profile")]
        assert len(personal) == 1
        # The long sources do not fit the 500 token budget and are left out.
        assert personal[0].metadata["source_count_available"] == 5
        assert personal[0].metadata["source_count_included"] == 2


async def test_a_huge_profile_budget_prepares_on_the_profile_workers(config, user, monkeypatch):
    # A very large max_profile_tokens fills the profile with every related
    # source, so its text grows to the size of all of them. Hashing that text,
    # copying it for preparation and restaging it used to run on the caller's
    # event loop (issue #414), where a service shares one loop between projects.
    from prme.models import profile as profile_module
    from prme.models.profile import ProfilePublication
    from prme.storage import profile_work, vector_index

    seen: dict[str, set[str]] = {}

    def record(label, function):
        def wrapper(*args, **kwargs):
            seen.setdefault(label, set()).add(threading.current_thread().name)
            return function(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(profile_module, "profile_request_hash",
                        record("hash", profile_module.profile_request_hash))
    monkeypatch.setattr(ProfilePublication, "model_validate_json",
                        record("plan", ProfilePublication.model_validate_json))
    monkeypatch.setattr(profile_work.ProfileStageFence, "verify_plan",
                        record("stage", profile_work.ProfileStageFence.verify_plan))
    monkeypatch.setattr(vector_index.PreparedEmbedding, "model_validate_json",
                        record("embedding", vector_index.PreparedEmbedding.model_validate_json))
    source = "Aurora " + "northern office report about the budget. " * 2_000
    async with MemoryEngine.open(config) as engine:
        for i in range(3):
            await engine.store(f"{source} {i}", user_id=user, scope=Scope.PERSONAL)
        assert await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL,
            entity_names=["Aurora"], max_profile_tokens=10_000_000) == 1
        profiles = [node for node in await engine.query_nodes(user_id=user, node_type=NodeType.SUMMARY)
                    if (node.metadata or {}).get("entity_profile")]
    assert len(profiles) == 1
    # The budget was large enough to quote every source, so the prepared text
    # is the whole store's worth of records.
    assert profiles[0].metadata["source_count_included"] == 3
    assert len(profiles[0].content) > 3 * len(source)
    # The hash and the plan are also touched inside off-loop transactions, so
    # only the event loop's own calls are ruled out here.
    assert "MainThread" not in seen["hash"] and any(
        name.startswith("prme-profile") for name in seen["hash"])
    assert "MainThread" not in seen["plan"] and any(
        name.startswith("prme-profile") for name in seen["plan"])
    assert all(name.startswith("prme-profile") for name in seen.get("stage", set()))
    # The PostgreSQL publication path stages its vector in the graph store, so
    # the vector-index copy only appears on the DuckDB path.
    assert "MainThread" not in seen.get("embedding", set())


async def test_a_slow_profile_hash_does_not_hold_the_event_loop(config, user, monkeypatch):
    # The hash serializes the whole profile and every source it quotes. Once a
    # large budget makes that text the size of the store, a slow hash must run on
    # a profile worker rather than on the shared loop (issue #414).
    from prme.models import profile as profile_module

    original = profile_module.profile_request_hash

    def slow_hash(*args, **kwargs):
        time.sleep(0.3)
        return original(*args, **kwargs)

    monkeypatch.setattr(profile_module, "profile_request_hash", slow_hash)
    async with MemoryEngine.open(config) as engine:
        for i in range(3):
            await engine.store(f"Aurora note {i}", user_id=user, scope=Scope.PERSONAL)
        await engine.store("Aurora plan", user_id=user, scope=Scope.PERSONAL)
        built, elapsed, gap = await longest_gap(engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL,
            entity_names=["Aurora"], max_profile_tokens=10_000_000))
    assert built == 1
    assert elapsed > 0.3
    assert gap < MAX_GAP, f"event loop blocked for {gap:.3f} s of a {elapsed:.3f} s build"


async def test_long_names_against_text_that_matches_them_slowly_keep_the_loop_running(config, user):
    # Every position of the text starts a match that fails only at the last
    # character of the name, so one search of it compares about 400 million
    # characters.
    async with MemoryEngine.open(config) as engine:
        await engine.store("-" * 200_000, user_id=user, scope=Scope.PERSONAL)
        names = ["-" * 2_000 + "x", "-" * 2_000 + "y"]
        built, elapsed, gap = await longest_gap(
            engine.consolidate_knowledge(user_id=user, scope=Scope.PERSONAL, entity_names=names))
        assert built == 0
        measurable(elapsed)
        assert gap < MAX_GAP, f"event loop blocked for {gap:.3f} s of a {elapsed:.3f} s build"


def test_windowed_mention_search_matches_one_search_of_the_whole_text(monkeypatch):
    def whole(text: str, name: str) -> bool:
        return bool(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE))

    # Tiny windows put matches and their boundary characters across every
    # window edge; the alphabet mixes word and non-word characters and
    # characters whose case forms differ in length or are shared.
    monkeypatch.setattr(profiles, "_MATCH_WORK", 7)
    rng = random.Random(405)
    alphabet = "aAb -'_İıßK\u212ak1"
    for _ in range(50_000):
        name = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 4)))
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        assert mentions_entity(text, name) is whole(text, name), (text, name)


async def test_profile_work_runs_on_its_own_workers(config, user, monkeypatch):
    # Concurrent slow builds must not fill the loop's default executor, which
    # storage and retrieval share.
    threads: set[str] = set()
    build = profiles.build_profile

    def recording(*args, **kwargs):
        threads.add(threading.current_thread().name)
        return build(*args, **kwargs)

    monkeypatch.setattr(profiles, "build_profile", recording)
    async with MemoryEngine.open(config) as engine:
        for scope in (Scope.PERSONAL, Scope.PROJECT):
            for i in range(2):
                await engine.store(f"Aurora note {i}", user_id=user, scope=scope)
        assert await engine.consolidate_knowledge(user_id=user, entity_names=["Aurora"]) == 2
    assert threads and all(name.startswith("prme-profile") for name in threads)


async def test_profile_work_runs_at_most_two_steps_at_once():
    lock = threading.Lock()
    both = threading.Barrier(2, timeout=10)
    running, most = 0, 0

    def step():
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        both.wait()
        time.sleep(0.01)
        with lock:
            running -= 1

    # Each pair of steps meets at the barrier, so two run together; a third
    # running at the same time would show in the count.
    await asyncio.gather(*(run_profile_work(step) for _ in range(6)))
    assert most == profiles._PROFILE_WORKERS == 2


async def test_cancelled_build_drops_its_queued_profile_step():
    release = threading.Event()
    ran: list[str] = []

    def hold():
        release.wait(10)

    busy = [asyncio.ensure_future(run_profile_work(hold)) for _ in range(2)]
    queued = asyncio.ensure_future(run_profile_work(ran.append, "queued"))
    await asyncio.sleep(0.05)
    queued.cancel()
    # The cancellation reaches the pool's future on the loop's next pass.
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.gather(*busy)
    await run_profile_work(ran.append, "later")
    assert queued.cancelled() and ran == ["later"]


def test_forked_process_gets_its_own_profile_workers(monkeypatch):
    parent = profiles._profile_executor()
    monkeypatch.setattr(profiles, "_executor", parent)
    profiles._reset_after_fork()
    child = profiles._profile_executor()
    try:
        assert child is not parent
        assert profiles._profile_executor() is child
    finally:
        child.shutdown()
