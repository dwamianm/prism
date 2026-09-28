"""Serve the explorer and PRME API together, without a frontend build step."""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from prme.api.app import create_app
from prme.api.models import NodeResponse
from prme.api.routes import _get_engine, _node_to_response, _user_id, require_api_key
from prme.api.server import UnauthenticatedBindError, _check_bind
from prme.config import PRMEConfig, _ProjectSettings
from prme.models.edges import MemoryEdge
from prme.types import EdgeType


class ConnectionPage(BaseModel):
    """Stored incident edges, not an inferred or historical graph snapshot."""

    node: NodeResponse
    nodes: list[NodeResponse] = Field(default_factory=list)
    edges: list[MemoryEdge] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: UUID | None = None


router = APIRouter(prefix="/v1/explorer", dependencies=[Depends(require_api_key)])


@router.get("/nodes/{node_id}/connections", response_model=ConnectionPage)
async def connections(
    request: Request,
    node_id: UUID,
    user_id: str | None = None,
    after_id: UUID | None = None,
    limit: int = Query(default=24, ge=1, le=100),
    include_proposals: bool = False,
) -> ConnectionPage:
    """Inspect one hop within the selected node's owner and exact scope.

    Includes closed edges and retired nodes for inspection. Unverified alias
    proposals require explicit opt-in. Independent pages are not a snapshot.
    The backend currently materializes the selected node's incident edges before
    this adapter filters and pages them; it never scans the complete graph.
    """
    owner = _user_id(request, user_id, required=True)
    engine = _get_engine(request)
    node = await engine.get_node(str(node_id), user_id=owner, include_superseded=True)
    if node is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    edges = await engine._graph_store.get_edges(node_ids=[str(node_id)])
    candidates = []
    for edge in edges:
        metadata = edge.metadata or {}
        proposal = (
            edge.edge_type == EdgeType.RELATES_TO
            and metadata.get("relation") == "alias"
            and str(metadata.get("identity_verified")).lower() == "false"
        )
        if edge.user_id == owner and (include_proposals or not proposal):
            candidates.append(edge)

    counterpart_ids = sorted({
        str(edge.target_id if edge.source_id == node_id else edge.source_id)
        for edge in candidates
    })
    visible = {node.id: node}
    # Batch hydration to avoid one query per edge and oversized SQL parameter lists.
    for start in range(0, len(counterpart_ids), 200):
        neighbors = await engine._graph_store.get_nodes(
            counterpart_ids[start:start + 200], include_superseded=True,
        )
        visible.update({n.id: n for n in neighbors if n.scope == node.scope and n.user_id == owner})
    eligible = sorted(
        (e for e in candidates if e.source_id in visible and e.target_id in visible
         and (after_id is None or e.id.int > after_id.int)),
        key=lambda e: e.id.int,
    )
    page = eligible[:limit]
    ids = {endpoint for edge in page for endpoint in (edge.source_id, edge.target_id)}
    return ConnectionPage(
        node=_node_to_response(node),
        nodes=[_node_to_response(visible[key]) for key in sorted(ids - {node.id}, key=str)],
        edges=page,
        has_more=len(eligible) > limit,
        next_cursor=page[-1].id if len(eligible) > limit else None,
    )


class ExplorerSettings(_ProjectSettings):
    """Explorer-only settings, read from the environment and `.env` like PRMEConfig."""

    model_config = {"env_prefix": "PRME_"}

    chat_data_dir: Path | None = Field(
        default=None,
        description="Local pack folder shared with examples/chat.py. Supplies db_path, "
                    "vector_path and lexical_path when those are not set themselves.",
    )


_PACK_FILES = {"db_path": "memory.duckdb", "vector_path": "vectors.usearch", "lexical_path": "lexical_index"}


def explorer_config() -> PRMEConfig:
    """Load PRMEConfig, placing unset local pack paths inside PRME_CHAT_DATA_DIR.

    Without this, the explorer used the working-directory defaults and silently
    created an empty pack in the repository root instead of opening the chat
    example's pack. A missing folder is an error for the same reason: the
    explorer inspects an existing pack and should not start one at a mistyped
    path. Explicit PRME_DB_PATH, PRME_VECTOR_PATH and PRME_LEXICAL_PATH still
    win, and PostgreSQL (PRME_DATABASE_URL) ignores the folder.
    """
    config = PRMEConfig()
    data_dir = ExplorerSettings().chat_data_dir
    if data_dir is None or config.database_url is not None:
        return config
    data_dir = data_dir.expanduser().resolve()
    if not data_dir.is_dir():
        raise FileNotFoundError(f"PRME_CHAT_DATA_DIR is not a directory: {data_dir}")
    paths = {
        field: str(data_dir / name)
        for field, name in _PACK_FILES.items()
        if field not in config.model_fields_set
    }
    return PRMEConfig(**paths) if paths else config


def create_explorer_app(config: PRMEConfig | None = None):
    app = create_app(config)
    app.include_router(router)

    @app.middleware("http")
    async def no_memory_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="explorer")
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="PRME memory explorer", allow_abbrev=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        config = explorer_config()
    except FileNotFoundError as exc:
        parser.error(str(exc))
    try:
        _check_bind(args.host, config, allow_unauthenticated_external_bind=False)
    except UnauthenticatedBindError as exc:
        parser.error(str(exc))
    import uvicorn

    uvicorn.run(create_explorer_app(config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
