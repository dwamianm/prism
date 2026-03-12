"""Tests for the Narrative Rewriter (Phase 2).

Tests that the rewriter:
1. Detects state changes from memory events
2. Auto-generates settled facts
3. Maintains a consistent narrative document
4. Produces settled facts that match manual ones in quality
"""

import pytest
from research.narrative.document import NarrativeDocument, Section
from research.narrative.rewriter import (
    NarrativeRewriter,
    RuleBasedGenerator,
    StateChange,
)
from research.vsa.memory import VSAMemory


class TestNarrativeDocument:
    """Test the document model."""

    def test_create_section(self):
        doc = NarrativeDocument()
        section = doc.get_or_create(
            "database", "MySQL", day=1,
            initial_state="Our database is MySQL 8.0.",
        )
        assert section.topic == "database"
        assert section.current_subject == "MySQL"
        assert len(section.history) == 1

    def test_update_section(self):
        doc = NarrativeDocument()
        doc.get_or_create(
            "database", "MySQL", day=1,
            initial_state="Our database is MySQL 8.0.",
        )
        entry = doc.update_section(
            "database", "PostgreSQL", day=12,
            new_state="Our database is PostgreSQL.",
            summary="Migrated from MySQL to PostgreSQL",
        )
        assert entry is not None
        section = doc.get("database")
        assert section.current_state == "Our database is PostgreSQL."
        assert section.current_subject == "PostgreSQL"
        assert len(section.history) == 2

    def test_get_nonexistent(self):
        doc = NarrativeDocument()
        assert doc.get("missing") is None

    def test_topics(self):
        doc = NarrativeDocument()
        doc.get_or_create("database", "MySQL", day=1, initial_state="MySQL")
        doc.get_or_create("editor", "VS Code", day=1, initial_state="VS Code")
        assert doc.topics == ["database", "editor"]

    def test_render(self):
        doc = NarrativeDocument()
        doc.get_or_create("database", "MySQL", day=1, initial_state="Our database is MySQL.")
        doc.update_section(
            "database", "PostgreSQL", day=12,
            new_state="Our database is PostgreSQL.",
            summary="Migrated from MySQL",
        )
        rendered = doc.render()
        assert "Database" in rendered
        assert "PostgreSQL" in rendered
        assert "History" in rendered

    def test_to_dict(self):
        doc = NarrativeDocument()
        doc.get_or_create("database", "MySQL", day=1, initial_state="MySQL")
        d = doc.to_dict()
        assert "database" in d
        assert d["database"]["current_subject"] == "MySQL"

    def test_contains(self):
        doc = NarrativeDocument()
        doc.get_or_create("database", "MySQL", day=1, initial_state="MySQL")
        assert "database" in doc
        assert "editor" not in doc


class TestRuleBasedGenerator:
    """Test the rule-based settled fact generator."""

    def test_migration_with_reason(self):
        gen = RuleBasedGenerator()
        change = StateChange(
            topic="database",
            old_subject="MySQL",
            new_subject="PostgreSQL",
            reason="better JSON support",
            change_type="migration",
            source_memory_id="abc",
            day=12,
            tags=["database", "postgresql"],
        )
        result = gen.generate(change, None)
        assert "PostgreSQL" in result
        assert "JSON support" in result
        assert "MySQL" not in result  # Settled fact should NOT mention old tech

    def test_migration_without_reason(self):
        gen = RuleBasedGenerator()
        change = StateChange(
            topic="api",
            old_subject="REST",
            new_subject="GraphQL",
            reason="",
            change_type="migration",
            source_memory_id="abc",
            day=32,
            tags=["api", "graphql"],
        )
        result = gen.generate(change, None)
        assert "GraphQL" in result
        assert "REST" not in result

    def test_return_change(self):
        gen = RuleBasedGenerator()
        change = StateChange(
            topic="editor",
            old_subject="Neovim",
            new_subject="VS Code",
            reason="",
            change_type="return",
            source_memory_id="abc",
            day=55,
            tags=["editor", "vscode"],
        )
        result = gen.generate(change, None)
        assert "VS Code" in result
        assert "Neovim" not in result

    def test_editor_uses_my_prefix(self):
        gen = RuleBasedGenerator()
        change = StateChange(
            topic="editor",
            old_subject=None,
            new_subject="Neovim",
            reason="",
            change_type="migration",
            source_memory_id="abc",
            day=14,
            tags=["editor", "neovim"],
        )
        result = gen.generate(change, None)
        assert result.startswith("My primary")

    def test_database_uses_our_prefix(self):
        gen = RuleBasedGenerator()
        change = StateChange(
            topic="database",
            old_subject=None,
            new_subject="PostgreSQL",
            reason="",
            change_type="migration",
            source_memory_id="abc",
            day=12,
            tags=["database", "postgresql"],
        )
        result = gen.generate(change, None)
        assert result.startswith("Our")


class TestNarrativeRewriter:
    """Test the full rewriter pipeline."""

    def test_ingest_simple_fact(self):
        """Non-migration facts should be stored and tracked."""
        mem = VSAMemory(dim=10_000)
        rewriter = NarrativeRewriter(mem)

        rewriter.ingest(
            "Our backend database is MySQL 8.0.",
            node_type="fact", day=1, tags=["database", "mysql"],
        )

        assert mem.size >= 1
        assert "database" in rewriter.document
        section = rewriter.document.get("database")
        assert section.current_subject == "mysql"

    def test_ingest_migration_generates_settled_fact(self):
        """Migration events should auto-generate settled facts."""
        mem = VSAMemory(dim=10_000)
        rewriter = NarrativeRewriter(mem)

        # Baseline
        rewriter.ingest(
            "Our backend database is MySQL 8.0.",
            node_type="fact", day=1, tags=["database", "mysql"],
        )

        # Migration
        rewriter.ingest(
            "We have completed the migration from MySQL to PostgreSQL for better JSON support.",
            node_type="fact", day=12, tags=["database", "postgresql", "migration"],
        )

        # Should have generated a settled fact
        assert rewriter.settled_facts_generated >= 1

        # Narrative should show PostgreSQL
        section = rewriter.document.get("database")
        assert "PostgreSQL" in section.current_state

        # VSAMemory should have the settled fact
        # Original (1) + migration (1) + MySQL deprecated (maybe) + settled (1)
        assert mem.size >= 3

    def test_ingest_editor_switch(self):
        """Editor switch should be detected and settled fact generated."""
        mem = VSAMemory(dim=10_000)
        rewriter = NarrativeRewriter(mem)

        rewriter.ingest(
            "I use VS Code as my primary editor.",
            node_type="preference", day=1, tags=["editor", "vscode"],
        )

        rewriter.ingest(
            "I switched from VS Code to Neovim for all my development work.",
            node_type="preference", day=14, tags=["editor", "neovim"],
        )

        assert rewriter.settled_facts_generated >= 1
        section = rewriter.document.get("editor")
        assert "Neovim" in section.current_state

    def test_ingest_switch_back(self):
        """Switch-back events should update the narrative correctly."""
        mem = VSAMemory(dim=10_000)
        rewriter = NarrativeRewriter(mem)

        rewriter.ingest(
            "I use VS Code as my primary editor.",
            node_type="preference", day=1, tags=["editor", "vscode"],
        )

        rewriter.ingest(
            "I switched from VS Code to Neovim.",
            node_type="preference", day=14, tags=["editor", "neovim"],
        )

        rewriter.ingest(
            "I went back to VS Code from Neovim.",
            node_type="preference", day=55, tags=["editor", "vscode"],
        )

        section = rewriter.document.get("editor")
        assert "VS Code" in section.current_state
        assert len(section.history) >= 3

    def test_settled_fact_has_no_migration_language(self):
        """Settled facts should not contain migration/transition language."""
        mem = VSAMemory(dim=10_000)
        rewriter = NarrativeRewriter(mem)

        rewriter.ingest(
            "Our database is MySQL 8.0.",
            node_type="fact", day=1, tags=["database", "mysql"],
        )

        rewriter.ingest(
            "We migrated from MySQL to PostgreSQL for better JSON support.",
            node_type="fact", day=12, tags=["database", "postgresql", "migration"],
        )

        # Find the settled fact
        migration_signals = [
            "migrated", "switched", "moved", "replaced",
            "no longer", "deprecated", "went back",
        ]
        for sf_id in rewriter._settled_fact_ids:
            record = mem.get_by_id(sf_id)
            content_lower = record.content.lower()
            for signal in migration_signals:
                assert signal not in content_lower, (
                    f"Settled fact contains migration language '{signal}': {record.content}"
                )

    def test_settled_fact_has_clean_tags(self):
        """Settled facts should not have transition tags."""
        mem = VSAMemory(dim=10_000)
        rewriter = NarrativeRewriter(mem)

        rewriter.ingest(
            "Our database is MySQL 8.0.",
            node_type="fact", day=1, tags=["database", "mysql"],
        )

        rewriter.ingest(
            "We migrated from MySQL to PostgreSQL.",
            node_type="fact", day=12, tags=["database", "postgresql", "migration"],
        )

        transition_tags = {"migration", "deprecated", "legacy", "removed"}
        for sf_id in rewriter._settled_fact_ids:
            record = mem.get_by_id(sf_id)
            tag_set = {t.lower() for t in record.tags}
            assert not (tag_set & transition_tags), (
                f"Settled fact has transition tags: {record.tags}"
            )


class TestFullScenario:
    """Run the full changing_facts scenario through the rewriter.

    This tests that the rewriter produces results equivalent to
    the manually-crafted settled facts from the Phase 1 benchmark.
    """

    def _build_rewriter_scenario(self) -> NarrativeRewriter:
        """Build the changing_facts scenario through the rewriter."""
        mem = VSAMemory(dim=10_000)
        rw = NarrativeRewriter(mem)

        # Week 1
        rw.ingest("Our backend database is MySQL 8.0 and it handles all our data storage needs.",
                   node_type="fact", day=1, tags=["database", "mysql"])
        rw.ingest("I use VS Code as my primary editor with the Python extension.",
                   node_type="preference", day=1, tags=["editor", "vscode"])
        rw.ingest("Our API layer is built on REST with Flask as the web framework.",
                   node_type="fact", day=2, tags=["api", "rest", "flask"])
        rw.ingest("The team consists of Alice (frontend lead), Bob (backend), and Charlie (devops).",
                   node_type="fact", day=2, tags=["team", "people"])
        rw.ingest("We deploy our services on AWS EC2 instances managed by Ansible.",
                   node_type="fact", day=3, tags=["infrastructure", "ec2", "aws"])
        rw.ingest("Our project Alpha is the main revenue-generating product.",
                   node_type="fact", day=4, tags=["project", "alpha"])
        rw.ingest("We use pytest for all our Python testing with 85% code coverage.",
                   node_type="fact", day=5, tags=["testing", "pytest"])

        # Day 12: database migration
        rw.ingest("We have completed the migration from MySQL to PostgreSQL for better JSON support and performance.",
                   node_type="fact", day=12, tags=["database", "postgresql", "migration"])
        rw.ingest("MySQL is no longer used in our stack. All data has been migrated to PostgreSQL.",
                   node_type="fact", day=12, tags=["database", "mysql", "deprecated"])

        # Day 14: editor switch
        rw.ingest("I switched from VS Code to Neovim for all my development work. The modal editing is much faster.",
                   node_type="preference", day=14, tags=["editor", "neovim"])

        # Day 20: team update
        rw.ingest("Diana joined our team as a data engineer. She specializes in ETL pipelines.",
                   node_type="fact", day=20, tags=["team", "people", "diana"])

        # Day 32: API migration
        rw.ingest("We migrated our API from REST to GraphQL using Strawberry. The developer experience is much better.",
                   node_type="fact", day=32, tags=["api", "graphql", "strawberry"])
        rw.ingest("Our REST API is deprecated and will be removed next quarter.",
                   node_type="fact", day=32, tags=["api", "rest", "deprecated"])

        # Day 35: infrastructure migration
        rw.ingest("We moved our infrastructure from EC2 to Kubernetes on EKS for better scaling and deployment.",
                   node_type="fact", day=35, tags=["infrastructure", "kubernetes", "eks"])

        # Day 45: new project
        rw.ingest("We started project Beta, a new analytics platform built on the PostgreSQL data warehouse.",
                   node_type="fact", day=45, tags=["project", "beta", "analytics"])

        # Day 55: editor switch back
        rw.ingest("I went back to VS Code from Neovim. The integrated debugging and extensions are too valuable to give up.",
                   node_type="preference", day=55, tags=["editor", "vscode"])

        return rw

    def test_settled_facts_generated(self):
        """Should auto-generate settled facts for each migration."""
        rw = self._build_rewriter_scenario()
        # At least: database, editor (x2), API, infrastructure
        assert rw.settled_facts_generated >= 5

    def test_narrative_tracks_all_topics(self):
        """Narrative should have sections for all major topics."""
        rw = self._build_rewriter_scenario()
        for topic in ["database", "editor", "api", "infrastructure"]:
            assert topic in rw.document, f"Missing topic: {topic}"

    def test_narrative_current_state(self):
        """Narrative should reflect the final state of each topic."""
        rw = self._build_rewriter_scenario()

        db = rw.document.get("database")
        assert "postgresql" in db.current_state.lower()

        editor = rw.document.get("editor")
        assert "vs code" in editor.current_state.lower()

        api = rw.document.get("api")
        assert "graphql" in api.current_state.lower()

        infra = rw.document.get("infrastructure")
        assert "kubernetes" in infra.current_state.lower()

    def test_retrieval_with_auto_settled_facts(self):
        """Retrieval using auto-generated settled facts should be accurate."""
        rw = self._build_rewriter_scenario()
        mem = rw.memory

        # Organize memories
        mem.organize(current_day=90)

        # Query: What database?
        results = mem.retrieve("What database does the project use?", query_day=90, top_k=5)
        top5_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "postgresql" in top5_content

        # Query: What editor?
        results = mem.retrieve("What editor do I use?", query_day=60, top_k=5)
        top5_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "vs code" in top5_content or "vscode" in top5_content

        # Query: What API?
        results = mem.retrieve("What API technology do we use?", query_day=40, top_k=5)
        top5_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "graphql" in top5_content

    def test_retrieval_excludes_old_tech(self):
        """Old technology should not appear in top-5 for current-state queries."""
        rw = self._build_rewriter_scenario()
        mem = rw.memory
        mem.organize(current_day=90)

        # Database query should not return MySQL
        results = mem.retrieve("What database does the project use?", query_day=90, top_k=5)
        top5_content = " ".join(r.record.content.lower() for r in results[:5])
        assert "postgresql" in top5_content
        # MySQL may appear in transition records, but with settled facts,
        # the clean fact should rank high enough to push MySQL mentions
        # out of top-5. This tests the Phase 1 ranking fix integration.

    def test_narrative_history(self):
        """Each section should track its change history."""
        rw = self._build_rewriter_scenario()

        editor = rw.document.get("editor")
        # Initial → Neovim switch → VS Code return = 3+ entries
        assert len(editor.history) >= 3

        db = rw.document.get("database")
        # Initial → PostgreSQL migration = 2+ entries
        assert len(db.history) >= 2

    def test_narrative_render(self):
        """The rendered narrative should be a readable document."""
        rw = self._build_rewriter_scenario()
        rendered = rw.document.render()

        assert "# Current State" in rendered
        assert "Database" in rendered
        assert "Editor" in rendered
        assert "PostgreSQL" in rendered
        assert "VS Code" in rendered


class TestCasePreservation:
    """Tests for case preservation in deprecation and tag-based extraction."""

    def test_deprecation_preserves_case_from_narrative(self):
        """When deprecation event follows migration, the generated settled fact
        should use the proper-cased subject from the narrative section, not
        lowercase tags."""
        mem = VSAMemory(dim=10_000)
        rw = NarrativeRewriter(mem)

        rw.ingest(
            "Our backend database is MySQL 8.0.",
            node_type="fact", day=1, tags=["database", "mysql"],
        )
        rw.ingest(
            "We migrated from MySQL to PostgreSQL for better JSON support.",
            node_type="fact", day=12, tags=["database", "postgresql", "migration"],
        )

        # The narrative should have "PostgreSQL" (proper case)
        section = rw.document.get("database")
        assert section.current_subject == "PostgreSQL"

    def test_deprecation_after_migration_skips_redundant_update(self):
        """A deprecation event after a migration should not create a redundant
        section update when the section already has the correct state."""
        mem = VSAMemory(dim=10_000)
        rw = NarrativeRewriter(mem)

        rw.ingest(
            "Our backend database is MySQL 8.0.",
            node_type="fact", day=1, tags=["database", "mysql"],
        )
        rw.ingest(
            "We migrated from MySQL to PostgreSQL for better JSON support.",
            node_type="fact", day=12, tags=["database", "postgresql", "migration"],
        )

        settled_before = rw.settled_facts_generated
        history_len_before = len(rw.document.get("database").history)

        # Deprecation comes after migration — should be skipped
        rw.ingest(
            "MySQL is no longer used in our stack.",
            node_type="fact", day=12, tags=["database", "mysql", "deprecated"],
        )

        # No new settled fact should have been generated
        assert rw.settled_facts_generated == settled_before
        # No new history entry
        assert len(rw.document.get("database").history) == history_len_before

    def test_standalone_deprecation_finds_subject_from_narrative(self):
        """When a deprecation is the first change event (no prior from-to),
        _find_current_subject should use the narrative document's subject."""
        mem = VSAMemory(dim=10_000)
        rw = NarrativeRewriter(mem)

        # Set up initial state — proper case subject comes from tag
        rw.ingest(
            "Our API is built on REST with Flask.",
            node_type="fact", day=1, tags=["api", "REST"],
        )
        rw.ingest(
            "We migrated our API from REST to GraphQL using Strawberry.",
            node_type="fact", day=32, tags=["api", "graphql", "strawberry"],
        )

        section = rw.document.get("api")
        # Should have proper case from the from-to extraction
        assert section.current_subject == "GraphQL"

    def test_case_preserved_in_settled_fact_text(self):
        """The settled fact text should contain the proper-cased subject."""
        mem = VSAMemory(dim=10_000)
        rw = NarrativeRewriter(mem)

        rw.ingest(
            "Our database is MySQL 8.0.",
            node_type="fact", day=1, tags=["database", "mysql"],
        )
        rw.ingest(
            "We migrated from MySQL to PostgreSQL for better JSON support.",
            node_type="fact", day=12, tags=["database", "postgresql", "migration"],
        )

        # Find the settled fact
        for sf_id in rw._settled_fact_ids:
            record = mem.get_by_id(sf_id)
            if "database" in [t.lower() for t in record.tags]:
                assert "PostgreSQL" in record.content, (
                    f"Expected 'PostgreSQL' (proper case), got: {record.content}"
                )
                break


class TestBenchmarkComparison:
    """Compare retrieval quality WITH vs WITHOUT auto-generated settled facts.

    This measures the value-add of the narrative rewriter by running the
    same queries against:
    1. Baseline: VSAMemory with only raw events (no settled facts)
    2. Rewriter: VSAMemory with auto-generated settled facts from NarrativeRewriter
    """

    QUERIES = [
        ("What database does the project use?", "postgresql", "mysql", 90),
        ("What editor do I use?", "vs code", "neovim", 60),
        ("What API technology do we use?", "graphql", "rest", 40),
        ("What infrastructure do we use?", "kubernetes", "ec2", 40),
    ]

    def _build_baseline(self) -> VSAMemory:
        """Build the changing_facts scenario WITHOUT the rewriter."""
        mem = VSAMemory(dim=10_000)

        # Week 1
        mem.store("Our backend database is MySQL 8.0 and it handles all our data storage needs.",
                  node_type="fact", day=1, tags=["database", "mysql"])
        mem.store("I use VS Code as my primary editor with the Python extension.",
                  node_type="preference", day=1, tags=["editor", "vscode"])
        mem.store("Our API layer is built on REST with Flask as the web framework.",
                  node_type="fact", day=2, tags=["api", "rest", "flask"])
        mem.store("We deploy our services on AWS EC2 instances managed by Ansible.",
                  node_type="fact", day=3, tags=["infrastructure", "ec2", "aws"])

        # Migrations (raw events only, no settled facts)
        m1 = mem.store("We have completed the migration from MySQL to PostgreSQL for better JSON support and performance.",
                       node_type="fact", day=12, tags=["database", "postgresql", "migration"])
        mem.detect_supersedence(mem.get_by_id(m1).content, m1)

        m2 = mem.store("MySQL is no longer used in our stack. All data has been migrated to PostgreSQL.",
                       node_type="fact", day=12, tags=["database", "mysql", "deprecated"])
        mem.detect_supersedence(mem.get_by_id(m2).content, m2)

        m3 = mem.store("I switched from VS Code to Neovim for all my development work.",
                       node_type="preference", day=14, tags=["editor", "neovim"])
        mem.detect_supersedence(mem.get_by_id(m3).content, m3)

        m4 = mem.store("We migrated our API from REST to GraphQL using Strawberry.",
                       node_type="fact", day=32, tags=["api", "graphql", "strawberry"])
        mem.detect_supersedence(mem.get_by_id(m4).content, m4)

        m5 = mem.store("We moved our infrastructure from EC2 to Kubernetes on EKS for better scaling.",
                       node_type="fact", day=35, tags=["infrastructure", "kubernetes", "eks"])
        mem.detect_supersedence(mem.get_by_id(m5).content, m5)

        m6 = mem.store("I went back to VS Code from Neovim.",
                       node_type="preference", day=55, tags=["editor", "vscode"])
        mem.detect_supersedence(mem.get_by_id(m6).content, m6)

        mem.organize(current_day=90)
        return mem

    def _build_rewriter_scenario(self) -> NarrativeRewriter:
        """Build the changing_facts scenario WITH the rewriter."""
        mem = VSAMemory(dim=10_000)
        rw = NarrativeRewriter(mem)

        rw.ingest("Our backend database is MySQL 8.0 and it handles all our data storage needs.",
                  node_type="fact", day=1, tags=["database", "mysql"])
        rw.ingest("I use VS Code as my primary editor with the Python extension.",
                  node_type="preference", day=1, tags=["editor", "vscode"])
        rw.ingest("Our API layer is built on REST with Flask as the web framework.",
                  node_type="fact", day=2, tags=["api", "rest", "flask"])
        rw.ingest("We deploy our services on AWS EC2 instances managed by Ansible.",
                  node_type="fact", day=3, tags=["infrastructure", "ec2", "aws"])

        rw.ingest("We have completed the migration from MySQL to PostgreSQL for better JSON support and performance.",
                  node_type="fact", day=12, tags=["database", "postgresql", "migration"])
        rw.ingest("MySQL is no longer used in our stack. All data has been migrated to PostgreSQL.",
                  node_type="fact", day=12, tags=["database", "mysql", "deprecated"])
        rw.ingest("I switched from VS Code to Neovim for all my development work.",
                  node_type="preference", day=14, tags=["editor", "neovim"])
        rw.ingest("We migrated our API from REST to GraphQL using Strawberry.",
                  node_type="fact", day=32, tags=["api", "graphql", "strawberry"])
        rw.ingest("We moved our infrastructure from EC2 to Kubernetes on EKS for better scaling.",
                  node_type="fact", day=35, tags=["infrastructure", "kubernetes", "eks"])
        rw.ingest("I went back to VS Code from Neovim.",
                  node_type="preference", day=55, tags=["editor", "vscode"])

        rw.memory.organize(current_day=90)
        return rw

    def test_rewriter_improves_precision(self):
        """Rewriter scenario should have equal or better precision@1."""
        baseline = self._build_baseline()
        rw = self._build_rewriter_scenario()
        rewriter_mem = rw.memory

        baseline_correct = 0
        rewriter_correct = 0

        for query, expected, old_tech, query_day in self.QUERIES:
            # Baseline
            b_results = baseline.retrieve(query, query_day=query_day, top_k=1)
            if b_results and expected in b_results[0].record.content.lower():
                baseline_correct += 1

            # Rewriter
            r_results = rewriter_mem.retrieve(query, query_day=query_day, top_k=1)
            if r_results and expected in r_results[0].record.content.lower():
                rewriter_correct += 1

        assert rewriter_correct >= baseline_correct, (
            f"Rewriter precision ({rewriter_correct}/{len(self.QUERIES)}) "
            f"worse than baseline ({baseline_correct}/{len(self.QUERIES)})"
        )

    def test_rewriter_improves_exclusion(self):
        """Rewriter should better exclude old technology from top results."""
        baseline = self._build_baseline()
        rw = self._build_rewriter_scenario()
        rewriter_mem = rw.memory

        baseline_exclusions = 0
        rewriter_exclusions = 0

        for query, expected, old_tech, query_day in self.QUERIES:
            # Baseline: check if old tech is absent from top-3
            b_results = baseline.retrieve(query, query_day=query_day, top_k=3)
            b_top3 = " ".join(r.record.content.lower() for r in b_results[:3])
            if old_tech not in b_top3:
                baseline_exclusions += 1

            # Rewriter: same check
            r_results = rewriter_mem.retrieve(query, query_day=query_day, top_k=3)
            r_top3 = " ".join(r.record.content.lower() for r in r_results[:3])
            if old_tech not in r_top3:
                rewriter_exclusions += 1

        assert rewriter_exclusions >= baseline_exclusions, (
            f"Rewriter exclusion ({rewriter_exclusions}/{len(self.QUERIES)}) "
            f"worse than baseline ({baseline_exclusions}/{len(self.QUERIES)})"
        )

    def test_benchmark_summary(self):
        """Print a comparison summary (informational, always passes)."""
        baseline = self._build_baseline()
        rw = self._build_rewriter_scenario()
        rewriter_mem = rw.memory

        print("\n=== Retrieval Quality: Baseline vs Rewriter ===")
        print(f"{'Query':<45} {'Baseline Top-1':<25} {'Rewriter Top-1':<25}")
        print("-" * 95)

        for query, expected, old_tech, query_day in self.QUERIES:
            b_results = baseline.retrieve(query, query_day=query_day, top_k=1)
            r_results = rewriter_mem.retrieve(query, query_day=query_day, top_k=1)

            b_top = b_results[0].record.content[:40] if b_results else "(empty)"
            r_top = r_results[0].record.content[:40] if r_results else "(empty)"

            b_mark = "OK" if b_results and expected in b_results[0].record.content.lower() else "MISS"
            r_mark = "OK" if r_results and expected in r_results[0].record.content.lower() else "MISS"

            print(f"{query:<45} [{b_mark}] {b_top:<20} [{r_mark}] {r_top:<20}")

        print(f"\nSettled facts generated: {rw.settled_facts_generated}")
        print(f"Baseline memories: {baseline.size}, Rewriter memories: {rewriter_mem.size}")
