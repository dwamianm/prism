"""Tests for VSA memory store.

Includes unit tests and an integration test that mirrors
the PRME changing_facts simulation scenario.
"""

import pytest

from research.vsa.memory import VSAMemory


@pytest.fixture
def mem():
    return VSAMemory(dim=10_000)


class TestStore:
    def test_store_returns_id(self, mem):
        mid = mem.store("Hello world", node_type="fact", day=1)
        assert isinstance(mid, str)
        assert len(mid) > 0

    def test_store_increments_size(self, mem):
        assert mem.size == 0
        mem.store("First", day=1)
        assert mem.size == 1
        mem.store("Second", day=2)
        assert mem.size == 2

    def test_get_by_id(self, mem):
        mid = mem.store("Test content", node_type="fact", day=1)
        record = mem.get_by_id(mid)
        assert record is not None
        assert record.content == "Test content"
        assert record.node_type == "fact"


class TestRetrieve:
    def test_basic_retrieval(self, mem):
        mem.store("Python is a programming language", node_type="fact", day=1)
        mem.store("The weather is sunny today", node_type="fact", day=1)
        mem.store("Python has dynamic typing", node_type="fact", day=2)

        results = mem.retrieve("Python programming", top_k=5)
        assert len(results) > 0

        # Python-related results should rank higher
        contents = [r.record.content for r in results]
        # At least one Python result in top 2
        top2 = " ".join(contents[:2]).lower()
        assert "python" in top2

    def test_retrieve_with_day(self, mem):
        mem.store("Old fact from day 1", node_type="fact", day=1)
        mem.store("New fact from day 100", node_type="fact", day=100)

        results = mem.retrieve("fact", query_day=100, top_k=5)
        assert len(results) > 0

    def test_empty_memory_returns_empty(self, mem):
        results = mem.retrieve("anything")
        assert results == []

    def test_lifecycle_weighting(self, mem):
        """Superseded memories should score lower."""
        id1 = mem.store("MySQL is our database", node_type="fact", day=1)
        id2 = mem.store("PostgreSQL is our database", node_type="fact", day=10)
        mem.supersede(id1, id2)

        results = mem.retrieve("database", top_k=5, include_superseded=True)
        assert len(results) >= 2

        # Find scores for each
        scores = {}
        for r in results:
            if "mysql" in r.record.content.lower():
                scores["mysql"] = r.composite_score
            elif "postgresql" in r.record.content.lower():
                scores["postgres"] = r.composite_score

        assert scores.get("postgres", 0) > scores.get("mysql", 0)


class TestSupersedence:
    def test_supersede_changes_state(self, mem):
        id1 = mem.store("Old fact", day=1)
        id2 = mem.store("New fact", day=2)
        mem.supersede(id1, id2)

        record = mem.get_by_id(id1)
        assert record.lifecycle_state == "superseded"
        assert record.superseded_by == id2

    def test_superseded_excluded_by_default(self, mem):
        id1 = mem.store("MySQL is the database", node_type="fact", day=1)
        id2 = mem.store("PostgreSQL is the database", node_type="fact", day=10)
        mem.supersede(id1, id2)

        results = mem.retrieve("database", top_k=5)
        contents = [r.record.content.lower() for r in results]
        assert not any("mysql" in c for c in contents)

    def test_detect_supersedence_migration(self, mem):
        id1 = mem.store(
            "Our backend database is MySQL 8.0",
            node_type="fact", day=1,
            tags=["database", "mysql"],
        )
        id2 = mem.store(
            "We have completed the migration from MySQL to PostgreSQL",
            node_type="fact", day=12,
            tags=["database", "postgresql"],
        )

        superseded = mem.detect_supersedence(
            "We have completed the migration from MySQL to PostgreSQL",
            id2,
        )

        # Should detect that the MySQL fact is superseded
        assert id1 in superseded

    def test_detect_supersedence_switch(self, mem):
        id1 = mem.store(
            "I use VS Code as my primary editor",
            node_type="preference", day=1,
            tags=["editor", "vscode"],
        )
        id2 = mem.store(
            "I switched from VS Code to Neovim for development",
            node_type="preference", day=14,
            tags=["editor", "neovim"],
        )

        superseded = mem.detect_supersedence(
            "I switched from VS Code to Neovim for development",
            id2,
        )
        assert id1 in superseded


class TestOrganize:
    def test_promotion_after_threshold(self, mem):
        mem.store("A fact", node_type="fact", day=1)
        counts = mem.organize(current_day=10)
        assert counts["promoted"] == 1

    def test_no_promotion_before_threshold(self, mem):
        mem.store("A fact", node_type="fact", day=5)
        counts = mem.organize(current_day=6)
        assert counts["promoted"] == 0

    def test_lifecycle_counts(self, mem):
        mem.store("Fact 1", day=1)
        mem.store("Fact 2", day=2)
        mem.store("Fact 3", day=3)
        mem.organize(current_day=20)

        counts = mem.query_lifecycle_counts()
        assert counts.get("stable", 0) == 3


class TestChangingFactsScenario:
    """Integration test mirroring the PRME changing_facts simulation.

    This is the critical benchmark: can the VSA memory store handle
    the same scenario that PRME handles with four separate stores?
    """

    def _setup_scenario(self) -> VSAMemory:
        """Set up the changing facts scenario."""
        mem = VSAMemory(dim=10_000)

        # Week 1 (days 1-7): establish baseline
        mem.store(
            "Our backend database is MySQL 8.0 and it handles all our data storage needs.",
            node_type="fact", day=1, tags=["database", "mysql"],
        )
        mem.store(
            "I use VS Code as my primary editor with the Python extension.",
            node_type="preference", day=1, tags=["editor", "vscode"],
        )
        mem.store(
            "Our API layer is built on REST with Flask as the web framework.",
            node_type="fact", day=2, tags=["api", "rest", "flask"],
        )
        mem.store(
            "The team consists of Alice (frontend lead), Bob (backend), and Charlie (devops).",
            node_type="fact", day=2, tags=["team", "people"],
        )
        mem.store(
            "We deploy our services on AWS EC2 instances managed by Ansible.",
            node_type="fact", day=3, tags=["infrastructure", "ec2", "aws"],
        )
        mem.store(
            "Our project Alpha is the main revenue-generating product.",
            node_type="fact", day=4, tags=["project", "alpha"],
        )
        mem.store(
            "We use pytest for all our Python testing with 85% code coverage.",
            node_type="fact", day=5, tags=["testing", "pytest"],
        )

        # Day 12: database migration
        id_pg = mem.store(
            "We have completed the migration from MySQL to PostgreSQL for better JSON support and performance.",
            node_type="fact", day=12, tags=["database", "postgresql", "migration"],
        )
        mem.detect_supersedence(
            "We have completed the migration from MySQL to PostgreSQL for better JSON support and performance.",
            id_pg,
        )

        id_no_mysql = mem.store(
            "MySQL is no longer used in our stack. All data has been migrated to PostgreSQL.",
            node_type="fact", day=12, tags=["database", "mysql", "deprecated"],
        )
        mem.detect_supersedence(
            "MySQL is no longer used in our stack. All data has been migrated to PostgreSQL.",
            id_no_mysql,
        )

        # Settled fact
        mem.store("Our database is PostgreSQL with JSON support and strong performance.",
                  node_type="fact", day=13, tags=["database", "postgresql"])

        # Day 14: editor switch
        id_nvim = mem.store(
            "I switched from VS Code to Neovim for all my development work. The modal editing is much faster.",
            node_type="preference", day=14, tags=["editor", "neovim"],
        )
        mem.detect_supersedence(
            "I switched from VS Code to Neovim for all my development work. The modal editing is much faster.",
            id_nvim,
        )

        # Settled fact
        mem.store("My primary editor is Neovim with modal editing.",
                  node_type="preference", day=14, tags=["editor", "neovim"])

        # Day 20: team update
        mem.store(
            "Diana joined our team as a data engineer. She specializes in ETL pipelines.",
            node_type="fact", day=20, tags=["team", "people", "diana"],
        )

        # Day 32: API migration
        id_gql = mem.store(
            "We migrated our API from REST to GraphQL using Strawberry. The developer experience is much better.",
            node_type="fact", day=32, tags=["api", "graphql", "strawberry"],
        )
        mem.detect_supersedence(
            "We migrated our API from REST to GraphQL using Strawberry. The developer experience is much better.",
            id_gql,
        )

        id_rest_dep = mem.store(
            "Our REST API is deprecated and will be removed next quarter.",
            node_type="fact", day=32, tags=["api", "rest", "deprecated"],
        )
        mem.detect_supersedence(
            "Our REST API is deprecated and will be removed next quarter.",
            id_rest_dep,
        )

        # Settled fact
        mem.store("Our API layer is GraphQL powered by Strawberry framework.",
                  node_type="fact", day=33, tags=["api", "graphql", "strawberry"])

        # Day 35: infrastructure migration
        id_k8s = mem.store(
            "We moved our infrastructure from EC2 to Kubernetes on EKS for better scaling and deployment.",
            node_type="fact", day=35, tags=["infrastructure", "kubernetes", "eks"],
        )
        mem.detect_supersedence(
            "We moved our infrastructure from EC2 to Kubernetes on EKS for better scaling and deployment.",
            id_k8s,
        )

        # Settled fact
        mem.store("Our services run on Kubernetes (EKS) for scaling and deployment.",
                  node_type="fact", day=36, tags=["infrastructure", "kubernetes", "eks"])

        # Day 45: new project
        mem.store(
            "We started project Beta, a new analytics platform built on the PostgreSQL data warehouse.",
            node_type="fact", day=45, tags=["project", "beta", "analytics"],
        )

        # Day 55: editor switch back
        id_vscode_back = mem.store(
            "I went back to VS Code from Neovim. The integrated debugging and extensions are too valuable to give up.",
            node_type="preference", day=55, tags=["editor", "vscode"],
        )
        mem.detect_supersedence(
            "I went back to VS Code from Neovim. The integrated debugging and extensions are too valuable to give up.",
            id_vscode_back,
        )

        # Settled fact
        mem.store("My primary editor is VS Code with integrated debugging and extensions.",
                  node_type="preference", day=55, tags=["editor", "vscode"])

        return mem

    def test_database_after_migration(self):
        """Day 15: PostgreSQL should dominate after MySQL→PostgreSQL migration."""
        mem = self._setup_scenario()
        mem.organize(current_day=15)

        results = mem.retrieve("What database does the project use?", query_day=15, top_k=5)
        top_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "postgresql" in top_content, f"PostgreSQL not in top results: {top_content[:200]}"

        # PostgreSQL should rank above MySQL
        pg_rank = None
        mysql_rank = None
        for i, r in enumerate(results[:5]):
            c = r.record.content.lower()
            if "postgresql" in c and pg_rank is None:
                pg_rank = i
            if "mysql" in c and "postgresql" not in c and mysql_rank is None:
                mysql_rank = i

        if pg_rank is not None and mysql_rank is not None:
            assert pg_rank < mysql_rank, (
                f"PostgreSQL (rank {pg_rank}) should rank above MySQL (rank {mysql_rank})"
            )

    def test_editor_after_switch(self):
        """Day 15: Neovim should appear after editor switch."""
        mem = self._setup_scenario()
        mem.organize(current_day=15)

        results = mem.retrieve("What editor do I use for development?", query_day=15, top_k=5)
        top_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "neovim" in top_content, f"Neovim not in results: {top_content[:200]}"

    def test_team_members_persist(self):
        """Day 25: Long-lived observed facts (team) should persist."""
        mem = self._setup_scenario()
        mem.organize(current_day=25)

        results = mem.retrieve("Who is on the team?", query_day=25, top_k=5)
        top_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "alice" in top_content, f"Alice not in results: {top_content[:200]}"
        assert "bob" in top_content, f"Bob not in results: {top_content[:200]}"

        # Check lifecycle promotion
        counts = mem.query_lifecycle_counts()
        assert counts.get("stable", 0) >= 1

    def test_api_after_migration(self):
        """Day 40: GraphQL should dominate after REST→GraphQL migration."""
        mem = self._setup_scenario()
        mem.organize(current_day=40)

        results = mem.retrieve("What API technology do we use?", query_day=40, top_k=5)
        top_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "graphql" in top_content, f"GraphQL not in results: {top_content[:200]}"

    def test_infrastructure_after_migration(self):
        """Day 40: Kubernetes should dominate after EC2→K8s migration."""
        mem = self._setup_scenario()
        mem.organize(current_day=40)

        results = mem.retrieve("How do we deploy our services?", query_day=40, top_k=5)
        top_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "kubernetes" in top_content, f"Kubernetes not in results: {top_content[:200]}"

    def test_editor_after_switch_back(self):
        """Day 60: VS Code should be current after switching back from Neovim."""
        mem = self._setup_scenario()
        mem.organize(current_day=60)

        results = mem.retrieve("What editor do I use?", query_day=60, top_k=5)
        top_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "vs code" in top_content or "vscode" in top_content, (
            f"VS Code not in results: {top_content[:200]}"
        )

    def test_stable_facts_at_90_days(self):
        """Day 90: Stable observed facts should still be retrievable."""
        mem = self._setup_scenario()
        mem.organize(current_day=90)

        results = mem.retrieve("Who are the team members?", query_day=90, top_k=5)
        top_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "alice" in top_content, f"Alice not in day 90 results: {top_content[:200]}"

        counts = mem.query_lifecycle_counts()
        assert counts.get("stable", 0) >= 1

    def test_database_persists_at_90_days(self):
        """Day 90: PostgreSQL should still be the known database."""
        mem = self._setup_scenario()
        mem.organize(current_day=90)

        results = mem.retrieve("What database does the project use currently?", query_day=90, top_k=5)
        top_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "postgresql" in top_content, (
            f"PostgreSQL not in day 90 results: {top_content[:200]}"
        )
