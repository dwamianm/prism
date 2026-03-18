"""LoCoMo-style benchmark for long conversation QA.

Generates synthetic 300+ turn conversations covering project evolution,
team changes, and tech stack updates. Tests question answering accuracy,
event summarization, and temporal reasoning over extended dialogues.

Includes both synthetic (no external deps) and real dataset adapters.
The real LoCoMo-10 dataset can be downloaded via:
    python scripts/download_benchmarks.py --locomo
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.metrics import exclusion_score, keyword_match_score
from benchmarks.models import BenchmarkResult, QueryResult

if TYPE_CHECKING:
    from benchmarks.llm_judge import LLMJudgeConfig
    from prme.storage.engine import MemoryEngine

from prme.types import EpistemicType, NodeType, Scope

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthetic conversation data generators
# ---------------------------------------------------------------------------


@dataclass
class ConversationTurn:
    """A single turn in a synthetic conversation."""

    turn_number: int
    day: int
    role: str
    content: str
    node_type: str = "fact"
    epistemic_type: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class GroundTruthQuery:
    """A query with expected ground truth for evaluation."""

    query: str
    category: str  # qa, summarization, temporal
    expected_keywords: list[str]
    excluded_keywords: list[str] = field(default_factory=list)
    day: int = 0  # day at which to evaluate


def generate_conversation(turns: int = 300) -> tuple[
    list[ConversationTurn], list[GroundTruthQuery]
]:
    """Generate a synthetic long conversation with ground truth.

    Themes: project evolution, team changes, tech stack updates,
    decision making, and preference tracking.

    Args:
        turns: Target number of conversation turns (minimum 300).

    Returns:
        Tuple of (conversation_turns, ground_truth_queries).
    """
    conversation: list[ConversationTurn] = []
    queries: list[GroundTruthQuery] = []
    turn_num = 0

    # Phase 1 (days 1-15): Establish baseline project
    baseline_facts = [
        ("Our main project is called Neptune and it's a data analytics platform.", "fact", ["neptune", "analytics"], "observed"),
        ("The team uses Python 3.11 as the primary language.", "fact", ["python", "language"], None),
        ("Alice is the tech lead, Bob handles backend, Carol does frontend.", "fact", ["alice", "bob", "carol", "team"], "observed"),
        ("We deploy on AWS using Docker containers.", "fact", ["aws", "docker", "deploy"], None),
        ("The database is PostgreSQL 15 with read replicas.", "fact", ["postgresql", "database"], None),
        ("Our CI/CD pipeline uses GitHub Actions.", "fact", ["github-actions", "ci-cd"], None),
        ("We follow a two-week sprint cycle with retrospectives.", "fact", ["sprint", "agile"], None),
        ("The frontend is built with React 18 and TypeScript.", "fact", ["react", "typescript", "frontend"], None),
        ("We use Redis for caching and session management.", "fact", ["redis", "caching"], None),
        ("Code reviews require two approvals before merging.", "decision", ["code-review", "process"], "observed"),
        ("I prefer dark mode in all my development tools.", "preference", ["dark-mode", "preference"], None),
        ("Our API documentation lives in Swagger/OpenAPI.", "fact", ["swagger", "api-docs"], None),
        ("Testing coverage target is 80% for all new code.", "decision", ["testing", "coverage"], None),
        ("We use Slack for team communication.", "fact", ["slack", "communication"], None),
        ("The project started 6 months ago and has 50k users.", "fact", ["neptune", "users"], "observed"),
    ]

    for i, (content, ntype, tags, et) in enumerate(baseline_facts):
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=1 + (i // 3), role="user",
            content=content, node_type=ntype,
            epistemic_type=et, tags=tags,
        ))
        turn_num += 1

    # Interleave assistant responses
    assistant_responses_1 = [
        "Got it, Neptune sounds like an interesting analytics platform. I'll keep track of the team structure.",
        "Python 3.11 with PostgreSQL and React is a solid modern stack.",
        "Two-week sprints with code review gates is a good practice.",
        "Docker on AWS with GitHub Actions CI/CD gives you good automation.",
        "80% coverage target is reasonable for a growing codebase.",
    ]
    for resp in assistant_responses_1:
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=5, role="assistant",
            content=resp, node_type="note", tags=["response"],
        ))
        turn_num += 1

    # Phase 2 (days 16-40): Daily standup conversations
    standup_topics = [
        ("Working on the data ingestion pipeline for Neptune. Using Apache Kafka for streaming.", "fact", ["kafka", "ingestion", "streaming"], 16),
        ("Found a performance bottleneck in the query optimizer. CPU usage spikes to 90%.", "fact", ["performance", "query-optimizer", "bottleneck"], 17),
        ("Fixed the query optimizer issue by adding query plan caching.", "fact", ["query-optimizer", "caching", "fix"], 18),
        ("Diana joined the team as a QA engineer. She has 8 years of experience.", "fact", ["diana", "qa", "team"], 19),
        ("Starting to evaluate moving from REST to GraphQL for the API layer.", "fact", ["graphql", "api", "evaluation"], 20),
        ("The monitoring stack is now Prometheus + Grafana.", "fact", ["prometheus", "grafana", "monitoring"], 21),
        ("Sprint 12 retrospective: need to improve documentation.", "fact", ["sprint-12", "retro", "documentation"], 22),
        ("Deployed feature flags using LaunchDarkly.", "fact", ["launchdarkly", "feature-flags"], 23),
        ("Bob is working on the real-time notification system using WebSockets.", "fact", ["bob", "websockets", "notifications"], 24),
        ("Our error tracking is handled by Sentry.", "fact", ["sentry", "error-tracking"], 25),
        ("Starting sprint 13 with focus on performance improvements.", "fact", ["sprint-13", "performance"], 26),
        ("Carol redesigned the dashboard with new data visualization components.", "fact", ["carol", "dashboard", "visualization"], 27),
        ("Implemented rate limiting on the API. Max 1000 requests per minute per user.", "decision", ["rate-limiting", "api"], 28),
        ("Added pagination to all list endpoints. Default page size is 25.", "fact", ["pagination", "api"], 29),
        ("Completed the Kafka integration for real-time data streaming.", "fact", ["kafka", "streaming", "complete"], 30),
        ("Security audit revealed we need to add input validation on all endpoints.", "fact", ["security", "validation", "audit"], 31),
        ("Decided to use JWT tokens for API authentication.", "decision", ["jwt", "authentication"], 32),
        ("Neptune reached 75,000 users this week.", "fact", ["neptune", "users", "growth"], 33),
        ("Planning to add multi-tenant support in Q2.", "fact", ["multi-tenant", "planning"], 34),
        ("Alice proposed switching from REST to GraphQL. Team agreed.", "decision", ["graphql", "api", "decision"], 35),
        ("Started implementing GraphQL schema for Neptune.", "fact", ["graphql", "implementation"], 36),
        ("Bob completed the WebSocket notification system.", "fact", ["bob", "websockets", "complete"], 37),
        ("New hire: Eve joins as a DevOps engineer next week.", "fact", ["eve", "devops", "team"], 38),
        ("Sprint 13 velocity was 42 story points, up from 38.", "fact", ["sprint-13", "velocity"], 39),
        ("Completed migration from Prometheus to Datadog. Datadog is now our monitoring tool.", "fact", ["datadog", "monitoring", "monitoring-tool", "migration"], 40),
    ]

    for content, ntype, tags, day in standup_topics:
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=day, role="user",
            content=content, node_type=ntype, tags=tags,
        ))
        turn_num += 1
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=day, role="assistant",
            content=f"Noted. I've tracked this update about {tags[0]}.",
            node_type="note", tags=["ack"],
        ))
        turn_num += 1

    # Phase 3 (days 41-70): Major changes and migrations
    major_changes = [
        ("We completed the migration from REST to GraphQL. The REST API is now deprecated.", "fact", ["graphql", "rest", "migration", "deprecated"], 45),
        ("Alice is transitioning from tech lead to architect role.", "fact", ["alice", "architect", "role-change"], 46),
        ("Bob is promoted to senior backend engineer.", "fact", ["bob", "promotion", "senior"], 47),
        ("We switched from Docker Compose to Kubernetes for orchestration.", "fact", ["kubernetes", "docker", "orchestration"], 48),
        ("The frontend is being rewritten from React to SvelteKit for better performance.", "fact", ["sveltekit", "react", "migration", "frontend"], 50),
        ("Eve set up the new Kubernetes cluster on EKS.", "fact", ["eve", "kubernetes", "eks"], 51),
        ("Neptune reached 100,000 users. Planning a celebration.", "fact", ["neptune", "users", "milestone"], 52),
        ("Decided to adopt trunk-based development instead of GitFlow.", "decision", ["trunk-based", "gitflow", "branching"], 53),
        ("Frank joined as a data scientist for the ML features.", "fact", ["frank", "data-scientist", "ml", "team"], 55),
        ("Starting to build ML-powered anomaly detection for Neptune.", "fact", ["ml", "anomaly-detection", "neptune"], 56),
        ("Replaced Sentry with Datadog APM for unified observability.", "fact", ["datadog", "sentry", "observability", "migration"], 58),
        ("Sprint velocity stabilized at 45 story points.", "fact", ["velocity", "sprint"], 60),
        ("Database migration: added TimescaleDB extension for time-series data.", "fact", ["timescaledb", "postgresql", "time-series"], 62),
        ("I switched my editor from VS Code to Neovim.", "preference", ["neovim", "vscode", "editor"], 63),
        ("Implemented RBAC (Role-Based Access Control) for multi-tenant support.", "fact", ["rbac", "multi-tenant", "security"], 65),
        ("Carol left the team. Grace is the new frontend lead.", "fact", ["carol", "grace", "frontend", "team-change"], 67),
        ("GraphQL subscriptions added for real-time data updates.", "fact", ["graphql", "subscriptions", "real-time"], 68),
        ("The deployment pipeline now includes canary releases.", "fact", ["canary", "deployment", "pipeline"], 70),
    ]

    for content, ntype, tags, day in major_changes:
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=day, role="user",
            content=content, node_type=ntype, tags=tags,
        ))
        turn_num += 1
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=day, role="assistant",
            content=f"Important update recorded regarding {tags[0]}.",
            node_type="note", tags=["ack"],
        ))
        turn_num += 1

    # Phase 4 (days 71-100): Continued evolution
    evolution_facts = [
        ("Neptune's ML anomaly detection is in beta with 10 early adopters.", "fact", ["ml", "anomaly", "beta"], 72),
        ("We adopted Terraform for infrastructure as code.", "fact", ["terraform", "iac", "infrastructure"], 73),
        ("Frank built a recommendation engine using collaborative filtering.", "fact", ["frank", "recommendation", "ml"], 75),
        ("Switched project management from Jira to Linear. Linear is now our project management tool.", "fact", ["linear", "jira", "project-management", "project-management-tool", "migration"], 77),
        ("Alice presented Neptune at a tech conference. Great reception.", "fact", ["alice", "conference", "neptune"], 78),
        ("Security: implemented SOC2 compliance measures.", "fact", ["soc2", "security", "compliance"], 80),
        ("Bob is now leading the API team with 3 reports.", "fact", ["bob", "team-lead", "api-team"], 82),
        ("Switched back to VS Code from Neovim. The extensions are too useful.", "preference", ["vscode", "neovim", "editor"], 83),
        ("Neptune reached 150,000 users. Revenue growing 20% month over month.", "fact", ["neptune", "users", "revenue", "growth"], 85),
        ("Grace introduced Storybook for component development.", "fact", ["grace", "storybook", "components"], 86),
        ("We added end-to-end testing with Playwright.", "fact", ["playwright", "e2e-testing"], 88),
        ("Frank's ML model achieved 95% accuracy on anomaly detection.", "fact", ["frank", "ml", "accuracy"], 90),
        ("Planning to open-source the Neptune core engine.", "fact", ["open-source", "neptune", "planning"], 92),
        ("Diana automated the QA regression suite. Tests run in 15 minutes.", "fact", ["diana", "automation", "qa", "regression"], 94),
        ("Moved from AWS to a multi-cloud setup with GCP for ML workloads.", "fact", ["gcp", "aws", "multi-cloud", "migration"], 95),
        ("Eve containerized all microservices with Helm charts.", "fact", ["eve", "helm", "microservices"], 97),
        ("Sprint velocity reached 50 points. Team is firing on all cylinders.", "fact", ["velocity", "sprint", "improvement"], 99),
        ("Neptune won 'Best Analytics Tool' at the industry awards.", "fact", ["neptune", "award", "analytics"], 100),
    ]

    for content, ntype, tags, day in evolution_facts:
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=day, role="user",
            content=content, node_type=ntype, tags=tags,
        ))
        turn_num += 1
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=day, role="assistant",
            content=f"Update recorded for {tags[0]}.",
            node_type="note", tags=["ack"],
        ))
        turn_num += 1

    # Pad to reach target turn count with recap/discussion turns
    day = 100
    while turn_num < turns:
        day += 1
        idx = turn_num % len(_PADDING_TOPICS)
        topic = _PADDING_TOPICS[idx]
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=day, role="user",
            content=topic[0], node_type="fact", tags=topic[1],
        ))
        turn_num += 1
        conversation.append(ConversationTurn(
            turn_number=turn_num, day=day, role="assistant",
            content=f"Understood, noted the update about {topic[1][0]}.",
            node_type="note", tags=["ack"],
        ))
        turn_num += 1

    # Ground truth queries
    queries = [
        # QA category
        GroundTruthQuery(
            query="What is the main project called?",
            category="qa",
            expected_keywords=["Neptune"],
            day=110,
        ),
        GroundTruthQuery(
            query="Who is the tech lead?",
            category="qa",
            expected_keywords=["Alice"],
            day=50,
        ),
        GroundTruthQuery(
            query="What database does the project use?",
            category="qa",
            expected_keywords=["PostgreSQL"],
            day=110,
        ),
        GroundTruthQuery(
            query="What programming language does the team use?",
            category="qa",
            expected_keywords=["Python"],
            day=110,
        ),
        GroundTruthQuery(
            query="Who handles QA on the team?",
            category="qa",
            expected_keywords=["Diana"],
            day=110,
        ),
        GroundTruthQuery(
            query="What is used for caching?",
            category="qa",
            expected_keywords=["Redis"],
            day=110,
        ),
        GroundTruthQuery(
            query="What CI/CD tool does the team use?",
            category="qa",
            expected_keywords=["GitHub Actions"],
            day=110,
        ),
        GroundTruthQuery(
            query="Who is the data scientist on the team?",
            category="qa",
            expected_keywords=["Frank"],
            day=110,
        ),
        GroundTruthQuery(
            query="What authentication mechanism is used?",
            category="qa",
            expected_keywords=["JWT"],
            day=110,
        ),
        GroundTruthQuery(
            query="What is the API rate limit?",
            category="qa",
            expected_keywords=["1000"],
            day=110,
        ),
        # Summarization category
        GroundTruthQuery(
            query="Summarize the team changes that happened",
            category="summarization",
            expected_keywords=["Diana", "Eve", "Frank"],
            day=110,
        ),
        GroundTruthQuery(
            query="What technology migrations occurred?",
            category="summarization",
            expected_keywords=["GraphQL"],
            day=110,
        ),
        GroundTruthQuery(
            query="What were the key milestones for Neptune?",
            category="summarization",
            expected_keywords=["100,000"],
            day=110,
        ),
        # Temporal reasoning category
        GroundTruthQuery(
            query="What API technology does the project currently use?",
            category="temporal",
            expected_keywords=["GraphQL"],
            excluded_keywords=[],
            day=110,
        ),
        GroundTruthQuery(
            query="What frontend framework is being used now?",
            category="temporal",
            expected_keywords=["SvelteKit"],
            day=110,
        ),
        GroundTruthQuery(
            query="What editor do I currently use?",
            category="temporal",
            expected_keywords=["VS Code"],
            day=110,
        ),
        GroundTruthQuery(
            query="Who is the current frontend lead?",
            category="temporal",
            expected_keywords=["Grace"],
            day=110,
        ),
        GroundTruthQuery(
            query="What monitoring tool is currently used?",
            category="temporal",
            expected_keywords=["Datadog"],
            day=110,
        ),
        GroundTruthQuery(
            query="What container orchestration does the team use?",
            category="temporal",
            expected_keywords=["Kubernetes"],
            day=110,
        ),
        GroundTruthQuery(
            query="What project management tool is currently used?",
            category="temporal",
            expected_keywords=["Linear"],
            day=110,
        ),
    ]

    return conversation, queries


# Padding topics for reaching turn count targets
_PADDING_TOPICS = [
    ("Working on improving query performance for large datasets.", ["performance", "query"]),
    ("Updated the API documentation with the new GraphQL schemas.", ["documentation", "graphql"]),
    ("Running load tests to validate the Kubernetes autoscaling.", ["load-testing", "kubernetes"]),
    ("Code review session found some potential memory leaks.", ["code-review", "memory"]),
    ("Discussing architecture for the new reporting module.", ["architecture", "reporting"]),
    ("Optimized database indexes for common query patterns.", ["database", "optimization"]),
    ("Set up automated backup rotation for production databases.", ["backup", "database"]),
    ("Reviewing pull requests for the authentication module.", ["pull-request", "auth"]),
    ("Team standup: all features on track for sprint deadline.", ["standup", "sprint"]),
    ("Analyzing user behavior data for feature prioritization.", ["analytics", "prioritization"]),
    ("Testing the new caching layer under high concurrency.", ["caching", "concurrency"]),
    ("Writing integration tests for the notification service.", ["testing", "notifications"]),
    ("Deploying the updated search functionality to staging.", ["deployment", "search"]),
    ("Refactoring the data pipeline for better maintainability.", ["refactoring", "pipeline"]),
    ("Monitoring dashboard shows stable performance metrics.", ["monitoring", "performance"]),
]


# ---------------------------------------------------------------------------
# Benchmark class
# ---------------------------------------------------------------------------


class LoCoMoBenchmark:
    """LoCoMo-style benchmark for long conversation QA evaluation.

    Generates synthetic 300+ turn conversations and evaluates PRME's
    ability to answer questions, summarize events, and perform
    temporal reasoning over extended dialogues.

    If *dataset_path* is provided (or real data has been downloaded via
    ``python scripts/download_benchmarks.py``), the benchmark will use
    the real LoCoMo dataset instead of synthetic data.
    """

    name = "locomo"

    def __init__(self, turns: int = 300, dataset_path: str | None = None) -> None:
        self.turns = turns
        self.dataset_path = dataset_path

    async def run(self, engine: MemoryEngine) -> BenchmarkResult:
        """Execute the LoCoMo benchmark against the engine.

        Args:
            engine: Initialized MemoryEngine to benchmark.

        Returns:
            BenchmarkResult with QA accuracy, summarization, and
            temporal reasoning scores.
        """
        start_time = time.monotonic()

        # Try loading real dataset; fall back to synthetic generation
        conversation: list[ConversationTurn] | None = None
        queries: list[GroundTruthQuery] | None = None
        try:
            from benchmarks.datasets import load_locomo_dataset
            data = load_locomo_dataset(self.dataset_path)
            if data and "turns" in data[0] and not data[0].get("conversation_id", "").startswith("synthetic"):
                # Real dataset available -- convert to internal format
                conv_data = data[0]  # use first conversation
                conversation = [
                    ConversationTurn(
                        turn_number=i,
                        day=t.get("day", 1 + i // 5),
                        role=t["role"],
                        content=t["content"],
                    )
                    for i, t in enumerate(conv_data["turns"])
                ]
                queries = [
                    GroundTruthQuery(
                        query=q["question"],
                        category=q.get("category", "qa"),
                        expected_keywords=[q["answer"]],
                        day=110,
                    )
                    for q in conv_data.get("questions", [])
                ]
        except Exception:
            pass  # fall through to synthetic

        if conversation is None or queries is None:
            conversation, queries = generate_conversation(self.turns)

        user_id = "bench-locomo"

        # Store all conversation turns
        for turn in conversation:
            kwargs: dict = {
                "user_id": user_id,
                "role": turn.role,
                "node_type": NodeType(turn.node_type),
                "scope": Scope.PERSONAL,
            }
            if turn.epistemic_type:
                kwargs["epistemic_type"] = EpistemicType(turn.epistemic_type)
            await engine.store(turn.content, **kwargs)

        # Evaluate queries
        details: list[QueryResult] = []
        category_results: list[tuple[str, float]] = []

        for gt_query in queries:
            response = await engine.retrieve(
                gt_query.query, user_id=user_id
            )
            top_content = " ".join(
                r.node.content for r in response.results[:5]
            )

            kw_score = keyword_match_score(
                gt_query.expected_keywords, top_content
            )
            ex_score = exclusion_score(
                gt_query.excluded_keywords, top_content
            )
            combined_score = kw_score * ex_score

            is_correct = combined_score >= 0.5
            details.append(QueryResult(
                query=gt_query.query,
                category=gt_query.category,
                expected=", ".join(gt_query.expected_keywords),
                actual=top_content[:200],
                correct=is_correct,
                score=combined_score,
            ))
            category_results.append((gt_query.category, combined_score))

        # Compute metrics
        from benchmarks.metrics import category_scores as compute_categories

        cat_scores = compute_categories(category_results)
        correct = sum(1 for d in details if d.correct)
        incorrect = sum(1 for d in details if not d.correct)

        overall = sum(d.score for d in details) / len(details) if details else 0.0
        duration_ms = (time.monotonic() - start_time) * 1000

        return BenchmarkResult(
            benchmark_name="locomo",
            overall_score=overall,
            category_scores=cat_scores,
            total_queries=len(details),
            correct=correct,
            incorrect=incorrect,
            abstained=0,
            duration_ms=duration_ms,
            details=details,
        )


# ---------------------------------------------------------------------------
# Real dataset benchmark
# ---------------------------------------------------------------------------

# LoCoMo category int → name mapping
_LOCOMO_CATEGORIES: dict[int, str] = {
    1: "single_hop",
    2: "temporal",
    3: "inference",
    4: "multi_hop",
    # 5 = adversarial (requires LLM judge, skipped)
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOCOMO_PATH = _PROJECT_ROOT / "data" / "benchmarks" / "locomo" / "locomo10.json"


def _extract_sessions(conversation: dict) -> list[tuple[list[dict], str | None]]:
    """Extract ordered sessions from a LoCoMo conversation dict.

    Returns list of (turns, date_time_str) tuples sorted by session number.
    """
    session_nums = set()
    for key in conversation:
        m = re.match(r"^session_(\d+)$", key)
        if m:
            session_nums.add(int(m.group(1)))

    sessions = []
    for num in sorted(session_nums):
        turns = conversation[f"session_{num}"]
        date_str = conversation.get(f"session_{num}_date_time")
        sessions.append((turns, date_str))
    return sessions


class LoCoMoRealBenchmark:
    """LoCoMo benchmark using the real LoCoMo-10 dataset.

    Evaluates PRME retrieval against the published LoCoMo dataset
    (arXiv 2402.17753). Uses keyword-match scoring (reproducible,
    no LLM judge needed). Category 5 (adversarial) is skipped since
    it requires LLM judgment.

    Download the dataset first::

        python scripts/download_benchmarks.py --locomo
    """

    name = "locomo-real"

    def __init__(
        self,
        dataset_path: str | None = None,
        max_conversations: int = 1,
    ) -> None:
        self.dataset_path = Path(dataset_path) if dataset_path else _DEFAULT_LOCOMO_PATH
        self.max_conversations = max_conversations

    async def run(self, engine: MemoryEngine) -> BenchmarkResult:
        if not self.dataset_path.exists():
            raise FileNotFoundError(
                f"LoCoMo dataset not found at {self.dataset_path}. "
                "Run: python scripts/download_benchmarks.py --locomo"
            )

        start_time = time.monotonic()

        with open(self.dataset_path) as f:
            all_conversations = json.load(f)

        conversations = all_conversations[: self.max_conversations]
        logger.info(
            "Running LoCoMo-real on %d/%d conversations",
            len(conversations),
            len(all_conversations),
        )

        all_details: list[QueryResult] = []
        category_results: list[tuple[str, float]] = []

        for conv_idx, conv_data in enumerate(conversations):
            sample_id = conv_data.get("sample_id", f"conv-{conv_idx}")
            user_id = f"bench-locomo-real-{sample_id}"
            conversation = conv_data["conversation"]
            sessions = _extract_sessions(conversation)

            # Ingest all sessions with speaker + date context
            total_turns = 0
            speakers: set[str] = set()
            for sess_idx, (turns, date_str) in enumerate(sessions):
                session_id = f"{sample_id}-s{sess_idx}"
                # Extract date for temporal context (e.g. "1:56 pm on 8 May, 2023")
                date_prefix = f"({date_str}) " if date_str else ""
                for turn in turns:
                    text = turn["text"].strip()
                    if len(text) < 15:
                        continue  # skip very short turns (greetings, "ok", etc.)
                    speaker = turn.get("speaker", "")
                    if speaker:
                        speakers.add(speaker)
                    enriched = f"{date_prefix}{speaker}: {text}"
                    await engine.store(
                        enriched,
                        user_id=user_id,
                        role="user",
                        node_type=NodeType.FACT,
                        scope=Scope.PERSONAL,
                        session_id=session_id,
                    )
                    total_turns += 1

            logger.info(
                "Ingested %s: %d sessions, %d turns (after filtering)",
                sample_id,
                len(sessions),
                total_turns,
            )

            # Evaluate QA (skip category 5)
            qa_list = conv_data.get("qa", [])
            for qa in qa_list:
                cat_num = qa["category"]
                if cat_num not in _LOCOMO_CATEGORIES:
                    continue

                cat_name = _LOCOMO_CATEGORIES[cat_num]
                answer = str(qa.get("answer", ""))
                if not answer:
                    continue

                response = await engine.retrieve(
                    qa["question"], user_id=user_id
                )
                top_content = " ".join(
                    r.node.content for r in response.results[:30]
                )

                # Try exact answer first, then individual words for
                # multi-word answers (e.g. "Adoption agencies" → ["Adoption", "agencies"])
                kw_score = keyword_match_score([answer], top_content)
                if kw_score < 0.5 and len(answer.split()) > 1:
                    words = [w for w in answer.split() if len(w) > 2]
                    if words:
                        kw_score = keyword_match_score(words, top_content)
                is_correct = kw_score >= 0.5

                all_details.append(QueryResult(
                    query=qa["question"],
                    category=cat_name,
                    expected=answer,
                    actual=top_content[:200],
                    correct=is_correct,
                    score=kw_score,
                ))
                category_results.append((cat_name, kw_score))

        from benchmarks.metrics import category_scores as compute_categories

        cat_scores = compute_categories(category_results)
        correct = sum(1 for d in all_details if d.correct)
        incorrect = sum(1 for d in all_details if not d.correct)
        overall = (
            sum(d.score for d in all_details) / len(all_details)
            if all_details
            else 0.0
        )
        duration_ms = (time.monotonic() - start_time) * 1000

        return BenchmarkResult(
            benchmark_name="locomo-real",
            overall_score=overall,
            category_scores=cat_scores,
            total_queries=len(all_details),
            correct=correct,
            incorrect=incorrect,
            abstained=0,
            duration_ms=duration_ms,
            details=all_details,
        )

    async def run_with_llm(
        self, engine: MemoryEngine, llm_config: LLMJudgeConfig
    ) -> BenchmarkResult:
        """Run LoCoMo-real with LLM generation + judge scoring.

        Same ingestion as run(), but after retrieval, generates an answer
        via LLM and uses LLM-as-judge to score against the ground truth.
        Keyword-match score is still computed for comparison.

        Uses concurrent evaluation (asyncio.gather + semaphores) to avoid
        sequential bottleneck on LLM API calls.
        """
        from benchmarks.llm_judge import generate_answer, judge_answer, reformulate_query

        if not self.dataset_path.exists():
            raise FileNotFoundError(
                f"LoCoMo dataset not found at {self.dataset_path}. "
                "Run: python scripts/download_benchmarks.py --locomo"
            )

        start_time = time.monotonic()

        with open(self.dataset_path) as f:
            all_conversations = json.load(f)

        conversations = all_conversations[: self.max_conversations]
        logger.info(
            "Running LoCoMo-real (LLM judge) on %d/%d conversations",
            len(conversations),
            len(all_conversations),
        )

        # Phase 1: Ingest all conversations sequentially (shared engine)
        qa_items: list[tuple[dict, str]] = []  # (qa_dict, user_id)

        for conv_idx, conv_data in enumerate(conversations):
            sample_id = conv_data.get("sample_id", f"conv-{conv_idx}")
            user_id = f"bench-locomo-real-{sample_id}"
            conversation = conv_data["conversation"]
            sessions = _extract_sessions(conversation)

            total_turns = 0
            for sess_idx, (turns, date_str) in enumerate(sessions):
                session_id = f"{sample_id}-s{sess_idx}"
                date_prefix = f"({date_str}) " if date_str else ""
                for turn in turns:
                    text = turn["text"].strip()
                    if len(text) < 15:
                        continue
                    speaker = turn.get("speaker", "")
                    enriched = f"{date_prefix}{speaker}: {text}"
                    await engine.store(
                        enriched,
                        user_id=user_id,
                        role="user",
                        node_type=NodeType.FACT,
                        scope=Scope.PERSONAL,
                        session_id=session_id,
                    )
                    total_turns += 1

            logger.info(
                "Ingested %s: %d sessions, %d turns",
                sample_id, len(sessions), total_turns,
            )

            # Collect QA items for concurrent evaluation
            for qa in conv_data.get("qa", []):
                cat_num = qa["category"]
                if cat_num not in _LOCOMO_CATEGORIES:
                    continue
                answer = str(qa.get("answer", ""))
                if not answer:
                    continue
                qa_items.append((qa, user_id))

        # Phase 2: Evaluate all QA items concurrently
        concurrency = 10
        llm_concurrency = 10
        semaphore = asyncio.Semaphore(concurrency)
        llm_semaphore = asyncio.Semaphore(llm_concurrency)
        completed = 0
        _names_re = re.compile(r'\b([A-Z][a-z]{2,})\b')
        _skip = {"What", "When", "Where", "Who", "How", "Would", "Does", "Did", "Has", "Have", "Could", "Can", "The"}

        logger.info(
            "Evaluating %d questions, concurrency=%d, llm_concurrency=%d",
            len(qa_items), concurrency, llm_concurrency,
        )

        async def _process_qa(
            qa: dict, user_id: str,
        ) -> tuple[QueryResult, tuple[str, float]]:
            nonlocal completed
            cat_name = _LOCOMO_CATEGORIES[qa["category"]]
            answer = str(qa["answer"])

            # Retrieval (semaphore-gated to avoid overwhelming shared engine)
            async with semaphore:
                response = await engine.retrieve(
                    qa["question"], user_id=user_id
                )
                seen_ids = {str(r.node.id) for r in response.results}
                all_results = list(response.results)

                async with llm_semaphore:
                    alt_queries = await reformulate_query(
                        qa["question"], llm_config
                    )

                # Entity-focused: extract proper nouns and search for each
                entity_names = _names_re.findall(qa["question"])
                entity_names = [n for n in entity_names if n not in _skip]
                for name in entity_names[:2]:
                    alt_queries.append(name)

                for alt_q in alt_queries:
                    alt_response = await engine.retrieve(
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

            top_content = "\n".join(
                f"[{i+1}] {r.node.content}"
                for i, r in enumerate(all_results[:50])
            )

            # LLM generate + judge (semaphore-gated)
            async with llm_semaphore:
                generated = await generate_answer(
                    qa["question"], top_content, llm_config
                )
                llm_score = await judge_answer(
                    qa["question"], answer, generated, llm_config
                )

            is_correct = llm_score >= 0.5
            completed += 1
            if completed % 25 == 0:
                elapsed = (time.monotonic() - start_time) / 60
                logger.info(
                    "Progress: %d/%d questions (%.1f min)",
                    completed, len(qa_items), elapsed,
                )

            return (
                QueryResult(
                    query=qa["question"],
                    category=cat_name,
                    expected=answer,
                    actual=top_content[:200],
                    correct=is_correct,
                    score=llm_score,
                    generated_answer=generated,
                ),
                (cat_name, llm_score),
            )

        results = await asyncio.gather(
            *[_process_qa(qa, uid) for qa, uid in qa_items],
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
        overall = (
            sum(d.score for d in all_details) / len(all_details)
            if all_details
            else 0.0
        )
        duration_ms = (time.monotonic() - start_time) * 1000

        return BenchmarkResult(
            benchmark_name="locomo-real (llm-judge)",
            overall_score=overall,
            category_scores=cat_scores,
            total_queries=len(all_details),
            correct=correct,
            incorrect=incorrect,
            abstained=0,
            duration_ms=duration_ms,
            details=all_details,
        )
