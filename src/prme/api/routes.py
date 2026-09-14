"""PRME HTTP API route definitions.

All routes are thin wrappers around MemoryEngine methods.
No business logic belongs here — delegate everything to the engine.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from prme import __version__
from prme.storage.reinforcement import ReinforcementConflict
from prme.storage.condition_evaluation import ConditionEvaluationConflict
from prme.storage.lifecycle import LifecycleConflict
from prme.storage.citations import CitationConflict
from prme.models.relevance import (
    AnswerCitationRecord,
    AnswerCitationSubmission,
    RelevanceRecord,
    RelevanceSubmission,
    RetrievalReceipt,
)
from prme.models.provenance import NodeProvenance
from prme.api.models import (
    AcceptedWorkErrorResponse,
    AnswerCitationRequest,
    ConditionEvaluationRequest,
    ContradictionRequest,
    ContradictionResolutionRequest,
    ErrorResponse,
    ExtractionProcessRequest,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    MaterializationProcessRequest,
    NodeListResponse,
    NodePageResponse,
    NodeResponse,
    OrganizeRequest,
    OrganizeResponse,
    RelevanceRequest,
    ReinforceRequest,
    RetrieveRequest,
    RetrieveResponse,
    RetrieveResultItem,
    StatsResponse,
    StoreRequest,
    StoreResponse,
    SupersedenceRequest,
)
from prme.types import LifecycleState, NodeType, Scope
from prme.models.extraction import ExtractionRecord
from prme.models.extraction_work import ExtractionStatus, ExtractionProcessingResult
from prme.models.processing import ProcessingStatus, ProcessingResult
from prme.ingestion.errors import ExtractionError, MaterializationError, extraction_failure_code

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
        session_id=node.session_id,
        node_type=node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
        content=node.content,
        lifecycle_state=node.lifecycle_state.value if hasattr(node.lifecycle_state, "value") else str(node.lifecycle_state),
        confidence=node.confidence,
        salience=node.salience,
        confidence_base=node.confidence_base,
        salience_base=node.salience_base,
        reinforcement_boost=node.reinforcement_boost,
        last_reinforced_at=node.last_reinforced_at.isoformat() if node.last_reinforced_at else None,
        decay_profile=node.decay_profile.value,
        epistemic_type=node.epistemic_type.value if node.epistemic_type and hasattr(node.epistemic_type, "value") else (str(node.epistemic_type) if node.epistemic_type else None),
        source_type=node.source_type.value if node.source_type and hasattr(node.source_type, "value") else (str(node.source_type) if node.source_type else None),
        scope=node.scope.value if hasattr(node.scope, "value") else str(node.scope),
        metadata=node.metadata,
        created_at=node.created_at.isoformat(),
        updated_at=node.updated_at.isoformat(),
        event_time=node.event_time.isoformat() if node.event_time else None,
        valid_from=node.valid_from.isoformat(),
        valid_to=node.valid_to.isoformat() if node.valid_to else None,
        ttl_days=node.ttl_days,
        superseded_by=str(node.superseded_by) if node.superseded_by else None,
        evidence_refs=[str(r) for r in node.evidence_refs],
        pinned=node.pinned,
    )


# ---------------------------------------------------------------------------
# Store / Ingest
# ---------------------------------------------------------------------------


def _accepted_work_failure(exc: ExtractionError | MaterializationError) -> JSONResponse:
    """Expose a saved source receipt without echoing provider messages."""
    try:
        event_id = str(UUID(exc.event_id))
    except (TypeError, ValueError, AttributeError):
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    body = AcceptedWorkErrorResponse(
        detail="Source saved; processing did not complete. Inspect its status and retry the saved work.",
        event_id=event_id, reason_code=extraction_failure_code(exc),
    )
    return JSONResponse(status_code=503, content=body.model_dump(mode="json"))


@router.post(
    "/store",
    response_model=StoreResponse,
    summary="Store a memory node",
    responses={422: {"model": ErrorResponse}, 503: {"model": AcceptedWorkErrorResponse | ErrorResponse}},
)
async def store(request: Request, body: StoreRequest) -> StoreResponse | JSONResponse:
    """Store content across all four backends."""
    engine = _get_engine(request)

    kwargs: dict[str, Any] = {
        "content": body.content,
        "user_id": _user_id(request, body.user_id, required=True),
        "role": body.role,
    }
    for name in ("node_type", "scope", "epistemic_type", "metadata", "session_id",
                 "source_type", "confidence", "event_time"):
        value = getattr(body, name)
        if value is not None:
            kwargs[name] = value
    # Omitted TTL means use the engine's configured default; JSON null is an
    # explicit request to disable it. exclude_none would erase that distinction.
    if "ttl_days" in body.model_fields_set:
        kwargs["ttl_days"] = body.ttl_days

    try:
        receipt = await engine.store_with_receipt(**kwargs)
    except MaterializationError as exc:
        return _accepted_work_failure(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return StoreResponse(
        event_id=str(receipt.event_id),
        node_id=str(receipt.node_id),
        processing_status=receipt.processing_status,
    )


@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Full LLM ingestion pipeline",
    responses={422: {"model": ErrorResponse}, 503: {"model": AcceptedWorkErrorResponse | ErrorResponse}},
)
async def ingest(request: Request, body: IngestRequest) -> IngestResponse | JSONResponse:
    """Ingest content through the full LLM extraction pipeline."""
    engine = _get_engine(request)

    kwargs: dict[str, Any] = {
        "content": body.content,
        "user_id": _user_id(request, body.user_id, required=True),
        "role": body.role,
        "wait_for_extraction": body.wait_for_extraction,
    }
    for name in ("scope", "session_id", "metadata", "event_time"):
        value = getattr(body, name)
        if value is not None:
            kwargs[name] = value

    try:
        event_id = await engine.ingest(**kwargs)
    except (ExtractionError, MaterializationError) as exc:
        return _accepted_work_failure(exc)
    return IngestResponse(event_id=event_id)


@router.get("/events/{event_id}", summary="Read original source evidence")
async def get_event(request: Request, event_id: UUID):
    event_key = str(event_id)
    event = await _get_engine(request).get_event(event_key, user_id=_user_id(request))
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event.model_dump(mode="json")


@router.get("/events/{event_id}/nodes", response_model=NodeListResponse, summary="Resolve source derivations")
async def get_event_nodes(request: Request, event_id: UUID):
    event_key = str(event_id)
    engine = _get_engine(request)
    event = await engine.get_event(event_key, user_id=_user_id(request))
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    nodes = await engine.get_event_nodes(event_key, user_id=event.user_id)
    return NodeListResponse(nodes=[_node_to_response(n) for n in nodes], count=len(nodes))


@router.get("/events/{event_id}/processing-status", response_model=ProcessingStatus,
            summary="Inspect saved source/index processing")
async def processing_status(request: Request, event_id: UUID):
    engine = _get_engine(request)
    event = await engine.get_event(str(event_id), user_id=_user_id(request))
    if event is None:
        raise HTTPException(status_code=404, detail="Processing work not found")
    status = await engine.processing_status(str(event_id), user_id=event.user_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Processing work not found")
    return status


@router.post("/materializations/process", response_model=ProcessingResult,
             summary="Repair one owner's saved source/index work without LLM extraction")
async def process_materializations(request: Request, body: MaterializationProcessRequest):
    owner = _user_id(request, body.user_id, required=True)
    return await _get_engine(request).process_pending(user_id=owner, budget_ms=body.budget_ms)


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


@router.get("/events/{event_id}/extraction-status", response_model=ExtractionStatus,
            summary="Inspect durable extraction progress")
async def extraction_status(request: Request, event_id: UUID):
    engine = _get_engine(request)
    event = await engine.get_event(str(event_id), user_id=_user_id(request))
    if event is None:
        raise HTTPException(status_code=404, detail="Extraction work not found")
    status = await engine.extraction_status(str(event_id), user_id=event.user_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Extraction work not found")
    return status


@router.post("/events/{event_id}/retry-extraction", response_model=ExtractionStatus,
             summary="Queue an extraction retry without calling a model")
async def retry_extraction(request: Request, event_id: UUID, replan: bool = False):
    engine = _get_engine(request)
    event = await engine.get_event(str(event_id), user_id=_user_id(request))
    if event is None:
        raise HTTPException(status_code=404, detail="Extraction work not found")
    status = await engine.retry_extraction(str(event_id), user_id=event.user_id, replan=replan)
    if status is None:
        raise HTTPException(status_code=404, detail="Extraction work not found")
    return status


@router.post("/extractions/process", response_model=ExtractionProcessingResult,
             summary="Process due extraction jobs for one owner")
async def process_extractions(request: Request, body: ExtractionProcessRequest):
    owner = _user_id(request, body.user_id, required=True)
    return await _get_engine(request).process_extractions(user_id=owner, limit=body.limit, budget_ms=body.budget_ms)


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

    for name in ("limit", "min_score", "token_budget", "ranking_multipliers", "min_fidelity"):
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
                source_type=node.source_type.value,
                scope=node.scope.value,
                session_id=node.session_id,
                event_time=node.event_time.isoformat() if node.event_time else None,
                valid_from=node.valid_from.isoformat(),
                valid_to=node.valid_to.isoformat() if node.valid_to else None,
                evidence_refs=[str(ref) for ref in node.evidence_refs],
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
    "/nodes/scan",
    response_model=NodePageResponse,
    summary="Enumerate stored nodes",
    responses={422: {"model": ErrorResponse}},
)
async def scan_nodes(
    request: Request,
    user_id: str | None = None,
    scope: Scope | None = None,
    type: NodeType | None = None,
    state: list[LifecycleState] | None = Query(default=None),
    after_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> NodePageResponse:
    """Read an owner-scoped page in immutable UUID order.

    Pass ``next_cursor`` as ``after_id`` while ``has_more`` is true. Pages
    cover an unchanged store; concurrent writes or lifecycle changes can alter
    matches between requests.
    """
    owner = _user_id(request, user_id, required=True)
    page = await _get_engine(request).scan_nodes(
        user_id=owner,
        scope=scope,
        node_type=type,
        lifecycle_states=state,
        after_id=str(after_id) if after_id is not None else None,
        limit=limit + 1,
    )
    has_more = len(page) > limit
    nodes = page[:limit]
    return NodePageResponse(
        nodes=[_node_to_response(node) for node in nodes],
        count=len(nodes),
        has_more=has_more,
        next_cursor=nodes[-1].id if has_more else None,
    )


@router.get(
    "/nodes/{node_id}",
    response_model=NodeResponse,
    summary="Get a single node",
    responses={404: {"model": ErrorResponse}},
)
async def get_node(request: Request, node_id: UUID) -> NodeResponse:
    """Retrieve a single node by ID."""
    node_key = str(node_id)
    engine = _get_engine(request)
    node = await engine.get_node(node_key, include_superseded=True, user_id=_user_id(request))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found")
    return _node_to_response(node)


@router.get(
    "/nodes/{node_id}/provenance",
    response_model=NodeProvenance,
    summary="Get node evidence and transition history",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def get_node_provenance(
    request: Request,
    node_id: UUID,
    operation_cursor: str | None = None,
    operation_limit: int = Query(default=100, ge=1, le=1000),
) -> NodeProvenance:
    """Read a tenant-scoped chronological page of node provenance."""
    try:
        result = await _get_engine(request).get_provenance(
            str(node_id), user_id=_user_id(request),
            operation_cursor=operation_cursor, operation_limit=operation_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"Node {str(node_id)!r} not found")
    return result


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
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
               422: {"model": ErrorResponse}},
)
async def promote_node(
    request: Request,
    node_id: UUID,
    idempotency_key: UUID | None = Header(default=None, alias="Idempotency-Key"),
) -> NodeResponse:
    """Promote a tentative node to stable; an idempotency key makes retries safe."""
    node_key = str(node_id)
    engine = _get_engine(request)

    # Verify node exists
    node = await engine.get_node(
        node_key, include_superseded=True, user_id=_user_id(request)
    )
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found")

    try:
        await engine.promote(
            node_key,
            user_id=_user_id(request),
            request_id=str(idempotency_key) if idempotency_key else None,
            actor_id=_user_id(request) or "api-operator",
        )
    except LifecycleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Re-fetch to get updated state
    updated = await engine.get_node(node_key, include_superseded=True, user_id=_user_id(request))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found after promote")
    return _node_to_response(updated)


@router.put(
    "/nodes/{node_id}/archive",
    summary="Archive a node",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
               422: {"model": ErrorResponse}},
)
async def archive_node(
    request: Request,
    node_id: UUID,
    idempotency_key: UUID | None = Header(default=None, alias="Idempotency-Key"),
) -> NodeResponse:
    """Archive a node; an idempotency key makes retries safe."""
    node_key = str(node_id)
    engine = _get_engine(request)

    node = await engine.get_node(node_key, include_superseded=True, user_id=_user_id(request))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found")

    try:
        await engine.archive(
            node_key,
            user_id=_user_id(request),
            request_id=str(idempotency_key) if idempotency_key else None,
            actor_id=_user_id(request) or "api-operator",
        )
    except LifecycleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    updated = await engine.get_node(node_key, include_superseded=True, user_id=_user_id(request))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found after archive")
    return _node_to_response(updated)


@router.put(
    "/nodes/{node_id}/reinforce",
    summary="Reinforce a node",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
               422: {"model": ErrorResponse}},
)
async def reinforce_node(
    request: Request, node_id: UUID,
    idempotency_key: UUID | None = Header(default=None, alias="Idempotency-Key"),
    body: ReinforceRequest | None = None,
) -> NodeResponse:
    """Confirm a node; reuse a UUID Idempotency-Key header for safe retries."""
    node_key = str(node_id)
    engine = _get_engine(request)

    try:
        kwargs = {"user_id": _user_id(request)}
        if idempotency_key is not None:
            kwargs["request_id"] = str(idempotency_key)
        if body is not None and body.evidence_id is not None:
            kwargs["evidence_id"] = str(body.evidence_id)
        await engine.reinforce(node_key, **kwargs)
    except ReinforcementConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    updated = await engine.get_node(node_key, include_superseded=True, user_id=_user_id(request))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found after reinforce")
    return _node_to_response(updated)


@router.put(
    "/nodes/{node_id}/condition",
    summary="Evaluate a conditional memory",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
               422: {"model": ErrorResponse}},
)
async def evaluate_condition(
    request: Request,
    node_id: UUID,
    body: ConditionEvaluationRequest,
    idempotency_key: UUID | None = Header(default=None, alias="Idempotency-Key"),
) -> NodeResponse:
    """Record a condition state; reuse Idempotency-Key for safe retries."""
    node_key = str(node_id)
    engine = _get_engine(request)
    owner = _user_id(request)
    node = await engine.get_node(node_key, include_superseded=True, user_id=owner)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found")
    try:
        updated = await engine.evaluate_condition(
            node_key,
            body.state,
            user_id=owner,
            evidence_id=str(body.evidence_id) if body.evidence_id else None,
            request_id=str(idempotency_key) if idempotency_key else None,
            evaluation_method=body.evaluation_method,
            reason=body.reason,
            actor_id=owner or "api-operator",
            evaluated_at=body.evaluated_at,
        )
    except ConditionEvaluationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _node_to_response(updated)


@router.post(
    "/supersedences",
    response_model=NodeListResponse,
    summary="Replace an outdated memory claim",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def supersede(request: Request, body: SupersedenceRequest) -> NodeListResponse:
    """Retire an old claim in favor of a replacement; exact retries are safe."""
    engine = _get_engine(request)
    owner = _user_id(request)
    ids = [str(body.old_node_id), str(body.new_node_id)]
    for node_id in ids:
        if await engine.get_node(node_id, include_superseded=True, user_id=owner) is None:
            raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    try:
        await engine.supersede(
            *ids,
            evidence_id=str(body.evidence_id) if body.evidence_id else None,
            user_id=owner,
            actor_id=owner or "api-operator",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    nodes = [
        await engine.get_node(node_id, include_superseded=True, user_id=owner)
        for node_id in ids
    ]
    return NodeListResponse(
        nodes=[_node_to_response(node) for node in nodes if node is not None],
        count=len(nodes),
    )


@router.post(
    "/contradictions",
    response_model=NodeListResponse,
    summary="Mark two memory claims as contradictory",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def contradict(request: Request, body: ContradictionRequest) -> NodeListResponse:
    """Contest two claims; an exact retry returns the existing result."""
    engine = _get_engine(request)
    owner = _user_id(request)
    ids = [str(body.node_a_id), str(body.node_b_id)]
    for node_id in ids:
        if await engine.get_node(node_id, include_superseded=True, user_id=owner) is None:
            raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    try:
        nodes = await engine.contradict(
            *ids, evidence_id=str(body.evidence_id) if body.evidence_id else None,
            user_id=owner, actor_id=owner or "api-operator",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NodeListResponse(nodes=[_node_to_response(node) for node in nodes], count=2)


@router.post(
    "/contradictions/resolve",
    response_model=NodeListResponse,
    summary="Resolve a memory contradiction",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def resolve_contradiction(
    request: Request, body: ContradictionResolutionRequest,
) -> NodeListResponse:
    """Choose a winner and deprecate the loser; exact retries are safe."""
    engine = _get_engine(request)
    owner = _user_id(request)
    ids = [str(body.winner_id), str(body.loser_id)]
    for node_id in ids:
        if await engine.get_node(node_id, include_superseded=True, user_id=owner) is None:
            raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    try:
        nodes = await engine.resolve_contradiction(
            *ids, evidence_id=str(body.evidence_id) if body.evidence_id else None,
            user_id=owner, resolver_actor_id=owner or "api-operator",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NodeListResponse(nodes=[_node_to_response(node) for node in nodes], count=2)


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
    node_id: UUID,
    max_hops: int = 2,
) -> NodeListResponse:
    """Get nodes within N hops of a starting node."""
    node_key = str(node_id)
    engine = _get_engine(request)

    # Verify node exists
    node = await engine.get_node(node_key, include_superseded=True, user_id=_user_id(request))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found")

    neighbors = await engine._graph_store.get_neighborhood(
        node_key, max_hops=max_hops
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
    node_id: UUID,
    direction: str = "forward",
) -> NodeListResponse:
    """Get the supersedence chain from a node."""
    node_key = str(node_id)
    engine = _get_engine(request)

    # Verify node exists
    node = await engine.get_node(node_key, include_superseded=True, user_id=_user_id(request))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_key!r} not found")

    chain = await engine._graph_store.get_supersedence_chain(
        node_key, direction=direction
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


@router.get("/retrievals/{request_id}", response_model=RetrievalReceipt,
            responses={404: {"model": ErrorResponse}})
async def get_retrieval_receipt(request: Request, request_id: UUID, user_id: str | None = None):
    engine = _get_engine(request)
    result = await engine.get_retrieval_receipt(str(request_id), user_id=_user_id(request, user_id, required=True))
    if result is None:
        raise HTTPException(status_code=404, detail="Retrieval receipt not found")
    return result


@router.post("/relevance", response_model=RelevanceRecord,
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def record_relevance(request: Request, body: RelevanceRequest):
    engine = _get_engine(request)
    owner = _user_id(request, body.user_id, required=True)
    if await engine.get_retrieval_receipt(str(body.request_id), user_id=owner) is None:
        raise HTTPException(status_code=404, detail="Retrieval receipt not found")
    submission = RelevanceSubmission.model_validate(body.model_dump(exclude={"user_id"}))
    try:
        return await engine.record_relevance(submission, user_id=owner)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/relevance", response_model=list[RelevanceRecord])
async def list_relevance(request: Request, user_id: str | None = None,
                         limit: int = Query(default=100, ge=1, le=1000), after_id: UUID | None = None):
    return await _get_engine(request).list_relevance(
        user_id=_user_id(request, user_id, required=True), limit=limit,
        after_id=str(after_id) if after_id is not None else None)


@router.get("/relevance/{feedback_id}", response_model=RelevanceRecord,
            responses={404: {"model": ErrorResponse}})
async def get_relevance(request: Request, feedback_id: UUID, user_id: str | None = None):
    result = await _get_engine(request).get_relevance(
        str(feedback_id), user_id=_user_id(request, user_id, required=True))
    if result is None:
        raise HTTPException(status_code=404, detail="Relevance record not found")
    return result


@router.post("/answer-citations", response_model=AnswerCitationRecord,
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse}})
async def record_answer_citations(request: Request, body: AnswerCitationRequest):
    """Record the content-bearing memories cited by one generated answer."""
    engine = _get_engine(request)
    owner = _user_id(request, body.user_id, required=True)
    if await engine.get_retrieval_receipt(str(body.request_id), user_id=owner) is None:
        raise HTTPException(status_code=404, detail="Retrieval receipt not found")
    submission = AnswerCitationSubmission.model_validate(body.model_dump(exclude={"user_id"}))
    try:
        return await engine.record_answer_citations(submission, user_id=owner)
    except CitationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/answer-citations", response_model=list[AnswerCitationRecord])
async def list_answer_citations(
    request: Request, user_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000), after_id: UUID | None = None,
):
    return await _get_engine(request).list_answer_citations(
        user_id=_user_id(request, user_id, required=True), limit=limit,
        after_id=str(after_id) if after_id is not None else None,
    )


@router.get("/answer-citations/{citation_id}", response_model=AnswerCitationRecord,
            responses={404: {"model": ErrorResponse}})
async def get_answer_citations(
    request: Request, citation_id: UUID, user_id: str | None = None,
):
    result = await _get_engine(request).get_answer_citations(
        str(citation_id), user_id=_user_id(request, user_id, required=True),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Answer citation record not found")
    return result
