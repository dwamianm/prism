"""Tests for Scope enum expansion to 6 members per RFC-0004 S3.

Verifies:
- Scope enum has exactly 6 members
- ORG member no longer exists (AttributeError)
- ORGANISATION, AGENT, SYSTEM, SANDBOX resolve correctly
- LLM extraction prompts reference all 6 scope values
- Schema field descriptions reference all 6 scope values
"""

import pytest


class TestScopeEnumExpansion:
    """Scope enum has 6 members matching RFC-0004 S3."""

    def test_scope_has_six_members(self):
        from prme.types import Scope

        assert len(Scope) == 6, f"Expected 6 Scope members, got {len(Scope)}"

    def test_scope_org_does_not_exist(self):
        from prme.types import Scope

        with pytest.raises(AttributeError):
            _ = Scope.ORG

    def test_scope_organisation_value(self):
        from prme.types import Scope

        assert Scope.ORGANISATION.value == "organisation"

    def test_scope_agent_value(self):
        from prme.types import Scope

        assert Scope.AGENT.value == "agent"

    def test_scope_system_value(self):
        from prme.types import Scope

        assert Scope.SYSTEM.value == "system"

    def test_scope_sandbox_value(self):
        from prme.types import Scope

        assert Scope.SANDBOX.value == "sandbox"

    def test_scope_personal_unchanged(self):
        from prme.types import Scope

        assert Scope.PERSONAL.value == "personal"

    def test_scope_project_unchanged(self):
        from prme.types import Scope

        assert Scope.PROJECT.value == "project"

    def test_scope_construction_from_string(self):
        """All 6 scope values can be constructed from their string representation."""
        from prme.types import Scope

        assert Scope("personal") == Scope.PERSONAL
        assert Scope("project") == Scope.PROJECT
        assert Scope("organisation") == Scope.ORGANISATION
        assert Scope("agent") == Scope.AGENT
        assert Scope("system") == Scope.SYSTEM
        assert Scope("sandbox") == Scope.SANDBOX


class TestExtractionPromptScopes:
    """LLM extraction prompts reference all 6 scope values."""

    def test_extraction_prompt_contains_organisation(self):
        from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT

        assert "organisation" in EXTRACTION_SYSTEM_PROMPT

    def test_extraction_prompt_contains_agent(self):
        from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT

        assert '"agent"' in EXTRACTION_SYSTEM_PROMPT

    def test_extraction_prompt_contains_system(self):
        from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT

        assert '"system"' in EXTRACTION_SYSTEM_PROMPT

    def test_extraction_prompt_contains_sandbox(self):
        from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT

        assert '"sandbox"' in EXTRACTION_SYSTEM_PROMPT

    def test_extraction_prompt_no_org_scope(self):
        """Prompt should not reference 'org' as a scope value."""
        from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT

        # The prompt section for scope classification should not have "org"
        # as a standalone scope value (it may appear as part of "organisation")
        lines = EXTRACTION_SYSTEM_PROMPT.split("\n")
        for line in lines:
            if line.strip().startswith('- "org"'):
                pytest.fail(f"Found old 'org' scope value in prompt: {line}")


class TestSchemaFieldDescriptions:
    """Schema field descriptions reference all 6 scope values."""

    def test_extracted_entity_scope_mentions_organisation(self):
        from prme.ingestion.schema import ExtractedEntity

        desc = ExtractedEntity.model_fields["scope"].description
        assert "organisation" in desc

    def test_extracted_entity_scope_mentions_agent(self):
        from prme.ingestion.schema import ExtractedEntity

        desc = ExtractedEntity.model_fields["scope"].description
        assert "agent" in desc

    def test_extracted_entity_scope_mentions_sandbox(self):
        from prme.ingestion.schema import ExtractedEntity

        desc = ExtractedEntity.model_fields["scope"].description
        assert "sandbox" in desc

    def test_extracted_fact_scope_mentions_organisation(self):
        from prme.ingestion.schema import ExtractedFact

        desc = ExtractedFact.model_fields["scope"].description
        assert "organisation" in desc

    def test_extracted_fact_scope_mentions_agent(self):
        from prme.ingestion.schema import ExtractedFact

        desc = ExtractedFact.model_fields["scope"].description
        assert "agent" in desc

    def test_extracted_fact_scope_mentions_sandbox(self):
        from prme.ingestion.schema import ExtractedFact

        desc = ExtractedFact.model_fields["scope"].description
        assert "sandbox" in desc
