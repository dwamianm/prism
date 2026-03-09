"""Evaluation scenarios with ground truth for IR metric computation.

Three deterministic scenarios that exercise retrieval quality:

1. **Factual Retrieval** -- store 20 diverse facts, query specific subsets,
   measure precision/recall.
2. **Temporal Retrieval** -- store facts at different simulated times, query
   with temporal context, verify recency handling.
3. **Supersedence Handling** -- store conflicting facts, verify superseded
   content does not surface above current content.

Each checkpoint carries a ``GroundTruth`` instance so the evaluation harness
automatically computes precision@k, recall@k, nDCG@k, MRR, F1@k, and
hit rate.
"""

from simulations.evaluation import GroundTruth
from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ======================================================================
# Scenario 1: Factual Retrieval
# ======================================================================

_FACTUAL_MESSAGES = [
    # Programming languages
    SimMessage(day=1, role="user",
               content="Python is our primary backend language for all microservices.",
               tags=["language", "python"], node_type="fact"),
    SimMessage(day=1, role="user",
               content="TypeScript is used for all frontend applications and React components.",
               tags=["language", "typescript"], node_type="fact"),
    SimMessage(day=1, role="user",
               content="Rust is used for our high-performance data pipeline workers.",
               tags=["language", "rust"], node_type="fact"),
    SimMessage(day=1, role="user",
               content="Go is used for our service mesh sidecar proxies.",
               tags=["language", "go"], node_type="fact"),
    # Databases
    SimMessage(day=2, role="user",
               content="PostgreSQL is our primary relational database for transactional data.",
               tags=["database", "postgresql"], node_type="fact"),
    SimMessage(day=2, role="user",
               content="Redis is used for caching and session management across services.",
               tags=["database", "redis"], node_type="fact"),
    SimMessage(day=2, role="user",
               content="Elasticsearch powers our full-text search and log analytics.",
               tags=["database", "elasticsearch"], node_type="fact"),
    SimMessage(day=2, role="user",
               content="MongoDB is used for our document storage and user profiles.",
               tags=["database", "mongodb"], node_type="fact"),
    # Infrastructure
    SimMessage(day=3, role="user",
               content="Kubernetes orchestrates all our production workloads on AWS EKS.",
               tags=["infra", "kubernetes"], node_type="fact"),
    SimMessage(day=3, role="user",
               content="Terraform manages our infrastructure as code for all cloud resources.",
               tags=["infra", "terraform"], node_type="fact"),
    SimMessage(day=3, role="user",
               content="ArgoCD handles continuous deployment with GitOps workflows.",
               tags=["infra", "argocd"], node_type="fact"),
    SimMessage(day=3, role="user",
               content="Prometheus and Grafana provide monitoring and alerting for services.",
               tags=["infra", "monitoring"], node_type="fact"),
    # Team
    SimMessage(day=4, role="user",
               content="Alice leads the platform engineering team of five engineers.",
               tags=["team", "alice"], node_type="fact", epistemic_type="observed"),
    SimMessage(day=4, role="user",
               content="Bob is the tech lead for the data engineering squad.",
               tags=["team", "bob"], node_type="fact", epistemic_type="observed"),
    SimMessage(day=4, role="user",
               content="Carol manages the frontend team and drives UX decisions.",
               tags=["team", "carol"], node_type="fact", epistemic_type="observed"),
    SimMessage(day=4, role="user",
               content="Dave handles DevOps and maintains the CI/CD pipelines.",
               tags=["team", "dave"], node_type="fact", epistemic_type="observed"),
    # Projects
    SimMessage(day=5, role="user",
               content="Project Mercury is our customer-facing SaaS platform built with React.",
               tags=["project", "mercury"], node_type="fact"),
    SimMessage(day=5, role="user",
               content="Project Atlas is the internal analytics dashboard for business metrics.",
               tags=["project", "atlas"], node_type="fact"),
    SimMessage(day=5, role="user",
               content="Project Nova handles real-time event processing using Kafka streams.",
               tags=["project", "nova"], node_type="fact"),
    SimMessage(day=5, role="user",
               content="Project Orbit is the mobile application built with React Native.",
               tags=["project", "orbit"], node_type="fact"),
]

_FACTUAL_CHECKPOINTS = [
    SimCheckpoint(
        day=10,
        query="What programming languages do we use for backend and frontend?",
        expected_keywords=["Python", "TypeScript"],
        excluded_keywords=[],
        description="Retrieve all programming languages from stored facts",
        ground_truth=GroundTruth(
            query="What programming languages do we use for backend and frontend?",
            relevant_keywords=["Python", "TypeScript", "Rust", "Go"],
            irrelevant_keywords=["PostgreSQL", "Redis", "Kubernetes"],
            relevance_grades={"Python": 3, "TypeScript": 2, "Rust": 2, "Go": 2},
        ),
    ),
    SimCheckpoint(
        day=10,
        query="What databases and data stores do we use?",
        expected_keywords=["PostgreSQL", "Redis"],
        excluded_keywords=[],
        description="Retrieve database technologies",
        ground_truth=GroundTruth(
            query="What databases and data stores do we use?",
            relevant_keywords=["PostgreSQL", "Redis", "Elasticsearch", "MongoDB"],
            irrelevant_keywords=["Python", "Kubernetes", "Terraform"],
            relevance_grades={
                "PostgreSQL": 3, "Redis": 2,
                "Elasticsearch": 2, "MongoDB": 2,
            },
        ),
    ),
    SimCheckpoint(
        day=10,
        query="Who are the team leads and what do they do?",
        expected_keywords=["Alice", "Bob"],
        excluded_keywords=[],
        description="Retrieve team member information",
        ground_truth=GroundTruth(
            query="Who are the team leads and what do they do?",
            relevant_keywords=["Alice", "Bob", "Carol", "Dave"],
            irrelevant_keywords=["PostgreSQL", "Python", "Kubernetes"],
            relevance_grades={"Alice": 3, "Bob": 3, "Carol": 2, "Dave": 2},
        ),
    ),
    SimCheckpoint(
        day=10,
        query="What are our active projects?",
        expected_keywords=["Mercury"],
        excluded_keywords=[],
        description="Retrieve project information",
        ground_truth=GroundTruth(
            query="What are our active projects?",
            relevant_keywords=["Mercury", "Atlas", "Nova", "Orbit"],
            irrelevant_keywords=["Python", "PostgreSQL", "Alice"],
            relevance_grades={"Mercury": 3, "Atlas": 2, "Nova": 2, "Orbit": 2},
        ),
    ),
]

FACTUAL_RETRIEVAL_SCENARIO = SimScenario(
    name="eval_factual_retrieval",
    description=(
        "Store 20 diverse facts across 5 categories and query specific "
        "subsets to measure precision and recall of retrieval."
    ),
    messages=_FACTUAL_MESSAGES,
    checkpoints=_FACTUAL_CHECKPOINTS,
)

# ======================================================================
# Scenario 2: Temporal Retrieval
# ======================================================================

_TEMPORAL_MESSAGES = [
    # Early facts (day 1-5)
    SimMessage(day=1, role="user",
               content="Our Q1 revenue target is $2 million for the enterprise segment.",
               tags=["revenue", "q1"], node_type="fact"),
    SimMessage(day=2, role="user",
               content="The sprint velocity for January was 42 story points.",
               tags=["velocity", "january"], node_type="fact"),
    SimMessage(day=3, role="user",
               content="We hired three junior developers in the first week of January.",
               tags=["hiring", "january"], node_type="fact", epistemic_type="observed"),
    SimMessage(day=5, role="user",
               content="Server response time averaged 120ms in early January.",
               tags=["performance", "january"], node_type="fact"),
    # Mid-period facts (day 15-20)
    SimMessage(day=15, role="user",
               content="February sprint velocity improved to 58 story points.",
               tags=["velocity", "february"], node_type="fact"),
    SimMessage(day=16, role="user",
               content="We released version 2.0 of the platform in mid-February.",
               tags=["release", "february"], node_type="fact"),
    SimMessage(day=18, role="user",
               content="Customer satisfaction score reached 4.5 out of 5 in February.",
               tags=["satisfaction", "february"], node_type="fact"),
    SimMessage(day=20, role="user",
               content="Server response time dropped to 85ms after the February optimization.",
               tags=["performance", "february"], node_type="fact"),
    # Recent facts (day 30-35)
    SimMessage(day=30, role="user",
               content="March sprint velocity hit a record 72 story points.",
               tags=["velocity", "march"], node_type="fact"),
    SimMessage(day=31, role="user",
               content="We signed five new enterprise customers in early March.",
               tags=["customers", "march"], node_type="fact", epistemic_type="observed"),
    SimMessage(day=33, role="user",
               content="The March deployment introduced the new analytics dashboard.",
               tags=["release", "march"], node_type="fact"),
    SimMessage(day=35, role="user",
               content="Server response time is now 62ms after the March infrastructure upgrade.",
               tags=["performance", "march"], node_type="fact"),
]

_TEMPORAL_CHECKPOINTS = [
    SimCheckpoint(
        day=40,
        query="What is the current sprint velocity?",
        expected_keywords=["72"],
        excluded_keywords=[],
        description="Most recent velocity (March) should rank highest",
        ranking_assertions=[("72", "58"), ("72", "42")],
        ground_truth=GroundTruth(
            query="What is the current sprint velocity?",
            relevant_keywords=["72", "velocity"],
            irrelevant_keywords=["revenue", "customers"],
            relevance_grades={"72": 3, "velocity": 1},
        ),
    ),
    SimCheckpoint(
        day=40,
        query="What is our server response time performance?",
        expected_keywords=["62ms"],
        excluded_keywords=[],
        description="Most recent performance metric should rank highest",
        ranking_assertions=[("62ms", "85ms"), ("62ms", "120ms")],
        ground_truth=GroundTruth(
            query="What is our server response time performance?",
            relevant_keywords=["62ms", "85ms", "120ms", "response time"],
            irrelevant_keywords=["velocity", "revenue"],
            relevance_grades={"62ms": 3, "85ms": 2, "120ms": 1},
        ),
    ),
    SimCheckpoint(
        day=40,
        query="What happened in February?",
        expected_keywords=["February"],
        excluded_keywords=[],
        description="February-specific content should surface for temporal query",
        ground_truth=GroundTruth(
            query="What happened in February?",
            relevant_keywords=["February", "version 2.0", "4.5"],
            irrelevant_keywords=["January", "March"],
            relevance_grades={"February": 2, "version 2.0": 3, "4.5": 2},
        ),
    ),
]

TEMPORAL_RETRIEVAL_SCENARIO = SimScenario(
    name="eval_temporal_retrieval",
    description=(
        "Store facts at different simulated times and query with temporal "
        "context to verify recency-aware retrieval."
    ),
    messages=_TEMPORAL_MESSAGES,
    checkpoints=_TEMPORAL_CHECKPOINTS,
)

# ======================================================================
# Scenario 3: Supersedence Handling
# ======================================================================

_SUPERSEDENCE_MESSAGES = [
    # Original facts
    SimMessage(day=1, role="user",
               content="Our office is located in downtown San Francisco at 100 Market Street.",
               tags=["office", "location"], node_type="fact"),
    SimMessage(day=1, role="user",
               content="The company CEO is John Smith who founded the company in 2019.",
               tags=["leadership", "ceo"], node_type="fact", epistemic_type="observed"),
    SimMessage(day=1, role="user",
               content="Our pricing plan starts at $29 per month for the basic tier.",
               tags=["pricing", "basic"], node_type="fact"),
    SimMessage(day=1, role="user",
               content="The engineering team uses Slack for all internal communication.",
               tags=["communication", "slack"], node_type="fact"),
    SimMessage(day=1, role="user",
               content="Our main product supports only English language.",
               tags=["product", "language"], node_type="fact"),
    # Superseding facts
    SimMessage(day=15, role="user",
               content="We moved our office from San Francisco to Austin, Texas at 500 Congress Avenue.",
               tags=["office", "location", "austin"], node_type="fact"),
    SimMessage(day=20, role="user",
               content="Jane Doe replaced John Smith as CEO after the board restructuring.",
               tags=["leadership", "ceo", "jane"], node_type="fact",
               epistemic_type="observed"),
    SimMessage(day=25, role="user",
               content="We changed our pricing from $29 to $39 per month for the basic tier.",
               tags=["pricing", "basic", "new"], node_type="fact"),
    SimMessage(day=30, role="user",
               content="The team migrated from Slack to Microsoft Teams for communication.",
               tags=["communication", "teams"], node_type="fact"),
    SimMessage(day=35, role="user",
               content="Our product now supports English, Spanish, and French languages.",
               tags=["product", "language", "multilingual"], node_type="fact"),
]

_SUPERSEDENCE_CHECKPOINTS = [
    SimCheckpoint(
        day=40,
        query="Where is our office located?",
        expected_keywords=["Austin"],
        excluded_keywords=[],
        description="New office location (Austin) should supersede old (San Francisco)",
        ranking_assertions=[("Austin", "San Francisco")],
        ground_truth=GroundTruth(
            query="Where is our office located?",
            relevant_keywords=["Austin", "500 Congress"],
            irrelevant_keywords=["San Francisco", "100 Market"],
            relevance_grades={"Austin": 3, "500 Congress": 2},
        ),
    ),
    SimCheckpoint(
        day=40,
        query="Who is the CEO of the company?",
        expected_keywords=["Jane"],
        excluded_keywords=[],
        description="New CEO (Jane Doe) should supersede old (John Smith)",
        ranking_assertions=[("Jane", "John Smith")],
        ground_truth=GroundTruth(
            query="Who is the CEO of the company?",
            relevant_keywords=["Jane Doe", "CEO"],
            irrelevant_keywords=["John Smith"],
            relevance_grades={"Jane Doe": 3, "CEO": 1},
        ),
    ),
    SimCheckpoint(
        day=40,
        query="What is our product pricing?",
        expected_keywords=["$39"],
        excluded_keywords=[],
        description="New pricing ($39) should supersede old ($29)",
        ranking_assertions=[("$39", "$29")],
        ground_truth=GroundTruth(
            query="What is our product pricing?",
            relevant_keywords=["$39", "pricing"],
            irrelevant_keywords=["$29"],
            relevance_grades={"$39": 3, "pricing": 1},
        ),
    ),
    SimCheckpoint(
        day=40,
        query="What communication tool does the team use?",
        expected_keywords=["Teams"],
        excluded_keywords=[],
        description="New tool (Teams) should supersede old (Slack)",
        ranking_assertions=[("Teams", "Slack")],
        ground_truth=GroundTruth(
            query="What communication tool does the team use?",
            relevant_keywords=["Microsoft Teams", "Teams"],
            irrelevant_keywords=["Slack"],
            relevance_grades={"Microsoft Teams": 3, "Teams": 2},
        ),
    ),
    SimCheckpoint(
        day=40,
        query="What languages does our product support?",
        expected_keywords=["Spanish", "French"],
        excluded_keywords=[],
        description="Multilingual support should supersede English-only",
        ranking_assertions=[("Spanish", "only English")],
        ground_truth=GroundTruth(
            query="What languages does our product support?",
            relevant_keywords=["Spanish", "French", "English"],
            irrelevant_keywords=[],
            relevance_grades={"Spanish": 3, "French": 3, "English": 1},
        ),
    ),
]

SUPERSEDENCE_HANDLING_SCENARIO = SimScenario(
    name="eval_supersedence_handling",
    description=(
        "Store conflicting facts (old then new) and verify that superseded "
        "content does not surface above current content."
    ),
    messages=_SUPERSEDENCE_MESSAGES,
    checkpoints=_SUPERSEDENCE_CHECKPOINTS,
    config_overrides={"enable_store_supersedence": True},
)
