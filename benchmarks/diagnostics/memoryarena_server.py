"""Local raw-memory adapter for MemoryArena's three-endpoint memory contract.

This is benchmark integration, not the authenticated PRME HTTP service. Each
initialize starts a fresh logical memory while retaining old source events.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from prme import MemoryEngine, PRMEConfig
from prme.retrieval.tokenization import count_tokens
from prme.types import SourceType
from benchmarks.diagnostics.hybrid_lexical import raw_config


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    memory_system_name: str


class Add(Identity):
    chunk: str


class Query(Identity):
    question: str = Field(min_length=1)


def create_app(config: PRMEConfig, *, memory_tokens: int = 4096) -> FastAPI:
    if memory_tokens < 128:
        raise ValueError("Memory budget must be at least 128 tokens")
    wrapper = "<memory_context>\n\n</memory_context>"
    # Reserve the wrapper and boundary-token headroom; verify the final block.
    inner_budget = memory_tokens - count_tokens(wrapper, config.packing.tokenizer) - 8

    @asynccontextmanager
    async def lifespan(app):
        async with MemoryEngine.open(config) as engine:
            app.state.engine = engine
            app.state.owners = {}
            app.state.lock = asyncio.Lock()
            yield

    app = FastAPI(title="PRME MemoryArena raw adapter", lifespan=lifespan)

    def check(identity):
        if identity.memory_system_name != "prme":
            raise HTTPException(400, "This adapter serves memory_system_name='prme'")

    def owner(identity):
        check(identity)
        if identity.user_id not in app.state.owners:
            raise HTTPException(404, "User not initialized")
        return app.state.owners[identity.user_id]

    @app.post("/memory/initialize")
    async def initialize(identity: Identity):
        check(identity)
        async with app.state.lock:
            app.state.owners[identity.user_id] = str(uuid4())
        return {"status": "ok", **identity.model_dump()}

    @app.post("/memory/add")
    async def add(request: Add):
        async with app.state.lock:
            user = owner(request)
            engine = app.state.engine
            event_id = await engine.store(
                request.chunk, user_id=user, role="system",
                source_type=SourceType.EXTERNAL_DOCUMENT,
                metadata={"record_kind": "agent_environment_trace"},
            )
            status = await engine.processing_status(event_id, user_id=user)
            if status is None or status.status != "complete":
                raise HTTPException(503, {"error": "Indexing remains pending", "event_id": event_id})
            return {"status": "ok", "user_id": request.user_id,
                    "response": {"event_id": event_id}}

    @app.post("/memory/wrap_user_prompt")
    async def wrap(request: Query):
        async with app.state.lock:
            result = await app.state.engine.retrieve(
                request.question, user_id=owner(request), token_budget=inner_budget,
                include_cross_scope=False,
            )
            context = result.bundle.render() or "None"
            block = f"<memory_context>\n{context}\n</memory_context>"
            if count_tokens(block, config.packing.tokenizer) > memory_tokens:
                raise HTTPException(500, "Rendered memory exceeds declared budget")
            return {"status": "ok", "user_id": request.user_id,
                    "prompt": block + f"\nUser: {request.question}"}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8008)
    parser.add_argument("--memory-tokens", type=int, default=4096)
    args = parser.parse_args()
    if args.directory.exists():
        raise ValueError("Use a fresh directory for each benchmark server run")
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be 1–65535")
    config = raw_config().model_copy(update={
        "db_path": str(args.directory / "memory.duckdb"),
        "vector_path": str(args.directory / "vectors.usearch"),
        "lexical_path": str(args.directory / "lexical"),
        "duckdb_threads": 1,
    })
    import uvicorn
    uvicorn.run(create_app(config, memory_tokens=args.memory_tokens),
                host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
