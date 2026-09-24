"""Under rank fusion, min_score fails open when the vector path is down (issue #150).

Only the vector path gives a result its semantic cosine, which is what min_score
compares under rank fusion. When it fails or detects an embedding mismatch,
retrieval skips the floor, keeps the fused order and reports min_score_skipped.
"""

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

from prme import MemoryEngine
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, PackingConfig
from prme.retrieval.models import MemoryBundle
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.selection import rank_fusion_skips_min_score, with_rank_fusion_relevance
from prme.retrieval.session_context import expand_session_context
from prme.types import Scope
from tests import test_durable_ingestion, test_rank_fusion_min_score
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_rank_fusion import EXECUTION, NOW, RRF, candidate

config = test_durable_ingestion.config
user = test_durable_ingestion.user
keyword_config = test_rank_fusion_min_score.keyword_config
UNRELATED = test_rank_fusion_min_score.UNRELATED

FIXTURES = Path(__file__).parent / "fixtures/relevance"
# Written by make_receipt on main before this change: rank fusion, a session
# neighbor at session_context_rank_fusion_score_decay 0.7 and a min_score of 0.5.
V17_CHECKSUM = "565860a967edc591f4599570d573dbf853b636b6c64ad2b60f138f41088fea0d"
VECTOR_DOWN = {"VECTOR": "backend_error"}
QUERY = "telescope lens"
KWARGS = {"scope": Scope.PROJECT, "include_cross_scope": False, "reference_time": NOW}
# With the vector path down, only lexical search finds the primary memory, and
# the hint pass runs only when the primary pass found something. PostgreSQL's
# plainto_tsquery requires every query word (Tantivy needs any one), so the
# primary memory contains all of QUERY to be found on both backends.
PRIMARY = "The telescope lens is blue."


def _fail_vector(engine, monkeypatch, failure="backend_error"):
    provider = engine._vector_index._provider
    if failure == "backend_error":
        monkeypatch.setattr(provider, "embed", AsyncMock(side_effect=RuntimeError("private detail")))
    else:
        monkeypatch.setattr(provider, "model_version", "incompatible-version")


async def _operation_payload(engine, request_id):
    rows = await engine._relevance._query(
        "SELECT payload FROM operations WHERE op_type = 'RETRIEVAL_REQUEST' AND target_id = $1",
        str(request_id))
    payload = rows[0]["payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


def _lexical_pool(*extra):
    ranked, _ = score_and_rank([candidate(1, lexical=1.0), candidate(2, lexical=.4), *extra],
                               RRF, now=NOW)
    return with_rank_fusion_relevance(ranked)


# --- Decision -----------------------------------------------------------------


@pytest.mark.parametrize("reason", ["backend_error", "embedding_mismatch"])
def test_the_floor_is_skipped_when_the_vector_path_failed_and_nothing_has_a_cosine(reason):
    assert rank_fusion_skips_min_score(_lexical_pool(), min_score=.3,
                                       backend_failures={"VECTOR": reason})


@pytest.mark.parametrize("min_score,failures,extra", [
    (None, VECTOR_DOWN, ()),
    (0, VECTOR_DOWN, ()),
    # A healthy vector path that returned nothing keeps failing closed.
    (.3, {}, ()),
    (.3, {"LEXICAL": "backend_error", "LEXICAL_AGG": "backend_error"}, ()),
    # A cosine from another pass, such as a query reformulation.
    (.3, VECTOR_DOWN, (candidate(3, semantic=.2),)),
])
def test_the_floor_is_kept_otherwise(min_score, failures, extra):
    assert not rank_fusion_skips_min_score(_lexical_pool(*extra), min_score=min_score,
                                           backend_failures=failures)


def test_an_empty_pool_cannot_evaluate_the_floor():
    # Nothing to compare; cross-scope hints then follow the same decision.
    assert rank_fusion_skips_min_score([], min_score=.3, backend_failures=VECTOR_DOWN)
    assert not rank_fusion_skips_min_score([], min_score=.3, backend_failures={})


# --- Receipts -----------------------------------------------------------------


def _skipped_receipt(items=None, *, packing=None, skipped=True, scoring=RRF):
    items = _lexical_pool() if items is None else items
    return make_receipt(request_id=UUID(int=150), user_id="owner", query="telescope",
                        reference_time=NOW, scopes=(Scope.PROJECT,), scoring=scoring,
                        packing=packing or PackingConfig(), candidates=items,
                        bundle=MemoryBundle(), min_score=.3, execution=EXECUTION,
                        min_score_skipped=skipped)


def test_a_skipped_floor_is_recorded_in_a_version_18_receipt():
    saved = _skipped_receipt()
    raw = saved.model_dump_json()
    assert saved.schema_version == 18 and saved.min_score == .3 and saved.min_score_skipped
    assert json.loads(raw)["min_score_skipped"] is True
    assert [c.semantic_relevance for c in saved.candidates] == [0.0, 0.0]
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.model_dump_json() == raw and restored.checksum == saved.checksum
    assert restored.replay_ranking() == tuple(c.node_id for c in saved.candidates)
    # Without the flag the same selection contradicts the floor, as before.
    with pytest.raises(ValidationError, match="min_score selection"):
        _skipped_receipt(skipped=False)
    # Weighted scoring never skips its floor.
    ranked, _ = score_and_rank([candidate(1, lexical=1.0)], now=NOW)
    with pytest.raises(ValueError, match="Only rank fusion skips min_score"):
        _skipped_receipt(ranked, scoring=DEFAULT_SCORING_WEIGHTS)


def test_saved_version_17_receipts_keep_their_bytes():
    # The version 17 rules were rewritten to admit version 18.
    raw = (FIXTURES / "receipt-v17-rrf.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == V17_CHECKSUM
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 17 and not restored.min_score_skipped
    assert restored.packing.session_context_rank_fusion_score_decay == .7
    assert restored.model_dump_json() == raw and restored.checksum == V17_CHECKSUM
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)
    assert [c.score for c in restored.candidates] == [1.0, 0.9838709677, .7]


def test_other_receipts_omit_the_flag():
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], RRF, now=NOW)
    saved = _skipped_receipt(with_rank_fusion_relevance(ranked), skipped=False)
    assert saved.schema_version == 16 and not saved.min_score_skipped
    assert "min_score_skipped" not in json.loads(saved.model_dump_json())


async def test_version_18_also_records_and_checks_the_rank_fusion_session_decay():
    trigger = candidate(1, lexical=.9, session="conversation")
    neighbor = candidate(2, session="conversation")
    ranked, _ = score_and_rank([trigger], RRF, now=NOW)
    packing = PackingConfig(session_context_rank_fusion_score_decay=.7)
    graph = Mock(query_nodes=AsyncMock(return_value=[trigger.node, neighbor.node]))
    expanded = await expand_session_context(ranked, graph, "owner", packing)
    saved = _skipped_receipt(with_rank_fusion_relevance(expanded), packing=packing)
    data = json.loads(saved.model_dump_json())
    assert data["schema_version"] == 18
    assert data["packing"]["session_context_rank_fusion_score_decay"] == .7
    assert RetrievalReceipt.model_validate(data) == saved
    data["packing"]["session_context_rank_fusion_score_decay"] = .65
    with pytest.raises(ValidationError, match="does not match the recorded rank fusion session decay"):
        RetrievalReceipt.model_validate(data)


@pytest.mark.parametrize("version", [15, 16, 17])
def test_earlier_rank_fusion_receipts_cannot_record_a_skipped_floor(version):
    data = json.loads(_skipped_receipt().model_dump_json())
    data["schema_version"] = version
    if version == 15:
        for item in data["candidates"]:
            item.pop("semantic_relevance")
    with pytest.raises(ValidationError, match="A skipped min_score requires a version 18 receipt"):
        RetrievalReceipt.model_validate(data)


@pytest.mark.parametrize("change,match", [
    ("not_skipped", "Version 18 records a skipped min_score"),
    ("no_floor", "positive min_score"),
    ("zero_floor", "positive min_score"),
    ("negative_floor", "positive min_score"),
    ("cosine", "no candidate has a cosine"),
])
def test_skipped_floor_receipts_are_validated(change, match):
    data = json.loads(_skipped_receipt().model_dump_json())
    if change == "not_skipped":
        data.pop("min_score_skipped")
    elif change == "no_floor":
        data["min_score"] = None
    elif change == "zero_floor":
        data["min_score"] = 0
    elif change == "negative_floor":
        data["min_score"] = -1
    else:
        data["candidates"][0]["semantic_relevance"] = .4
    with pytest.raises(ValidationError, match=match):
        RetrievalReceipt.model_validate(data)


# --- Retrieval ----------------------------------------------------------------


@pytest.mark.parametrize("failure", ["backend_error", "embedding_mismatch"])
async def test_a_vector_failure_skips_the_floor_and_reports_it(keyword_config, user, monkeypatch,
                                                               failure):
    async with MemoryEngine.open(keyword_config) as engine:
        for text in UNRELATED:
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        healthy = await engine.retrieve(QUERY, user_id=user, min_score=.3, **KWARGS)
        assert len(healthy.results) == 2 and healthy.metadata.min_score_skipped is False

        _fail_vector(engine, monkeypatch, failure)
        unfloored = await engine.retrieve(QUERY, user_id=user, **KWARGS)
        floored = await engine.retrieve(QUERY, user_id=user, min_score=.3, **KWARGS)
        zero = await engine.retrieve(QUERY, user_id=user, min_score=0, **KWARGS)

        assert floored.metadata.backend_failures == {"VECTOR": failure}
        assert floored.metadata.embedding_mismatch is (failure == "embedding_mismatch")
        assert floored.metadata.min_score_skipped is True and floored.metadata.min_score == .3
        # The results the floor would have removed, in their fused order.
        assert floored.results
        assert [c.node.id for c in floored.results] == [c.node.id for c in unfloored.results]
        assert [c.composite_score for c in floored.results] == [
            c.composite_score for c in unfloored.results
        ]
        assert all(c.semantic_relevance == 0 for c in floored.results)
        assert not any(item.reason == "below_threshold" for item in floored.excluded)
        assert floored.bundle.included_count == unfloored.bundle.included_count
        # Without a positive floor there is nothing to skip.
        assert not unfloored.metadata.min_score_skipped and not zero.metadata.min_score_skipped

        saved = await engine.get_retrieval_receipt(str(floored.metadata.request_id), user_id=user)
        assert saved.schema_version == 18 and saved.min_score == .3 and saved.min_score_skipped
        assert [c.node_id for c in saved.candidates] == [c.node.id for c in floored.results]
        assert all(c.semantic_relevance == 0 for c in saved.candidates)
        plain = await engine.get_retrieval_receipt(str(unfloored.metadata.request_id), user_id=user)
        assert plain.schema_version == 16 and not plain.min_score_skipped
        logged = await _operation_payload(engine, floored.metadata.request_id)
        assert logged["min_score_skipped"] is True and logged["min_score"] == .3
        assert (await _operation_payload(engine, unfloored.metadata.request_id))[
            "min_score_skipped"
        ] is False


async def test_a_per_request_rank_fusion_skips_the_floor_too(keyword_config, user, monkeypatch):
    settings = keyword_config.model_copy(update={"scoring": DEFAULT_SCORING_WEIGHTS})
    async with MemoryEngine.open(settings) as engine:
        for text in UNRELATED:
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        _fail_vector(engine, monkeypatch)
        response = await engine.retrieve(QUERY, user_id=user, weights=RRF, min_score=.3, **KWARGS)
        saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
    assert response.results and response.metadata.min_score_skipped is True
    assert saved.schema_version == 18 and saved.scoring.fusion == "rrf"


async def test_another_backend_failure_keeps_the_floor(keyword_config, user, monkeypatch):
    async with MemoryEngine.open(keyword_config) as engine:
        for text in UNRELATED:
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        monkeypatch.setattr(engine._lexical_index, "search",
                            AsyncMock(side_effect=OSError("private path")))
        relevant = await engine.retrieve(QUERY, user_id=user, min_score=.3, **KWARGS)
        unrelated = await engine.retrieve("What is the boiling point of mercury?", user_id=user,
                                          min_score=.3, **KWARGS)
    assert relevant.metadata.backend_failures == {"LEXICAL": "backend_error"}
    assert [c.node.content for c in relevant.results] == ["The telescope lens is cracked.",
                                                        "The telescope is blue."]
    assert unrelated.results == []
    assert not relevant.metadata.min_score_skipped and not unrelated.metadata.min_score_skipped


async def test_a_vector_search_that_finds_nothing_keeps_the_floor(keyword_config, user):
    packing = keyword_config.packing.model_copy(update={"vector_k": 0})
    async with MemoryEngine.open(keyword_config.model_copy(update={"packing": packing})) as engine:
        for text in UNRELATED:
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        unfloored = await engine.retrieve(QUERY, user_id=user, **KWARGS)
        floored = await engine.retrieve(QUERY, user_id=user, min_score=.3, **KWARGS)
    assert unfloored.results and floored.metadata.backend_failures == {}
    assert floored.results == [] and floored.metadata.min_score_skipped is False


async def test_cross_scope_hints_follow_the_skipped_floor(keyword_config, user, monkeypatch):
    async with MemoryEngine.open(keyword_config) as engine:
        await engine.store(PRIMARY, user_id=user, scope=Scope.PROJECT)
        await engine.store("The telescope lens is cracked.", user_id=user, scope=Scope.PERSONAL)
        _fail_vector(engine, monkeypatch)
        response = await engine.retrieve(QUERY, user_id=user, scope=Scope.PROJECT, min_score=.3,
                                         reference_time=NOW)
    assert response.metadata.min_score_skipped is True
    assert [c.node.content for c in response.results] == [PRIMARY]
    assert [c.node.content for c in response.cross_scope_hints] == ["The telescope lens is cracked."]


async def test_cross_scope_hints_with_a_cosine_keep_the_floor(keyword_config, user, monkeypatch):
    async with MemoryEngine.open(keyword_config) as engine:
        await engine.store(PRIMARY, user_id=user, scope=Scope.PROJECT)
        await engine.store("The telescope lens is cracked.", user_id=user, scope=Scope.PERSONAL)
        await engine.store("We bought bread yesterday.", user_id=user, scope=Scope.PERSONAL)
        provider = engine._vector_index._provider
        embed, calls = provider.embed, []

        async def fails_once(texts):
            # The primary search fails; the hint pass's own search succeeds.
            calls.append(texts)
            if len(calls) == 1:
                raise RuntimeError("private detail")
            return await embed(texts)

        monkeypatch.setattr(provider, "embed", fails_once)
        response = await engine.retrieve(QUERY, user_id=user, scope=Scope.PROJECT, min_score=.3,
                                         reference_time=NOW)
    assert len(calls) == 2 and response.metadata.backend_failures == VECTOR_DOWN
    assert response.metadata.min_score_skipped is True
    assert [c.node.content for c in response.results] == [PRIMARY]
    assert [(c.node.content, c.semantic_relevance >= .3) for c in response.cross_scope_hints] == [
        ("The telescope lens is cracked.", True),
    ]


async def test_weighted_scoring_keeps_its_floor_when_the_vector_path_fails(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue.", user_id=user, scope=Scope.PROJECT)
        await engine.store("We bought bread.", user_id=user, scope=Scope.PROJECT)
        _fail_vector(engine, monkeypatch)
        response = await engine.retrieve("telescope", user_id=user, reference_time=NOW)
        best = response.results[0].composite_score
        floored = await engine.retrieve("telescope", user_id=user, reference_time=NOW,
                                        min_score=best + .01)
        saved = await engine.get_retrieval_receipt(str(floored.metadata.request_id), user_id=user)
    assert floored.metadata.backend_failures == VECTOR_DOWN
    assert floored.results == [] and floored.metadata.min_score_skipped is False
    assert saved.schema_version == 12 and not saved.min_score_skipped


async def test_http_and_mcp_report_the_skipped_floor(keyword_config, user, monkeypatch):
    settings = keyword_config.model_copy(update={"mcp": MCPConfig(user_id=user)})
    async with MemoryEngine.open(settings) as engine:
        for text in UNRELATED:
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        body = {"query": QUERY, "min_score": .3}

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        async def both(version):
            async with client_for(app_for(settings, engine, user)) as client:
                response = await client.post("/v1/retrieve", json=body)
                assert response.status_code == 200, response.text
                request_id = response.json()["metrics"]["request_id"]
                served = await client.get(f"/v1/retrievals/{request_id}")
            assert served.status_code == 200, served.text
            saved = await engine.get_retrieval_receipt(request_id, user_id=user)
            assert served.json() == json.loads(saved.model_dump_json())
            assert served.json()["schema_version"] == version
            server = create_mcp_server(settings, lifespan=lifespan)
            async with create_connected_server_and_client_session(
                server._mcp_server, raise_exceptions=True,
            ) as session:
                await session.initialize()
                reply = await session.call_tool("memory_retrieve", body)
            assert "private detail" not in response.text + reply.content[0].text
            return response.json(), json.loads(reply.content[0].text)

        healthy = await both(16)
        _fail_vector(engine, monkeypatch)
        degraded = await both(18)

    for payload in healthy:
        assert payload["metrics"]["min_score_skipped"] is False and payload["results"]
    for payload in degraded:
        assert payload["metrics"]["min_score_skipped"] is True
        assert payload["metrics"]["backend_failures"] == VECTOR_DOWN
        assert payload["results"] and all(item["semantic_relevance"] == 0
                                          for item in payload["results"])
    assert [item["node_id"] for item in degraded[0]["results"]] == [
        item["node_id"] for item in degraded[1]["results"]
    ]
