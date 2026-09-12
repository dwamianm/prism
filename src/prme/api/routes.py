"""PRME HTTP API route definitions.

All routes are thin wrappers around MemoryEngine methods.
No business logic belongs here — delegate everything to the engine.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from prme import __version__
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
from prme.types import LifecycleState, NodeType
from prme.models.extraction import ExtractionRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Require a valid bearer token when an API key is configured.

    Per-user keys bind requests to their configured owner. The legacy global
    key retains operator access. With neither configured, access is local and
    unrestricted. Every configured mode requires a bearer token.
    """
    config = getattr(request.app.state, "config", None)
    api = config.api if config is not None else None
    request.state.user_id = None
    if api is None or (api.api_key is None and not api.user_keys):
        return
    token = credentials.credentials.encode("utf-8") if credentials else b""
    if api.user_keys:
        # Inspect all configured credentials; never derive identity from request data.
        owner = None
        for user_id, key in api.user_keys.items():
            if secrets.compare_digest(token, key.get_secret_value().encode("utf-8")):
                owner = user_id
        if owner is not None:
            request.state.user_id = owner
            return
    elif credentials and secrets.compare_digest(token, api.api_key.get_secret_value().encode("utf-8")):
        return
    raise HTTPException(
        status_code=401, detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _user_id(request: Request, requested: str | None = None, *, required: bool = False) -> str | None:
    """Use the authenticated identity and reject attempts to select another owner."""
    bound = getattr(request.state, "user_id", None)
    if bound is not None and requested is not None and requested != bound:
        raise HTTPException(status_code=403, detail="User does not match authenticated identity")
    owner = bound if bound is not None else requested
    if required and not owner:
        raise HTTPException(status_code=422, detail="user_id is required without a bound identity")
    return owner


# Protected router: all memory operations require authentication when
# an API key is configured.
router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

# Public router: liveness/health checks only — never requires auth.
public_router = APIRouter(prefix="/v1")


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
        "user_id": _user_id(request, body.user_id, required=True),
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

    nodes = await engine.get_event_nodes(event_id, user_id=kwargs["user_id"])
    node_id = next((str(n.id) for n in nodes
                    if n.content == body.content and n.node_type == (body.node_type or NodeType.NOTE)), None)

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
        "user_id": _user_id(request, body.user_id, required=True),
        "role": body.role,
    }
    if body.scope is not None:
        kwargs["scope"] = body.scope

    event_id = await engine.ingest(**kwargs)
    return IngestResponse(event_id=event_id)


@router.get("/events/{event_id}", summary="Read original source evidence")
async def get_event(request: Request, event_id: str):
    event = await _get_engine(request).get_event(event_id, user_id=_user_id(request))
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event.model_dump(mode="json")


@router.get("/events/{event_id}/nodes", response_model=NodeListResponse, summary="Resolve source derivations")
async def get_event_nodes(request: Request, event_id: str):
    engine = _get_engine(request)
    event = await engine.get_event(event_id, user_id=_user_id(request))
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    nodes = await engine.get_event_nodes(event_id, user_id=event.user_id)
    return NodeListResponse(nodes=[_node_to_response(n) for n in nodes], count=len(nodes))


@router.get("/events/{event_id}/extraction", response_model=ExtractionRecord, summary="Read saved model extraction")
async def get_extraction(request: Request, event_id: UUID):
    engine = _get_engine(request)
    event = await engine.get_event(str(event_id), user_id=_user_id(request))
    if event is None:
        raise HTTPException(status_code=404, detail="Extraction not found")
    record = await engine.get_extraction(str(event_id), user_id=event.user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction not found")
    return record


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
        "user_id": _user_id(request, body.user_id, required=True),
    }
    if body.reference_time is not None:
        kwargs["reference_time"] = body.reference_time

    for name in ("limit", "min_score", "token_budget"):
        value = getattr(body, name)
        if value is not None:
            kwargs[name] = value
    if body.mode is not None:
        kwargs["retrieval_mode"] = body.mode
    if body.filters is not None:
        kwargs.update(body.filters.model_dump(exclude_none=True))

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
    owner = _user_id(request, body.user_id)
    if owner is not None:
        kwargs["user_id"] = owner
    if body.jobs is not None:
        kwargs["jobs"] = body.jobs
    if body.budget_ms is not None:
        kwargs["budget_ms"] = body.budget_ms

    if getattr(request.state, "user_id", None) is not None:
        from prme.organizer import ALL_JOBS

        if body.jobs is not None and "feedback_apply" in body.jobs:
            raise HTTPException(status_code=403, detail="Global feedback maintenance requires an operator")
        if body.jobs is None:
            kwargs["jobs"] = [name for name in ALL_JOBS if name != "feedback_apply"]
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
    node = await engine.get_node(node_id, include_superseded=True, user_id=_user_id(request))
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
    owner = _user_id(request, user_id)
    if owner is not None:
        kwargs["user_id"] = owner
    if type is not None:
        try:
            kwargs["node_type"] = NodeType(type)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"Invalid node type: {type!r}"
            )
    if state is not None:
        try:
            kwargs["lifecycle_states"] = [LifecycleState(state)]
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
    node = await engine.get_node(node_id, user_id=_user_id(request))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")

    try:
        await engine.promote(node_id, user_id=_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Re-fetch to get updated state
    updated = await engine.get_node(node_id, include_superseded=True, user_id=_user_id(request))
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

    node = await engine.get_node(node_id, include_superseded=True, user_id=_user_id(request))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")

    try:
        await engine.archive(node_id, user_id=_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    updated = await engine.get_node(node_id, include_superseded=True, user_id=_user_id(request))
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
        await engine.reinforce(node_id, user_id=_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    updated = await engine.get_node(node_id, include_superseded=True, user_id=_user_id(request))
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
    node = await engine.get_node(node_id, include_superseded=True, user_id=_user_id(request))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")

    neighbors = await engine._graph_store.get_neighborhood(
        node_id, max_hops=max_hops
    )
    owner = _user_id(request)
    if owner is not None:
        neighbors = [n for n in neighbors if n.user_id == owner]
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
    node = await engine.get_node(node_id, include_superseded=True, user_id=_user_id(request))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")

    chain = await engine._graph_store.get_supersedence_chain(
        node_id, direction=direction
    )
    owner = _user_id(request)
    if owner is not None:
        chain = [n for n in chain if n.user_id == owner]
    return NodeListResponse(
        nodes=[_node_to_response(n) for n in chain],
        count=len(chain),
    )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@public_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health(request: Request) -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok", version=__version__)


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="System statistics",
)
async def stats(request: Request, user_id: str | None = None) -> StatsResponse:
    """Get system statistics, optionally scoped to a single user."""
    engine = _get_engine(request)
    user_id = _user_id(request, user_id)

    node_count = 0
    event_count = 0
    backend = "duckdb"
    details: dict[str, Any] = {}

    try:
        node_count = await engine.count_nodes(user_id=user_id)
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
