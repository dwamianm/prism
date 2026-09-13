"""Explicit retry identities survive concurrency, restarts and lost acknowledgements."""

import asyncio
from uuid import uuid4

import pytest

from prme import MemoryEngine
from tests import test_durable_ingestion
from tests.test_reinforcement_atomic import records

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_reinforcement_retry_is_one_signal_across_restart(config, user):
    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        evidence = await engine.store("A confirming observation", user_id=user)
        await asyncio.gather(
            *(
                engine.reinforce(
                    str(node.id),
                    evidence_id=evidence,
                    user_id=user,
                    request_id=request_id,
                )
                for _ in range(3)
            )
        )
        after = await engine.get_node(str(node.id), user_id=user)
        assert after.reinforcement_boost == pytest.approx(0.15)
        assert len(await records(engine, str(node.id))) == 1
    async with MemoryEngine.open(config) as engine:
        await engine.reinforce(
            str(node.id), evidence_id=evidence, user_id=user, request_id=request_id
        )
        assert await engine.get_node(str(node.id), user_id=user) == after
        assert len(await records(engine, str(node.id))) == 1
        await engine.reinforce(
            str(node.id), evidence_id=evidence, user_id=user, request_id=str(uuid4())
        )
        assert (
            await engine.get_node(str(node.id), user_id=user)
        ).reinforcement_boost == pytest.approx(0.3)


@pytest.mark.parametrize("changed", ["node", "evidence"])
async def test_retry_identity_rejects_changed_request_without_mutation(
    config, user, changed
):
    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        sources = [
            await engine.store(f"Observation {i}", user_id=user) for i in range(3)
        ]
        nodes = [(await engine.get_event_nodes(s, user_id=user))[0] for s in sources]
        await engine.reinforce(
            str(nodes[0].id),
            evidence_id=sources[1],
            user_id=user,
            request_id=request_id,
        )
        before = [await engine.get_node(str(n.id), user_id=user) for n in nodes]
        with pytest.raises(ValueError, match="request_id"):
            await engine.reinforce(
                str(nodes[1 if changed == "node" else 0].id),
                evidence_id=sources[2 if changed == "evidence" else 1],
                user_id=user,
                request_id=request_id,
            )
        assert [await engine.get_node(str(n.id), user_id=user) for n in nodes] == before


async def test_retry_ids_are_owner_scoped_and_do_not_restore_later_state(config, user):
    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        for owner in [user, user + "-other"]:
            source = await engine.store("Observation", user_id=owner)
            node = (await engine.get_event_nodes(source, user_id=owner))[0]
            await engine.reinforce(str(node.id), user_id=owner, request_id=request_id)
            await engine.archive(str(node.id), user_id=owner)
            archived = await engine.get_node(
                str(node.id), include_superseded=True, user_id=owner
            )
            await engine.reinforce(str(node.id), user_id=owner, request_id=request_id)
            assert (
                await engine.get_node(
                    str(node.id), include_superseded=True, user_id=owner
                )
                == archived
            )
            assert len(await records(engine, str(node.id))) == 1
            with pytest.raises(ValueError, match="not found"):
                await engine.reinforce(
                    str(node.id), user_id=owner + "-foreign", request_id=request_id
                )


async def test_retry_after_lost_commit_acknowledgement_does_not_increment_again(
    config, user, monkeypatch
):
    request_id = uuid4()
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        original = engine._graph_store.reinforce_node

        async def lost_ack(*args, **kwargs):
            await original(*args, **kwargs)
            raise ConnectionError("Authored lost acknowledgement")

        with monkeypatch.context() as fault:
            fault.setattr(engine._graph_store, "reinforce_node", lost_ack)
            with pytest.raises(ConnectionError):
                await engine.reinforce(
                    str(node.id), user_id=user, request_id=request_id
                )
        after = await engine.get_node(str(node.id), user_id=user)
        await engine.reinforce(str(node.id), user_id=user, request_id=request_id)
        assert await engine.get_node(str(node.id), user_id=user) == after
        assert after.reinforcement_boost == pytest.approx(0.15)
        assert len(await records(engine, str(node.id))) == 1


async def test_cancelled_duckdb_confirmation_can_be_retried_once(
    config, user, monkeypatch
):
    import threading
    from prme.storage import reinforcement

    if config.backend != "duckdb":
        pytest.skip("DuckDB native worker cancellation boundary")
    entered, release = threading.Event(), threading.Event()
    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]

        def pause(stage):
            if stage == "journal":
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Authored checkpoint was not released")

        with monkeypatch.context() as fault:
            fault.setattr(reinforcement, "_checkpoint", pause)
            task = asyncio.create_task(
                engine.reinforce(str(node.id), user_id=user, request_id=request_id)
            )
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                task.cancel()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        after = await engine.get_node(str(node.id), user_id=user)
        assert after.reinforcement_boost == pytest.approx(0.15)
        await engine.reinforce(str(node.id), user_id=user, request_id=request_id)
        assert await engine.get_node(str(node.id), user_id=user) == after
        assert len(await records(engine, str(node.id))) == 1


async def test_failed_keyed_attempt_can_retry_after_transaction_abort(
    config, user, monkeypatch
):
    from prme.storage import reinforcement

    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]

        def fail(stage):
            if stage == "journal":
                raise RuntimeError("Authored rollback")

        with monkeypatch.context() as fault:
            fault.setattr(reinforcement, "_checkpoint", fail)
            with pytest.raises(RuntimeError, match="Authored rollback"):
                await engine.reinforce(
                    str(node.id), user_id=user, request_id=request_id
                )
        assert await records(engine, str(node.id)) == []
        await engine.reinforce(str(node.id), user_id=user, request_id=request_id)
        assert (
            await engine.get_node(str(node.id), user_id=user)
        ).reinforcement_boost == pytest.approx(0.15)
        assert len(await records(engine, str(node.id))) == 1


async def test_concurrent_reuse_for_different_nodes_commits_only_one(config, user):
    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        sources = [
            await engine.store(f"Observation {i}", user_id=user) for i in range(2)
        ]
        nodes = [(await engine.get_event_nodes(s, user_id=user))[0] for s in sources]
        outcomes = await asyncio.gather(
            *(
                engine.reinforce(str(n.id), user_id=user, request_id=request_id)
                for n in nodes
            ),
            return_exceptions=True,
        )
        assert sum(value is None for value in outcomes) == 1
        assert sum(isinstance(value, ValueError) for value in outcomes) == 1
        after = [await engine.get_node(str(n.id), user_id=user) for n in nodes]
        assert sorted(n.reinforcement_boost for n in after) == pytest.approx([0, 0.15])
        assert sum([len(await records(engine, str(n.id))) for n in nodes]) == 1


def test_legacy_reinforcement_record_keeps_its_original_checksum():
    import hashlib
    import json
    from prme.models import MemoryNode
    from prme.storage.reinforcement import read_record

    node = MemoryNode(user_id="alice", node_type="note", content="Prior observation")
    legacy = {
        "version": 1,
        "policy": "additive_caps_v1",
        "operation_id": str(uuid4()),
        "evidence_id": None,
        "before": node.model_dump(mode="json"),
        "after": node.model_dump(mode="json"),
    }
    raw = json.dumps(legacy)
    envelope = {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    parsed = read_record(envelope)
    assert parsed.version == 1 and parsed.request_id is None
    assert parsed.before == parsed.after == node
    assert envelope["record"] == raw
