"""PRME MCP Server — tools and resources for memory operations.

All tools are thin wrappers around MemoryEngine methods.
No business logic belongs here — delegate everything to the engine.
"""

import argparse
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import AwareDatetime, StrictBool, TypeAdapter

from prme import __version__
from prme.config import PRMEConfig
from prme.ingestion.errors import MaterializationError, extraction_failure_code
from prme.models.aggregation import AssertionQuery, QuantityAggregationQuery
from prme.models.temporal import AssertionStateQuery
from prme.models.relevance import AnswerCitationSubmission, RelevanceSubmission
from prme.models.learning import LearningConfig, RankingMultipliers
from prme.types import (
    ConditionEvaluationMethod,
    ConditionState,
    EpistemicType,
    LifecycleState,
    NodeType,
    RepresentationLevel,
    RetrievalMode,
    Scope,
    SourceType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Convert a MemoryNode to a JSON-serializable dict."""
    return {
        "id": str(node.id),
        "user_id": node.user_id,
        "session_id": node.session_id,
        "node_type": node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
        "content": node.content,
        "lifecycle_state": node.lifecycle_state.value if hasattr(node.lifecycle_state, "value") else str(node.lifecycle_state),
        "confidence": node.confidence,
        "salience": node.salience,
        "confidence_base": node.confidence_base,
        "salience_base": node.salience_base,
        "reinforcement_boost": node.reinforcement_boost,
        "last_reinforced_at": node.last_reinforced_at.isoformat() if node.last_reinforced_at else None,
        "decay_profile": node.decay_profile.value if node.decay_profile else None,
        "epistemic_type": node.epistemic_type.value if node.epistemic_type and hasattr(node.epistemic_type, "value") else None,
        "source_type": node.source_type.value if node.source_type and hasattr(node.source_type, "value") else None,
        "scope": node.scope.value if hasattr(node.scope, "value") else str(node.scope),
        "metadata": node.metadata,
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
        "event_time": node.event_time.isoformat() if node.event_time else None,
        "valid_from": node.valid_from.isoformat(),
        "valid_to": node.valid_to.isoformat() if node.valid_to else None,
        "ttl_days": node.ttl_days,
        "superseded_by": str(node.superseded_by) if node.superseded_by else None,
        "evidence_refs": [str(r) for r in node.evidence_refs],
        "pinned": node.pinned,
    }


def _get_engine(ctx: Context) -> Any:
    """Extract the MemoryEngine from MCP lifespan context."""
    return ctx.request_context.lifespan_context["engine"]


def _get_user_id(engine, requested: str | None = None, *, required: bool = False) -> str | None:
    """Resolve the server-bound stdio owner or verified HTTP principal."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    identity = engine._config.mcp
    owner = identity.user_id
    if identity.user_keys:
        token = get_access_token()
        if token is None or token.client_id not in identity.user_keys:
            raise PermissionError("Authenticated MCP identity required")
        owner = token.client_id
    if owner is not None and requested is not None and owner != requested:
        raise PermissionError("User does not match authenticated identity")
    resolved = owner if owner is not None else requested
    if required and not resolved:
        raise PermissionError("user_id is required without a bound identity")
    return resolved


def _internal_error(operation: str, exc: Exception) -> str:
    """Log an unexpected error server-side and return a generic payload.

    Raw exception strings can leak internal paths, SQL fragments, or
    library internals to remote callers (issue #34) — details go to the
    server log only.
    """
    logger.error("Unexpected error in %s", operation, exc_info=exc)
    return json.dumps({"error": f"Internal error in {operation}"})


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def engine_lifespan(server: FastMCP):
    """Manage MemoryEngine lifecycle for the MCP server."""
    from prme.storage.engine import MemoryEngine

    config = server.prme_config or PRMEConfig()
    logger.info("Starting PRME MemoryEngine (backend=%s)...", config.backend)
    engine = await MemoryEngine.create(config)
    logger.info("PRME MemoryEngine ready")

    try:
        yield {"engine": engine}
    finally:
        logger.info("Shutting down PRME MemoryEngine...")
        await engine.close()
        logger.info("PRME MemoryEngine shut down")


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def memory_store(
    content: str,
    user_id: Optional[str] = None,
    node_type: str = "note",
    scope: str = "personal",
    event_time: Optional[AwareDatetime] = None,
    role: str = "user",
    session_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    confidence: Optional[float] = None,
    epistemic_type: Optional[EpistemicType] = None,
    source_type: Optional[SourceType] = None,
    ctx: Context = None,
) -> str:
    """Store a memory.

    Stores content as a typed memory node with vector embedding and
    full-text indexing. Returns the event ID and node ID.

    Args:
        content: The text content to store as a memory.
        user_id: User who owns this memory.
        node_type: Type of memory node. One of: entity, fact, decision,
            preference, task, instruction, summary, note. Default: note.
        scope: Memory scope. One of: personal, project, organisation. Default: personal.
        event_time: Original source time with timezone; separate from admission and validity.
        role: Source role used for default provenance inference.
        session_id: Optional conversation or episode identifier.
        metadata: Optional structured application metadata.
        confidence: Optional initial confidence from zero to one.
        epistemic_type: Optional explicit epistemic classification.
        source_type: Optional explicit provenance classification.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine, user_id, required=True)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    try:
        nt = NodeType(node_type)
    except ValueError:
        return json.dumps({"error": f"Invalid node_type: {node_type!r}. Valid: {[e.value for e in NodeType]}"})

    try:
        sc = Scope(scope)
    except ValueError:
        return json.dumps({"error": f"Invalid scope: {scope!r}. Valid: {[e.value for e in Scope]}"})

    try:
        receipt = await engine.store_with_receipt(
            content,
            user_id=user_id,
            node_type=nt,
            scope=sc,
            role=role,
            session_id=session_id,
            metadata=metadata,
            confidence=confidence,
            epistemic_type=epistemic_type,
            source_type=source_type,
            event_time=event_time,
        )

        return json.dumps({
            "event_id": str(receipt.event_id),
            "node_id": str(receipt.node_id),
            "processing_status": receipt.processing_status.model_dump(mode="json"),
        })
    except MaterializationError as exc:
        return json.dumps({
            "error": "Stored source needs recovery",
            "accepted": exc.event_id is not None,
            "event_id": exc.event_id,
            "reason_code": exc.reason_code or extraction_failure_code(exc),
        })
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return _internal_error("memory_store", e)


async def memory_retrieve(
    query: str,
    user_id: Optional[str] = None,
    scope: Optional[str | list[str]] = None,
    knowledge_at: Optional[str] = None,
    ctx: Context = None,
    min_score: Optional[float] = None,
    limit: Optional[int] = None,
    token_budget: Optional[int] = None,
    ranking_multipliers: Optional[RankingMultipliers] = None,
    reference_time: Optional[AwareDatetime] = None,
    time_from: Optional[AwareDatetime] = None,
    time_to: Optional[AwareDatetime] = None,
    event_time_from: Optional[AwareDatetime] = None,
    event_time_to: Optional[AwareDatetime] = None,
    include_cross_scope: StrictBool = True,
    min_fidelity: Optional[RepresentationLevel] = None,
    mode: Optional[RetrievalMode] = None,
    include_context: StrictBool = False,
) -> str:
    """Search memories using hybrid retrieval.

    Runs the full hybrid retrieval pipeline: semantic similarity,
    lexical search, graph proximity, recency, salience, and confidence
    scoring. Returns the top matching memories ranked by composite score.

    Args:
        query: Natural language search query.
        user_id: User whose memories to search.
        min_score: Inclusive composite score floor, not a probability.
        limit: Maximum primary results; zero returns none.
        token_budget: Maximum packed context tokens.
        scope: One scope or a nonempty list of scopes.
        ranking_multipliers: Explicit bounded ranking trial; does not activate a profile.
        reference_time: Timezone-aware scoring clock for reproducible comparisons.
        time_from: Inclusive validity window start, with timezone.
        time_to: Validity window end, with timezone.
        event_time_from: Inclusive source event-time lower bound, with timezone.
        event_time_to: Source event-time upper bound, with timezone.
        include_cross_scope: Allow supplementary hints from other scopes.
        min_fidelity: Minimum packed representation level.
        mode: Epistemic filtering mode within generated candidates.
        include_context: Include the rendered, token-budgeted context in the response.
        knowledge_at: Optional timezone-aware ISO datetime used as an ingestion
            cutoff over current indexes. Returned historical_coverage states
            why this is not an exact prior-state replay.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine, user_id, required=True)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    kwargs: dict[str, Any] = {
        "query": query,
        "user_id": user_id,
    }

    if scope is not None:
        try:
            if isinstance(scope, list):
                if not scope:
                    raise ValueError("Scope list must not be empty")
                kwargs["scope"] = [Scope(value) for value in scope]
            else:
                kwargs["scope"] = Scope(scope)
        except ValueError:
            return json.dumps({"error": f"Invalid scope: {scope!r}"})

    if knowledge_at:
        try:
            kwargs["knowledge_at"] = TypeAdapter(AwareDatetime).validate_python(
                knowledge_at
            )
        except ValueError:
            return json.dumps({"error": f"Invalid knowledge_at datetime: {knowledge_at!r}"})

    try:
        for name, value in (("reference_time", reference_time), ("time_from", time_from),
                            ("time_to", time_to), ("event_time_from", event_time_from),
                            ("event_time_to", event_time_to)):
            if value is not None:
                kwargs[name] = TypeAdapter(AwareDatetime).validate_python(value)
        if ranking_multipliers is not None:
            kwargs["ranking_multipliers"] = RankingMultipliers.model_validate(ranking_multipliers)
        kwargs["include_cross_scope"] = TypeAdapter(StrictBool).validate_python(include_cross_scope)
        include_context = TypeAdapter(StrictBool).validate_python(include_context)
        if min_fidelity is not None:
            kwargs["min_fidelity"] = RepresentationLevel(min_fidelity)
        if mode is not None:
            kwargs["retrieval_mode"] = RetrievalMode(mode)
        from prme.retrieval.selection import validate_selection
        validate_selection(min_score, limit)
        if token_budget is not None and token_budget < 0:
            raise ValueError("token_budget must be nonnegative")
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    try:
        response = await engine.retrieve(**kwargs, min_score=min_score, limit=limit, token_budget=token_budget)

        results = []
        for candidate in response.results:
            node = candidate.node
            results.append({
                "node_id": str(node.id),
                "content": node.content,
                "score": round(candidate.composite_score, 4),
                "node_type": node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
                "lifecycle_state": node.lifecycle_state.value if hasattr(node.lifecycle_state, "value") else str(node.lifecycle_state),
                "confidence": node.confidence,
            })

        payload = {
            "results": results,
            "count": len(results),
            "metrics": response.metadata.model_dump(mode="json"),
        }
        if include_context:
            payload["context"] = response.bundle.render()
        return json.dumps(payload)
    except Exception as e:
        return _internal_error("memory_retrieve", e)


async def memory_ingest(
    content: str,
    user_id: Optional[str] = None,
    role: str = "user",
    scope: str = "personal",
    ctx: Context = None,
    session_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    event_time: Optional[AwareDatetime] = None,
) -> str:
    """Ingest content with LLM-powered extraction.

    Processes content through the full LLM extraction pipeline to
    automatically identify entities, facts, relationships, preferences,
    and decisions. Requires a configured extraction provider.

    Args:
        content: The text content to ingest (e.g. a conversation message).
        user_id: User who owns this memory.
        session_id: Optional source session identifier.
        metadata: Optional source metadata.
        event_time: Original source time with timezone; anchors relative dates.
        role: Role of the speaker (user or assistant). Default: user.
        scope: Memory scope. One of: personal, project, organisation. Default: personal.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine, user_id, required=True)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    try:
        sc = Scope(scope)
    except ValueError:
        return json.dumps({"error": f"Invalid scope: {scope!r}"})

    try:
        event_id = await engine.ingest(
            content,
            user_id=user_id,
            role=role,
            scope=sc,
            session_id=session_id,
            metadata=metadata,
            event_time=event_time,
        )
        return json.dumps({"event_id": event_id})
    except Exception as e:
        return _internal_error("memory_ingest", e)


async def memory_get_retrieval_receipt(request_id: str, user_id: Optional[str] = None, ctx: Context = None) -> str:
    """Read a saved retrieval snapshot; request_id comes from retrieval metrics."""
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        result = await engine.get_retrieval_receipt(request_id, user_id=owner)
        return result.model_dump_json() if result else json.dumps({"error": "Retrieval receipt not found"})
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_get_retrieval_receipt", exc)


async def memory_record_relevance(request_id: str, labels: dict[str, StrictBool],
                                  feedback_id: Optional[str] = None,
                                  surface: Literal["results", "context"] = "results",
                                  method: Literal["explicit_user", "structured_evaluation"] = "explicit_user",
                                  user_id: Optional[str] = None, ctx: Context = None) -> str:
    """Save explicit relevance labels for a saved retrieval; reuse feedback_id on retry.

    Labels do not change facts or weights. Unlabelled candidates are not negatives.
    Context positives require included source content, not reference-only entries.
    """
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        data = {"request_id": request_id, "labels": labels, "surface": surface, "method": method}
        if feedback_id is not None:
            data["feedback_id"] = feedback_id
        submission = RelevanceSubmission.model_validate(data)
        return (await engine.record_relevance(submission, user_id=owner)).model_dump_json()
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_record_relevance", exc)


async def memory_get_relevance(feedback_id: str, user_id: Optional[str] = None, ctx: Context = None) -> str:
    """Read an owned immutable relevance record by its retry identity."""
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        result = await engine.get_relevance(feedback_id, user_id=owner)
        return result.model_dump_json() if result else json.dumps({"error": "Relevance record not found"})
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_get_relevance", exc)


async def memory_list_relevance(user_id: Optional[str] = None, limit: int = 100,
                                after_id: Optional[str] = None, ctx: Context = None) -> str:
    """Page owned relevance records by feedback UUID (not creation time)."""
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        records = await engine.list_relevance(user_id=owner, limit=limit, after_id=after_id)
        return json.dumps([record.model_dump(mode="json") for record in records])
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_list_relevance", exc)


async def memory_evaluate_learning(
    user_id: Optional[str] = None,
    scopes: Optional[list[str]] = None,
    surface: Literal["results", "context"] = "results",
    config: Optional[LearningConfig] = None,
    query_groups: Optional[dict[str, str]] = None,
    max_records: int = 10000,
    ctx: Context = None,
) -> str:
    """Evaluate an owner-scoped offline ranking proposal without activating it.

    The report retains its feedback and receipt identities, train/validation
    coverage, uncertainty, exclusions, and proposed bounded multipliers. It
    never changes active weights.
    """
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        if scopes is not None and not scopes:
            raise ValueError("scopes must be omitted or contain at least one scope")
        parsed_scopes = [Scope(scope) for scope in scopes] if scopes is not None else None
        parsed_groups: dict[UUID, str] | None = None
        if query_groups is not None:
            parsed_groups = {}
            for request_id, group in query_groups.items():
                identity = UUID(request_id)
                if identity in parsed_groups:
                    raise ValueError("query_groups contains duplicate UUID identities")
                parsed_groups[identity] = group
        result = await engine.evaluate_learning(
            user_id=owner,
            scopes=parsed_scopes,
            surface=surface,
            config=config,
            query_groups=parsed_groups,
            max_records=max_records,
        )
        return result.model_dump_json()
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_evaluate_learning", exc)


async def memory_record_answer_citations(
    request_id: str,
    answer_id: str,
    cited_node_ids: list[str],
    citation_id: Optional[str] = None,
    answer_sha256: Optional[str] = None,
    method: Literal["model_reported", "application_verified", "human_verified"] = "model_reported",
    user_id: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Record which packed memories supported an answer; reuse citation_id on retry.

    Pass an empty cited_node_ids list to explicitly report an answer with no
    memory citations. Every nonempty citation must identify content that was
    actually included in the saved retrieval context.
    """
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        data = {
            "request_id": request_id,
            "answer_id": answer_id,
            "cited_node_ids": cited_node_ids,
            "answer_sha256": answer_sha256,
            "method": method,
        }
        if citation_id is not None:
            data["citation_id"] = citation_id
        submission = AnswerCitationSubmission.model_validate(data)
        return (
            await engine.record_answer_citations(submission, user_id=owner)
        ).model_dump_json()
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_record_answer_citations", exc)


async def memory_get_answer_citations(
    citation_id: str, user_id: Optional[str] = None, ctx: Context = None,
) -> str:
    """Read one owned answer citation record by its retry identity."""
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        result = await engine.get_answer_citations(citation_id, user_id=owner)
        return result.model_dump_json() if result else json.dumps({
            "error": "Answer citation record not found",
        })
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_get_answer_citations", exc)


async def memory_list_answer_citations(
    user_id: Optional[str] = None, limit: int = 100,
    after_id: Optional[str] = None, ctx: Context = None,
) -> str:
    """Page owned answer citation records by citation UUID."""
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        records = await engine.list_answer_citations(
            user_id=owner, limit=limit, after_id=after_id,
        )
        return json.dumps([record.model_dump(mode="json") for record in records])
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_list_answer_citations", exc)


async def memory_organize(
    user_id: Optional[str] = None,
    jobs: Optional[str] = None,
    budget_ms: int = 5000,
    ctx: Context = None,
) -> str:
    """Run memory organization jobs.

    Executes background maintenance: promotion, decay, deduplication,
    summarization, consolidation, and archival. Can target specific
    jobs or run all.

    Args:
        user_id: Optional user to scope jobs to.
        jobs: Optional comma-separated list of jobs to run (e.g.
            "promote,decay_sweep,deduplicate"). Omit to run all.
        budget_ms: Time budget in milliseconds. Default: 5000.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine, user_id)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    kwargs: dict[str, Any] = {"budget_ms": budget_ms}
    if user_id:
        kwargs["user_id"] = user_id
    if jobs:
        kwargs["jobs"] = [j.strip() for j in jobs.split(",")]

    if engine._config.mcp.user_id is not None or engine._config.mcp.user_keys:
        from prme.organizer import ALL_JOBS

        if "feedback_apply" in kwargs.get("jobs", []):
            return json.dumps({"error": "Global feedback maintenance requires an operator"})
        if not jobs:
            kwargs["jobs"] = [name for name in ALL_JOBS if name != "feedback_apply"]
    try:
        result = await engine.organize(**kwargs)
        return json.dumps({
            "jobs_run": result.jobs_run,
            "duration_ms": result.duration_ms,
        })
    except Exception as e:
        return _internal_error("memory_organize", e)


async def memory_get_node(
    node_id: str,
    ctx: Context = None,
) -> str:
    """Get a memory node by ID.

    Retrieves the full details of a specific memory node including
    content, type, lifecycle state, confidence, and metadata.

    Args:
        node_id: The UUID of the memory node.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    try:
        node = await engine.get_node(node_id, include_superseded=True, user_id=user_id)
        if node is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})
        return json.dumps(_node_to_dict(node))
    except Exception as e:
        return _internal_error("memory_get_node", e)


async def memory_scan_nodes(
    user_id: Optional[str] = None,
    scope: Optional[str] = None,
    node_type: Optional[str] = None,
    lifecycle_states: Optional[list[str]] = None,
    after_id: Optional[str] = None,
    limit: int = 100,
    ctx: Context = None,
) -> str:
    """Enumerate a deterministic page of owner-scoped stored records.

    Pass ``next_cursor`` as ``after_id`` while ``has_more`` is true. The
    default lifecycle filter includes active records. Pages are complete for
    an unchanged store and do not call a model.

    Args:
        user_id: Owner to scan; omit when the MCP server binds an owner.
        scope: Optional memory scope filter.
        node_type: Optional stored node type filter.
        lifecycle_states: Optional lifecycle filters; an empty list matches none.
        after_id: UUID cursor returned by the previous page.
        limit: Page size from 1 through 1000.
    """
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 through 1000")
        parsed_scope = Scope(scope) if scope is not None else None
        parsed_type = NodeType(node_type) if node_type is not None else None
        parsed_states = (
            [LifecycleState(state) for state in lifecycle_states]
            if lifecycle_states is not None else None
        )
        cursor = str(UUID(after_id)) if after_id is not None else None
        page = await engine.scan_nodes(
            user_id=owner,
            scope=parsed_scope,
            node_type=parsed_type,
            lifecycle_states=parsed_states,
            after_id=cursor,
            limit=limit + 1,
        )
        has_more = len(page) > limit
        nodes = page[:limit]
        return json.dumps({
            "nodes": [_node_to_dict(node) for node in nodes],
            "count": len(nodes),
            "has_more": has_more,
            "next_cursor": str(nodes[-1].id) if has_more else None,
            "order": "id",
            "consistency": "page",
        })
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_scan_nodes", exc)


async def memory_aggregate_assertions(
    user_id: Optional[str] = None,
    subjects: Optional[list[str]] = None,
    predicates: Optional[list[str]] = None,
    objects: Optional[list[str]] = None,
    polarities: Optional[list[str]] = None,
    group_by: Optional[list[Literal["subject", "predicate", "object", "polarity"]]] = None,
    scopes: Optional[list[str]] = None,
    node_types: Optional[list[str]] = None,
    lifecycle_states: Optional[list[str]] = None,
    retrieval_mode: str = "default",
    event_time_from: Optional[str] = None,
    event_time_to: Optional[str] = None,
    valid_at: Optional[str] = None,
    knowledge_at: Optional[str] = None,
    group_limit: int = 1000,
    sample_limit: int = 10,
    ctx: Context = None,
) -> str:
    """Count and group exact structured assertions across an owner's stored set.

    Selectors use exact Unicode/case/whitespace-normalized matching; predicates
    also treat spaces and hyphens as underscores. The result scans every page
    for an unchanged store and does not call a model. Stored-set completeness
    does not imply complete extraction or real-world truth.

    Args:
        user_id: Owner to aggregate; omit when the MCP server binds an owner.
        subjects: Optional exact subject selectors.
        predicates: Optional exact predicate selectors.
        objects: Optional exact object selectors.
        polarities: Optional exact polarity selectors; defaults to positive.
        group_by: Structured fields that define a distinct result group.
        scopes: Optional memory scopes; omission scans every scope.
        node_types: Assertion node types; defaults to facts, decisions, and preferences.
        lifecycle_states: Optional lifecycle filters; an empty list matches none.
        retrieval_mode: ``default`` applies ordinary epistemic filters; ``explicit`` retains all.
        event_time_from: Inclusive timezone-aware event-time lower bound.
        event_time_to: Inclusive timezone-aware event-time upper bound.
        valid_at: Timezone-aware validity snapshot.
        knowledge_at: Timezone-aware ingestion cutoff over current graph state.
        group_limit: Maximum groups returned, from 0 through 10000.
        sample_limit: Maximum node and evidence samples per group, from 0 through 100.
    """
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        raw = {
            "subjects": subjects,
            "predicates": predicates,
            "objects": objects,
            "polarities": polarities,
            "group_by": group_by,
            "scopes": scopes,
            "node_types": node_types,
            "lifecycle_states": lifecycle_states,
            "retrieval_mode": retrieval_mode,
            "event_time_from": event_time_from,
            "event_time_to": event_time_to,
            "valid_at": valid_at,
            "knowledge_at": knowledge_at,
            "group_limit": group_limit,
            "sample_limit": sample_limit,
        }
        query = AssertionQuery.model_validate({key: value for key, value in raw.items() if value is not None})
        result = await engine.aggregate_assertions(query, user_id=owner)
        return result.model_dump_json()
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_aggregate_assertions", exc)


async def memory_aggregate_quantities(
    user_id: Optional[str] = None,
    subjects: Optional[list[str]] = None,
    predicates: Optional[list[str]] = None,
    objects: Optional[list[str]] = None,
    polarities: Optional[list[str]] = None,
    units: Optional[list[str]] = None,
    group_by: Optional[list[Literal["subject", "predicate", "object", "polarity", "unit"]]] = None,
    scopes: Optional[list[str]] = None,
    node_types: Optional[list[str]] = None,
    lifecycle_states: Optional[list[str]] = None,
    retrieval_mode: str = "default",
    event_time_from: Optional[str] = None,
    event_time_to: Optional[str] = None,
    valid_at: Optional[str] = None,
    knowledge_at: Optional[str] = None,
    group_limit: int = 1000,
    sample_limit: int = 10,
    ctx: Context = None,
) -> str:
    """Sum exact source-grounded decimals while keeping units separate.

    The result includes count, total, minimum, maximum, and source/evidence
    samples for each normalized group. ``group_by`` must include ``unit``;
    PRME never converts units or infers currencies. It scans every selected
    structured record for an unchanged store and does not call a model.

    Args:
        user_id: Owner to aggregate; omit when the MCP server binds an owner.
        subjects: Optional exact subject selectors.
        predicates: Optional exact predicate selectors.
        objects: Optional exact object selectors.
        polarities: Optional exact polarity selectors; defaults to positive.
        units: Optional exact unit selectors after Unicode/case/whitespace normalization.
        group_by: Group fields; must include unit to prevent incompatible sums.
        scopes: Optional memory scopes; omission scans every scope.
        node_types: Assertion node types; defaults to facts, decisions, and preferences.
        lifecycle_states: Optional lifecycle filters; an empty list matches none.
        retrieval_mode: ``default`` applies ordinary epistemic filters; ``explicit`` retains all.
        event_time_from: Inclusive timezone-aware event-time lower bound.
        event_time_to: Inclusive timezone-aware event-time upper bound.
        valid_at: Timezone-aware validity snapshot.
        knowledge_at: Timezone-aware ingestion cutoff over current graph state.
        group_limit: Maximum groups returned, from 0 through 10000.
        sample_limit: Maximum source/evidence samples per group, from 0 through 100.
    """
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        raw = {
            "subjects": subjects,
            "predicates": predicates,
            "objects": objects,
            "polarities": polarities,
            "units": units,
            "group_by": group_by,
            "scopes": scopes,
            "node_types": node_types,
            "lifecycle_states": lifecycle_states,
            "retrieval_mode": retrieval_mode,
            "event_time_from": event_time_from,
            "event_time_to": event_time_to,
            "valid_at": valid_at,
            "knowledge_at": knowledge_at,
            "group_limit": group_limit,
            "sample_limit": sample_limit,
        }
        query = QuantityAggregationQuery.model_validate({
            key: value for key, value in raw.items() if value is not None
        })
        result = await engine.aggregate_quantities(query, user_id=owner)
        return result.model_dump_json()
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_aggregate_quantities", exc)


async def memory_get_assertion_state(
    subject: str,
    predicate: str,
    scope: str,
    valid_at: str,
    user_id: Optional[str] = None,
    knowledge_at: Optional[str] = None,
    node_types: Optional[list[str]] = None,
    limit: int = 1000,
    ctx: Context = None,
) -> str:
    """Inspect exact current claim candidates and their temporal audit trail.

    This operation does not use semantic retrieval or choose a newer claim as
    truth. Differing eligible values remain ``multiple`` unless explicit
    contradiction state makes them ``contested``.

    Args:
        subject: Exact assertion subject after Unicode/case/whitespace normalization.
        predicate: Exact predicate; spaces and hyphens normalize to underscores.
        scope: One memory scope. State is never mixed across scopes.
        valid_at: Required timezone-aware validity instant.
        user_id: Owner to inspect; omit when the MCP server binds an owner.
        knowledge_at: Optional ingestion cutoff over current graph state; not replay.
        node_types: Assertion node types; defaults to facts, decisions, and preferences.
        limit: Maximum returned values, candidates, timeline rows, and conflicts.
    """
    engine = _get_engine(ctx)
    try:
        owner = _get_user_id(engine, user_id, required=True)
        raw = {
            "subject": subject,
            "predicate": predicate,
            "scope": scope,
            "valid_at": valid_at,
            "knowledge_at": knowledge_at,
            "node_types": node_types,
            "limit": limit,
        }
        query = AssertionStateQuery.model_validate({
            key: value for key, value in raw.items() if value is not None
        })
        result = await engine.get_assertion_state(query, user_id=owner)
        return result.model_dump_json()
    except (PermissionError, ValueError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_get_assertion_state", exc)


async def memory_get_provenance(
    node_id: str,
    operation_cursor: str | None = None,
    operation_limit: int = 100,
    ctx: Context = None,
) -> str:
    """Get owned source evidence, transitions, and contradiction links.

    Pass next_operation_cursor from a response to continue chronological
    operation history.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    try:
        result = await engine.get_provenance(
            node_id, user_id=user_id, operation_cursor=operation_cursor,
            operation_limit=operation_limit,
        )
        if result is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})
        return result.model_dump_json()
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_get_provenance", exc)


async def memory_get_event(event_id: str, ctx: Context = None) -> str:
    """Read the original source event behind a node's evidence_refs.

    Use this when retrieved assertions omit surrounding actions or conditions.
    Returns original content, source timestamps, role, session, scope, and IDs.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
        event = await engine.get_event(event_id, user_id=user_id)
        if event is None:
            return json.dumps({"error": "Event not found"})
        return event.model_dump_json()
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_get_event", exc)


async def memory_get_extraction(event_id: str, ctx: Context = None) -> str:
    """Inspect saved grounded model output for an owned source event.

    Does not run inference. Saved output does not imply graph processing has
    completed, and source grounding does not prove semantic correctness.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
        record = await engine.get_extraction(event_id, user_id=user_id)
        if record is None:
            return json.dumps({"error": "Extraction not found"})
        return record.model_dump_json()
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    except ValueError:
        return json.dumps({"error": "Invalid event ID or saved extraction"})
    except Exception as exc:
        return _internal_error("memory_get_extraction", exc)


async def memory_extraction_status(event_id: str, ctx: Context = None) -> str:
    """Inspect owned extraction work without calling a model."""
    engine = _get_engine(ctx)
    try:
        status = await engine.extraction_status(event_id, user_id=_get_user_id(engine))
        return status.model_dump_json() if status is not None else json.dumps({"error": "Extraction work not found"})
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    except ValueError:
        return json.dumps({"error": "Invalid event ID"})
    except Exception as exc:
        return _internal_error("memory_extraction_status", exc)


async def memory_retry_extraction(event_id: str, ctx: Context = None, replan: bool = False) -> str:
    """Queue owned work; replan=True preserves the old plan and queues a new revision.

    Does not call a model or interrupt a live worker. Processing reuses saved
    extraction; a new plan revision may require new embedding inference.
    """
    engine = _get_engine(ctx)
    try:
        status = await engine.retry_extraction(event_id, user_id=_get_user_id(engine), replan=replan)
        return status.model_dump_json() if status is not None else json.dumps({"error": "Extraction work not found"})
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    except ValueError:
        return json.dumps({"error": "Invalid event ID"})
    except Exception as exc:
        return _internal_error("memory_retry_extraction", exc)


async def memory_process_extractions(limit: int = 100, budget_ms: float = 5000, ctx: Context = None) -> str:
    """Run due extraction jobs for this caller; may call the configured model.

    Budget is checked between jobs. Provider calls retain their own timeouts.
    """
    engine = _get_engine(ctx)
    try:
        result = await engine.process_extractions(user_id=_get_user_id(engine), limit=limit, budget_ms=budget_ms)
        return result.model_dump_json()
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    except ValueError:
        return json.dumps({"error": "Invalid extraction processing bounds"})
    except Exception as exc:
        return _internal_error("memory_process_extractions", exc)


async def memory_promote_node(
    node_id: str,
    request_id: str | None = None,
    ctx: Context = None,
) -> str:
    """Promote a memory node's lifecycle state.

    Advances a tentative node to stable, indicating the memory has
    been confirmed or reinforced.

    Args:
        node_id: The UUID of the node to promote.
        request_id: Optional UUID to reuse after an ambiguous response.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    try:
        node = await engine.get_node(
            node_id, include_superseded=True, user_id=user_id
        )
        if node is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})

        await engine.promote(
            node_id, user_id=user_id, request_id=request_id,
            actor_id=user_id or "mcp-operator",
        )

        updated = await engine.get_node(node_id, include_superseded=True, user_id=user_id)
        if updated is None:
            return json.dumps({"error": f"Node {node_id!r} not found after promote"})
        return json.dumps(_node_to_dict(updated))
    except ValueError as e:
        # Controlled validation message (e.g. invalid lifecycle transition).
        return json.dumps({"error": str(e)})
    except Exception as e:
        return _internal_error("memory_promote_node", e)


async def memory_archive_node(
    node_id: str,
    request_id: str | None = None,
    ctx: Context = None,
) -> str:
    """Archive a memory node.

    Moves a node to the archived terminal state. Archived nodes are
    excluded from default retrieval but remain in the event log.

    Args:
        node_id: The UUID of the node to archive.
        request_id: Optional UUID to reuse after an ambiguous response.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    try:
        node = await engine.get_node(node_id, include_superseded=True, user_id=user_id)
        if node is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})

        await engine.archive(
            node_id, user_id=user_id, request_id=request_id,
            actor_id=user_id or "mcp-operator",
        )

        updated = await engine.get_node(node_id, include_superseded=True, user_id=user_id)
        if updated is None:
            return json.dumps({"error": f"Node {node_id!r} not found after archive"})
        return json.dumps(_node_to_dict(updated))
    except ValueError as e:
        # Controlled validation message (e.g. invalid lifecycle transition).
        return json.dumps({"error": str(e)})
    except Exception as e:
        return _internal_error("memory_archive_node", e)


async def memory_evaluate_condition(
    node_id: str,
    state: ConditionState,
    evidence_id: str | None = None,
    request_id: str | None = None,
    evaluation_method: ConditionEvaluationMethod = ConditionEvaluationMethod.USER,
    reason: str | None = None,
    evaluated_at: AwareDatetime | None = None,
    ctx: Context = None,
) -> str:
    """Record whether a saved conditional claim currently applies.

    PRME records the supplied evaluation and provenance; it does not infer the
    condition. Reuse request_id after an ambiguous response to avoid a duplicate
    transition.
    """
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    try:
        updated = await engine.evaluate_condition(
            node_id,
            state,
            user_id=user_id,
            evidence_id=evidence_id,
            request_id=request_id,
            evaluation_method=evaluation_method,
            reason=reason,
            actor_id=user_id or "mcp-operator",
            evaluated_at=evaluated_at,
        )
        return json.dumps(_node_to_dict(updated))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_evaluate_condition", exc)


async def memory_mark_contradiction(
    node_a_id: str,
    node_b_id: str,
    evidence_id: str | None = None,
    ctx: Context = None,
) -> str:
    """Mark two owned claims as contested; exact retries are safe."""
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    try:
        nodes = await engine.contradict(
            node_a_id, node_b_id, evidence_id=evidence_id, user_id=user_id,
            actor_id=user_id or "mcp-operator",
        )
        return json.dumps({"nodes": [_node_to_dict(node) for node in nodes], "count": 2})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_mark_contradiction", exc)


async def memory_supersede(
    old_node_id: str,
    new_node_id: str,
    evidence_id: str | None = None,
    ctx: Context = None,
) -> str:
    """Replace an outdated owned claim; an exact retry is safe."""
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    try:
        await engine.supersede(
            old_node_id,
            new_node_id,
            evidence_id=evidence_id,
            user_id=user_id,
            actor_id=user_id or "mcp-operator",
        )
        nodes = [
            await engine.get_node(node_id, include_superseded=True, user_id=user_id)
            for node_id in (old_node_id, new_node_id)
        ]
        return json.dumps({
            "nodes": [_node_to_dict(node) for node in nodes if node is not None],
            "count": len(nodes),
        })
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_supersede", exc)


async def memory_resolve_contradiction(
    winner_id: str,
    loser_id: str,
    evidence_id: str | None = None,
    ctx: Context = None,
) -> str:
    """Choose the accepted claim and deprecate its contradicted alternative."""
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    try:
        nodes = await engine.resolve_contradiction(
            winner_id, loser_id, evidence_id=evidence_id, user_id=user_id,
            resolver_actor_id=user_id or "mcp-operator",
        )
        return json.dumps({"nodes": [_node_to_dict(node) for node in nodes], "count": 2})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return _internal_error("memory_resolve_contradiction", exc)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


async def resource_health() -> str:
    """PRME engine health status."""
    return json.dumps({"status": "ok", "version": __version__})


async def resource_stats(ctx: Context) -> str:
    """Memory database statistics — node count, backend type."""
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    node_count = 0
    backend = "duckdb"
    try:
        node_count = await engine.count_nodes(user_id=user_id)
    except Exception:
        logger.warning("Failed to count nodes for stats", exc_info=True)

    try:
        backend = engine._config.backend
    except Exception:
        pass

    return json.dumps({
        "node_count": node_count,
        "backend": backend,
        "version": __version__,
    })


async def resource_node(node_id: str, ctx: Context) -> str:
    """Get a specific memory node by ID."""
    engine = _get_engine(ctx)
    try:
        user_id = _get_user_id(engine)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})

    try:
        node = await engine.get_node(node_id, include_superseded=True, user_id=user_id)
        if node is None:
            return json.dumps({"error": f"Node {node_id!r} not found"})
        return json.dumps(_node_to_dict(node))
    except Exception as e:
        return _internal_error("memory://nodes resource", e)


def create_mcp_server(config: PRMEConfig | None = None, *, lifespan=engine_lifespan) -> FastMCP:
    """Build an isolated MCP server with request-scoped tools and resources."""
    server = FastMCP(
        "prme", instructions="PRME — Portable Relational Memory Engine. "
        "Store, retrieve, and organize long-term memory for AI agents.",
        lifespan=lifespan, stateless_http=True, json_response=True,
    )
    server.prme_config = config
    for tool in (memory_store, memory_retrieve, memory_ingest, memory_organize,
                 memory_get_node, memory_scan_nodes, memory_aggregate_assertions,
                 memory_aggregate_quantities, memory_get_assertion_state,
                 memory_get_event, memory_get_extraction,
                 memory_get_retrieval_receipt, memory_record_relevance,
                 memory_get_relevance, memory_list_relevance, memory_evaluate_learning,
                 memory_record_answer_citations, memory_get_answer_citations,
                 memory_list_answer_citations,
                 memory_extraction_status, memory_retry_extraction, memory_process_extractions,
                 memory_promote_node, memory_archive_node, memory_evaluate_condition,
                 memory_get_provenance, memory_supersede, memory_mark_contradiction,
                 memory_resolve_contradiction):
        server.tool()(tool)
    server.resource("memory://health")(resource_health)
    @server.resource("memory://stats")
    async def bound_stats() -> str:
        """Statistics for the current caller's memory."""
        return await resource_stats(server.get_context())

    server.resource("memory://nodes/{node_id}")(resource_node)
    return server


class _UserTokenVerifier:
    def __init__(self, keys):
        self.keys = keys

    async def verify_token(self, token: str):
        from mcp.server.auth.provider import AccessToken

        owner = None
        for user_id, key in self.keys.items():
            if secrets.compare_digest(token.encode("utf-8"), key.get_secret_value().encode("utf-8")):
                owner = user_id
        if owner is not None:
            return AccessToken(token=token, client_id=owner, scopes=["memory"])
        return None


def create_http_app(config: PRMEConfig | None = None):
    """Authenticated stateless Streamable HTTP for pre-provisioned bearer keys.

    This uses the MCP SDK's bearer and request-context middleware. It does not
    provide OAuth token issuance/discovery; clients must supply their configured
    credential explicitly. Stateless requests cannot reuse another user's session.
    """
    from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
    from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
    from starlette.middleware.authentication import AuthenticationMiddleware

    config = config or PRMEConfig()
    if not config.mcp.user_keys:
        raise ValueError("MCP HTTP requires PRME_MCP_USER_KEYS with a distinct key per user")
    from prme.storage.engine import MemoryEngine

    shared_engine = None

    @asynccontextmanager
    async def request_lifespan(server):
        if shared_engine is None:
            raise RuntimeError("MCP HTTP engine is not initialized")
        yield {"engine": shared_engine}

    server = create_mcp_server(config, lifespan=request_lifespan)
    app = server.streamable_http_app()

    @asynccontextmanager
    async def app_lifespan(app):
        nonlocal shared_engine
        async with MemoryEngine.open(config) as engine:
            shared_engine = engine
            try:
                async with server.session_manager.run():
                    yield
            finally:
                shared_engine = None

    app.router.lifespan_context = app_lifespan
    for route in app.routes:
        route.app = RequireAuthMiddleware(route.app, required_scopes=["memory"])
    app.add_middleware(AuthContextMiddleware)
    app.add_middleware(AuthenticationMiddleware, backend=BearerAuthBackend(_UserTokenVerifier(config.mcp.user_keys)))
    return app


mcp = create_mcp_server()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    """Run the PRME MCP server."""
    parser = argparse.ArgumentParser(
        prog="prme-mcp",
        description="PRME MCP Server — memory backend for MCP-compatible clients",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the memory directory (default: current directory)",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http", "sse"],
        help="MCP transport (default: stdio)",
    )
    args = parser.parse_args()

    # Configure PRME paths from --db-path
    if args.db_path:
        db_dir = os.path.abspath(args.db_path)
        os.makedirs(db_dir, exist_ok=True)
        lexical_dir = os.path.join(db_dir, "lexical_index")
        os.makedirs(lexical_dir, exist_ok=True)
        os.environ["PRME_DB_PATH"] = os.path.join(db_dir, "memory.duckdb")
        os.environ["PRME_VECTOR_PATH"] = os.path.join(db_dir, "vectors.usearch")
        os.environ["PRME_LEXICAL_PATH"] = lexical_dir

    config = PRMEConfig()
    if args.transport == "sse":
        parser.error("Unauthenticated SSE is no longer supported; use streamable-http with PRME_MCP_USER_KEYS")
    if args.transport == "streamable-http":
        import uvicorn

        uvicorn.run(create_http_app(config), host="127.0.0.1", port=8000)
    else:
        if config.mcp.user_keys:
            parser.error("Bearer keys require streamable-http; use PRME_MCP_USER_ID for stdio")
        create_mcp_server(config).run(transport="stdio")
