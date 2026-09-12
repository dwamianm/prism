"""Malformed object identities fail at the HTTP boundary, before storage."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from prme.api.app import create_app
from prme.api.models import ErrorResponse
from prme.config import APIConfig, PRMEConfig


ROUTES = [
    ("GET", "/events/{event_id}"), ("GET", "/events/{event_id}/nodes"),
    ("GET", "/events/{event_id}/processing-status"),
    ("GET", "/events/{event_id}/extraction"), ("GET", "/events/{event_id}/extraction-status"),
    ("POST", "/events/{event_id}/retry-extraction"),
    ("GET", "/nodes/{node_id}"), ("PUT", "/nodes/{node_id}/promote"),
    ("PUT", "/nodes/{node_id}/archive"), ("PUT", "/nodes/{node_id}/reinforce"),
    ("GET", "/nodes/{node_id}/neighborhood"), ("GET", "/nodes/{node_id}/chain"),
]


def application():
    app = create_app(PRMEConfig(api=APIConfig(user_keys={"alice": "owner-token"})))
    # Unexpected storage access is intentionally loud; HTTP validation must run
    # before any engine operation, including mutations.
    engine = SimpleNamespace(**{name: AsyncMock(side_effect=RuntimeError("storage sentinel"))
                               for name in ("get_event", "get_node", "reinforce", "promote", "archive")})
    app.state.engine = engine
    return app, engine


@pytest.mark.parametrize("method,path", ROUTES)
async def test_bad_uuid_is_422_and_never_reaches_storage(method, path):
    app, engine = application()
    path = "/v1" + path.format(event_id="not-a-uuid", node_id="not-a-uuid")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, raise_app_exceptions=False),
                                base_url="http://test", headers={"Authorization": "Bearer owner-token"}) as client:
        response = await client.request(method, path)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert ErrorResponse.model_validate(response.json()).detail == detail
    assert detail[0]["loc"][0] == "path" and detail[0]["type"] == "uuid_parsing"
    for operation in vars(engine).values():
        operation.assert_not_awaited()


async def test_valid_unknown_uuid_is_canonicalized_and_still_404():
    app, engine = application()
    engine.get_event = AsyncMock(return_value=None)
    engine.get_node = AsyncMock(return_value=None)
    identity = uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                headers={"Authorization": "Bearer owner-token"}) as client:
        for category in ("events", "nodes"):
            response = await client.get(f"/v1/{category}/{str(identity).upper()}")
            assert response.status_code == 404
    engine.get_event.assert_awaited_once_with(str(identity), user_id="alice")
    engine.get_node.assert_awaited_once_with(str(identity), include_superseded=True, user_id="alice")


def test_openapi_documents_uuid_path_parameters():
    app, _ = application()
    schema = app.openapi()
    shapes = schema["components"]["schemas"]["ErrorResponse"]["properties"]["detail"]["anyOf"]
    assert {shape["type"] for shape in shapes} == {"string", "array"}
    for method, path in ROUTES:
        parameters = schema["paths"]["/v1" + path][method.lower()]["parameters"]
        field = next(p for p in parameters if p["in"] == "path")
        assert field["schema"]["type"] == "string" and field["schema"]["format"] == "uuid"
