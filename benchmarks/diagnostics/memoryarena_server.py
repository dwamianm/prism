"""Local raw-memory adapter for MemoryArena's three-endpoint memory contract.

This is benchmark integration, not the authenticated PRME HTTP service. Each
initialize starts a fresh logical memory while retaining old source events.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from prme import MemoryEngine, PRMEConfig
from prme.retrieval.tokenization import count_tokens
from prme.types import SourceType
from benchmarks.diagnostics.hybrid_lexical import raw_config


_PLAN_MARKER = re.compile(r"^=== .+?'s Plan ===\s*$", re.MULTILINE)
_POSSESSIVE_NAME = re.compile(r"\b([A-Z][A-Za-z'-]+)[’']s\b")
_COMPANION_NAME = re.compile(
    r"\b(?:join|with)\s+([A-Z][A-Za-z'-]+)\b",
)


def _trace_projection(chunk: str) -> tuple[str | None, str | None, bool]:
    """Return a compact traveler/final-plan view without discarding the source."""
    try:
        value = json.loads(chunk)
    except (TypeError, json.JSONDecodeError):
        return None, None, False
    if not isinstance(value, dict):
        return None, None, False
    name = value.get("name")
    plan = value.get("final_plan")
    if not isinstance(name, str) or not name.strip():
        return None, None, False
    if not isinstance(plan, str) or not plan.strip():
        return None, None, False
    name = name.strip()
    matches = list(_PLAN_MARKER.finditer(plan))
    if matches:
        plan = plan[matches[-1].start():]
    is_base = value.get("is_base_person") is True
    parts = [f"Traveler: {name}"]
    query = value.get("query")
    if is_base and isinstance(query, str) and query.strip():
        parts.append(f"Trip request:\n{query.strip()}")
    parts.append(f"Final plan:\n{plan.strip()}")
    return "\n".join(parts), name, is_base


def _travel_reference_query(question: str, base_name: str | None) -> str:
    """Route by plan dependencies, excluding the participant-roster preamble."""
    lines = [line.strip() for line in question.splitlines() if line.strip()]
    constraints = "\n".join(lines[2:]) if len(lines) > 2 else question
    names: list[str] = []
    if base_name:
        names.append(base_name)
    for pattern in (_POSSESSIVE_NAME, _COMPANION_NAME):
        for match in pattern.finditer(constraints):
            name = match.group(1)
            if name not in names:
                names.append(name)
    return " ".join(names) if names else question


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
            app.state.base_names = {}
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
            app.state.base_names[identity.user_id] = None
        return {"status": "ok", **identity.model_dump()}

    @app.post("/memory/add")
    async def add(request: Add):
        async with app.state.lock:
            user = owner(request)
            engine = app.state.engine
            projection, traveler_name, is_base = _trace_projection(request.chunk)
            if is_base:
                app.state.base_names[request.user_id] = traveler_name
            event_id = await engine.store(
                request.chunk, user_id=user, role="system",
                source_type=SourceType.EXTERNAL_DOCUMENT,
                retrieval_content=projection,
                metadata={
                    "record_kind": "agent_environment_trace",
                    "retrieval_projection": (
                        "traveler_final_plan_v2" if projection is not None else "source_v1"
                    ),
                },
            )
            status = await engine.processing_status(event_id, user_id=user)
            if status is None or status.status != "complete":
                raise HTTPException(503, {"error": "Indexing remains pending", "event_id": event_id})
            return {"status": "ok", "user_id": request.user_id,
                    "response": {"event_id": event_id}}

    @app.post("/memory/wrap_user_prompt")
    async def wrap(request: Query):
        async with app.state.lock:
            user = owner(request)
            retrieval_query = _travel_reference_query(
                request.question,
                app.state.base_names[request.user_id],
            )
            result = await app.state.engine.retrieve(
                retrieval_query, user_id=user, token_budget=inner_budget,
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
