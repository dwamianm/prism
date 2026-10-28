"""AC06 actual routing integration; policy/native boundaries remain fixtures."""
import asyncio
from datetime import timedelta

import pytest

from prme.privacy import PrivacyDenied
from prme.privacy.local_keys import public_registry
from tests.privacy_source_support import (
    MEMORY, SOURCE, PROHIBITED, opened, store_pair,
    backend_layout as backend_layout, isolated_privacy_postgres as isolated_privacy_postgres,
    local_analyzer_fixture as local_analyzer_fixture, native as native_fixture,  # noqa: F401
)


async def selection(engine, runtime, owner, ids, *, kind="runtime"):
    records = [await engine._privacy.repository.get(identifier, user_id=owner) for identifier in ids]
    return await runtime.protector.authority.grant(
        kind, tuple((record.evidence.context, record.snapshot) for record in records),
        expires_at=runtime.clock() + timedelta(minutes=1),
    )


async def test_AC06_incomplete_protected_projection_preserves_admitted_group(tmp_path, analyzer, native):
    import json

    engine, runtime, policy, custody, evidence, _ = await opened(
        tmp_path, "directory", analyzer, policy_options={"evidence_use": "runtime"},
    )
    owner = str(policy.owner_id)
    texts = ("Mara likes tea.", "Mara likes oranges.")
    now = runtime.clock()
    captured = {}
    try:
        ids = [await engine.store(text, user_id=owner, event_time=now - timedelta(minutes=2 - index))
               for index, text in enumerate(texts)]
        records = [await engine._privacy.repository.get(identifier, user_id=owner) for identifier in ids]
        original_nodes = []
        for identifier in ids:
            nodes = await engine._graph_store.get_event_nodes(identifier, user_id=owner)
            assert len(nodes) == 1
            original_nodes.append(nodes[0])
        assert {str(node.id) for node in original_nodes}.isdisjoint(ids)
        # Normal versioned reinforcement establishes the dependency set without
        # rewriting a source event, admission, plan or journal payload.
        for node, other_event in zip(original_nodes, reversed(ids), strict=True):
            await asyncio.wait_for(engine.reinforce(str(node.id), evidence_id=other_event, user_id=owner), 15)
        assert [await engine._privacy.repository.get(identifier, user_id=owner) for identifier in ids] == records
        admitted = {str(node.id): await engine._graph_store.get_node(str(node.id)) for node in original_nodes}
        assert all({str(identifier) for identifier in node.evidence_refs} == set(ids) for node in admitted.values())

        async def retrieve(label, selected, cap, **options):
            pipeline = engine._retrieval_pipeline
            pipeline._packing_config = pipeline._packing_config.model_copy(update={
                "evidence_projection_top_k": 3, "evidence_projection_max_sources": cap,
            })
            access = await selection(engine, runtime, owner, selected)
            response = await asyncio.wait_for(engine.retrieve(
                "tea", user_id=owner, scope=policy.scope, include_cross_scope=False,
                reference_time=now, source_access=access, **options,
            ), 15)
            captured[label] = response.model_dump(mode="json")
            (tmp_path / "protected-projection-coverage.json").write_text(json.dumps(captured, indent=2) + "\n")
            return response

        def assert_retained(response, expected):
            returned = {str(item.node.id): item.node for item in response.results}
            assert set(returned) == set(expected)
            packed = {str(item.node.id) for section in response.bundle.sections.values() for item in section}
            assert set(expected) <= packed
            for identifier in expected:
                assert returned[identifier].content == admitted[identifier].content
                assert returned[identifier].evidence_refs == admitted[identifier].evidence_refs
                assert admitted[identifier].content in response.bundle.render()

        with custody.unlocked((evidence.ref,), expires_at=now + timedelta(minutes=2)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            try:
                capped = await retrieve("capped", ids, 1)
                assert_retained(capped, admitted)
                missing = await retrieve("missing", ids[:1], 2)
                assert_retained(missing, admitted)
                filtered = await retrieve("filtered", ids, 2, event_time_to=now - timedelta(seconds=90))
                assert_retained(filtered, (str(original_nodes[0].id),))
                complete = await retrieve("complete", ids, 2)
                assert {str(item.node.id) for item in complete.results} == set(ids)
                assert all(item.node.metadata["source_representation"] == "sanitized" for item in complete.results)
                assert {str(item.node.id) for section in complete.bundle.sections.values() for item in section} == set(ids)
                assert all(text in complete.bundle.render() for text in texts)
            finally:
                runtime.protector.registry = public_registry(custody.path)
        assert [await engine._privacy.repository.get(identifier, user_id=owner) for identifier in ids] == records
    finally:
        await engine.close()


@pytest.mark.parametrize("error", [PrivacyDenied("privacy_denied"), RuntimeError("backend unavailable")])
async def test_AC06_candidate_failure_settles_both_child_queries(error):
    """A failed retrieval must not leave a query alive when its caller closes."""
    from prme.retrieval.candidates import _generate_pinned_candidates

    started, raised, release, settled = (asyncio.Event() for _ in range(4))
    children = []

    class Graph:
        async def query_nodes(self, **kwargs):
            if kwargs.get("min_salience") == 1.0:
                await started.wait()
                raised.set()
                raise error
            children.append(asyncio.current_task())
            started.set()
            try:
                await release.wait()
                return []
            finally:
                settled.set()

    request = asyncio.create_task(_generate_pinned_candidates(Graph(), "owner"))
    try:
        await asyncio.wait_for(raised.wait(), 2)
        completed, _ = await asyncio.wait({request}, timeout=0.05)
        assert not completed, "The caller returned while its sibling query was still running"
        release.set()
        with pytest.raises(type(error)) as failure:
            await asyncio.wait_for(request, 2)
        assert failure.value is error
        assert settled.is_set()
    finally:
        release.set()
        await asyncio.gather(request, *children, return_exceptions=True)


async def test_AC06_two_selected_standard_sources_use_finite_lease(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, custody, evidence, _ = await opened(
        tmp_path, layout, analyzer, policy_options={"evidence_use": "runtime"},
    )
    owner = str(policy.owner_id)
    import json
    from pathlib import Path
    supported = next(row["text"] for row in json.loads((Path(__file__).parent / "fixtures/privacy/sanitizer-v1.json").read_text())["rows"] if row["id"] == "email-01")
    admitted = []
    original_admit = runtime.sanitizer.admit
    async def observed_admit(request):
        result = await original_admit(request)
        if request.mode == "ingress":
            admitted.append(json.loads(result.fields_json)["content"])
        return result
    monkeypatch.setattr(runtime.sanitizer, "admit", observed_admit)
    try:
        ids = [await engine.store(f"{MEMORY}. {SOURCE}. Contact: {PROHIBITED}. {supported}",
                                  retrieval_content=MEMORY, user_id=owner) for _ in range(2)]
        expected = dict(zip(ids, admitted, strict=True))
        # A third source remains outside the exact grant even though it is a hit.
        unselected = await store_pair(engine, owner)
        pipeline = engine._retrieval_pipeline
        pipeline._packing_config = pipeline._packing_config.model_copy(update={"evidence_projection_top_k": 3})
        access = await selection(engine, runtime, owner, ids)
        ordinary = await engine.retrieve(MEMORY, user_id=owner)
        assert SOURCE not in ordinary.model_dump_json()
        with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            response = await asyncio.wait_for(engine.retrieve(MEMORY, user_id=owner, source_access=access), 15)
        sources = [item.node for item in response.results if item.node.metadata.get("source_representation") == "sanitized"]
        assert {str(node.id) for node in sources} == set(ids)
        assert all(node.content.encode() == expected[str(node.id)].encode() for node in sources)
        assert all(SOURCE in node.content and "sample.person@example.com" not in node.content for node in sources)
        # Frozen .test remains a visible pinned-recognizer miss, never a removal pass.
        assert all(PROHIBITED in node.content and "[EMAIL_REMOVED]" in node.content for node in sources)
        assert unselected not in {str(node.id) for node in sources}
        assert SOURCE in response.bundle.render()
        assert "explicitly selected sanitized evidence" in response.metadata.source_limitation
        assert all(node.metadata["source_availability"][0]["state"] == "available" for node in sources)
        pipeline._packing_config = pipeline._packing_config.model_copy(update={
            "evidence_projection_top_k": 0, "evidence_augmentation_top_k": 3,
        })
        with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            augmented = await engine.retrieve(MEMORY, user_id=owner, source_access=access)
        assert SOURCE in augmented.bundle.render() and MEMORY in augmented.bundle.render()
        assert {str(item.node.id) for item in augmented.results if item.node.metadata.get("source_representation") == "sanitized"} == set(ids)
        assert any(item.node.content == MEMORY for item in augmented.results)
        # Explicit diagnostic access returns a labeled source but cannot drive retrieval.
        diagnostic = await selection(engine, runtime, owner, ids, kind="diagnostic")
        with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            view = await engine.read_source(ids[0], user_id=owner, access=diagnostic)
            with pytest.raises(PrivacyDenied):
                await engine.read_source(unselected, user_id=owner, access=diagnostic)
            with pytest.raises(PrivacyDenied):
                await engine.read_source(ids[0], user_id=owner, access=None)
        assert view.representation == "sanitized" and view.content.encode() == expected[ids[0]].encode()
        assert "sample.person@example.com" not in view.content and PROHIBITED in view.content
        with pytest.raises(PrivacyDenied):
            await engine.retrieve(MEMORY, user_id=owner, source_access=diagnostic)
        assert SOURCE not in (await engine.retrieve(MEMORY, user_id=owner)).model_dump_json()
    finally:
        await engine.close()


async def test_AC06_selected_deadline_and_effect_denials_stop_response(tmp_path, layout, analyzer, native):
    engine, runtime, policy, custody, evidence, _ = await opened(
        tmp_path, layout, analyzer, policy_options={"evidence_use": "runtime"},
    )
    owner = str(policy.owner_id)
    initial = runtime.clock()
    try:
        ids = [await store_pair(engine, owner), await store_pair(engine, owner)]
        engine._retrieval_pipeline._packing_config = engine._retrieval_pipeline._packing_config.model_copy(
            update={"evidence_projection_top_k": 2},
        )
        with custody.unlocked((evidence.ref,), expires_at=initial + timedelta(minutes=10)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            for failure in ("transmission", "expiry", "policy"):
                access = await selection(engine, runtime, owner, ids)
                before = len(runtime.embedder.sends)
                if failure == "transmission":
                    runtime.authority.denied.add("transmission")
                else:
                    async def after_send():
                        if failure == "expiry":
                            runtime.clock.now = access.expires_at
                        else:
                            runtime.authority.revisions[policy.policy_id] = policy.revision + 1
                    runtime.embedder.after_send = after_send
                with pytest.raises(PrivacyDenied):
                    await asyncio.wait_for(engine.retrieve(MEMORY, user_id=owner, source_access=access), 15)
                assert len(runtime.embedder.sends) - before == (0 if failure == "transmission" else 1)
                runtime.authority.denied.clear()
                runtime.authority.revisions.clear()
                runtime.embedder.after_send = None
                runtime.clock.now = initial
    finally:
        await engine.close()


async def test_AC06_query_provider_dispatch_admits_input_output_and_refuses_retry(tmp_path, layout, analyzer, native, monkeypatch):
    import json
    import logging
    import httpx
    from pydantic import SecretStr
    from pathlib import Path
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    sends = []
    mode = "allow"
    supported = next(row["text"] for row in json.loads((Path(__file__).parent / "fixtures/privacy/sanitizer-v1.json").read_text())["rows"] if row["id"] == "email-01")
    async def exchange(request):
        assert runtime.authority._lock.locked()
        sends.append(json.loads(request.content))
        logging.getLogger("httpx").warning("synthetic provider log", extra={"private_query": supported})
        if mode == "error":
            return httpx.Response(500, json={"error": {"message": supported}})
        output = {"queries": [MEMORY] if mode == "allow" else ["password=synthetic-output"]}
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 0,
            "model": "synthetic", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(output)}}]})
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(exchange))
    try:
        await store_pair(engine, owner)
        pipeline = engine._retrieval_pipeline
        pipeline._enable_query_reformulation = True
        pipeline._query_reformulation_provider = "openai"
        pipeline._query_reformulation_model = "synthetic"
        pipeline._query_reformulation_api_key = SecretStr("synthetic-offline-key")
        pipeline._query_reformulation_base_url = "https://example.invalid/v1"
        query = "Alex tea " + supported
        response = await engine.retrieve(query, user_id=owner)
        assert response.results and MEMORY in response.bundle.render()
        assert sends and SOURCE not in str(sends) and "sample.person@example.com" not in str(sends)
        assert "[EMAIL_REMOVED]" in str(sends)
        for mode in ("error", "generated_denial"):
            before = len(sends)
            with pytest.raises(PrivacyDenied):
                await engine.retrieve(query, user_id=owner)
            assert len(sends) == before + 1
    finally:
        await engine.close()


async def test_AC06_asymmetric_queries_and_source_limit_even_without_results(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        await store_pair(engine, owner)
        calls = []
        async def unguarded(text):
            raise AssertionError("unguarded asymmetric encoder")
        async def guarded(text, snapshot, *, guard):
            async with guard():
                calls.append(text)
                return (await runtime.embedder.embed([text]))[0]
        monkeypatch.setattr(runtime.embedder, "embed_query", unguarded, raising=False)
        # A document-only protected adapter cannot silently change query semantics.
        with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
            await engine.retrieve(MEMORY, user_id=owner)
        monkeypatch.setattr(runtime.embedder, "encode_query", guarded, raising=False)
        response = await engine.retrieve(MEMORY, user_id=owner)
        assert calls and response.results and MEMORY in response.bundle.render()
        empty = await engine.retrieve(MEMORY, user_id=owner, limit=0, token_budget=0)
        assert not empty.results and empty.metadata.source_limitation
        assert SOURCE not in empty.model_dump_json()
    finally:
        await engine.close()


async def test_AC04_AC06_real_graph_seed_walk_excludes_unadmitted_neighbors(tmp_path, layout, analyzer, native, monkeypatch):
    from uuid import uuid4
    from prme.models import MemoryNode, MemoryEdge
    from prme.retrieval.config import ScoringWeights
    from prme.types import NodeType, EdgeType, RetrievalMode
    from tests.privacy_source_support import FIXTURE, assert_no_source
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    row = FIXTURE["qualifiers"][5]
    runtime.extractor.result = {
        "entities": [{"name": name, "entity_type": "person"} for name in ("Alex", "Jordan")],
        "facts": [], "relationships": [{"source_entity": "Alex", "target_entity": "Jordan",
            "relationship_type": row["predicate"], "evidence_quote": row["source"],
            "condition": row["condition"], "epistemic_type": "conditional", "polarity": "positive"}],
        "summary": None,
    }
    try:
        identifier = await engine.ingest(row["source"], user_id=owner, metadata={"private_source": SOURCE}, wait_for_extraction=True)
        nodes = await engine.get_event_nodes(identifier, user_id=owner)
        claim = next(node for node in nodes if node.node_type != NodeType.ENTITY)
        seeds = [node for node in nodes if node.node_type == NodeType.ENTITY]
        weights = ScoringWeights(fusion="rrf", rrf_graph_seed_count=True)
        response = await engine.retrieve("How would Alex work with Jordan?", user_id=owner, weights=weights,
                                         reference_time=runtime.clock(), retrieval_mode=RetrievalMode.EXPLICIT)
        candidate = next(item for item in response.results if item.node.id == claim.id)
        assert candidate.graph_seed_reach == (2, 2, 1)
        assert candidate.score_provenance.rank_fusion.graph_rank is not None
        assert candidate.node.content == row["expected"]
        assert_no_source(response.model_dump_json())
        # Existing foreign/unclassified fixture substitutions reach actual storage.
        # They are adversarial backend fixtures, never claimed as admitted writes.
        for hidden_owner in (owner, str(uuid4())):
            hidden = MemoryNode(user_id=hidden_owner, node_type=NodeType.FACT, content=SOURCE)
            await engine._graph_store.create_node(hidden)
            await engine._graph_store.create_edge(MemoryEdge(
                source_id=seeds[0].id, target_id=hidden.id, user_id=owner,
                edge_type=EdgeType.RELATES_TO, provenance_event_id=identifier,
            ))
        after = await engine.retrieve("How would Alex work with Jordan?", user_id=owner, weights=weights,
                                         reference_time=runtime.clock(), retrieval_mode=RetrievalMode.EXPLICIT)
        selected = next(item for item in after.results if item.node.id == claim.id)
        assert selected.graph_seed_reach == candidate.graph_seed_reach
        assert selected.composite_score == candidate.composite_score
        assert_no_source(after.model_dump_json())
        original = engine._graph_store.get_edges
        async def denied(**kwargs):
            # Policy activation occurs at an actual graph read, before hydration.
            runtime.authority.denied.add("disclosure")
            return await original(**kwargs)
        monkeypatch.setattr(engine._graph_store, "get_edges", denied)
        with pytest.raises(PrivacyDenied):
            await engine.retrieve("How would Alex work with Jordan?", user_id=owner, weights=weights,
                                         reference_time=runtime.clock(), retrieval_mode=RetrievalMode.EXPLICIT)
    finally:
        runtime.authority.denied.clear()
        await engine.close()


async def test_AC06_temporal_provider_and_gate_use_per_send_guards(tmp_path, layout, analyzer, native):
    import json
    import httpx
    from prme.retrieval.temporal_relation_providers import OllamaTemporalResolver, JevTemporalRelationGate
    from prme.retrieval.temporal_relations import TemporalRelationConfig, TemporalRelationEnricher
    from tests.test_temporal_relation_providers import _records, _resolution, _identity_response, _ollama_response, NOW
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    requests = []
    deny_gate = False
    async def exchange(request):
        assert runtime.authority._lock.locked()
        requests.append((request.url.path, request.content.decode()))
        identity = _identity_response(request)
        if identity is not None:
            return identity
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            records = json.loads(body["messages"][1]["content"])["records"]
            resolution = _resolution().model_dump(mode="json")
            for operand, phrase in zip(resolution["operands"], ("finished the frame", "hung the frame"), strict=True):
                operand["evidence_id"] = next(record["id"] for record in records if phrase in record["text"])
            if deny_gate:
                runtime.authority.denied.add("transmission")
            return httpx.Response(200, json=_ollama_response(json.dumps(resolution)))
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {
            "operand_0": {"type": "noul", "noul": 0.91}, "operand_1": {"type": "noul", "noul": 0.91},
        }, "usage": {"input_tokens": 100, "output_tokens": 10}})
    try:
        for record in _records():
            await engine.store(f"{record.text} {SOURCE}", retrieval_content=record.text,
                               event_time=record.event_time, user_id=owner)
        config = TemporalRelationConfig(enabled=True, gate_api_key="synthetic-offline-key")
        transport = httpx.MockTransport(exchange)
        engine._retrieval_pipeline._temporal_relation_enricher = TemporalRelationEnricher(
            config, OllamaTemporalResolver(config, transport=transport), JevTemporalRelationGate(config, transport=transport),
        )
        query = "List all frame milestones and how long passed between finishing and hanging the frame?"
        response = await engine.retrieve(query, user_id=owner, reference_time=NOW, include_cross_scope=False)
        assert response.metadata.temporal_relation.status == "accepted"
        assert response.metadata.temporal_relation.value == "7 days"
        assert "Deterministic calendar-date difference: 7 days" in response.bundle.render()
        assert SOURCE not in str(requests) and SOURCE not in response.model_dump_json()
        assert any(path not in {"/api/chat", "/api/tags", "/api/version"} for path, _ in requests)
        deny_gate = True
        before = len(requests)
        with pytest.raises(PrivacyDenied):
            await engine.retrieve(query, user_id=owner, reference_time=NOW, include_cross_scope=False)
        assert requests[-1][0] == "/api/chat" and len(requests) == before + 3
    finally:
        runtime.authority.denied.clear()
        await engine.close()
