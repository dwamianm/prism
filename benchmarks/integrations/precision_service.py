"""Scratch-only PRME adapter for the upstream PrecisionMemBench HTTP contract.

This is a benchmark service, not a production API. It owns temporary packs;
/reset cannot address an existing user pack. Each (user, named scope) gets a pack.
Opaque belief IDs round-trip as metadata and never influence ranking.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from prme import MemoryEngine, PRMEConfig
from prme.config import EmbeddingConfig


class AddRequest(BaseModel):
    text: str
    user_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)


class UpdateRequest(AddRequest):
    beliefId: str


class SearchRequest(BaseModel):
    query: str
    user_id: str
    scope: str | None = None
    limit: int = Field(default=20, ge=0, le=1000)


def create_app() -> FastAPI:
    engines: dict[tuple[str, str | None], MemoryEngine] = {}
    current_nodes: dict[tuple[str, str | None, str], str] = {}
    lock = asyncio.Lock()
    scratch: TemporaryDirectory | None = None

    async def close_packs():
        for engine in engines.values():
            await engine.close()
        engines.clear()
        current_nodes.clear()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal scratch
        scratch = TemporaryDirectory(prefix="prme-precision-")
        try:
            yield
        finally:
            await close_packs()
            scratch.cleanup()

    app = FastAPI(lifespan=lifespan)

    async def get_engine(user_id: str, scope: str | None):
        if scratch is None:
            raise RuntimeError("Benchmark service has not started")
        key = (user_id, scope)
        if key not in engines:
            digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
            path = Path(scratch.name) / digest
            path.mkdir(exist_ok=True)
            (path / "lexical").mkdir(exist_ok=True)
            engines[key] = await MemoryEngine.create(PRMEConfig(
                database_url=None, db_path=str(path / "memory.duckdb"),
                vector_path=str(path / "vectors.usearch"), lexical_path=str(path / "lexical"),
                embedding=EmbeddingConfig(provider="fastembed", model_name="BAAI/bge-small-en-v1.5", dimension=384),
                organizer={"opportunistic_enabled": False},
                enable_store_supersedence=False,
            ))
        return engines[key]

    async def add(body: AddRequest, *, replace: bool = False):
        belief_id = body.metadata.get("beliefId")
        if not isinstance(belief_id, str) or not belief_id:
            raise HTTPException(422, "metadata.beliefId is required")
        scope = body.metadata.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise HTTPException(422, "metadata.scope must be a string")
        engine = await get_engine(body.user_id, scope)
        event_id = await engine.store(body.text, user_id=body.user_id, metadata=body.metadata)
        node = (await engine.get_event_nodes(event_id, user_id=body.user_id))[0]
        key = (body.user_id, scope, belief_id)
        previous = current_nodes.get(key)
        if replace and previous is not None:
            await engine.supersede(previous, str(node.id), user_id=body.user_id)
        current_nodes[key] = str(node.id)
        return {"id": belief_id, "node_id": str(node.id), "event_id": event_id}

    @app.post("/add")
    async def add_memory(body: AddRequest):
        async with lock:
            return await add(body)

    @app.put("/update")
    async def update_memory(body: UpdateRequest):
        if body.metadata.get("beliefId") != body.beliefId:
            raise HTTPException(422, "beliefId must match metadata")
        async with lock:
            return await add(body, replace=True)

    @app.post("/search")
    async def search(body: SearchRequest):
        async with lock:
            if body.limit == 0 or not body.query.strip():
                return {"results": []}
            # An empty namespace should not need a new embedding engine.
            engine = engines.get((body.user_id, body.scope))
            if engine is None:
                return {"results": []}
            response = await engine.retrieve(body.query, user_id=body.user_id)
            return {"results": [
                {"id": candidate.node.metadata["beliefId"], "memory": candidate.node.content,
                 "metadata": candidate.node.metadata, "score": candidate.composite_score}
                for candidate in response.results[:body.limit]
            ]}

    @app.delete("/reset")
    async def reset():
        nonlocal scratch
        async with lock:
            await close_packs()
            if scratch is not None:
                scratch.cleanup()
            scratch = TemporaryDirectory(prefix="prme-precision-")
        return {"status": "reset"}

    return app


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=53091)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
