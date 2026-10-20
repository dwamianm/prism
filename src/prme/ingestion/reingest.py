"""Re-derive one owner's stored sources after an engine change (``MemoryEngine.reingest()``, prism-monorepo #278).

Memories saved before an engine change keep the entities, claims and links the
engine derived then, or none, when ``store()`` saved them plainly. This runs the
batched extraction ``ingest()`` runs over an owner's sources again, in event
order, and replaces what was derived from each source with what the present
engine derives:

- A source is a memory's event. The event and its raw note are never written.
- The model output is saved first (``REINGEST_EXTRACTED``), then one transaction
  publishes the new nodes and edges, retires the source's earlier claims and
  writes the marker that makes the source current (``REINGEST_COMMITTED``;
  ``prme.storage.reingest`` says why these records stand beside the derivation
  journal). A source whose marker names the engine's present settings is
  skipped, so a second run does nothing, and a run that stops continues where
  the saved output and the markers left it.
- An earlier claim that the new derivation states again is superseded by it. One
  it no longer states is archived, with a pointer to the claims that replaced it.
  Entities stay: they are shared by every source that names them, and a new
  claim reuses them.
- Sources are batched as ``ingest()`` batches them: those of one session share a
  request up to ``extraction_batch_target_tokens``, and one without a session
  goes alone. Every request goes through the pipeline's own batched request, so
  its checks, its halving of a request that fails as a whole and its repeat of
  turns the model left out apply here too.
- With ``enable_windowed_extraction`` or ``enable_fact_text_resolution`` on,
  extraction is not batched (#91): each source is its own request, shown the
  turns before it, and the settings that name those options give the run its own
  fingerprint, so sources derived without them are derived again.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from types import SimpleNamespace
from uuid import UUID

import structlog

from prme.ingestion.errors import ExtractionError, ReingestBusyError
from prme.ingestion.graph_writer import WriteQueueGraphWriter
from prme.ingestion.pipeline import BatchRun
from prme.ingestion.schema import ExtractionResult
from prme.models.derivation import StaleDerivationPlanError
from prme.models.events import Event
from prme.models.nodes import MemoryNode
from prme.models.reingest import ReingestArchive, ReingestRecord, ReingestResult
from prme.privacy.policy import PrivacyDenied
from prme.storage.index_eviction import evict_from_indexes
from prme.storage.reingest import REINGEST_KEY, SourceRef, scope_value
from prme.types import ACTIVE_LIFECYCLE_STATES, EpistemicType, LifecycleState, NodeType, Scope

if TYPE_CHECKING:
    from prme.ingestion.pipeline import IngestionPipeline
    from prme.ingestion.planning import PlanningGraph
    from prme.models.derivation import DerivationPlan
    from prme.models.extraction import ExtractionRecord

logger = structlog.get_logger(__name__)

# Sources read, grouped into requests and derived at a time. Requests are made
# up inside a window, so a session that spans two windows is asked in two.
WINDOW = 100
# Pages of sources the scan reads, and of markers it looks up.
_SCAN_PAGE = 1000
# Failed requests in a row that end a run: a provider that does not answer would
# otherwise fail every request of the run in turn.
_FAILED_REQUESTS_IN_A_ROW = 3
_CLAIM_KINDS = frozenset({"fact", "relationship"})


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def extraction_settings(pipeline: IngestionPipeline) -> dict:
    """What decides the model's output for a source: the engine, the model, the prompt and the checks.

    The prompt is the digest of the request the provider would send for one
    sample turn, so a change to its text or its layout changes the settings.
    """
    from prme import __version__
    from prme.ingestion.extraction import BatchTurn, _extraction_prompt_for_role, batch_request_sha256
    from prme.ingestion.pipeline import _GROUNDING_POLICY

    provider = pipeline._extraction_provider
    sample = BatchTurn(index=1, role="user", content="Sample turn.", speaker="Sample",
                       time=datetime(2000, 1, 1, tzinfo=timezone.utc))
    settings = {
        "engine": __version__,
        "provider": provider.provider_name,
        "model": provider.model_name,
        "grounding_policy": _GROUNDING_POLICY,
        "request": batch_request_sha256([sample]),
        "batch_target_tokens": pipeline._batch_target_tokens,
        "extraction_corrections": pipeline._extraction_corrections,
        "claim_fixes": pipeline._claim_fixes,
        "speaker_grounding": pipeline._speaker_grounding,
    }
    if pipeline._extraction_window_turns > 0 or pipeline._resolve_fact_text:
        # Each source is asked on its own, with these prompts, so they and the window decide the output. The
        # settings of an engine with both options off are those it had before them.
        prompt = _extraction_prompt_for_role("user", windowed=pipeline._extraction_window_turns > 0,
                                             resolving=pipeline._resolve_fact_text)
        settings["request"] = hashlib.sha256(prompt.encode()).hexdigest()
        settings["window_turns"] = pipeline._extraction_window_turns
        settings["window_owner_wide"] = pipeline._extraction_window_owner_wide
        settings["resolve_fact_text"] = pipeline._resolve_fact_text
    return settings


def derivation_settings(pipeline: IngestionPipeline) -> dict:
    """The extraction settings and the options that decide what is derived from the output."""
    return {
        **extraction_settings(pipeline),
        "merge_repeated_claims": pipeline._merge_repeated_claims,
        "bind_speaker_references": pipeline._bind_speaker_references,
        "claim_sentence_text": pipeline._claim_sentence_text,
        "link_mentions": pipeline._link_mentions,
        "owner_name_carry": pipeline._owner_name_carry,
        "link_owner_groups": pipeline._link_owner_groups,
    }


def _position(ref: SourceRef) -> tuple:
    return (ref.effective, ref.stamp, ref.event_id)


def encode_cursor(ref: SourceRef) -> str:
    raw = json.dumps([ref.effective.isoformat(), ref.stamp.isoformat(), ref.event_id])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> SourceRef:
    try:
        effective, stamp, event_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        ref = SourceRef(datetime.fromisoformat(effective), datetime.fromisoformat(stamp), str(UUID(event_id)), None)
        if ref.effective.tzinfo is None or ref.stamp.tzinfo is None:
            raise ValueError("Cursor times need a timezone")
        return ref
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid reingest cursor") from exc


def _normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().replace("_", " ").split())


def _statement(node: MemoryNode) -> tuple:
    """What a claim states: subject, predicate, object and polarity, compared as the claim merge compares text."""
    metadata = node.metadata or {}
    return (_normalized(metadata.get("subject", "")), _normalized(metadata.get("predicate", "")),
            _normalized(metadata.get("object", "")), metadata.get("polarity", "unknown"))


def _is_claim(node: MemoryNode) -> bool:
    return node.node_type != NodeType.ENTITY and (node.metadata or {}).get("extraction_kind") in _CLAIM_KINDS


def request_groups(events: list[Event], target_tokens: int) -> list[list[Event]]:
    """The requests the pipeline would send for ``events``, in order, as ``_queued_requests`` counts them.

    Each event joins the request its session has open in its scope while the
    text of the request stays within the target; one that does not fit, or has no
    session, starts a request.
    """
    from prme.retrieval.tokenization import count_tokens

    groups: list[list[Event]] = []
    open_requests: dict[tuple, tuple[int, int]] = {}
    for event in events:
        tokens = count_tokens(event.content)
        key = (event.scope, event.session_id)
        opened = open_requests.get(key) if event.session_id else None
        if opened is not None and opened[1] < target_tokens and opened[1] + tokens <= target_tokens:
            groups[opened[0]].append(event)
            open_requests[key] = (opened[0], opened[1] + tokens)
            continue
        groups.append([event])
        if event.session_id:
            open_requests[key] = (len(groups) - 1, tokens)
    return groups


class Reingester:
    """Runs ``reingest()`` for one pipeline; holds the owners it is running for."""

    def __init__(self, pipeline: IngestionPipeline) -> None:
        self._pipeline = pipeline
        self._running: set[str] = set()
        self._scopes: dict[str, Scope | None] = {}

    async def _check(self, user_id: str, scope=None, *, events=(), nodes=()) -> None:
        coordinator = self._pipeline._privacy
        if coordinator is not None:
            await coordinator.require_legacy_reingest(user_id, self._scopes.get(user_id, scope))
            await coordinator.require_legacy_inputs(events, nodes)

    async def _submit(self, operation, *, label, user_id, scope, nodes=()):
        async def checked():
            await self._check(user_id, scope, nodes=nodes)
            return await operation()
        return await self._pipeline._write_queue.submit(checked, label=label)

    async def run(self, *, user_id: str, scope: Scope | str | None = None, limit: int | None = None,
                  cursor: str | None = None, dry_run: bool = False) -> ReingestResult:
        await self._check(user_id, scope)
        if not user_id:
            raise ValueError("Reingest requires user_id")
        if limit is not None and limit < 0:
            raise ValueError("Reingest limit cannot be negative")
        after = decode_cursor(cursor) if cursor else None
        scope = Scope(scope) if scope is not None else None
        if user_id in self._running:
            raise ReingestBusyError("A re-derivation of this owner's sources is already running")
        self._running.add(user_id)
        self._scopes[user_id] = scope
        try:
            return await self._run(user_id, scope, limit, after, dry_run)
        finally:
            self._running.discard(user_id)
            self._scopes.pop(user_id, None)

    async def _run(self, user_id: str, scope: Scope | None, limit: int | None, after: SourceRef | None,
                   dry_run: bool) -> ReingestResult:
        pipeline = self._pipeline
        repo = pipeline._event_store.reingest
        await self._check(user_id, scope)
        extraction_key = _digest(extraction_settings(pipeline))
        fingerprint = _digest(derivation_settings(pipeline))

        refs: list[SourceRef] = []
        page_after: SourceRef | None = None
        while True:
            page = await repo.scan_sources(user_id=user_id, scope=scope_value(scope), after=page_after,
                                           limit=_SCAN_PAGE)
            refs.extend(page)
            if len(page) < _SCAN_PAGE:
                break
            page_after = page[-1]
        # A source whose own extraction has not finished has not been derived once yet:
        # process_extractions() does that, and a re-derivation beside it would double it.
        sources = [ref for ref in refs if ref.work_status in (None, "complete")]
        current: set[str] = set()
        for start in range(0, len(sources), _SCAN_PAGE):
            await self._check(user_id, scope)
            ids = [ref.event_id for ref in sources[start:start + _SCAN_PAGE]]
            current |= await repo.current(ids, fingerprint)
        pending = [ref for ref in sources if ref.event_id not in current]
        ahead = [ref for ref in pending if after is None or _position(ref) > _position(after)]
        selected = ahead if limit is None else ahead[:limit]

        counts = {"processed": 0, "failed": 0, "attempted": 0, "calls": 0, "tokens": 0}
        if dry_run:
            await self._plan_only(user_id, selected, extraction_key, counts)
            counts["attempted"] = len(selected)
        else:
            await self._derive_all(user_id, selected, extraction_key, fingerprint, counts)
        attempted = selected[:counts["attempted"]]
        after_attempted = ahead[len(attempted):]
        await self._check(user_id, scope)
        return ReingestResult(
            user_id=user_id, scope=scope, dry_run=dry_run, total=len(sources), current=len(current),
            unfinished=len(refs) - len(sources), selected=len(attempted), processed=counts["processed"],
            failed=counts["failed"], remaining=len(pending) - counts["processed"],
            cursor=encode_cursor(attempted[-1]) if attempted and after_attempted else None,
            model_calls=counts["calls"], source_tokens=counts["tokens"], fingerprint=fingerprint,
        )

    async def _events(self, user_id: str, refs: list[SourceRef]) -> list[Event]:
        events = []
        for ref in refs:
            await self._check(user_id)
            event = await self._pipeline._event_store.get(ref.event_id)
            if event is not None and event.user_id == user_id:
                await self._check(user_id, event.scope, events=(event,))
                events.append(event)
        return events

    async def _plan_only(self, user_id: str, selected: list[SourceRef], extraction_key: str, counts: dict) -> None:
        """The requests the selected sources need, without sending any."""
        from prme.retrieval.tokenization import count_tokens

        repo = self._pipeline._event_store.reingest
        for start in range(0, len(selected), WINDOW):
            events = await self._events(user_id, selected[start:start + WINDOW])
            saved = await repo.saved_extractions([str(event.id) for event in events], user_id=user_id,
                                                 extraction_key=extraction_key)
            await self._check(user_id, events=events)
            asked = [event for event in events if str(event.id) not in saved]
            counts["calls"] += len(request_groups(asked, self._pipeline._batch_target_tokens))
            counts["tokens"] += sum(count_tokens(event.content) for event in asked)

    async def _derive_all(self, user_id: str, selected: list[SourceRef], extraction_key: str, fingerprint: str,
                          counts: dict) -> None:
        from prme.retrieval.tokenization import count_tokens

        pipeline = self._pipeline
        repo = pipeline._event_store.reingest
        run = BatchRun(save=lambda batch, records: repo.save_extractions(batch, records,
                                                                         extraction_key=extraction_key),
                       check=lambda: self._check(user_id))
        in_a_row = 0
        try:
            for start in range(0, len(selected), WINDOW):
                window = selected[start:start + WINDOW]
                events = await self._events(user_id, window)
                by_id = {str(event.id): event for event in events}
                saved = await repo.saved_extractions(list(by_id), user_id=user_id, extraction_key=extraction_key)
                await self._check(user_id, events=events)
                groups = request_groups([event for event in events if str(event.id) not in saved],
                                        pipeline._batch_target_tokens)
                group_of = {str(event.id): index for index, group in enumerate(groups) for event in group}
                asked: set[int] = set()
                for ref in window:
                    counts["attempted"] += 1
                    event = by_id.get(ref.event_id)
                    if event is None:
                        counts["failed"] += 1
                        continue
                    if ref.event_id not in saved and group_of[ref.event_id] not in asked:
                        asked.add(group_of[ref.event_id])
                        group = groups[group_of[ref.event_id]]
                        counts["tokens"] += sum(count_tokens(item.content) for item in group)
                        saved.update(await self._request(user_id, group, run, extraction_key))
                        in_a_row = 0 if ref.event_id in saved else in_a_row + 1
                    record = saved.get(ref.event_id)
                    if record is None:
                        counts["failed"] += 1
                        if in_a_row >= _FAILED_REQUESTS_IN_A_ROW:
                            return
                        continue
                    try:
                        await self._derive(event, record, fingerprint, extraction_key)
                    except PrivacyDenied:
                        raise
                    except Exception as exc:
                        logger.error("reingest.source_failed", event_id=ref.event_id, error_type=type(exc).__name__)
                        counts["failed"] += 1
                    else:
                        counts["processed"] += 1
        finally:
            counts["calls"] = run.requests

    async def _request(self, user_id: str, group: list[Event], run: BatchRun, extraction_key: str,
                       ) -> dict[str, ExtractionRecord]:
        """Send one request for the group and return the output saved for its sources."""
        try:
            if self._pipeline._batch_target_tokens > 0:
                await self._pipeline._request_batch(group[0], group, claim=None, run=run)
            else:
                # Each source is its own request while the window or fact text resolution is on.
                await self._pipeline._request_single(group[0], run=run)
        except ExtractionError as exc:
            logger.warning("reingest.request_failed", event_id=str(group[0].id), reason=exc.reason_code)
        return await self._pipeline._event_store.reingest.saved_extractions(
            [str(event.id) for event in group], user_id=user_id, extraction_key=extraction_key)

    async def _owned_claims(self, event: Event, note_id: str) -> list[MemoryNode]:
        """The active claims an earlier derivation made from this source.

        Those its journaled receipt lists, those an earlier re-derivation stamped, and
        a claim that cites this source alone. A claim merged from several sources
        belongs to the source whose derivation made it.
        """
        pipeline = self._pipeline
        nodes = await pipeline._graph_store.get_event_nodes(str(event.id), user_id=event.user_id)
        await self._check(event.user_id, event.scope, events=(event,), nodes=nodes)
        derived = await pipeline._event_store.reingest.derived_node_ids(str(event.id))
        owned = []
        for node in nodes:
            stamped = ((node.metadata or {}).get(REINGEST_KEY) or {}).get("source_event_id")
            if (str(node.id) != note_id and _is_claim(node) and node.scope == event.scope
                    and node.lifecycle_state in ACTIVE_LIFECYCLE_STATES
                    and (str(node.id) in derived or stamped == str(event.id) or node.evidence_refs == [event.id])):
                owned.append(node)
        return owned

    async def _derive(self, event: Event, record: ExtractionRecord, fingerprint: str, extraction_key: str) -> None:
        pipeline = self._pipeline
        await self._check(event.user_id, event.scope, events=(event,))
        result = ExtractionResult.model_validate(record.result)
        direct = await pipeline._event_store.get_direct_store(str(event.id), user_id=event.user_id)
        note_id = str(direct.node.id) if direct is not None else str(event.id)
        for attempt in (1, 2):
            archived: list[ReingestArchive] = []
            async with pipeline._scope_lock(event.user_id, event.scope):
                owned = await self._owned_claims(event, note_id)

                async def before_prepare(graph: PlanningGraph) -> None:
                    await self._retire(graph, event, owned, fingerprint, archived)

                plan = await pipeline._prepare_plan(result, event, before_prepare=before_prepare,
                    effect_check=lambda: self._check(event.user_id, event.scope, events=(event,)))
                await self._stage(plan)
                planned = ReingestRecord(
                    event_id=event.id, user_id=event.user_id, scope=event.scope, fingerprint=fingerprint,
                    extraction_key=extraction_key, plan_id=plan.id, plan_checksum=plan.checksum,
                    node_ids=tuple(node.id for node in plan.nodes),
                    edge_ids=tuple(edge.id for edge in plan.edges + plan.replacements),
                    superseded=tuple(edge.target_id for edge in plan.replacements), archived=tuple(archived),
                )
                try:
                    committed = await self._submit(
                        lambda: pipeline._graph_store.commit_reingest(plan, planned),
                        label=f"reingest.commit:{event.id}",
                        user_id=event.user_id, scope=event.scope, nodes=plan.references,
                    )
                except StaleDerivationPlanError:
                    # Something this plan read changed since: the saved output plans again, with no model call.
                    if attempt == 2:
                        raise
                    continue
            break
        if committed.plan_id != plan.id:
            # Another run committed this source first, and its records stand: this plan was never published.
            return
        await self._after(plan, committed, event, record, note_id)

    async def _retire(self, graph: PlanningGraph, event: Event, owned: list[MemoryNode], fingerprint: str,
                      archived: list[ReingestArchive]) -> None:
        """Stamp the new claims, supersede the earlier ones they restate and set the rest aside to archive."""
        for node_id, node in list(graph.nodes.items()):
            if _is_claim(node):
                stamp = {"source_event_id": str(event.id), "fingerprint": fingerprint}
                graph.nodes[node_id] = node.model_copy(update={"metadata": {**(node.metadata or {}), REINGEST_KEY: stamp}})
        claims = [node for node in graph.nodes.values() if _is_claim(node)]
        used: set[UUID] = set()
        for old in sorted(owned, key=lambda node: (node.event_time or node.valid_from, str(node.id))):
            if graph.is_retired(str(old.id)):
                # The claim merge already retired it into the new claim of the same statement.
                continue
            match = next((new for new in claims if new.id not in used and _statement(new) == _statement(old)), None)
            if (match is not None and old.lifecycle_state in (LifecycleState.TENTATIVE, LifecycleState.STABLE)
                    and match.epistemic_type in (EpistemicType.OBSERVED, EpistemicType.ASSERTED)):
                used.add(match.id)
                await graph.supersede(str(old.id), str(match.id), evidence_id=str(event.id))
            if not graph.is_retired(str(old.id)):
                archived.append(ReingestArchive(node_id=old.id, replaced_by=tuple(new.id for new in claims)))

    async def _stage(self, plan: DerivationPlan) -> None:
        """Stage the plan's vectors and lexical documents before the graph commit, as a derivation does."""
        pipeline = self._pipeline
        await self._check(plan.user_id, plan.scope, nodes=plan.references)
        if getattr(pipeline._graph_store, "_conn", None) is None:
            # PostgreSQL writes its prepared vector and text columns in the commit's own transaction.
            return
        for embedding in plan.embeddings:
            await self._submit(
                lambda item=embedding: pipeline._vector_index.stage(item, user_id=plan.user_id),
                label=f"reingest.vector:{embedding.node_id}",
                user_id=plan.user_id, scope=plan.scope, nodes=plan.references,
            )
        await self._submit(
            lambda: pipeline._lexical_index.stage(plan), label=f"reingest.lexical:{plan.id}",
            user_id=plan.user_id, scope=plan.scope, nodes=plan.references,
        )

    async def _after(self, plan: DerivationPlan, marker: ReingestRecord, event: Event, record: ExtractionRecord,
                     note_id: str) -> None:
        """Take the retired claims out of the search indexes, then link the entities the source names.

        Both are best effort and outside the commit, as for a derivation: the commit is durable,
        and the index compaction job and ``prme rebuild`` repair an eviction this misses.
        """
        pipeline = self._pipeline
        await self._check(plan.user_id, plan.scope, events=(event,), nodes=plan.references)

        async def submit(operation, *, label):
            return await self._submit(operation, label=label, user_id=plan.user_id,
                                      scope=plan.scope, nodes=plan.references)

        queue = SimpleNamespace(submit=submit)
        retired = [str(edge.target_id) for edge in plan.replacements] + [str(item.node_id) for item in marker.archived]
        for node_id in retired:
            await evict_from_indexes(queue, pipeline._lexical_index, pipeline._vector_index, node_id)
            await self._check(plan.user_id, plan.scope)
        writer = (
            pipeline._graph_writer or WriteQueueGraphWriter(pipeline._graph_store, pipeline._write_queue)
        )
        if isinstance(writer, WriteQueueGraphWriter):
            writer = copy.copy(writer)
            writer._write_queue = queue

        async def create_edge(edge):
            await self._check(plan.user_id, plan.scope)
            return await writer.create_edge(edge)
        if pipeline._link_mentions:
            from prme.ingestion.mention_links import link_event_mentions

            await link_event_mentions(
                str(event.id), plan.nodes, user_id=event.user_id, graph_store=pipeline._graph_store,
                event_store=pipeline._event_store, with_note=True, create_edge=create_edge,
                extraction=record, note_id=note_id,
            )
        if pipeline._link_owner_groups:
            from prme.ingestion.group_links import link_group_claims

            if pipeline._privacy is not None:
                await pipeline._privacy.link_legacy_groups(
                    str(event.id), plan.nodes, user_id=event.user_id, scope=event.scope, event=event,
                    effect_check=lambda: self._check(
                        plan.user_id, plan.scope, events=(event,), nodes=plan.references),
                    submit=submit)
            else:
                await link_group_claims(
                    str(event.id), plan.nodes, user_id=event.user_id, graph_store=pipeline._graph_store,
                    event_store=pipeline._event_store, create_edge=create_edge,
                )
