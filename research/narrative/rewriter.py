"""Narrative Rewriter — watches memory events and auto-generates settled facts.

The rewriter wraps a VSAMemory instance and intercepts state-change events
(migrations, switches, deprecations). When detected, it:
1. Extracts what changed (topic, old subject, new subject, reason)
2. Generates a clean settled-fact summary
3. Stores the settled fact back into VSAMemory
4. Updates the narrative document

Two rewriter backends:
- RuleBasedRewriter: regex + template (no LLM, deterministic)
- LLMRewriter: pluggable LLM call (Phase 2b, not yet implemented)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, Callable

from research.narrative.document import NarrativeDocument, Section
from research.vsa.memory import VSAMemory, MemoryRecord


@dataclass
class StateChange:
    """A detected state change extracted from a memory event."""

    topic: str  # Primary topic (from tags)
    old_subject: str | None  # What was replaced (e.g., "MySQL")
    new_subject: str  # What replaced it (e.g., "PostgreSQL")
    reason: str  # Why (e.g., "for better JSON support")
    change_type: str  # migration, switch, deprecation, addition, return
    source_memory_id: str
    day: int
    tags: list[str]


class SettledFactGenerator(Protocol):
    """Protocol for generating settled fact text from a state change."""

    def generate(self, change: StateChange, section: Section | None) -> str: ...


class RuleBasedGenerator:
    """Generate settled facts using regex extraction + templates.

    No LLM dependency. Deterministic and fast. Handles common patterns:
    - "migrated from X to Y for Z" → "Our {topic} is Y with Z."
    - "switched from X to Y" → "My {topic} is Y."
    - "X is no longer used" → (no new settled fact needed, just marks old)
    - "went back to X from Y" → "My {topic} is X."
    """

    # Patterns for extracting the "new subject" and reason from content
    _FROM_TO_PATTERN = re.compile(
        r'(?:migrated?|migration|switched|moved|replaced|changed|completed\s+\w+\s+migration)'
        r'\s+(?:\w+\s+)*?from\s+([\w\s]+?)\s+to\s+([\w\s]+?)'
        r'(?:\s+(?:for|using|on|with)\s+(.*?))?'
        r'(?:\.|,|$)',
        re.IGNORECASE,
    )

    _BACK_TO_PATTERN = re.compile(
        r'(?:went\s+back\s+to|returned\s+to)\s+([\w\s]+?)'
        r'(?:\s+from\s+([\w\s]+?))?(?:\.|,|$)',
        re.IGNORECASE,
    )

    _DEPRECATED_PATTERN = re.compile(
        r'([\w\s]+?)\s+is\s+(?:no\s+longer|deprecated)',
        re.IGNORECASE,
    )

    # Topic → possessive prefix mapping
    _TOPIC_PREFIX = {
        "editor": "My primary",
        "preference": "My",
        "database": "Our",
        "api": "Our",
        "infrastructure": "Our",
        "testing": "Our",
        "team": "Our",
    }

    def generate(self, change: StateChange, section: Section | None) -> str:
        """Generate a clean settled fact from a state change."""
        prefix = self._TOPIC_PREFIX.get(change.topic, "Our")
        subject = change.new_subject.strip()
        reason = change.reason.strip() if change.reason else ""

        # Build the settled fact
        if change.change_type == "return":
            # "went back to X" — emphasize the current choice
            return f"{prefix} {change.topic} is {subject}."

        if reason:
            # Clean up reason — remove trailing noise
            reason = reason.rstrip(".,;: ")
            return f"{prefix} {change.topic} is {subject} with {reason}."

        return f"{prefix} {change.topic} is {subject}."

    def extract_reason_from_content(self, content: str) -> str:
        """Extract the reason/benefit clause from content."""
        # Look for "for ..." or "because ..." clauses
        for_match = re.search(
            r'\.\s*(.*?)$|for\s+(.*?)(?:\.|$)',
            content,
            re.IGNORECASE,
        )
        if for_match:
            reason = for_match.group(1) or for_match.group(2)
            if reason:
                return reason.strip().rstrip(".")
        return ""


class NarrativeRewriter:
    """Watches memory events and maintains a narrative document.

    Usage:
        mem = VSAMemory()
        rewriter = NarrativeRewriter(mem)

        # Store events through the rewriter instead of directly
        rewriter.ingest("Our database is MySQL 8.0.", node_type="fact",
                       day=1, tags=["database", "mysql"])

        # Migration event — rewriter auto-generates settled fact
        rewriter.ingest("We migrated from MySQL to PostgreSQL for better JSON support.",
                       node_type="fact", day=12, tags=["database", "postgresql", "migration"])

        # Check narrative
        print(rewriter.document.render())
    """

    # Migration signal words (shared with VSAMemory.retrieve)
    _MIGRATION_SIGNALS = (
        "migrated", "switched", "moved", "replaced",
        "no longer", "deprecated", "went back",
        "changed from", "migration",
    )

    # Tags that indicate transition records (not current-state facts)
    _TRANSITION_TAGS = {"migration", "deprecated", "legacy", "removed"}

    def __init__(
        self,
        memory: VSAMemory,
        generator: SettledFactGenerator | None = None,
    ) -> None:
        self.memory = memory
        self.document = NarrativeDocument()
        self.generator = generator or RuleBasedGenerator()
        self._settled_fact_ids: list[str] = []  # Track auto-generated facts

    @property
    def settled_facts_generated(self) -> int:
        """Number of settled facts auto-generated."""
        return len(self._settled_fact_ids)

    def ingest(
        self,
        content: str,
        node_type: str = "fact",
        day: int = 0,
        confidence: float = 1.0,
        scope: str = "personal",
        agent: str = "user",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Ingest a memory event through the rewriter.

        Stores the memory in VSAMemory, detects state changes, and
        auto-generates settled facts when migrations are detected.

        Returns the memory ID of the original event.
        """
        tags = tags or []

        # Store in VSAMemory
        memory_id = self.memory.store(
            content=content,
            node_type=node_type,
            day=day,
            confidence=confidence,
            scope=scope,
            agent=agent,
            tags=tags,
            metadata=metadata,
        )

        # Run supersedence detection
        superseded_ids = self.memory.detect_supersedence(content, memory_id)

        # Determine primary topic from tags (first non-transition tag)
        topic = self._extract_topic(tags)

        # Detect if this is a state change
        change = self._detect_change(content, memory_id, day, tags, topic, superseded_ids)

        if change:
            self._process_change(change)
        elif topic:
            # Not a state change — just a new fact. Update or create section.
            self._process_new_fact(content, memory_id, day, tags, topic, node_type)

        return memory_id

    def _extract_topic(self, tags: list[str]) -> str | None:
        """Extract primary topic from tags (first non-transition tag)."""
        for tag in tags:
            if tag.lower() not in self._TRANSITION_TAGS:
                return tag.lower()
        return None

    def _detect_change(
        self,
        content: str,
        memory_id: str,
        day: int,
        tags: list[str],
        topic: str | None,
        superseded_ids: list[str],
    ) -> StateChange | None:
        """Detect if a memory event represents a state change."""
        content_lower = content.lower()

        # Check for migration signals
        has_migration = any(sig in content_lower for sig in self._MIGRATION_SIGNALS)
        has_transition_tag = bool(self._TRANSITION_TAGS & {t.lower() for t in tags})

        if not has_migration and not has_transition_tag:
            return None

        if not topic:
            return None

        # Extract old/new subjects and reason using regex
        old_subject = None
        new_subject = None
        reason = ""
        change_type = "migration"

        # Try "went back to X from Y" first
        back_match = RuleBasedGenerator._BACK_TO_PATTERN.search(content)
        if back_match:
            new_subject = back_match.group(1).strip()
            old_subject = back_match.group(2).strip() if back_match.group(2) else None
            change_type = "return"
        else:
            # Try "from X to Y"
            from_to_match = RuleBasedGenerator._FROM_TO_PATTERN.search(content)
            if from_to_match:
                old_subject = from_to_match.group(1).strip()
                new_subject = from_to_match.group(2).strip()
                reason = from_to_match.group(3) or ""
                change_type = "migration"

        # Try "X is deprecated/no longer"
        if not new_subject:
            dep_match = RuleBasedGenerator._DEPRECATED_PATTERN.search(content)
            if dep_match:
                old_subject = dep_match.group(1).strip()
                change_type = "deprecation"
                # For deprecations, the new subject comes from the latest
                # non-deprecated memory with the same topic
                new_subject = self._find_current_subject(topic, day)

        # If we still can't extract subjects, try to use tags
        if not new_subject:
            # Use the most specific tag (not the topic itself, not transition tags)
            specific_tags = [
                t for t in tags
                if t.lower() != topic
                and t.lower() not in self._TRANSITION_TAGS
            ]
            if specific_tags:
                new_subject = specific_tags[0]
                change_type = "migration"

        if not new_subject:
            return None

        # Extract reason from content if not found by regex
        if not reason:
            # Look for "for better..." or trailing benefit clause
            reason_match = re.search(
                r'for\s+(better\s+.*?|improved\s+.*?|faster\s+.*?)(?:\.|,|$)',
                content,
                re.IGNORECASE,
            )
            if reason_match:
                reason = reason_match.group(1).strip().rstrip(".")

        return StateChange(
            topic=topic,
            old_subject=old_subject,
            new_subject=new_subject,
            reason=reason,
            change_type=change_type,
            source_memory_id=memory_id,
            day=day,
            tags=tags,
        )

    def _find_current_subject(self, topic: str, before_day: int) -> str | None:
        """Find the current subject for a topic.

        Checks the narrative document first (preserves proper case from
        earlier from-to extraction), then falls back to memory tags.
        """
        # Prefer the narrative document — it has proper-cased subjects
        section = self.document.get(topic)
        if section and section.current_subject:
            return section.current_subject

        # Fallback: scan memories for this topic
        candidates = []
        for rec in self.memory._memories:
            if rec.lifecycle_state in ("superseded", "archived", "deprecated"):
                continue
            if topic in [t.lower() for t in rec.tags]:
                if rec.day <= before_day:
                    candidates.append(rec)

        if candidates:
            candidates.sort(key=lambda r: r.day, reverse=True)
            # Try to extract a proper-cased name from content via from-to regex
            from_to = RuleBasedGenerator._FROM_TO_PATTERN.search(candidates[0].content)
            if from_to and from_to.group(2):
                return from_to.group(2).strip()
            # Fall back to tags
            for tag in candidates[0].tags:
                if tag.lower() != topic and tag.lower() not in self._TRANSITION_TAGS:
                    return tag
        return None

    def _process_change(self, change: StateChange) -> None:
        """Process a detected state change: update narrative + generate settled fact."""
        section = self.document.get(change.topic)

        # Skip redundant deprecation updates — if the section already reflects
        # the correct current subject (from an earlier migration event), a
        # follow-up deprecation like "MySQL is no longer used" is redundant.
        if (
            change.change_type == "deprecation"
            and section is not None
            and section.current_subject.lower() == change.new_subject.lower()
        ):
            return

        if section is None:
            # Create a new section from this change
            section = self.document.get_or_create(
                topic=change.topic,
                subject=change.new_subject,
                day=change.day,
                initial_state=f"Initial state: {change.new_subject}",
                tags=set(change.tags),
                memory_ids=[change.source_memory_id],
            )

        # Generate settled fact text
        settled_text = self.generator.generate(change, section)

        # Store settled fact in VSAMemory
        settled_tags = [
            t for t in change.tags
            if t.lower() not in self._TRANSITION_TAGS
        ]
        settled_id = self.memory.store(
            content=settled_text,
            node_type=section.history[0].memory_ids[0] if section.history else "fact",
            day=change.day + 1,  # Settled fact created "next day"
            tags=settled_tags,
        )
        # Fix: use the original node_type from the section or default to fact
        # Re-store with correct node type
        record = self.memory.get_by_id(settled_id)
        if record:
            record.node_type = "fact"

        self._settled_fact_ids.append(settled_id)

        # Update narrative section
        old_state = section.current_state if section else None
        summary = f"{change.change_type.title()}: {change.old_subject or '?'} → {change.new_subject}"
        section.update(
            new_state=settled_text,
            new_subject=change.new_subject,
            day=change.day,
            summary=summary,
            memory_ids=[change.source_memory_id, settled_id],
            old_state=old_state,
        )
        section.settled_fact_id = settled_id
        section.related_tags |= set(change.tags)

    def _process_new_fact(
        self,
        content: str,
        memory_id: str,
        day: int,
        tags: list[str],
        topic: str,
        node_type: str,
    ) -> None:
        """Process a non-migration fact — just update the narrative section."""
        section = self.document.get(topic)

        if section is None:
            # Extract subject from tags (first specific tag)
            subject = "unknown"
            for tag in tags:
                if tag.lower() != topic and tag.lower() not in self._TRANSITION_TAGS:
                    subject = tag
                    break

            self.document.get_or_create(
                topic=topic,
                subject=subject,
                day=day,
                initial_state=content,
                tags=set(tags),
                memory_ids=[memory_id],
            )
        else:
            # Update related tags
            section.related_tags |= set(tags)
