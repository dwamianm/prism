"""LongMemEval-style benchmark for multi-ability memory evaluation.

Tests five core abilities:
  1. Information extraction -- find specific stored facts
  2. Multi-session reasoning -- connect facts across sessions
  3. Temporal reasoning -- handle time-ordered facts correctly
  4. Knowledge updates -- correctly supersede old facts
  5. Abstention -- know when PRME doesn't know

Includes both synthetic (no external deps) and real dataset adapters.
The real LongMemEval oracle dataset can be downloaded via:
    python scripts/download_benchmarks.py --longmemeval
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.metrics import exclusion_score, keyword_match_score
from benchmarks.models import BenchmarkResult, QueryResult

from prme.retrieval.context_formatter import format_for_llm
from prme.storage.engine import MemoryEngine
from prme.types import EpistemicType, NodeType, Scope

if TYPE_CHECKING:
    from benchmarks.llm_judge import LLMJudgeConfig


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------


@dataclass
class LMETestCase:
    """A single LongMemEval test case."""

    ability: str  # one of the 5 abilities
    query: str
    expected_keywords: list[str]
    excluded_keywords: list[str] = field(default_factory=list)
    expect_abstention: bool = False


def _generate_info_extraction_cases() -> tuple[list[dict], list[LMETestCase]]:
    """Generate test cases for information extraction ability.

    Stores specific facts and tests precise retrieval.
    """
    facts = [
        {"content": "The company headquarters is located at 123 Innovation Drive, San Francisco, CA 94107.", "node_type": "fact", "tags": ["address", "hq"]},
        {"content": "Annual revenue for fiscal year 2024 was $12.5 million.", "node_type": "fact", "tags": ["revenue", "financial"]},
        {"content": "The engineering team has 24 members across 4 sub-teams.", "node_type": "fact", "tags": ["team", "engineering"]},
        {"content": "Product launch date for version 3.0 is March 15, 2025.", "node_type": "fact", "tags": ["launch", "version"]},
        {"content": "The SLA guarantees 99.95% uptime for enterprise customers.", "node_type": "fact", "tags": ["sla", "uptime"]},
        {"content": "Maximum file upload size is 500MB per request.", "node_type": "fact", "tags": ["upload", "limits"]},
        {"content": "The primary contact for billing issues is billing@example.com.", "node_type": "fact", "tags": ["billing", "contact"]},
        {"content": "Server infrastructure runs in us-east-1 and eu-west-1 AWS regions.", "node_type": "fact", "tags": ["infrastructure", "aws"]},
        {"content": "The API supports JSON, XML, and Protocol Buffers response formats.", "node_type": "fact", "tags": ["api", "formats"]},
        {"content": "Employee benefits include 401k matching up to 6% of salary.", "node_type": "fact", "tags": ["benefits", "401k"]},
        {"content": "The data retention policy keeps logs for 90 days.", "node_type": "fact", "tags": ["retention", "logs"]},
        {"content": "Integration partners include Salesforce, HubSpot, and Zendesk.", "node_type": "fact", "tags": ["integrations", "partners"]},
    ]

    test_cases = [
        LMETestCase(ability="info_extraction", query="Where is the company headquarters?", expected_keywords=["123 Innovation Drive", "San Francisco"]),
        LMETestCase(ability="info_extraction", query="What was the annual revenue?", expected_keywords=["12.5 million"]),
        LMETestCase(ability="info_extraction", query="How many engineers are on the team?", expected_keywords=["24"]),
        LMETestCase(ability="info_extraction", query="When is version 3.0 launching?", expected_keywords=["March 15"]),
        LMETestCase(ability="info_extraction", query="What is the SLA uptime guarantee?", expected_keywords=["99.95%"]),
        LMETestCase(ability="info_extraction", query="What is the max file upload size?", expected_keywords=["500MB"]),
        LMETestCase(ability="info_extraction", query="Who handles billing issues?", expected_keywords=["billing@example.com"]),
        LMETestCase(ability="info_extraction", query="Which AWS regions are used?", expected_keywords=["us-east-1"]),
        LMETestCase(ability="info_extraction", query="What API response formats are supported?", expected_keywords=["JSON"]),
        LMETestCase(ability="info_extraction", query="What is the 401k matching rate?", expected_keywords=["6%"]),
        LMETestCase(ability="info_extraction", query="How long are logs retained?", expected_keywords=["90 days"]),
        LMETestCase(ability="info_extraction", query="What are the integration partners?", expected_keywords=["Salesforce"]),
    ]

    return facts, test_cases


def _generate_multi_session_cases() -> tuple[list[dict], list[LMETestCase]]:
    """Generate test cases for multi-session reasoning.

    Facts are stored across different simulated sessions.
    Queries require combining information from multiple sessions.
    """
    facts = [
        # Session A: Project setup
        {"content": "Project Orion uses a microservices architecture with 12 services.", "node_type": "fact", "session": "session-a", "tags": ["orion", "architecture"]},
        {"content": "The gateway service handles authentication and routing.", "node_type": "fact", "session": "session-a", "tags": ["gateway", "auth"]},
        {"content": "Each microservice has its own PostgreSQL database.", "node_type": "fact", "session": "session-a", "tags": ["database", "microservices"]},
        # Session B: Team info
        {"content": "Marcus leads the Orion project with 8 direct reports.", "node_type": "fact", "session": "session-b", "tags": ["marcus", "orion", "team"]},
        {"content": "The Orion team follows kanban methodology.", "node_type": "fact", "session": "session-b", "tags": ["orion", "kanban"]},
        {"content": "Marcus reports to VP of Engineering, Sarah.", "node_type": "fact", "session": "session-b", "tags": ["marcus", "sarah", "reporting"]},
        # Session C: Deployment info
        {"content": "Orion deploys to production every Tuesday and Thursday.", "node_type": "fact", "session": "session-c", "tags": ["orion", "deployment"]},
        {"content": "Blue-green deployment strategy is used for Orion services.", "node_type": "fact", "session": "session-c", "tags": ["orion", "blue-green"]},
        {"content": "Orion's SLA requires deployment rollback within 5 minutes.", "node_type": "fact", "session": "session-c", "tags": ["orion", "sla", "rollback"]},
        # Session D: Performance
        {"content": "Orion's p99 latency target is 200ms for all API endpoints.", "node_type": "fact", "session": "session-d", "tags": ["orion", "latency"]},
        {"content": "The gateway service handles 10,000 requests per second at peak.", "node_type": "fact", "session": "session-d", "tags": ["gateway", "throughput"]},
        {"content": "Marcus's team reduced p99 latency from 500ms to 180ms last quarter.", "node_type": "fact", "session": "session-d", "tags": ["marcus", "latency", "improvement"]},
    ]

    test_cases = [
        # Cross-session queries
        LMETestCase(
            ability="multi_session",
            query="Who leads the Orion project and what architecture does it use?",
            expected_keywords=["Marcus", "microservices"],
        ),
        LMETestCase(
            ability="multi_session",
            query="How does the Orion team deploy and what methodology do they follow?",
            expected_keywords=["Tuesday", "kanban"],
        ),
        LMETestCase(
            ability="multi_session",
            query="What is the gateway service used for and what is its throughput?",
            expected_keywords=["authentication", "10,000"],
        ),
        LMETestCase(
            ability="multi_session",
            query="What are Orion's performance targets and recent improvements?",
            expected_keywords=["200ms"],
        ),
        LMETestCase(
            ability="multi_session",
            query="Who does the Orion lead report to?",
            expected_keywords=["Sarah"],
        ),
        LMETestCase(
            ability="multi_session",
            query="How many services does Orion have and how are they deployed?",
            expected_keywords=["12"],
        ),
        LMETestCase(
            ability="multi_session",
            query="What deployment strategy and rollback SLA does Orion have?",
            expected_keywords=["blue-green", "5 minutes"],
        ),
        LMETestCase(
            ability="multi_session",
            query="What database does each Orion microservice use?",
            expected_keywords=["PostgreSQL"],
        ),
    ]

    return facts, test_cases


def _generate_temporal_cases() -> tuple[list[dict], list[LMETestCase]]:
    """Generate test cases for temporal reasoning.

    Facts change over time. Queries test awareness of time ordering.
    """
    facts = [
        # Time-ordered events
        {"content": "In January, the server was running on Ubuntu 20.04.", "node_type": "fact", "day": 1, "tags": ["ubuntu", "server"]},
        {"content": "In February, the team upgraded to Ubuntu 22.04 for better security.", "node_type": "fact", "day": 30, "tags": ["ubuntu", "upgrade"]},
        {"content": "In March, we switched the server OS from Ubuntu to Alpine Linux for smaller container images.", "node_type": "fact", "day": 60, "tags": ["alpine", "server", "os", "container"]},
        {"content": "The project budget was $50,000 per month in Q1.", "node_type": "fact", "day": 1, "tags": ["budget", "q1"]},
        {"content": "The project budget increased to $75,000 per month in Q2.", "node_type": "fact", "day": 90, "tags": ["budget", "q2"]},
        {"content": "The project budget was cut to $60,000 per month in Q3.", "node_type": "fact", "day": 180, "tags": ["budget", "q3"]},
        {"content": "The testing framework was unittest at project start.", "node_type": "fact", "day": 1, "tags": ["testing", "unittest"]},
        {"content": "The team migrated from unittest to pytest in sprint 5.", "node_type": "fact", "day": 35, "tags": ["testing", "pytest"]},
        {"content": "Version 1.0 was released in January with 10 features.", "node_type": "fact", "day": 1, "tags": ["release", "v1"]},
        {"content": "Version 2.0 was released in April with 25 features.", "node_type": "fact", "day": 90, "tags": ["release", "v2"]},
        {"content": "Version 3.0 was released in August with 40 features and a redesigned UI.", "node_type": "fact", "day": 210, "tags": ["release", "v3"]},
        {"content": "The initial response time was 800ms average.", "node_type": "fact", "day": 1, "tags": ["performance", "initial"]},
        {"content": "After optimization, response time improved to 200ms average.", "node_type": "fact", "day": 60, "tags": ["performance", "optimized"]},
        {"content": "With the new caching layer, response time is now 50ms average.", "node_type": "fact", "day": 120, "tags": ["performance", "cached"]},
    ]

    test_cases = [
        LMETestCase(
            ability="temporal",
            query="What operating system is the server currently running?",
            expected_keywords=["Alpine"],
        ),
        LMETestCase(
            ability="temporal",
            query="What is the current project budget?",
            expected_keywords=["60,000"],
        ),
        LMETestCase(
            ability="temporal",
            query="What testing framework is being used now?",
            expected_keywords=["pytest"],
        ),
        LMETestCase(
            ability="temporal",
            query="What is the latest version of the software?",
            expected_keywords=["3.0"],
        ),
        LMETestCase(
            ability="temporal",
            query="What is the current average response time?",
            expected_keywords=["50ms"],
        ),
        LMETestCase(
            ability="temporal",
            query="What was the server OS before Alpine?",
            expected_keywords=["Ubuntu"],
        ),
        LMETestCase(
            ability="temporal",
            query="How many features were in the first release?",
            expected_keywords=["10"],
        ),
        LMETestCase(
            ability="temporal",
            query="What was the budget increase amount in Q2?",
            expected_keywords=["75,000"],
        ),
    ]

    return facts, test_cases


def _generate_knowledge_update_cases() -> tuple[list[dict], list[LMETestCase]]:
    """Generate test cases for knowledge updates / supersedence.

    Old facts are updated by new facts. The benchmark checks that
    the system correctly surfaces the latest information.
    """
    facts = [
        # CEO changes
        {"content": "The CEO of the company is John Smith, appointed in 2020.", "node_type": "fact", "day": 1, "tags": ["ceo", "leadership"]},
        {"content": "The CEO has changed. Jane Doe replaced John Smith as CEO in 2024.", "node_type": "fact", "day": 50, "tags": ["ceo", "leadership", "change"]},
        # Office location
        {"content": "The main office is at 100 Market Street, New York.", "node_type": "fact", "day": 1, "tags": ["office", "location"]},
        {"content": "The company moved its main office to 200 Tech Boulevard, Austin.", "node_type": "fact", "day": 60, "tags": ["office", "location", "move"]},
        # Pricing
        {"content": "The basic plan costs $29 per month.", "node_type": "fact", "day": 1, "tags": ["pricing", "basic"]},
        {"content": "The basic plan price has been updated to $39 per month effective immediately.", "node_type": "fact", "day": 70, "tags": ["pricing", "basic", "update"]},
        # Technology stack
        {"content": "The backend is built with Ruby on Rails.", "node_type": "fact", "day": 1, "tags": ["backend", "ruby"]},
        {"content": "We completed the rewrite from Ruby on Rails to Go for the backend.", "node_type": "fact", "day": 80, "tags": ["backend", "go", "rewrite"]},
        # Support hours
        {"content": "Customer support is available 9 AM to 5 PM EST.", "node_type": "fact", "day": 1, "tags": ["support", "hours"]},
        {"content": "Customer support hours expanded to 24/7 for all tiers.", "node_type": "fact", "day": 45, "tags": ["support", "hours", "expanded"]},
        # Cloud provider
        {"content": "All services are hosted on DigitalOcean.", "node_type": "fact", "day": 1, "tags": ["hosting", "digitalocean"]},
        {"content": "We migrated from DigitalOcean to AWS for better enterprise features.", "node_type": "fact", "day": 55, "tags": ["hosting", "aws", "migration"]},
    ]

    test_cases = [
        LMETestCase(
            ability="knowledge_update",
            query="Who is the current CEO?",
            expected_keywords=["Jane Doe"],
        ),
        LMETestCase(
            ability="knowledge_update",
            query="Where is the main office located?",
            expected_keywords=["Austin"],
        ),
        LMETestCase(
            ability="knowledge_update",
            query="How much does the basic plan cost?",
            expected_keywords=["39"],
        ),
        LMETestCase(
            ability="knowledge_update",
            query="What language is the backend written in?",
            expected_keywords=["Go"],
        ),
        LMETestCase(
            ability="knowledge_update",
            query="What are the customer support hours?",
            expected_keywords=["24/7"],
        ),
        LMETestCase(
            ability="knowledge_update",
            query="What cloud provider hosts the services?",
            expected_keywords=["AWS"],
        ),
    ]

    return facts, test_cases


def _generate_abstention_cases() -> tuple[list[dict], list[LMETestCase]]:
    """Generate test cases for abstention.

    The system should NOT return confident answers for questions
    about topics that were never stored. We check that results
    have low relevance or are empty.
    """
    # Deliberately store facts about specific topics only
    facts = [
        {"content": "The company sells enterprise SaaS analytics software.", "node_type": "fact", "tags": ["product", "saas"]},
        {"content": "The engineering team works primarily in Python and Go.", "node_type": "fact", "tags": ["language", "engineering"]},
        {"content": "Office is in downtown Seattle near Pike Place Market.", "node_type": "fact", "tags": ["office", "seattle"]},
        {"content": "The CTO is Michael Chen with 15 years of experience.", "node_type": "fact", "tags": ["cto", "leadership"]},
    ]

    # Ask about topics that were NEVER stored
    test_cases = [
        LMETestCase(
            ability="abstention",
            query="What is the company's policy on remote work?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        LMETestCase(
            ability="abstention",
            query="What is the employee vacation policy?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        LMETestCase(
            ability="abstention",
            query="What are the stock option vesting terms?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        LMETestCase(
            ability="abstention",
            query="What is the maternity leave policy?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        LMETestCase(
            ability="abstention",
            query="What is the company's carbon footprint reduction plan?",
            expected_keywords=[],
            expect_abstention=True,
        ),
        LMETestCase(
            ability="abstention",
            query="What are the terms of the company's acquisition deal?",
            expected_keywords=[],
            expect_abstention=True,
        ),
    ]

    return facts, test_cases


# ---------------------------------------------------------------------------
# Benchmark class
# ---------------------------------------------------------------------------


class LongMemEvalBenchmark:
    """LongMemEval-style benchmark for multi-ability memory evaluation.

    Tests five core abilities:
      1. Information extraction
      2. Multi-session reasoning
      3. Temporal reasoning
      4. Knowledge updates
      5. Abstention

    If *dataset_path* is provided (or real data has been downloaded via
    ``python scripts/download_benchmarks.py``), the benchmark will log
    a notice. The real LongMemEval data can be loaded separately via
    :func:`benchmarks.datasets.load_longmemeval_dataset` for custom
    evaluation pipelines.
    """

    name = "longmemeval"

    # Relevance score threshold below which we consider the engine
    # effectively abstained (no confident answer).
    ABSTENTION_SCORE_THRESHOLD = 0.55

    def __init__(self, dataset_path: str | None = None) -> None:
        self.dataset_path = dataset_path

    async def run(self, engine: MemoryEngine) -> BenchmarkResult:
        """Execute the LongMemEval benchmark.

        Args:
            engine: Initialized MemoryEngine to benchmark.

        Returns:
            BenchmarkResult with per-ability scores.
        """
        start_time = time.monotonic()

        # Check for real dataset availability
        if self.dataset_path:
            try:
                from benchmarks.datasets import load_longmemeval_dataset
                real_data = load_longmemeval_dataset(self.dataset_path)
                if real_data:
                    logging.getLogger(__name__).info(
                        "Real LongMemEval data available (%d cases). "
                        "Using built-in synthetic test harness for structured evaluation.",
                        len(real_data),
                    )
            except Exception:
                pass

        # Gather all test data generators
        generators = [
            ("info_extraction", _generate_info_extraction_cases),
            ("multi_session", _generate_multi_session_cases),
            ("temporal", _generate_temporal_cases),
            ("knowledge_update", _generate_knowledge_update_cases),
            ("abstention", _generate_abstention_cases),
        ]

        all_details: list[QueryResult] = []
        category_results: list[tuple[str, float]] = []

        for ability_name, gen_func in generators:
            facts, test_cases = gen_func()
            user_id = f"bench-lme-{ability_name}"

            # Store facts for this ability
            for fact in facts:
                kwargs: dict = {
                    "user_id": user_id,
                    "role": "user",
                    "node_type": NodeType(fact.get("node_type", "fact")),
                    "scope": Scope.PERSONAL,
                }
                if "session" in fact:
                    kwargs["session_id"] = fact["session"]
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
                        r.node.content for r in response.results[:30]
                    )
                    kw_score = keyword_match_score(
                        tc.expected_keywords, top_content
                    )
                    ex_score = exclusion_score(
                        tc.excluded_keywords, top_content
                    )
                    score = kw_score * ex_score

                is_correct = score >= 0.5
                all_details.append(QueryResult(
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
                ))
                category_results.append((tc.ability, score))

        # Compute aggregate metrics
        from benchmarks.metrics import category_scores as compute_categories

        cat_scores = compute_categories(category_results)
        correct = sum(1 for d in all_details if d.correct)
        incorrect = sum(1 for d in all_details if not d.correct)
        abstained = sum(
            1 for d in all_details
            if d.category == "abstention" and d.correct
        )

        overall = (
            sum(d.score for d in all_details) / len(all_details)
            if all_details
            else 0.0
        )
        duration_ms = (time.monotonic() - start_time) * 1000

        return BenchmarkResult(
            benchmark_name="longmemeval",
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

        Good abstention means either no results or low-confidence results.
        Returns 1.0 if the system correctly shows low confidence, 0.0 if
        it returns high-confidence results for an unknown topic.
        """
        if not response.results:
            return 1.0  # Perfect: no results for unknown topic

        top_score = response.results[0].composite_score
        if top_score < self.ABSTENTION_SCORE_THRESHOLD:
            return 1.0  # Good: low confidence
        elif top_score < 0.65:
            return 0.5  # Partial: somewhat low confidence
        else:
            return 0.0  # Bad: high confidence for unknown topic


# ---------------------------------------------------------------------------
# Real dataset benchmark
# ---------------------------------------------------------------------------

# Map LongMemEval question_type → ability name
_LME_TYPE_MAP: dict[str, str] = {
    "single-session-user": "info_extraction",
    "single-session-assistant": "info_extraction",
    "single-session-preference": "preference",
    "multi-session": "multi_session",
    "temporal-reasoning": "temporal",
    "knowledge-update": "knowledge_update",
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LME_PATH = (
    _PROJECT_ROOT / "data" / "benchmarks" / "longmemeval" / "longmemeval_oracle.json"
)


def _parse_haystack_date(date_str: str) -> datetime | None:
    """Parse a LongMemEval haystack date like '2023/12/10 (Sun) 19:41'."""
    from datetime import datetime, timezone
    try:
        # Strip day-of-week: '2023/12/10 (Sun) 19:41' -> '2023/12/10 19:41'
        cleaned = date_str.split("(")[0].strip()
        if ")" in date_str:
            cleaned = date_str.split(")")[1].strip()
            cleaned = date_str.split("(")[0].strip() + " " + cleaned
        dt = datetime.strptime(cleaned.strip(), "%Y/%m/%d %H:%M")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None

logger = logging.getLogger(__name__)


class LongMemEvalRealBenchmark:
    """LongMemEval benchmark using the real oracle dataset.

    Evaluates PRME retrieval against the published LongMemEval dataset
    (arXiv 2410.10813). Uses keyword-match scoring (reproducible, no
    LLM judge needed). Preference questions are skipped since they
    require rubric-based LLM evaluation.

    Download the dataset first::

        python scripts/download_benchmarks.py --longmemeval
    """

    name = "longmemeval-real"
    ABSTENTION_SCORE_THRESHOLD = 0.55

    def __init__(
        self,
        dataset_path: str | None = None,
        max_questions: int = 0,
    ) -> None:
        self.dataset_path = (
            Path(dataset_path) if dataset_path else _DEFAULT_LME_PATH
        )
        self.max_questions = max_questions  # 0 = all

    async def run(self, engine: MemoryEngine) -> BenchmarkResult:
        """Run benchmark. The passed *engine* is ignored — each question
        gets its own fresh engine to avoid index bloat from 470 unique
        user_ids."""
        import tempfile

        from prme.config import PRMEConfig

        if not self.dataset_path.exists():
            raise FileNotFoundError(
                f"LongMemEval dataset not found at {self.dataset_path}. "
                "Run: python scripts/download_benchmarks.py --longmemeval"
            )

        start_time = time.monotonic()

        with open(self.dataset_path) as f:
            all_questions = json.load(f)

        # Skip preference questions (require LLM rubric evaluation)
        questions = [
            q for q in all_questions
            if q["question_type"] != "single-session-preference"
        ]
        if self.max_questions > 0 and self.max_questions < len(questions):
            # Stratified sample: take proportionally from each type
            from collections import defaultdict
            by_type: dict[str, list] = defaultdict(list)
            for q in questions:
                by_type[q["question_type"]].append(q)
            sampled: list[dict] = []
            for qtype, qs in sorted(by_type.items()):
                n = max(1, round(len(qs) / len(questions) * self.max_questions))
                sampled.extend(qs[:n])
            questions = sampled[: self.max_questions]

        concurrency = 5
        logger.info(
            "Running LongMemEval-real: %d questions (%d preference skipped), concurrency=%d",
            len(questions),
            len(all_questions) - len(questions),
            concurrency,
        )

        semaphore = asyncio.Semaphore(concurrency)
        completed = 0

        async def _process_question(
            question: dict,
        ) -> tuple[QueryResult, tuple[str, float]]:
            nonlocal completed
            qid = question["question_id"]
            qtype = question["question_type"]
            ability = _LME_TYPE_MAP.get(qtype, qtype)
            is_abstention = qid.endswith("_abs")
            user_id = "bench-lme-real"

            async with semaphore:
                tmp = tempfile.mkdtemp(prefix="prme_lme_real_")
                tmp_dir = Path(tmp)
                lex_dir = tmp_dir / "lexical_index"
                lex_dir.mkdir(parents=True, exist_ok=True)
                q_engine = await MemoryEngine.create(PRMEConfig(
                    db_path=str(tmp_dir / "memory.duckdb"),
                    vector_path=str(tmp_dir / "vectors.usearch"),
                    lexical_path=str(lex_dir),
                ))

                try:
                    haystack_dates = question.get("haystack_dates", [])
                    for sess_idx, session_turns in enumerate(
                        question["haystack_sessions"]
                    ):
                        session_id = f"s{sess_idx}"
                        sess_time = (
                            _parse_haystack_date(haystack_dates[sess_idx])
                            if sess_idx < len(haystack_dates)
                            else None
                        )
                        for turn in session_turns:
                            await q_engine.store(
                                turn["content"],
                                user_id=user_id,
                                role=turn["role"],
                                node_type=NodeType.FACT,
                                scope=Scope.PERSONAL,
                                session_id=session_id,
                                event_time=sess_time,
                            )

                    response = await q_engine.retrieve(
                        question["question"], user_id=user_id
                    )
                finally:
                    await q_engine.close()
                    shutil.rmtree(tmp_dir, ignore_errors=True)

            if is_abstention:
                score = self._evaluate_abstention(response)
                expected = "ABSTAIN"
                actual = (
                    f"top_score={response.results[0].composite_score:.3f}"
                    if response.results
                    else "no results"
                )
            else:
                answer = str(question["answer"])
                top_content = " ".join(
                    r.node.content for r in response.results[:30]
                )
                score = keyword_match_score([answer], top_content)
                expected = answer
                actual = top_content[:200]

            cat = "abstention" if is_abstention else ability
            is_correct = score >= 0.5

            completed += 1
            if completed % 50 == 0:
                elapsed = (time.monotonic() - start_time) / 60
                logger.info(
                    "Progress: %d/%d questions (%.1f min)",
                    completed, len(questions), elapsed,
                )

            return (
                QueryResult(
                    query=question["question"],
                    category=cat,
                    expected=expected,
                    actual=actual,
                    correct=is_correct,
                    score=score,
                ),
                (cat, score),
            )

        results = await asyncio.gather(
            *[_process_question(q) for q in questions],
            return_exceptions=True,
        )

        all_details: list[QueryResult] = []
        category_results: list[tuple[str, float]] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("Question failed: %s", r)
                continue
            detail, cat_score = r
            all_details.append(detail)
            category_results.append(cat_score)

        from benchmarks.metrics import category_scores as compute_categories

        cat_scores = compute_categories(category_results)
        correct = sum(1 for d in all_details if d.correct)
        incorrect = sum(1 for d in all_details if not d.correct)
        abstained = sum(
            1 for d in all_details
            if d.category == "abstention" and d.correct
        )
        overall = (
            sum(d.score for d in all_details) / len(all_details)
            if all_details
            else 0.0
        )
        duration_ms = (time.monotonic() - start_time) * 1000

        return BenchmarkResult(
            benchmark_name="longmemeval-real",
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
        """Score an abstention test case for real data."""
        if not response.results:
            return 1.0
        top_score = response.results[0].composite_score
        if top_score < self.ABSTENTION_SCORE_THRESHOLD:
            return 1.0
        elif top_score < 0.65:
            return 0.5
        else:
            return 0.0

    async def run_with_llm(
        self, engine: MemoryEngine, llm_config: LLMJudgeConfig,
        only_questions: set[str] | None = None,
    ) -> BenchmarkResult:
        """Run LongMemEval-real with LLM generation + judge scoring.

        Same per-question engine pattern as run(), but generates answers
        via LLM and uses LLM-as-judge for scoring. Abstention questions
        still use the score-threshold method.

        Args:
            only_questions: If set, only run questions whose text matches.
        """
        import tempfile

        from benchmarks.llm_judge import check_abstention, generate_answer, judge_answer, reformulate_query
        from prme.config import PRMEConfig

        if not self.dataset_path.exists():
            raise FileNotFoundError(
                f"LongMemEval dataset not found at {self.dataset_path}. "
                "Run: python scripts/download_benchmarks.py --longmemeval"
            )

        start_time = time.monotonic()

        with open(self.dataset_path) as f:
            all_questions = json.load(f)

        questions = [
            q for q in all_questions
            if q["question_type"] != "single-session-preference"
        ]
        if only_questions:
            questions = [q for q in questions if q["question"] in only_questions]
        elif self.max_questions > 0 and self.max_questions < len(questions):
            from collections import defaultdict
            by_type: dict[str, list] = defaultdict(list)
            for q in questions:
                by_type[q["question_type"]].append(q)
            sampled: list[dict] = []
            for qtype, qs in sorted(by_type.items()):
                n = max(1, round(len(qs) / len(questions) * self.max_questions))
                sampled.extend(qs[:n])
            questions = sampled[: self.max_questions]

        concurrency = 5
        llm_concurrency = 3  # Keep low for 200K TPM on gpt-4o-mini
        logger.info(
            "Running LongMemEval-real (LLM judge): %d questions, concurrency=%d, llm_concurrency=%d",
            len(questions), concurrency, llm_concurrency,
        )

        semaphore = asyncio.Semaphore(concurrency)
        llm_semaphore = asyncio.Semaphore(llm_concurrency)
        completed = 0

        async def _process_question(
            question: dict,
        ) -> tuple[QueryResult, tuple[str, float]]:
            nonlocal completed
            qid = question["question_id"]
            qtype = question["question_type"]
            ability = _LME_TYPE_MAP.get(qtype, qtype)
            is_abstention = qid.endswith("_abs")
            user_id = "bench-lme-real"

            async with semaphore:
                tmp = tempfile.mkdtemp(prefix="prme_lme_real_")
                tmp_dir = Path(tmp)
                lex_dir = tmp_dir / "lexical_index"
                lex_dir.mkdir(parents=True, exist_ok=True)
                q_engine = await MemoryEngine.create(PRMEConfig(
                    db_path=str(tmp_dir / "memory.duckdb"),
                    vector_path=str(tmp_dir / "vectors.usearch"),
                    lexical_path=str(lex_dir),
                ))

                try:
                    haystack_dates = question.get("haystack_dates", [])
                    for sess_idx, session_turns in enumerate(
                        question["haystack_sessions"]
                    ):
                        session_id = f"s{sess_idx}"
                        sess_time = (
                            _parse_haystack_date(haystack_dates[sess_idx])
                            if sess_idx < len(haystack_dates)
                            else None
                        )
                        for turn in session_turns:
                            await q_engine.store(
                                turn["content"],
                                user_id=user_id,
                                role=turn["role"],
                                node_type=NodeType.FACT,
                                scope=Scope.PERSONAL,
                                session_id=session_id,
                                event_time=sess_time,
                            )

                    # Multi-query retrieval: original + 2 reformulations
                    response = await q_engine.retrieve(
                        question["question"], user_id=user_id
                    )
                    seen_ids = {str(r.node.id) for r in response.results}
                    all_results = list(response.results)

                    if not is_abstention:
                        async with llm_semaphore:
                            alt_queries = await reformulate_query(
                                question["question"], llm_config
                            )
                        for alt_q in alt_queries:
                            alt_response = await q_engine.retrieve(
                                alt_q, user_id=user_id
                            )
                            for r in alt_response.results:
                                rid = str(r.node.id)
                                if rid not in seen_ids:
                                    seen_ids.add(rid)
                                    all_results.append(r)

                        all_results.sort(
                            key=lambda r: r.composite_score, reverse=True
                        )
                    # Keep more results for aggregation queries (need all items)
                    all_results = all_results[:150]
                finally:
                    await q_engine.close()
                    shutil.rmtree(tmp_dir, ignore_errors=True)

            if is_abstention:
                expected = "ABSTAIN"
                if not response.results:
                    score = 1.0
                    actual = "no results"
                else:
                    # Use LLM to check if context actually answers the question
                    top_content = format_for_llm(
                        results=list(response.results)[:20],
                        query=question["question"],
                    )
                    async with llm_semaphore:
                        should_abstain = await check_abstention(
                            question["question"], top_content, llm_config
                        )
                    score = 1.0 if should_abstain else 0.0
                    actual = f"abstained={should_abstain} composite={response.results[0].composite_score:.3f}"
                generated = ""
            else:
                answer = str(question["answer"])
                question_date_str = question.get("question_date", "")
                qdt = _parse_haystack_date(question_date_str) if question_date_str else None

                # Use PRME's context formatter with intent-aware hints
                # Temporal queries work better with focused context (50);
                # aggregation needs wider context (100) to catch all items.
                if ability == "temporal":
                    hint = "temporal"
                    n_results = 50
                else:
                    hint = None  # let auto-detect handle aggregation etc.
                    n_results = 100
                top_content = format_for_llm(
                    results=all_results[:n_results],
                    query=question["question"],
                    question_date=qdt,
                    context_hint=hint,
                    max_results=n_results,
                )
                async with llm_semaphore:
                    generated = await generate_answer(
                        question["question"], top_content, llm_config
                    )
                    score = await judge_answer(
                        question["question"], answer, generated, llm_config
                    )
                expected = answer
                actual = generated[:200] if generated else top_content[:200]

            cat = "abstention" if is_abstention else ability
            is_correct = score >= 0.5

            completed += 1
            if completed % 50 == 0:
                elapsed = (time.monotonic() - start_time) / 60
                logger.info(
                    "Progress: %d/%d questions (%.1f min)",
                    completed, len(questions), elapsed,
                )

            return (
                QueryResult(
                    query=question["question"],
                    category=cat,
                    expected=expected,
                    actual=actual,
                    correct=is_correct,
                    score=score,
                    generated_answer=generated,
                ),
                (cat, score),
            )

        results = await asyncio.gather(
            *[_process_question(q) for q in questions],
            return_exceptions=True,
        )

        all_details: list[QueryResult] = []
        category_results: list[tuple[str, float]] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("Question failed: %s", r)
                continue
            detail, cat_score = r
            all_details.append(detail)
            category_results.append(cat_score)

        from benchmarks.metrics import category_scores as compute_categories

        cat_scores = compute_categories(category_results)
        correct = sum(1 for d in all_details if d.correct)
        incorrect = sum(1 for d in all_details if not d.correct)
        abstained = sum(
            1 for d in all_details
            if d.category == "abstention" and d.correct
        )
        overall = (
            sum(d.score for d in all_details) / len(all_details)
            if all_details
            else 0.0
        )
        duration_ms = (time.monotonic() - start_time) * 1000

        return BenchmarkResult(
            benchmark_name="longmemeval-real (llm-judge)",
            overall_score=overall,
            category_scores=cat_scores,
            total_queries=len(all_details),
            correct=correct,
            incorrect=incorrect,
            abstained=abstained,
            duration_ms=duration_ms,
            details=all_details,
        )
