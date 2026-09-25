"""HTTP and MCP expose the same scoped ranking profile lifecycle."""
from contextlib import asynccontextmanager
import json
from uuid import uuid4

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme import MemoryEngine, RankingMultipliers
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from prme.types import Scope
from tests import test_durable_ingestion
from tests.previous_defaults import previous_defaults
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_ranking_profiles import _evidence

durable_config = test_durable_ingestion.config


@pytest.fixture
def config(durable_config):
    # Learned ranking profiles adjust the weighted formula: they keep the previous retrieval
    # defaults, which rank fusion and the reader format replaced on 2026-09-24.
    return previous_defaults(durable_config)


user = test_durable_ingestion.user


def _owned_evidence(engine, owner):
    return _evidence(engine, RankingMultipliers(lexical=2), owner=owner)


async def test_http_full_retrieval_and_profile_lifecycle(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue.", user_id=user, scope=Scope.PROJECT)
        async with client_for(app_for(config, engine, user)) as client:
            trials = []
            for index in range(2):
                body = {
                    "query": f"telescope color {index}",
                    "reference_time": "2026-09-14T20:00:00Z",
                    "limit": 1,
                    "filters": {"scope": "project", "include_cross_scope": False},
                }
                baseline = (await client.post("/v1/retrieve", json=body)).json()
                candidate = (await client.post("/v1/retrieve", json={
                    **body, "ranking_multipliers": {"lexical": 2},
                })).json()
                trials.append({
                    "group_id": f"query-{index}",
                    "baseline_request_id": baseline["metrics"]["request_id"],
                    "candidate_request_id": candidate["metrics"]["request_id"],
                    "relevant_node_ids": [baseline["results"][0]["node_id"]],
                })
            evaluated = await client.post("/v1/learning/evaluate-full-retrieval", json={
                "scopes": ["project"],
                "proposal_input_checksum": "a" * 64,
                "memory_artifact_sha256": "b" * 64,
                "candidate_multipliers": {"lexical": 2},
                "config": {"min_query_groups": 2, "bootstrap_samples": 100},
                "trials": trials,
            })
            assert evaluated.status_code == 200, evaluated.text
            assert evaluated.json()["decision"] == "no_improvement"
            assert evaluated.json()["coverage"]["query_groups"] == 2

            proposal, holdout = _owned_evidence(engine, user)
            profile_id, change_id = str(uuid4()), str(uuid4())
            created = await client.post("/v1/learning/profiles", json={
                "profile_id": profile_id,
                "proposal": proposal.model_dump(mode="json"),
                "holdout": holdout.model_dump(mode="json"),
            })
            assert created.status_code == 200, created.text
            assert created.json()["profile_id"] == profile_id
            assert len((await client.get("/v1/learning/profiles")).json()) == 1
            status = await client.get(f"/v1/learning/profiles/{profile_id}")
            assert status.json()["active"] is False
            activated = await client.post(
                f"/v1/learning/profiles/{profile_id}/activate",
                json={"change_id": change_id},
            )
            assert activated.status_code == 200, activated.text
            assert activated.json()["action"] == "activate"
            assert (await client.post(
                f"/v1/learning/profiles/{profile_id}/activate",
                json={"change_id": change_id},
            )).json() == activated.json()
            retrieved = (await client.post("/v1/retrieve", json={
                "query": "telescope", "filters": {"scope": "project"},
            })).json()
            assert retrieved["metrics"]["ranking_profile_status"] == "applied"
            assert retrieved["metrics"]["ranking_profile_id"] == profile_id
            assert (await client.get(
                "/v1/learning/profiles/active?scopes=project",
            )).json()["profile_id"] == profile_id
            deactivated = await client.post("/v1/learning/profiles/deactivate", json={
                "scopes": ["project"], "change_id": str(uuid4()),
            })
            assert deactivated.json()["action"] == "deactivate"
            assert (await client.get(
                "/v1/learning/profiles/active?scopes=project",
            )).json() is None
            rolled_back = await client.post("/v1/learning/profiles/rollback", json={
                "profile_id": profile_id, "scopes": ["project"],
            })
            assert rolled_back.status_code == 200, rolled_back.text
            assert rolled_back.json()["action"] == "rollback"
            assert len((await client.get(
                "/v1/learning/profiles/history?scopes=project",
            )).json()) == 3
            assert (await client.post("/v1/learning/profiles", json={
                "user_id": user + "-other",
                "proposal": proposal.model_dump(mode="json"),
                "holdout": holdout.model_dump(mode="json"),
            })).status_code == 403


async def test_mcp_profile_lifecycle_and_owner_binding(config, user):
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        proposal, holdout = _owned_evidence(engine, user)

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(
            server._mcp_server, raise_exceptions=True,
        ) as session:
            await session.initialize()

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.isError, response
                return json.loads(response.content[0].text)

            profile_id = str(uuid4())
            profile = await call("memory_create_ranking_profile", {
                "profile_id": profile_id,
                "proposal": proposal.model_dump(mode="json"),
                "holdout": holdout.model_dump(mode="json"),
            })
            assert profile["profile_id"] == profile_id
            assert len(await call("memory_list_ranking_profiles", {})) == 1
            assert (await call("memory_get_ranking_profile", {
                "profile_id": profile_id,
            }))["active"] is False
            assert (await call("memory_activate_ranking_profile", {
                "profile_id": profile_id,
            }))["action"] == "activate"
            assert (await call("memory_get_active_ranking_profile", {
                "scopes": ["project"],
            }))["profile_id"] == profile_id
            assert (await call("memory_deactivate_ranking_profile", {
                "scopes": ["project"],
            }))["action"] == "deactivate"
            assert await call("memory_get_active_ranking_profile", {
                "scopes": ["project"],
            }) is None
            assert (await call("memory_rollback_ranking_profile", {
                "profile_id": profile_id, "scopes": ["project"],
            }))["action"] == "rollback"
            assert len(await call("memory_list_ranking_profile_history", {
                "scopes": ["project"],
            })) == 3
            assert "error" in await call("memory_get_ranking_profile", {
                "profile_id": profile_id, "user_id": user + "-other",
            })
