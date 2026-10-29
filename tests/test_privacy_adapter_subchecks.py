"""PV-11 adapter subchecks that the case tests did not yet prove (#327).

Each test drives one public adapter for an unchanged frozen case. The case's
own engine tests carry the storage backends; these prove the adapter path.
"""

import asyncio
import json
import sys

import pytest

from prme import MemoryClient, MemoryEngine
from prme.privacy import PrivacyDenied
from prme.privacy.policy import MemoryRetention
from tests.privacy_fakes import config_for, make_runtime, policy_for

SOURCE = "Alex does not like tea unless it is cold."
MEMORY = "Alex likes tea."
MARKER = "SOURCE_ONLY_ADAPTER_SUBCHECK_327"
EMAIL = "private@example.test"


def two_projects(tmp_path):
    """Two concurrent projects with different immutable saved policies."""
    first, second = make_runtime(), make_runtime()
    # The first project's sanitizer removes the contact; the second keeps it.
    first.sanitizer.replace_text = (EMAIL, "[EMAIL_REMOVED]")
    return (
        (first, policy_for(first, memory_retention=MemoryRetention(mode="erase_after", seconds=3600)), tmp_path / "one"),
        (second, policy_for(second), tmp_path / "two"),
    )


def no_writes(client, owner):
    assert client.get_events(owner) == []


def test_AP02_sync_python_rejects_before_side_effects(tmp_path):
    r = make_runtime()
    p = policy_for(r)
    owner = str(p.owner_id)
    other = policy_for(make_runtime(), owner=p.owner_id)
    with MemoryClient(config=config_for(tmp_path, policy=p), embedding_provider=r.embedder, privacy_runtime=r) as client:
        # A policy from another project and a denying admission hook both
        # refuse before any event, graph, queue or provider write.
        with pytest.raises(PrivacyDenied):
            client.store(SOURCE, user_id=owner, policy=other)
        r.authority.denied.add("admission")
        for call in (lambda: client.store(SOURCE, user_id=owner),
                     lambda: client.ingest_fast_many([{"content": MEMORY}, {"content": SOURCE}], user_id=owner)):
            with pytest.raises(PrivacyDenied, match="privacy_stale_policy"):
                call()
        assert not r.embedder.sends and not r.extractor.sends
        no_writes(client, owner)
        r.authority.denied.clear()
        assert client.store(SOURCE, user_id=owner)


def test_AP03_sync_python_and_fast_batch_keep_concurrent_project_policies(tmp_path):
    (ra, pa, path_a), (rb, pb, path_b) = two_projects(tmp_path)
    a_owner, b_owner = str(pa.owner_id), str(pb.owner_id)
    with MemoryClient(config=config_for(path_a, policy=pa), embedding_provider=ra.embedder, privacy_runtime=ra) as a, \
            MemoryClient(config=config_for(path_b, policy=pb), embedding_provider=rb.embedder, privacy_runtime=rb) as b:
        stored = {"a": [], "b": []}
        for _ in range(2):
            stored["a"].append(a.store(f"Email {EMAIL}", user_id=a_owner))
            stored["b"].append(b.store(f"Email {EMAIL}", user_id=b_owner))
        stored["a"].extend(a.ingest_fast_many([{"content": f"Batch {EMAIL}"}], user_id=a_owner))
        stored["b"].extend(b.ingest_fast_many([{"content": f"Batch {EMAIL}"}], user_id=b_owner))
        a.process_pending(user_id=a_owner)
        b.process_pending(user_id=b_owner)
        a_text = [node.content for item in stored["a"] for node in a.get_event_nodes(item, user_id=a_owner)]
        b_text = [node.content for item in stored["b"] for node in b.get_event_nodes(item, user_id=b_owner)]
        assert a_text == ["Email [EMAIL_REMOVED]"] * 2 + ["Batch [EMAIL_REMOVED]"]
        assert b_text == [f"Email {EMAIL}"] * 2 + [f"Batch {EMAIL}"]
        # Each store keeps its own saved policy; the other project's policy is
        # refused and no owner bleeds across.
        assert a.inspect_privacy_policy(user_id=a_owner)["policy"]["policy"]["policy_id"] == str(pa.policy_id)
        assert b.inspect_privacy_policy(user_id=b_owner)["policy"]["policy"]["policy_id"] == str(pb.policy_id)
        assert a.inspect_privacy_policy(user_id=a_owner)["unlimited_memory"] is False
        assert b.inspect_privacy_policy(user_id=b_owner)["unlimited_memory"] is True
        with pytest.raises(PrivacyDenied):
            a.store(SOURCE, user_id=b_owner, policy=pb)
        with pytest.raises(PrivacyDenied):
            b.ingest_fast_many([{"content": SOURCE}], user_id=a_owner, policy=pa)
        # Reading the other project's owner is refused or empty, never shared.
        for client, foreign in ((a, b_owner), (b, a_owner)):
            try:
                assert client.get_events(foreign) == []
            except PrivacyDenied as exc:
                assert exc.code == "privacy_boundary_mismatch"


def test_AP03_HTTP_keeps_concurrent_project_policies(tmp_path):
    from fastapi.testclient import TestClient
    from prme.api.app import create_app

    (ra, pa, path_a), (rb, pb, path_b) = two_projects(tmp_path)
    first = create_app(config_for(path_a, policy=pa), privacy_runtime=ra, embedding_provider=ra.embedder)
    second = create_app(config_for(path_b, policy=pb), privacy_runtime=rb, embedding_provider=rb.embedder)
    with TestClient(first) as a, TestClient(second) as b:
        for _ in range(2):
            assert a.post("/v1/store", json={"content": f"Email {EMAIL}", "user_id": str(pa.owner_id)}).status_code == 200
            assert b.post("/v1/store", json={"content": f"Email {EMAIL}", "user_id": str(pb.owner_id)}).status_code == 200
        a_body = a.post("/v1/retrieve", json={"query": "Email", "user_id": str(pa.owner_id), "min_score": 0}).text
        b_body = b.post("/v1/retrieve", json={"query": "Email", "user_id": str(pb.owner_id), "min_score": 0}).text
        assert "[EMAIL_REMOVED]" in a_body and EMAIL not in a_body
        assert EMAIL in b_body
        denied = a.post("/v1/store", json={"content": SOURCE, "user_id": str(pb.owner_id),
                                           "policy": pb.model_dump(mode="json")})
        assert denied.status_code in (403, 422) and SOURCE not in denied.text
        policy = a.post("/v1/privacy/policy", json={"user_id": str(pa.owner_id)}).json()
        assert policy["policy"]["policy"]["policy_id"] == str(pa.policy_id)
        assert b.post("/v1/privacy/policy", json={"user_id": str(pb.owner_id)}).json()["unlimited_memory"] is True


async def test_AP03_AP06_MCP_project_policies_and_async_refusal(tmp_path):
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme.mcp.server import create_mcp_server

    (ra, pa, path_a), (rb, _, path_b) = two_projects(tmp_path)
    pb = policy_for(rb, durable_processing_input=False)
    first = create_mcp_server(config_for(path_a, policy=pa), privacy_runtime=ra, embedding_provider=ra.embedder)
    second = create_mcp_server(config_for(path_b, policy=pb), privacy_runtime=rb, embedding_provider=rb.embedder)
    async with create_connected_server_and_client_session(first._mcp_server, raise_exceptions=True) as a, \
            create_connected_server_and_client_session(second._mcp_server, raise_exceptions=True) as b:
        await a.initialize()
        await b.initialize()
        for client, policy in ((a, pa), (b, pb), (a, pa)):
            response = await client.call_tool("memory_store", {"content": f"Email {EMAIL}", "user_id": str(policy.owner_id)})
            assert json.loads(response.content[0].text)["node_id"]
        a_text = (await a.call_tool("memory_retrieve", {"query": "Email", "user_id": str(pa.owner_id)})).model_dump_json()
        b_text = (await b.call_tool("memory_retrieve", {"query": "Email", "user_id": str(pb.owner_id)})).model_dump_json()
        assert "[EMAIL_REMOVED]" in a_text and EMAIL not in a_text and EMAIL in b_text
        # AP06: with no durable processing input, async ingestion is refused
        # before enqueue and the refusal names resubmission, not a retry.
        before = len(rb.embedder.sends)
        refused = await b.call_tool("memory_ingest", {"content": SOURCE, "user_id": str(pb.owner_id)})
        assert "privacy_async_forbidden" in refused.model_dump_json() and SOURCE not in refused.model_dump_json()
        assert len(rb.embedder.sends) == before
        accepted = await b.call_tool("memory_ingest", {"content": SOURCE, "user_id": str(pb.owner_id),
                                                       "wait_for_extraction": True})
        assert "event_id" in json.loads(accepted.content[0].text)


def test_AC01_sync_python_ordinary_reads_return_admitted_memory(tmp_path):
    r = make_runtime()
    p = policy_for(r, capture="full", evidence_lifetime_seconds=3600)
    owner = str(p.owner_id)
    with MemoryClient(config=config_for(tmp_path, policy=p), embedding_provider=r.embedder, privacy_runtime=r) as client:
        event_id = client.store(f"{MEMORY} {MARKER}", retrieval_content=MEMORY, user_id=owner)
        nodes = client.get_event_nodes(event_id, user_id=owner)
        response = client.retrieve("tea", user_id=owner, min_score=0)
        views = [client.get_events(owner), nodes, client.get_node(str(nodes[0].id), user_id=owner), response]
        text = json.dumps([json.loads(item.model_dump_json()) if hasattr(item, "model_dump_json") else
                           [json.loads(value.model_dump_json()) for value in item] for item in views])
        assert MEMORY in text and MARKER not in text
        assert MEMORY in response.bundle.render() and MARKER not in response.bundle.render()


def _cli(monkeypatch, capsys, argv):
    import structlog
    from prme import cli

    monkeypatch.setattr(sys, "argv", ["prme", *argv])
    logging_config = structlog.get_config()
    try:
        with pytest.raises(SystemExit) as stopped:
            cli.main()
    finally:
        structlog.configure(**logging_config)
    return stopped.value.code, capsys.readouterr()


def test_AC01_CL08_CLI_reads_refuse_without_runtime_and_hide_markers(tmp_path, monkeypatch, capsys, caplog):
    r = make_runtime()
    p = policy_for(r, capture="full", evidence_lifetime_seconds=3600)
    owner = str(p.owner_id)
    config = config_for(tmp_path, policy=p)

    async def prepare():
        engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
        try:
            await engine.store(f"{MEMORY} {MARKER}", retrieval_content=MEMORY, user_id=owner)
        finally:
            await engine.close()

    asyncio.run(prepare())
    capsys.readouterr()
    # The ordinary CLI cannot supply a privacy runtime, so every read is a
    # fixed denial with guidance; neither the memory nor the marker leaks.
    for argv in (["nodes", config.db_path, "--user-id", owner, "--format", "json"],
                 ["search", config.db_path, MARKER, "--user-id", owner],
                 ["stats", config.db_path], ["info", config.db_path],
                 ["export", config.db_path]):
        code, output = _cli(monkeypatch, capsys, argv)
        assert code == 1, argv
        assert "privacy_capability_unavailable" in output.err, argv
        assert MARKER not in output.out + output.err + caplog.text, argv
        assert MEMORY not in output.out, argv


async def test_CL08_explorer_and_framework_errors_hide_markers(tmp_path, caplog, capsys):
    import httpx
    from langchain_core.messages import HumanMessage
    from llama_index.core.llms import ChatMessage
    from prme.integrations import langchain, llamaindex
    from web.server import create_explorer_app

    r = make_runtime()
    p = policy_for(r)
    owner = str(p.owner_id)
    config = config_for(tmp_path, policy=p)
    # Explorer: a provider failure carrying marker text and a rejected input
    # return fixed bodies; logs and responses never echo the marker.
    app = create_explorer_app(config, privacy_runtime=r, embedding_provider=r.embedder)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://explorer") as client:
            async def failing(texts):
                raise RuntimeError(f"provider failure {MARKER}")

            original = r.embedder.embed
            r.embedder.embed = failing
            try:
                failed = await client.post("/v1/retrieve", json={"query": f"tea {MARKER}", "user_id": owner})
            finally:
                r.embedder.embed = original
            rejected = await client.post("/v1/retrieve", json={"query": f"[reject] {MARKER}", "user_id": owner})
            for response in (failed, rejected):
                assert response.status_code >= 400 and MARKER not in response.text
    # Frameworks: protected history writes are refused with a fixed code and
    # the marker reaches neither the exception nor the logs.
    with MemoryClient(config=config, embedding_provider=r.embedder, privacy_runtime=r) as client:
        langchain.MemoryClient = lambda *args, **kwargs: client
        llamaindex.MemoryClient = lambda *args, **kwargs: client
        try:
            history = langchain.PRMEChatMessageHistory(user_id=owner, session_id="cl08")
            with pytest.raises(PrivacyDenied, match="privacy_unsupported_input") as first:
                history.add_message(HumanMessage(content=MARKER))
            store = llamaindex.PRMEChatStore()
            with pytest.raises(PrivacyDenied, match="privacy_unsupported_input") as second:
                store.add_message(owner + ":cl08", ChatMessage(role="user", content=MARKER))
        finally:
            langchain.MemoryClient = MemoryClient
            llamaindex.MemoryClient = MemoryClient
        assert MARKER not in str(first.value) + str(second.value)
        assert client.get_events(owner) == []
    captured = capsys.readouterr()
    assert MARKER not in caplog.text + captured.out + captured.err


async def test_AP06_fast_batch_refuses_before_enqueue_without_durable_input(tmp_path):
    r = make_runtime()
    p = policy_for(r, durable_processing_input=False)
    owner = str(p.owner_id)
    engine = await MemoryEngine.create(config_for(tmp_path, policy=p), embedding_provider=r.embedder,
                                       privacy_runtime=r)
    try:
        with pytest.raises(PrivacyDenied, match="privacy_async_forbidden"):
            await engine.ingest_fast_many([{"content": MEMORY}, {"content": SOURCE}], user_id=owner)
        assert await engine._event_store.get_by_user(owner) == []
        assert not r.embedder.sends and not r.extractor.sends
        inspection = await engine.inspect_privacy_policy(user_id=owner)
        assert inspection["processing_failure_recovery"] == "caller_resubmission_required"
    finally:
        await engine.close()
