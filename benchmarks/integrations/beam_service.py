"""PRME service for the official Mem0 BEAM benchmark's OSS HTTP boundary.

The upstream runner only sends source chat messages to ``POST /memories`` and
the neutral probing question to ``POST /search``.  This adapter implements those
two calls without reading BEAM rubrics, answers, question types, or source IDs.
It is an evaluation service, not a production compatibility API.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from prme import MemoryEngine, PRMEConfig
from prme.config import EmbeddingConfig, ExtractionConfig, OrganizerConfig
from prme.ingestion.errors import ExtractionError, MaterializationError


UPSTREAM_REPOSITORY = "https://github.com/mem0ai/memory-benchmarks"
UPSTREAM_COMMIT = "4b61c5d31b9c668a12b4f5e78064248a02c82d2b"
ADAPTER_SCHEMA = 4
_LEGACY_ADAPTER_SCHEMAS = (1, 2, 3)
MAX_RESULTS_PER_SOURCE = 1


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class AddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[Message] = Field(min_length=1)
    user_id: str = Field(min_length=1)
    timestamp: int | None = Field(default=None, strict=True)
    observation_date: str | None = None
    custom_instructions: str | None = None
    metadata: dict[str, Any] | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    limit: int = Field(default=200, ge=0, le=1000, strict=True)
    rerank: bool = False


def _request_hash(
    body: AddRequest, event_time: datetime | None, *, adapter_schema: int = ADAPTER_SCHEMA
) -> str:
    payload = {
        "adapter_schema": adapter_schema,
        "event_time": event_time.isoformat() if event_time else None,
        "messages": [message.model_dump() for message in body.messages],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _session_id(
    *, user_id: str, event_time: datetime | None, request_hash: str
) -> str:
    """Derive the strongest session boundary exposed by the upstream API.

    BEAM sends a conversation in two-message chunks without a session identifier.
    Chunks from one source session share an observation timestamp, so timestamp is
    the only neutral boundary that reconstructs those sessions.  When it is absent,
    keep the request itself isolated instead of joining the user's entire history.
    """
    boundary = event_time.isoformat() if event_time is not None else request_hash
    digest = hashlib.sha256(f"{user_id}\0{boundary}".encode()).hexdigest()
    return f"beam:{digest}"


def _event_time(body: AddRequest) -> datetime | None:
    if body.timestamp is not None:
        try:
            return datetime.fromtimestamp(body.timestamp, timezone.utc)
        except (OSError, OverflowError, ValueError) as exc:
            raise HTTPException(422, "timestamp is outside the supported range") from exc
    if body.observation_date is None:
        return None
    try:
        parsed = datetime.strptime(body.observation_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(422, "observation_date must use YYYY-MM-DD") from exc
    return parsed.replace(tzinfo=timezone.utc)


class _SourceIndex:
    """Lazy event index used to make exact upstream retries idempotent."""

    def __init__(self, engine: MemoryEngine):
        self.engine = engine
        self.loaded: set[str] = set()
        self.events: dict[tuple[str, str, int], str] = {}
        self.event_times: dict[str, datetime] = {}
        self.latest: dict[str, datetime] = {}

    def note(self, user_id: str, request_hash: str, index: int, event_id: str,
             event_time: datetime | None) -> None:
        self.events[(user_id, request_hash, index)] = event_id
        if event_time is not None:
            self.event_times[event_id] = event_time
            current = self.latest.get(user_id)
            if current is None or event_time > current:
                self.latest[user_id] = event_time

    def source_time(self, node: Any) -> datetime | None:
        """Return when the cited source was observed, not a date inside a fact."""
        observed = [
            self.event_times[str(reference)]
            for reference in node.evidence_refs
            if str(reference) in self.event_times
        ]
        return max(observed) if observed else None

    async def load(self, user_id: str) -> None:
        if user_id in self.loaded:
            return
        offset = 0
        while True:
            page = await self.engine.get_events(user_id, limit=1000, offset=offset)
            for event in page:
                metadata = event.metadata or {}
                request_hash = metadata.get("beam_request_hash")
                message_index = metadata.get("beam_message_index")
                if (
                    metadata.get("benchmark_adapter") == "beam"
                    and isinstance(request_hash, str)
                    and isinstance(message_index, int)
                ):
                    self.note(
                        user_id,
                        request_hash,
                        message_index,
                        str(event.id),
                        event.event_time or event.created_at,
                    )
            if len(page) < 1000:
                break
            offset += len(page)
        self.loaded.add(user_id)


def _result(node: Any, *, source_time: datetime | None = None) -> dict[str, Any]:
    created = source_time or node.event_time or node.created_at
    return {
        "id": str(node.id),
        "memory": node.content,
        "created_at": created.astimezone(timezone.utc).isoformat(),
    }


async def _complete_existing_extraction(
    engine: MemoryEngine, *, event_id: str, user_id: str
) -> None:
    status = await engine.extraction_status(event_id, user_id=user_id)
    if status is None:
        raise HTTPException(503, "Persisted source has no extraction work record")
    if status.status == "complete":
        return
    if status.status == "running":
        if status.lease_expires_at is None:
            raise HTTPException(503, "Persisted extraction has an invalid lease")
        wait_seconds = max(
            0.0,
            (status.lease_expires_at - datetime.now(timezone.utc)).total_seconds(),
        )
        # The benchmark profile uses a short lease so a service restart can
        # recover within the pinned upstream client's 300-second HTTP timeout.
        if wait_seconds > 65:
            raise HTTPException(503, "Persisted extraction lease exceeds recovery bound")
        if wait_seconds:
            await asyncio.sleep(wait_seconds + 0.05)
        status = await engine.extraction_status(event_id, user_id=user_id)
        if status is None:
            raise HTTPException(503, "Persisted extraction status disappeared")
        if status.status == "complete":
            return
        if (
            status.status == "running"
            and status.lease_expires_at is not None
            and status.lease_expires_at > datetime.now(timezone.utc)
        ):
            raise HTTPException(503, "Persisted extraction lease did not expire")
    if status.status != "running":
        await engine.retry_extraction(event_id, user_id=user_id)
    await engine.process_extractions(user_id=user_id, limit=100, budget_ms=300_000)
    status = await engine.extraction_status(event_id, user_id=user_id)
    if status is None or status.status != "complete":
        phase = status.phase if status is not None else "unknown"
        raise HTTPException(503, f"Persisted extraction did not complete ({phase})")


async def _complete_existing_materialization(
    engine: MemoryEngine, *, event_id: str, user_id: str
) -> None:
    status = await engine.processing_status(event_id, user_id=user_id)
    if status is None:
        raise HTTPException(503, "Persisted source has no materialization work record")
    if status.status == "complete":
        return
    await engine.process_pending(user_id=user_id, budget_ms=300_000)
    status = await engine.processing_status(event_id, user_id=user_id)
    if status is None or status.status != "complete":
        raise HTTPException(503, "Persisted source materialization did not complete")


def create_app(config: PRMEConfig, *, profile: Literal["raw", "extracted"] = "raw") -> FastAPI:
    """Create the loopback benchmark service over one persistent PRME pack."""

    if profile not in {"raw", "extracted"}:
        raise ValueError("profile must be 'raw' or 'extracted'")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with MemoryEngine.open(config) as engine:
            app.state.engine = engine
            app.state.sources = _SourceIndex(engine)
            app.state.lock = asyncio.Lock()
            yield

    app = FastAPI(title=f"PRME BEAM {profile} benchmark adapter", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "profile": profile,
            "adapter_schema": ADAPTER_SCHEMA,
            "upstream_commit": UPSTREAM_COMMIT,
        }

    @app.post("/memories")
    async def add(body: AddRequest) -> dict[str, Any]:
        if body.custom_instructions:
            raise HTTPException(422, "BEAM adapter does not accept custom instructions")
        if body.metadata:
            raise HTTPException(422, "BEAM adapter does not accept benchmark-side metadata")
        event_time = _event_time(body)
        request_hash = _request_hash(body, event_time)
        compatible_request_hashes = (
            request_hash,
            *(
                _request_hash(body, event_time, adapter_schema=schema)
                for schema in _LEGACY_ADAPTER_SCHEMAS
            ),
        )
        session_id = _session_id(
            user_id=body.user_id,
            event_time=event_time,
            request_hash=request_hash,
        )
        engine: MemoryEngine = app.state.engine
        sources: _SourceIndex = app.state.sources

        async with app.state.lock:
            await sources.load(body.user_id)
            output: list[dict[str, Any]] = []
            for index, message in enumerate(body.messages):
                event_id = next(
                    (
                        sources.events[(body.user_id, compatible_hash, index)]
                        for compatible_hash in compatible_request_hashes
                        if (body.user_id, compatible_hash, index) in sources.events
                    ),
                    None,
                )
                if event_id is None:
                    metadata = {
                        "benchmark_adapter": "beam",
                        "adapter_schema": ADAPTER_SCHEMA,
                        "beam_request_hash": request_hash,
                        "beam_message_index": index,
                        "beam_message_count": len(body.messages),
                    }
                    try:
                        if profile == "raw":
                            receipt = await engine.store_with_receipt(
                                message.content,
                                user_id=body.user_id,
                                role=message.role,
                                session_id=session_id,
                                metadata=metadata,
                                event_time=event_time,
                            )
                            event_id = str(receipt.event_id)
                            if receipt.processing_status.status != "complete":
                                sources.note(
                                    body.user_id,
                                    request_hash,
                                    index,
                                    event_id,
                                    event_time,
                                )
                                raise HTTPException(
                                    503, "Source saved; materialization remains pending"
                                )
                        else:
                            event_id = await engine.ingest(
                                message.content,
                                user_id=body.user_id,
                                role=message.role,
                                session_id=session_id,
                                metadata=metadata,
                                event_time=event_time,
                                wait_for_extraction=True,
                            )
                    except (ExtractionError, MaterializationError) as exc:
                        if exc.event_id:
                            sources.note(
                                body.user_id, request_hash, index, exc.event_id, event_time
                            )
                        raise HTTPException(
                            503,
                            {
                                "error": "Source saved; processing did not complete",
                                "event_id": exc.event_id,
                                "reason_code": exc.reason_code,
                            },
                        ) from exc
                    sources.note(body.user_id, request_hash, index, event_id, event_time)
                elif profile == "extracted":
                    await _complete_existing_extraction(
                        engine, event_id=event_id, user_id=body.user_id
                    )
                else:
                    await _complete_existing_materialization(
                        engine, event_id=event_id, user_id=body.user_id
                    )

                if profile == "extracted":
                    await _complete_existing_materialization(
                        engine, event_id=event_id, user_id=body.user_id
                    )

                event = await engine.get_event(event_id, user_id=body.user_id)
                if event is None:
                    raise HTTPException(503, "Persisted source is unavailable")
                sources.note(
                    body.user_id,
                    request_hash,
                    index,
                    event_id,
                    event.event_time or event.created_at,
                )
                nodes = await engine.get_event_nodes(event_id, user_id=body.user_id)
                output.extend(
                    {
                        **_result(node, source_time=sources.source_time(node)),
                        "event": "ADD",
                    }
                    for node in nodes
                )

            return {"results": output}

    @app.post("/search")
    async def search(body: SearchRequest) -> dict[str, Any]:
        if body.rerank:
            raise HTTPException(422, "BEAM adapter does not implement a separate reranker")
        if body.limit == 0:
            return {"results": []}
        engine: MemoryEngine = app.state.engine
        sources: _SourceIndex = app.state.sources
        async with app.state.lock:
            await sources.load(body.user_id)
            response = await engine.retrieve(
                body.query,
                user_id=body.user_id,
                reference_time=sources.latest.get(body.user_id),
                limit=body.limit,
                max_per_source=MAX_RESULTS_PER_SOURCE,
                include_cross_scope=False,
            )
            return {
                "results": [
                    {
                        **_result(
                            candidate.node,
                            source_time=sources.source_time(candidate.node),
                        ),
                        "score": candidate.composite_score,
                    }
                    for candidate in response.results[: body.limit]
                ]
            }

    return app


def _config(args: argparse.Namespace) -> PRMEConfig:
    base = PRMEConfig(
        database_url=None,
        encryption_enabled=False,
        enable_qa_pairing=False,
        enable_query_reformulation=False,
        enable_store_supersedence=False,
        enable_surprise_gating=False,
        enable_reranker=False,
        embedding=EmbeddingConfig(
            provider="fastembed",
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
        ),
        organizer=OrganizerConfig(opportunistic_enabled=False),
    )
    extraction = base.extraction
    if args.profile == "extracted":
        extraction = ExtractionConfig(
            provider=args.extraction_provider,
            model=args.extraction_model,
            base_url=args.extraction_base_url,
            reasoning_effort=args.extraction_reasoning_effort,
            max_retries=args.extraction_max_retries,
            timeout=args.extraction_timeout,
            lease_seconds=args.extraction_lease_seconds,
        )
    root = args.directory
    return base.model_copy(
        update={
            "db_path": str(root / "memory.duckdb"),
            "vector_path": str(root / "vectors.usearch"),
            "lexical_path": str(root / "lexical"),
            "duckdb_threads": args.duckdb_threads,
            "extraction": extraction,
        }
    )


def _manifest(args: argparse.Namespace, config: PRMEConfig) -> dict[str, Any]:
    extraction: dict[str, Any] | None = None
    if args.profile == "extracted":
        endpoint = config.extraction.base_url
        if endpoint is not None:
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "extraction base URL must be HTTP(S) without credentials, query, or fragment"
                )
        extraction = {
            "provider": config.extraction.provider,
            "model": config.extraction.model,
            "base_url": config.extraction.base_url,
            "reasoning_effort": config.extraction.reasoning_effort,
            "max_retries": config.extraction.max_retries,
            "temperature": config.extraction.temperature,
            "timeout": config.extraction.timeout,
            "lease_seconds": config.extraction.lease_seconds,
        }
    return {
        "adapter_schema": ADAPTER_SCHEMA,
        "adapter_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "profile": args.profile,
        "prme_version": distribution_version("prme"),
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_commit": UPSTREAM_COMMIT,
        "duckdb_threads": config.duckdb_threads,
        "embedding": {
            "provider": config.embedding.provider,
            "model": config.embedding.model_name,
            "dimension": config.embedding.dimension,
        },
        "scoring_version": config.scoring.version_id,
        "packing": config.packing.model_dump(mode="json"),
        "retrieval": {
            "max_per_source": MAX_RESULTS_PER_SOURCE,
            "passage_time": "latest_evidence_event",
        },
        "admission": {
            "raw_materialization_before_ack": True,
            "extraction_before_ack": args.profile == "extracted",
        },
        "extraction": extraction,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8889)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--profile", choices=("raw", "extracted"), default="raw")
    parser.add_argument("--duckdb-threads", type=int, default=1)
    parser.add_argument("--extraction-provider", default="ollama")
    parser.add_argument("--extraction-model", default="qwen3.5:9b")
    parser.add_argument("--extraction-base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument(
        "--extraction-reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default="none",
    )
    parser.add_argument("--extraction-max-retries", type=int, default=3)
    parser.add_argument("--extraction-timeout", type=float, default=300.0)
    parser.add_argument("--extraction-lease-seconds", type=float, default=60.0)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be 1-65535")
    if args.duckdb_threads < 1:
        raise ValueError("duckdb-threads must be positive")
    if args.extraction_max_retries < 1:
        raise ValueError("extraction-max-retries must be positive")
    if (
        not math.isfinite(args.extraction_timeout)
        or not math.isfinite(args.extraction_lease_seconds)
        or args.extraction_timeout <= 0
        or args.extraction_lease_seconds <= 0
    ):
        raise ValueError("extraction timeout and lease must be positive and finite")
    if args.extraction_lease_seconds > 60:
        raise ValueError("extraction lease must be at most 60 seconds for bounded resume")
    exists = args.directory.exists()
    if args.resume and not exists:
        raise ValueError("--resume requires an existing adapter directory")
    if not args.resume and exists:
        raise ValueError("Use a fresh directory, or pass --resume for the same run")
    args.directory.mkdir(parents=True, exist_ok=args.resume)
    (args.directory / "lexical").mkdir(exist_ok=True)

    config = _config(args)
    manifest_path = args.directory / "beam_adapter_manifest.json"
    manifest = _manifest(args, config)
    if args.resume:
        saved = json.loads(manifest_path.read_text())
        if saved != manifest:
            raise ValueError("Saved BEAM adapter identity does not match this launch")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")

    import uvicorn

    uvicorn.run(
        create_app(config, profile=args.profile),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
