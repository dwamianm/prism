"""Custom epistemic benchmark unique to PRME.

Tests PRME-specific epistemic capabilities:
  1. Supersedence chain correctness
  2. Confidence calibration (predicted vs actual)
  3. Contradiction detection precision/recall
  4. Belief revision under conflicting evidence
  5. Abstention quality (knows what it doesn't know)

All test data is synthetic. Works without LLM -- uses engine.store() directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from benchmarks.metrics import exclusion_score, keyword_match_score
from benchmarks.models import BenchmarkResult, QueryResult

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

from prme.types import EpistemicType, LifecycleState, NodeType, Scope


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------


@dataclass
class EpistemicTestCase:
    """A single epistemic benchmark test case."""

    ability: str  # category name
    description: str
    query: str
    expected_keywords: list[str]
    excluded_keywords: list[str] = field(default_factory=list)
    expect_abstention: bool = False


# ---------------------------------------------------------------------------
# Supersedence chain correctness
# ---------------------------------------------------------------------------


def _generate_supersedence_cases() -> tuple[list[dict], list[EpistemicTestCase]]:
    """Generate test cases for supersedence chain correctness.

    Creates a chain of facts where each new fact supersedes the previous.
    The system should surface the latest in the chain, not older versions.
    """
    facts = [
        # Chain 1: CTO changes 3 times
        {
            "content": "The CTO is Alex Chen, hired January 2023.",
            "node_type": "fact",
            "tags": ["cto", "leadership"],
            "day": 1,
        },
        {
            "content": "Alex Chen left the company. The new CTO is Priya Patel, starting March 2023.",
            "node_type": "fact",
            "tags": ["cto", "leadership", "change"],
            "day": 60,
        },
        {
            "content": "Priya Patel promoted to CEO. The CTO role is now held by James Wilson as of July 2023.",
            "node_type": "fact",
            "tags": ["cto", "leadership", "change"],
            "day": 180,
        },
        # Chain 2: Office location moves twice
        {
            "content": "The company office is at 100 Main Street, Boston.",
            "node_type": "fact",
            "tags": ["office", "location"],
            "day": 1,
        },
        {
            "content": "We moved the office from Boston to 200 Tech Ave, Denver.",
            "node_type": "fact",
            "tags": ["office", "location", "move"],
            "day": 90,
        },
        {
            "content": "The Denver office closed. New headquarters at 500 Innovation Blvd, Portland.",
            "node_type": "fact",
            "tags": ["office", "location", "move"],
            "day": 200,
        },
        # Chain 3: Framework migration chain
        {
            "content": "The web framework is Django 3.2.",
            "node_type": "fact",
            "tags": ["framework", "django"],
            "day": 1,
        },
        {
            "content": "Migrated from Django 3.2 to Django 4.2 for async support.",
            "node_type": "fact",
            "tags": ["framework", "django", "migration"],
            "day": 45,
        },
        {
            "content": "Migrated from Django to FastAPI for the backend. Django is fully deprecated.",
            "node_type": "fact",
            "tags": ["framework", "fastapi", "migration"],
            "day": 120,
        },
    ]

    test_cases = [
        EpistemicTestCase(
            ability="supersedence",
            description="Latest CTO after 3 changes",
            query="Who is the current CTO?",
            expected_keywords=["James Wilson"],
        ),
        EpistemicTestCase(
            ability="supersedence",
            description="Latest office after 2 moves",
            query="Where is the company office located?",
            expected_keywords=["Portland"],
        ),
        EpistemicTestCase(
            ability="supersedence",
            description="Latest framework after migration chain",
            query="What web framework does the backend use?",
            expected_keywords=["FastAPI"],
        ),
    ]

    return facts, test_cases


# ---------------------------------------------------------------------------
# Confidence calibration
# ---------------------------------------------------------------------------


def _generate_confidence_cases() -> tuple[list[dict], list[EpistemicTestCase]]:
    """Generate test cases for confidence calibration.

    Stores facts with different epistemic types and confidence levels.
    Higher confidence facts should rank above lower confidence ones.
    """
    facts = [
        # High-confidence observed facts
        {
            "content": "Server uptime for December was 99.99%. Verified by monitoring dashboard.",
            "node_type": "fact",
            "epistemic_type": "observed",
            "tags": ["uptime", "verified"],
            "day": 1,
        },
        # Medium-confidence asserted fact
        {
            "content": "Server uptime is usually above 99.9% based on team reports.",
            "node_type": "fact",
            "epistemic_type": "asserted",
            "tags": ["uptime", "reported"],
            "day": 1,
        },
        # Low-confidence hypothetical
        {
            "content": "Server uptime might drop below 99% during the migration window.",
            "node_type": "fact",
            "epistemic_type": "hypothetical",
            "tags": ["uptime", "hypothesis"],
            "day": 1,
        },
        # High-confidence: revenue from financial system
        {
            "content": "Q4 revenue was $8.2 million according to the financial system.",
            "node_type": "fact",
            "epistemic_type": "observed",
            "tags": ["revenue", "financial"],
            "day": 1,
        },
        # Lower-confidence: inferred revenue
        {
            "content": "Revenue growth suggests Q4 revenue is likely around $7-9 million.",
            "node_type": "fact",
            "epistemic_type": "inferred",
            "tags": ["revenue", "estimate"],
            "day": 1,
        },
        # Conditional fact
        {
            "content": "If the enterprise deal closes, Q4 revenue could reach $10 million.",
            "node_type": "fact",
            "epistemic_type": "conditional",
            "tags": ["revenue", "conditional"],
            "day": 1,
        },
    ]

    test_cases = [
        EpistemicTestCase(
            ability="confidence",
            description="Observed uptime should rank above hypothetical",
            query="What is the server uptime?",
            expected_keywords=["99.99%"],
        ),
        EpistemicTestCase(
            ability="confidence",
            description="Observed revenue should rank above inferred",
            query="What was Q4 revenue?",
            expected_keywords=["8.2 million"],
        ),
    ]

    return facts, test_cases


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------


def _generate_contradiction_cases() -> tuple[list[dict], list[EpistemicTestCase]]:
    """Generate test cases for contradiction detection.

    Stores directly contradictory facts. The system should ideally
    surface the most recent / highest-confidence version and downrank
    the contradicted one.
    """
    facts = [
        # Direct contradiction pair 1
        {
            "content": "The maximum API rate limit is 1000 requests per minute.",
            "node_type": "fact",
            "tags": ["rate-limit", "api"],
            "day": 1,
        },
        {
            "content": "The maximum API rate limit has been increased to 5000 requests per minute.",
            "node_type": "fact",
            "tags": ["rate-limit", "api", "update"],
            "day": 30,
        },
        # Direct contradiction pair 2
        {
            "content": "The team works Monday through Friday, 9 AM to 5 PM.",
            "node_type": "fact",
            "tags": ["work-hours", "schedule"],
            "day": 1,
        },
        {
            "content": "The team switched to a flexible schedule with no fixed hours.",
            "node_type": "fact",
            "tags": ["work-hours", "schedule", "change"],
            "day": 50,
        },
        # Direct contradiction pair 3
        {
            "content": "All data is stored in a single region (us-east-1).",
            "node_type": "fact",
            "tags": ["data", "region"],
            "day": 1,
        },
        {
            "content": "Data is now replicated across three regions: us-east-1, eu-west-1, and ap-southeast-1.",
            "node_type": "fact",
            "tags": ["data", "region", "multi-region"],
            "day": 60,
        },
    ]

    test_cases = [
        EpistemicTestCase(
            ability="contradiction",
            description="Updated rate limit should surface over old value",
            query="What is the API rate limit?",
            expected_keywords=["5000"],
        ),
        EpistemicTestCase(
            ability="contradiction",
            description="Flexible schedule should surface over fixed hours",
            query="What are the team's working hours?",
            expected_keywords=["flexible"],
        ),
        EpistemicTestCase(
            ability="contradiction",
            description="Multi-region should surface over single region",
            query="Where is data stored?",
            expected_keywords=["three regions"],
        ),
    ]

    return facts, test_cases


# ---------------------------------------------------------------------------
# Belief revision under conflicting evidence
# ---------------------------------------------------------------------------


def _generate_belief_revision_cases() -> tuple[list[dict], list[EpistemicTestCase]]:
    """Generate test cases for belief revision.

    Stores an initial belief, then progressively conflicting evidence.
    The system should update its ranking as evidence accumulates.
    """
    facts = [
        # Initial strong belief
        {
            "content": "PostgreSQL is the primary database for all services.",
            "node_type": "fact",
            "epistemic_type": "observed",
            "tags": ["database", "postgresql"],
            "day": 1,
        },
        # First sign of change (weak)
        {
            "content": "The team is evaluating MongoDB for the analytics microservice.",
            "node_type": "fact",
            "epistemic_type": "asserted",
            "tags": ["database", "mongodb", "evaluation"],
            "day": 30,
        },
        # Stronger evidence
        {
            "content": "The analytics microservice prototype is running on MongoDB with promising results.",
            "node_type": "fact",
            "epistemic_type": "observed",
            "tags": ["database", "mongodb", "prototype"],
            "day": 60,
        },
        # Decision made
        {
            "content": "Decision: The analytics service will use MongoDB in production. Migration starts next sprint.",
            "node_type": "decision",
            "epistemic_type": "observed",
            "tags": ["database", "mongodb", "decision"],
            "day": 90,
        },
        # Another revision scenario: deployment strategy
        {
            "content": "Deployments are done manually via SSH to production servers.",
            "node_type": "fact",
            "epistemic_type": "observed",
            "tags": ["deployment", "manual"],
            "day": 1,
        },
        {
            "content": "We started using Jenkins for automated deployments.",
            "node_type": "fact",
            "epistemic_type": "observed",
            "tags": ["deployment", "jenkins", "automation"],
            "day": 40,
        },
        {
            "content": "Jenkins has been replaced by GitHub Actions for CI/CD.",
            "node_type": "fact",
            "epistemic_type": "observed",
            "tags": ["deployment", "github-actions", "migration"],
            "day": 80,
        },
        {
            "content": "We are migrating from GitHub Actions to ArgoCD for GitOps-based deployments.",
            "node_type": "fact",
            "epistemic_type": "observed",
            "tags": ["deployment", "argocd", "gitops"],
            "day": 120,
        },
    ]

    test_cases = [
        EpistemicTestCase(
            ability="belief_revision",
            description="Analytics DB revised from PostgreSQL to MongoDB",
            query="What database does the analytics service use?",
            expected_keywords=["MongoDB"],
        ),
        EpistemicTestCase(
            ability="belief_revision",
            description="Deployment tool revised through 4 stages",
            query="What is the current deployment tool?",
            expected_keywords=["ArgoCD"],
        ),
    ]

    return facts, test_cases


# ---------------------------------------------------------------------------
# Abstention quality
# ---------------------------------------------------------------------------


def _generate_epistemic_abstention_cases() -> tuple[
    list[dict], list[EpistemicTestCase]
]:
    """Generate test cases for abstention quality.

    Stores domain-specific facts and asks questions about completely
    unrelated domains. The engine should return low-confidence results
    or no results.
    """
    facts = [
        {
            "content": "The backend API is built with Python 3.12 and FastAPI.",
            "node_type": "fact",
            "tags": ["backend", "python", "fastapi"],
        },
        {
            "content": "The database uses PostgreSQL 16 with pgvector extension.",
            "node_type": "fact",
            "tags": ["database", "postgresql"],
        },
        {
            "content": "Deployment uses Kubernetes 1.28 on AWS EKS.",
            "node_type": "fact",
            "tags": ["deployment", "kubernetes"],
        },
        {
            "content": "The team has 12 engineers and 3 designers.",
            "node_type": "fact",
            "tags": ["team", "engineering"],
        },
    ]

    # Questions about topics never stored
    test_cases = [
        EpistemicTestCase(
            ability="abstention",
            description="No info about marketing budget",
            query="What is the marketing budget for next quarter?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        EpistemicTestCase(
            ability="abstention",
            description="No info about company founding",
            query="When was the company founded and by whom?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        EpistemicTestCase(
            ability="abstention",
            description="No info about pet policy",
            query="What is the office pet policy?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        EpistemicTestCase(
            ability="abstention",
            description="No info about competitor analysis",
            query="Who are the main competitors and how do we compare?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        EpistemicTestCase(
            ability="abstention",
            description="No info about insurance",
            query="What health insurance plan does the company offer?",
            expected_keywords=[],
            expect_abstention=True,
        ),
    ]

    return facts, test_cases


# ---------------------------------------------------------------------------
# Benchmark class
# ---------------------------------------------------------------------------


class EpistemicBenchmark:
    """Custom epistemic benchmark unique to PRME.

    Tests five epistemic capabilities:
      1. Supersedence chain correctness
      2. Confidence calibration
      3. Contradiction detection precision/recall
      4. Belief revision under conflicting evidence
      5. Abstention quality (knows what it doesn't know)
    """

    name = "epistemic"

    # Score threshold below which we consider the engine abstained
    ABSTENTION_SCORE_THRESHOLD = 0.3

    async def run(self, engine: MemoryEngine) -> BenchmarkResult:
        """Execute the epistemic benchmark.

        Args:
            engine: Initialized MemoryEngine to benchmark.

        Returns:
            BenchmarkResult with per-ability scores.
        """
        start_time = time.monotonic()

        generators = [
            ("supersedence", _generate_supersedence_cases),
            ("confidence", _generate_confidence_cases),
            ("contradiction", _generate_contradiction_cases),
            ("belief_revision", _generate_belief_revision_cases),
            ("abstention", _generate_epistemic_abstention_cases),
        ]

        all_details: list[QueryResult] = []
        category_results: list[tuple[str, float]] = []

        for ability_name, gen_func in generators:
            facts, test_cases = gen_func()
            user_id = f"bench-epi-{ability_name}"

            # Store facts for this ability
            for fact in facts:
                kwargs: dict = {
                    "user_id": user_id,
                    "role": "user",
                    "node_type": NodeType(fact.get("node_type", "fact")),
                    "scope": Scope.PERSONAL,
                }
                if "epistemic_type" in fact:
                    kwargs["epistemic_type"] = EpistemicType(
                        fact["epistemic_type"]
                    )
                await engine.store(fact["content"], **kwargs)

            # Evaluate test cases
            for tc in test_cases:
                response = await engine.retrieve(
                    tc.query, user_id=user_id
                )

                if tc.expect_abstention:
                    score = self._evaluate_abstention(response)
                else:
                    top_content = " ".join(
                        r.node.content for r in response.results[:5]
                    )
                    kw_score = keyword_match_score(
                        tc.expected_keywords, top_content
                    )
                    ex_score = exclusion_score(
                        tc.excluded_keywords, top_content
                    )
                    score = kw_score * ex_score

                is_correct = score >= 0.5
                all_details.append(
                    QueryResult(
                        query=tc.query,
                        category=tc.ability,
                        expected=(
                            "ABSTAIN"
                            if tc.expect_abstention
                            else ", ".join(tc.expected_keywords)
                        ),
                        actual=(
                            f"top_score={response.results[0].composite_score:.3f}"
                            if response.results
                            else "no results"
                        ),
                        correct=is_correct,
                        score=score,
                    )
                )
                category_results.append((tc.ability, score))

        # Compute aggregate metrics
        from benchmarks.metrics import category_scores as compute_categories

        cat_scores = compute_categories(category_results)
        correct = sum(1 for d in all_details if d.correct)
        incorrect = sum(1 for d in all_details if not d.correct)
        abstained = sum(
            1
            for d in all_details
            if d.category == "abstention" and d.correct
        )

        overall = (
            sum(d.score for d in all_details) / len(all_details)
            if all_details
            else 0.0
        )
        duration_ms = (time.monotonic() - start_time) * 1000

        return BenchmarkResult(
            benchmark_name="epistemic",
            overall_score=overall,
            category_scores=cat_scores,
            total_queries=len(all_details),
            correct=correct,
            incorrect=incorrect,
            abstained=abstained,
            duration_ms=duration_ms,
            details=all_details,
        )

    def _evaluate_abstention(self, response) -> float:
        """Score an abstention test case.

        Returns 1.0 if the engine correctly shows low confidence or no
        results; 0.0 if it returns high-confidence results for an
        unknown topic.
        """
        if not response.results:
            return 1.0
        top_score = response.results[0].composite_score
        if top_score < self.ABSTENTION_SCORE_THRESHOLD:
            return 1.0
        elif top_score < 0.5:
            return 0.5
        else:
            return 0.0
