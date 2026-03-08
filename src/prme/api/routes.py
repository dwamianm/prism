"""PRME HTTP API route definitions.

All routes are thin wrappers around MemoryEngine methods.
No business logic belongs here — delegate everything to the engine.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from prme.api.models import (
    ErrorResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    NodeListResponse,
    NodeResponse,
    OrganizeRequest,
    OrganizeResponse,
    RetrieveRequest,
    RetrieveResponse,
    RetrieveResultItem,
    StatsResponse,
    StoreRequest,
    StoreResponse,
)
from prme.types import LifecycleState, NodeType, Scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_engine(request: Request):
    """Extract the MemoryEngine from app state."""
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return engine


def _node_to_response(node) -> NodeResponse:
    """Convert a MemoryNode to a NodeResponse."""
    return NodeResponse(
        id=str(node.id),
        user_id=node.user_id,
        node_type=node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
        content=node.content,
        lifecycle_state=node.lifecycle_state.value if hasattr(node.lifecycle_state, "value") else str(node.lifecycle_state),
        confidence=node.confidence,
        salience=node.salience,
        epistemic_type=node.epistemic_type.value if node.epistemic_type and hasattr(node.epistemic_type, "value") else (str(node.epistemic_type) if node.epistemic_type else None),
        source_type=node.source_type.value if node.source_type and hasattr(node.source_type, "value") else (str(node.source_type) if node.source_type else None),
        scope=node.scope.value if hasattr(node.scope, "value") else str(node.scope),
        metadata=node.metadata,
        created_at=node.created_at.isoformat(),
        updated_at=node.updated_at.isoformat(),
        superseded_by=str(node.superseded_by) if node.superseded_by else None,
        evidence_refs=[str(r) for r in node.evidence_refs],
        pinned=node.pinned,
    )


# ---------------------------------------------------------------------------
# Store / Ingest
# ---------------------------------------------------------------------------


@router.post(
    "/store",
    response_model=StoreResponse,
    summary="Store a memory node",
    responses={422: {"model": ErrorResponse}},
)
async def store(request: Request, body: StoreRequest) -> StoreResponse:
    """Store content across all four backends."""
    engine = _get_engine(request)

    kwargs: dict[str, Any] = {
        "content": body.content,
        "user_id": body.user_id,
        "role": body.role,
    }
    if body.node_type is not None:
        kwargs["node_type"] = body.node_type
    if body.scope is not None:
        kwargs["scope"] = body.scope
    if body.epistemic_type is not None:
        kwargs["epistemic_type"] = body.epistemic_type
    if body.metadata is not None:
        kwargs["metadata"] = body.metadata

    event_id = await engine.store(**kwargs)

    # Try to find the node created by this store call
    node_id: str | None = None
    try:
        nodes = await engine.query_nodes(user_id=body.user_id, limit=1)
        if nodes:
            # The most recently created node for this user
            # Sort by created_at descending if possible
            latest = max(nodes, key=lambda n: n.created_at)
            node_id = str(latest.id)
    except Exception:
        pass  # Non-fatal: node_id is optional

    return StoreResponse(event_id=event_id, node_id=node_id)


@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Full LLM ingestion pipeline",
    responses={422: {"model": ErrorResponse}},
)
async def ingest(request: Request, body: IngestRequest) -> IngestResponse:
    """Ingest content through the full LLM extraction pipeline."""
    engine = _get_engine(request)

    kwargs: dict[str, Any] = {
        "content": body.content,
        "user_id": body.user_id,
        "role": body.role,
    }
    if body.scope is not None:
        kwargs["scope"] = body.scope

    event_id = await engine.ingest(**kwargs)
    return IngestResponse(event_id=event_id)


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


@router.post(
    "/retrieve",
    response_model=RetrieveResponse,
    summary="Hybrid retrieval",
    responses={422: {"model": ErrorResponse}},
)
async def retrieve(request: Request, body: RetrieveRequest) -> RetrieveResponse:
    """Run hybrid retrieval pipeline."""
    engine = _get_engine(request)

    kwargs: dict[str, Any] = {
        "query": body.query,
        "user_id": body.user_id,
    }

    response = await engine.retrieve(**kwargs)

    # Convert results to API format
    items: list[RetrieveResultItem] = []
    for candidate in response.results:
        node = candidate.node
        items.append(
            RetrieveResultItem(
                node_id=str(node.id),
                content=node.content,
                score=candidate.composite_score,
                node_type=node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
                lifecycle_state=node.lifecycle_state.value if hasattr(node.lifecycle_state, "value") else str(node.lifecycle_state),
                confidence=node.confidence,
                salience=node.salience,
                epistemic_type=node.epistemic_type.value if node.epistemic_type and hasattr(node.epistemic_type, "value") else None,
                metadata=node.metadata,
            )
        )

    # Bundle as dict for serialization
    bundle_dict: dict[str, Any] | None = None
    try:
        bundle_dict = response.bundle.model_dump(mode="json")
    except Exception:
        pass

    # Metrics
    metrics: dict[str, Any] | None = None
    try:
        metrics = response.metadata.model_dump(mode="json")
    except Exception:
        pass

    return RetrieveResponse(
        results=items,
        bundle=bundle_dict,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Organize
# ---------------------------------------------------------------------------


@router.post(
    "/organize",
    response_model=OrganizeResponse,
    summary="Run organizer jobs",
    responses={422: {"model": ErrorResponse}},
)
async def organize(request: Request, body: OrganizeRequest) -> OrganizeResponse:
    """Run memory organization jobs."""
    engine = _get_engine(request)

    kwargs: dict[str, Any] = {}
    if body.user_id is not None:
        kwargs["user_id"] = body.user_id
    if body.jobs is not None:
        kwargs["jobs"] = body.jobs
    if body.budget_ms is not None:
        kwargs["budget_ms"] = body.budget_ms

    result = await engine.organize(**kwargs)

    per_job_dict: dict[str, Any] = {}
    for name, jr in result.per_job.items():
        try:
            per_job_dict[name] = jr.model_dump(mode="json")
        except Exception:
            per_job_dict[name] = {"job": name}

    return OrganizeResponse(
        jobs_run=result.jobs_run,
        per_job=per_job_dict,
        duration_ms=result.duration_ms,
    )


# ---------------------------------------------------------------------------
# Node Operations
# ---------------------------------------------------------------------------


@router.get(
    "/nodes/{node_id}",
    response_model=NodeResponse,
    summary="Get a single node",
    responses={404: {"model": ErrorResponse}},
)
async def get_node(request: Request, node_id: str) -> NodeResponse:
    """Retrieve a single node by ID."""
    engine = _get_engine(request)
    node = await engine.get_node(node_id, include_superseded=True)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    return _node_to_response(node)


@router.get(
    "/nodes",
    response_model=NodeListResponse,
    summary="Query nodes",
)
async def query_nodes(
    request: Request,
    type: str | None = None,
    state: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
) -> NodeListResponse:
    """Query nodes with flexible filters."""
    engine = _get_engine(request)

    kwargs: dict[str, Any] = {"limit": limit}
    if user_id is not None:
        kwargs["user_id"] = user_id
    if type is not None:
        try:
            kwargs["node_type"] = NodeType(type)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"Invalid node type: {type!r}"
            )
    if state is not None:
        try:
            kwargs["lifecycle_state"] = LifecycleState(state)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"Invalid lifecycle state: {state!r}"
            )

    nodes = await engine.query_nodes(**kwargs)
    return NodeListResponse(
        nodes=[_node_to_response(n) for n in nodes],
        count=len(nodes),
    )


@router.put(
    "/nodes/{node_id}/promote",
    summary="Promote node to stable",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def promote_node(request: Request, node_id: str) -> NodeResponse:
    """Promote a tentative node to stable."""
    engine = _get_engine(request)

    # Verify node exists
    node = await engine.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")

    try:
        await engine.promote(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Re-fetch to get updated state
    updated = await engine.get_node(node_id, include_superseded=True)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found after promote")
    return _node_to_response(updated)


@router.put(
    "/nodes/{node_id}/archive",
    summary="Archive a node",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def archive_node(request: Request, node_id: str) -> NodeResponse:
    """Archive a node (terminal state)."""
    engine = _get_engine(request)

    node = await engine.get_node(node_id, include_superseded=True)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")

    try:
        await engine.archive(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    updated = await engine.get_node(node_id, include_superseded=True)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found after archive")
    return _node_to_response(updated)


@router.put(
    "/nodes/{node_id}/reinforce",
    summary="Reinforce a node",
    responses={404: {"model": ErrorResponse}},
)
async def reinforce_node(request: Request, node_id: str) -> NodeResponse:
    """Reinforce a memory node, boosting confidence and salience."""
    engine = _get_engine(request)

    try:
        await engine.reinforce(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    updated = await engine.get_node(node_id, include_superseded=True)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found after reinforce")
    return _node_to_response(updated)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@router.get(
    "/nodes/{node_id}/neighborhood",
    response_model=NodeListResponse,
    summary="Get node neighborhood",
    responses={404: {"model": ErrorResponse}},
)
async def get_neighborhood(
    request: Request,
    node_id: str,
    max_hops: int = 2,
) -> NodeListResponse:
    """Get nodes within N hops of a starting node."""
    engine = _get_engine(request)

    # Verify node exists
    node = await engine.get_node(node_id, include_superseded=True)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")

    neighbors = await engine._graph_store.get_neighborhood(
        node_id, max_hops=max_hops
    )
    return NodeListResponse(
        nodes=[_node_to_response(n) for n in neighbors],
        count=len(neighbors),
    )


@router.get(
    "/nodes/{node_id}/chain",
    response_model=NodeListResponse,
    summary="Get supersedence chain",
    responses={404: {"model": ErrorResponse}},
)
async def get_chain(
    request: Request,
    node_id: str,
    direction: str = "forward",
) -> NodeListResponse:
    """Get the supersedence chain from a node."""
    engine = _get_engine(request)

    # Verify node exists
    node = await engine.get_node(node_id, include_superseded=True)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")

    chain = await engine._graph_store.get_supersedence_chain(
        node_id, direction=direction
    )
    return NodeListResponse(
        nodes=[_node_to_response(n) for n in chain],
        count=len(chain),
    )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health(request: Request) -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok", version="0.3.0")


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="System statistics",
)
async def stats(request: Request) -> StatsResponse:
    """Get system statistics."""
    engine = _get_engine(request)

    node_count = 0
    event_count = 0
    backend = "duckdb"
    details: dict[str, Any] = {}

    try:
        # Count nodes via query_nodes with high limit
        nodes = await engine.query_nodes(limit=10000)
        node_count = len(nodes)
    except Exception:
        logger.warning("Failed to count nodes for stats", exc_info=True)

    try:
        backend = engine._config.backend
    except Exception:
        pass

    return StatsResponse(
        node_count=node_count,
        event_count=event_count,
        backend=backend,
        details=details,
    )
