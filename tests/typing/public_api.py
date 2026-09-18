"""Static consumer contract; checked by mypy rather than executed."""

from datetime import datetime, timezone
from typing import assert_type
from uuid import UUID

from prme import AliasProposalInboxItem, AliasProposalReviewResult, AnswerCitationRecord, AnswerCitationSubmission, AssertionAggregation, AssertionQuery, AssertionState, AssertionStateQuery, ContextAblation, ContextPresenceCredit, FastIngestConflict, FastIngestItem, FullRetrievalEvaluation, FullRetrievalTrial, LearningEvaluation, MemoryValueBinding, ProductAlignmentCandidate, ProductCandidateEntity, QuantityAggregation, QuantityAggregationQuery, RankingMultipliers, RankingProfile, RankingProfileApplication, RankingProfileState, RankingProfileStatus, RelevanceRecord, RelevanceSubmission, RetrievedValueBinding, RetrievalMode, RetrievalReceipt, ExtractionRecord, ExtractionStatus, ExtractionProcessingResult, MemoryClient, RetrievalResponse, Scope, StoreReceipt, ToolArgumentBindingUse, ToolArgumentResolution, ablate_context, assess_context_presence, evaluate_full_retrieval
from prme.models import Event, MemoryNode, ProcessingResult
from prme.integrations.typesafe import JevProductProposal, ProductEntity
from prme.organizer.models import OrganizeResult


def consume(client: MemoryClient) -> None:
    assert issubclass(FastIngestConflict, ValueError)
    assert_type(client.promote("node-id", user_id="alice"), None)
    assert_type(client.archive("node-id", user_id="alice"), None)
    assert_type(client.ingest("Alice used Rust yesterday", user_id="alice",
                              event_time=datetime(2024, 3, 10, tzinfo=timezone.utc),
                              metadata={"source": "import"}), str)
    assert_type(client.store_with_receipt(
        "Alice uses Rust 2024 Edition",
        user_id="alice",
        value_bindings=[MemoryValueBinding(
            reference="rust-edition",
            kind="language",
            presentation="Rust 2024 Edition",
            lookup="rust-2024",
        )],
        valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ), StoreReceipt)
    assert_type(client.ingest_fast_many([
        FastIngestItem(content="first"),
        {"content": "second", "scope": Scope.PROJECT},
    ], user_id="alice", request_id="47ad2465-9d6d-4e8a-bd64-d4ff27ad1eae"), list[str])
    assert_type(client.get_retrieval_receipt("request-id", user_id="alice"), RetrievalReceipt | None)
    assert_type(client.get_relevance("feedback-id", user_id="alice"), RelevanceRecord | None)
    assert_type(client.list_relevance(user_id="alice"), list[RelevanceRecord])
    assert_type(client.retrieve("preferences", user_id="alice",
                                retrieval_mode=RetrievalMode.EXPLICIT), RetrievalResponse)
    response = client.retrieve("Rust", user_id="alice")
    assert_type(response.bundle.value_bindings(), tuple[RetrievedValueBinding, ...])
    resolution = response.bundle.resolve_tool_arguments(
        {"language": "Rust 2024 Edition"}
    )
    assert_type(resolution, ToolArgumentResolution)
    assert_type(resolution.binding_uses, tuple[ToolArgumentBindingUse, ...])
    assert_type(client.get_node("node-id"), MemoryNode | None)
    assert_type(
        client.propose_product_alignment(
            "left-node",
            "right-node",
            {"name": "Product"},
            {"name": "Product Pro"},
            user_id="alice",
        ),
        JevProductProposal,
    )
    assert_type(
        client.find_product_alignment_candidates(
            [
                ProductCandidateEntity(
                    node_id=UUID("11111111-1111-1111-1111-111111111111"),
                    product=ProductEntity(name="Product"),
                    catalog="left",
                ),
                {
                    "node_id": "22222222-2222-2222-2222-222222222222",
                    "product": {"name": "Product Pro"},
                    "catalog": "right",
                },
            ],
            user_id="alice",
            cross_catalog_only=True,
        ),
        list[ProductAlignmentCandidate],
    )
    assert_type(
        client.list_alias_proposals(user_id="alice", status="pending"),
        list[AliasProposalInboxItem],
    )
    assert_type(
        client.review_alias_proposal(
            "proposal-id",
            user_id="alice",
            decision="accepted",
            reviewer_id="human:alice",
        ),
        AliasProposalReviewResult,
    )
    assert_type(client.query_nodes(user_id="alice"), list[MemoryNode])
    assert_type(client.get_events("alice"), list[Event])
    assert_type(client.get_event("event-id", user_id="alice"), Event | None)
    assert_type(client.get_extraction("event-id", user_id="alice"), ExtractionRecord | None)
    assert_type(client.get_event_nodes("event-id", user_id="alice"), list[MemoryNode])
    assert_type(client.process_pending(user_id="alice"), ProcessingResult)
    assert_type(client.extraction_status("event-id", user_id="alice"), ExtractionStatus | None)
    assert_type(client.retry_extraction("event-id", user_id="alice", replan=True), ExtractionStatus | None)
    assert_type(client.process_extractions(user_id="alice"), ExtractionProcessingResult)
    assert_type(client.organize(user_id="alice"), OrganizeResult)
    assert_type(
        client.aggregate_assertions(
            AssertionQuery(predicates=("likes",)), user_id="alice"
        ),
        AssertionAggregation,
    )
    assert_type(
        client.aggregate_quantities(
            QuantityAggregationQuery(predicates=("spent",)), user_id="alice"
        ),
        QuantityAggregation,
    )
    assert_type(
        client.get_assertion_state(
            AssertionStateQuery(
                subject="Alice",
                predicate="lives_in",
                scope=Scope.PERSONAL,
                valid_at=datetime.now(timezone.utc),
            ),
            user_id="alice",
        ),
        AssertionState,
    )
    for node in client.iter_nodes(user_id="alice"):
        assert_type(node, MemoryNode)


def submit_relevance(client: MemoryClient, submission: RelevanceSubmission) -> None:
    assert_type(client.record_relevance(submission, user_id="alice"), RelevanceRecord)


def submit_citations(client: MemoryClient, submission: AnswerCitationSubmission) -> None:
    assert_type(client.record_answer_citations(submission, user_id="alice"), AnswerCitationRecord)
    assert_type(client.get_answer_citations(str(submission.citation_id), user_id="alice"), AnswerCitationRecord | None)
    assert_type(client.list_answer_citations(user_id="alice"), list[AnswerCitationRecord])


def assess_citation(record: AnswerCitationRecord, response: RetrievalResponse) -> None:
    ablation = ablate_context(response.bundle, [record.cited_node_ids[0]])
    assert_type(ablation, ContextAblation)
    assert_type(assess_context_presence(
        ablation, record, node_id=record.cited_node_ids[0],
        baseline_correct=True, counterfactual_correct=False,
        evaluation_id="fixed-reader-and-judge-v1",
    ), ContextPresenceCredit)


def assess_full_retrieval(receipts: list[RetrievalReceipt], trials: list[FullRetrievalTrial]) -> None:
    assert_type(evaluate_full_retrieval(
        receipts,
        trials,
        user_id="alice",
        scopes=[Scope.PROJECT],
        proposal_input_checksum="a" * 64,
        memory_artifact_sha256="b" * 64,
        candidate_multipliers=RankingMultipliers(lexical=2),
    ), FullRetrievalEvaluation)


def manage_ranking_profile(
    client: MemoryClient,
    proposal: LearningEvaluation,
    holdout: FullRetrievalEvaluation,
) -> None:
    assert_type(client.evaluate_full_retrieval(
        holdout.trials,
        user_id="alice",
        scopes=[Scope.PROJECT],
        proposal_input_checksum=proposal.input_checksum,
        memory_artifact_sha256="b" * 64,
        candidate_multipliers=proposal.multipliers,
    ), FullRetrievalEvaluation)
    profile = client.create_ranking_profile(proposal, holdout, user_id="alice")
    assert_type(profile, RankingProfile)
    assert_type(client.get_ranking_profile(str(profile.profile_id), user_id="alice"), RankingProfile | None)
    assert_type(client.list_ranking_profiles(user_id="alice"), list[RankingProfile])
    assert_type(client.get_active_ranking_profile(user_id="alice"), RankingProfile | None)
    assert_type(client.get_ranking_profile_status(
        str(profile.profile_id), user_id="alice",
    ), RankingProfileStatus | None)
    assert_type(client.activate_ranking_profile(
        str(profile.profile_id), user_id="alice",
    ), RankingProfileState)
    assert_type(profile.application, RankingProfileApplication)
    assert_type(client.list_ranking_profile_history(
        user_id="alice",
    ), list[RankingProfileState])
    assert_type(client.deactivate_ranking_profile(
        user_id="alice",
    ), RankingProfileState | None)
    assert_type(client.rollback_ranking_profile(
        str(profile.profile_id), user_id="alice",
    ), RankingProfileState)


async def postgres_workspace_consumer() -> None:
    from prme import MemoryWorkspace, NamespaceMemory, NamespaceInfo, PRMEConfig
    async with MemoryWorkspace.open_postgres(PRMEConfig(), name="app", max_connections=3) as workspace:
        assert_type(await workspace.list_namespaces(), list[NamespaceInfo])
        async with workspace.namespace("project") as memory:
            assert_type(memory, NamespaceMemory)
            assert_type(await memory.retrieve("query", user_id="alice"), RetrievalResponse)
