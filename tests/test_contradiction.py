"""Tests for contradiction detection module."""

import pytest

from prme.organizer.contradiction import ContentContradictionDetector


class TestHasContradictionSignal:
    def test_detects_migrated_from_to(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We migrated from MySQL to PostgreSQL")

    def test_detects_migrate_variant(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We migrate MySQL to PostgreSQL next week")

    def test_detects_switched_to(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("I switched from VS Code to Neovim")

    def test_detects_switch_variant(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We switch React to Vue next sprint")

    def test_detects_moved_to(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We moved from EC2 to Kubernetes")

    def test_detects_replaced_with(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We replaced Flask with FastAPI")

    def test_detects_no_longer_using(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We are no longer using MySQL")

    def test_detects_no_longer_used(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("MySQL is no longer used in our stack")

    def test_detects_deprecated(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("Our REST API is deprecated")

    def test_detects_went_back_to(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("I went back to VS Code from Neovim")

    def test_detects_abandoned(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We abandoned the old monolith architecture")

    def test_detects_phased_out(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("Jenkins was phased out in favor of GitHub Actions")

    def test_detects_phase_out(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We will phase out the legacy system")

    def test_detects_obsolete(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("The old API is now obsolete")

    def test_detects_retired(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("The service is retired")

    def test_no_signal_in_normal_content(self):
        d = ContentContradictionDetector()
        assert not d.has_contradiction_signal("We use PostgreSQL for our database")

    def test_no_signal_in_unrelated_text(self):
        d = ContentContradictionDetector()
        assert not d.has_contradiction_signal("The weather is nice today")

    def test_case_insensitive(self):
        d = ContentContradictionDetector()
        assert d.has_contradiction_signal("We MIGRATED FROM mysql TO postgresql")


class TestExtractSupersededTerms:
    def test_extracts_from_migrated(self):
        d = ContentContradictionDetector()
        terms = d.extract_superseded_terms("We migrated from MySQL to PostgreSQL")
        assert "mysql" in terms

    def test_extracts_from_switched(self):
        d = ContentContradictionDetector()
        terms = d.extract_superseded_terms("I switched from VS Code to Neovim")
        assert "vs code" in terms

    def test_extracts_from_replaced(self):
        d = ContentContradictionDetector()
        terms = d.extract_superseded_terms("We replaced Flask with FastAPI")
        assert "flask" in terms

    def test_extracts_from_moved(self):
        d = ContentContradictionDetector()
        terms = d.extract_superseded_terms("We moved from EC2 to Kubernetes")
        assert "ec2" in terms

    def test_no_terms_from_no_longer_using(self):
        d = ContentContradictionDetector()
        terms = d.extract_superseded_terms("We are no longer using MySQL")
        # "no longer using" has no capture group
        assert terms == []

    def test_no_terms_from_deprecated(self):
        d = ContentContradictionDetector()
        terms = d.extract_superseded_terms("The REST API is deprecated")
        assert terms == []

    def test_multiple_terms(self):
        d = ContentContradictionDetector()
        terms = d.extract_superseded_terms(
            "We migrated from MySQL to PostgreSQL and switched from Flask to FastAPI"
        )
        assert "mysql" in terms
        assert "flask" in terms


class TestFindSupersededContent:
    def test_finds_matching_node(self):
        d = ContentContradictionDetector()
        existing = [
            ("id1", "Our database is MySQL 8.0"),
            ("id2", "We use pytest for testing"),
        ]
        result = d.find_superseded_content(
            "We migrated from MySQL to PostgreSQL", existing
        )
        assert "id1" in result
        assert "id2" not in result

    def test_no_match_when_no_signal(self):
        d = ContentContradictionDetector()
        existing = [
            ("id1", "Our database is MySQL 8.0"),
        ]
        result = d.find_superseded_content(
            "We use PostgreSQL for our database", existing
        )
        assert result == []

    def test_no_match_when_no_terms(self):
        d = ContentContradictionDetector()
        existing = [
            ("id1", "Our database is MySQL 8.0"),
        ]
        # "deprecated" has no capture group, so no terms extracted
        result = d.find_superseded_content(
            "The old system is deprecated", existing
        )
        assert result == []

    def test_multiple_matches(self):
        d = ContentContradictionDetector()
        existing = [
            ("id1", "MySQL is our primary database"),
            ("id2", "We have a MySQL replica for reads"),
            ("id3", "Redis is our cache layer"),
        ]
        result = d.find_superseded_content(
            "We migrated from MySQL to PostgreSQL", existing
        )
        assert "id1" in result
        assert "id2" in result
        assert "id3" not in result

    def test_case_insensitive_matching(self):
        d = ContentContradictionDetector()
        existing = [
            ("id1", "Our database is MYSQL 8.0"),
        ]
        result = d.find_superseded_content(
            "We migrated from mysql to PostgreSQL", existing
        )
        assert "id1" in result

    def test_empty_existing_list(self):
        d = ContentContradictionDetector()
        result = d.find_superseded_content(
            "We migrated from MySQL to PostgreSQL", []
        )
        assert result == []
