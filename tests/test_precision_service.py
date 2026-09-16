"""External benchmark adapter: opaque IDs, namespaces, updates, scratch reset."""

import httpx

from benchmarks.integrations.precision_service import create_app


async def test_precision_adapter_contract():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            body = {"text": "The preferred deployment region is Virginia.", "user_id": "alice",
                    "metadata": {"beliefId": "opaque-item", "scope": "project:one"}}
            added = await client.post("/add", json=body)
            assert added.status_code == 200
            assert added.json()["id"] == "opaque-item"
            query = {"query": "preferred deployment region", "user_id": "alice", "scope": "project:one"}
            found = await client.post("/search", json=query)
            assert found.status_code == 200
            assert [r["id"] for r in found.json()["results"]] == ["opaque-item"]
            for change in ({"user_id": "bob"}, {"scope": "project:two"}, {"limit": 0}):
                assert (await client.post("/search", json={**query, **change})).json() == {"results": []}
            body["text"] = "The preferred deployment region is now Oregon."
            updated = await client.put("/update", json={**body, "beliefId": "opaque-item"})
            assert updated.status_code == 200
            assert updated.json()["node_id"] != added.json()["node_id"]
            results = (await client.post("/search", json=query)).json()["results"]
            assert len(results) == 1 and "Oregon" in results[0]["memory"]
            assert (await client.delete("/reset")).status_code == 200
            assert (await client.post("/search", json=query)).json() == {"results": []}
            assert (await client.post("/add", json=body)).status_code == 200
            assert (await client.post("/search", json=query)).json()["results"]
