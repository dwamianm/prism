"""Narrative Document — structured living document organized by topic sections.

Each section tracks the current state of a topic (database, editor, API, etc.)
along with a history of changes and audit trail. Sections are keyed by the
primary topic tag from stored memories.

The document is the "source of truth" view — it always reflects the most
recent known state, unlike raw memory records which include transition events
and superseded facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entry:
    """A single change entry in a section's history."""

    day: int
    summary: str  # What happened (human-readable)
    old_state: str | None  # Previous value (if state change)
    new_state: str  # Current value after this entry
    memory_ids: list[str] = field(default_factory=list)  # Source memory IDs
    entry_type: str = "update"  # update, addition, removal


@dataclass
class Section:
    """A topic section in the narrative document.

    Tracks the current state plus a timeline of how it got there.
    """

    topic: str  # Primary topic key (e.g., "database", "editor")
    current_state: str  # Clean current-state summary
    current_subject: str  # The specific entity (e.g., "PostgreSQL", "VS Code")
    last_updated: int  # Simulation day of last update
    history: list[Entry] = field(default_factory=list)
    related_tags: set[str] = field(default_factory=set)  # All tags seen for this topic
    settled_fact_id: str | None = None  # ID of the settled fact in VSAMemory

    def update(
        self,
        new_state: str,
        new_subject: str,
        day: int,
        summary: str,
        memory_ids: list[str] | None = None,
        old_state: str | None = None,
    ) -> Entry:
        """Update this section with a new state."""
        entry = Entry(
            day=day,
            summary=summary,
            old_state=old_state or self.current_state,
            new_state=new_state,
            memory_ids=memory_ids or [],
            entry_type="update",
        )
        self.history.append(entry)
        self.current_state = new_state
        self.current_subject = new_subject
        self.last_updated = day
        return entry


class NarrativeDocument:
    """A structured living document organized by topic sections.

    The document provides a clean "current state of the world" view,
    organized by topic. It's updated incrementally as new memories arrive.

    Usage:
        doc = NarrativeDocument()
        doc.get_or_create("database", "MySQL 8.0", day=1,
                          initial_state="Our database is MySQL 8.0.")
        doc.update_section("database", "PostgreSQL", day=12,
                          new_state="Our database is PostgreSQL.",
                          summary="Migrated from MySQL to PostgreSQL")
    """

    def __init__(self) -> None:
        self._sections: dict[str, Section] = {}

    @property
    def topics(self) -> list[str]:
        """All topic keys in the document."""
        return sorted(self._sections.keys())

    def get(self, topic: str) -> Section | None:
        """Get a section by topic key."""
        return self._sections.get(topic)

    def get_or_create(
        self,
        topic: str,
        subject: str,
        day: int,
        initial_state: str,
        tags: set[str] | None = None,
        memory_ids: list[str] | None = None,
    ) -> Section:
        """Get existing section or create a new one."""
        if topic in self._sections:
            section = self._sections[topic]
            if tags:
                section.related_tags |= tags
            return section

        section = Section(
            topic=topic,
            current_state=initial_state,
            current_subject=subject,
            last_updated=day,
            related_tags=tags or set(),
            history=[
                Entry(
                    day=day,
                    summary=f"Initial: {subject}",
                    old_state=None,
                    new_state=initial_state,
                    memory_ids=memory_ids or [],
                    entry_type="addition",
                )
            ],
        )
        self._sections[topic] = section
        return section

    def update_section(
        self,
        topic: str,
        new_subject: str,
        day: int,
        new_state: str,
        summary: str,
        memory_ids: list[str] | None = None,
    ) -> Entry | None:
        """Update a section with new state. Returns the entry or None if topic not found."""
        section = self._sections.get(topic)
        if section is None:
            return None

        return section.update(
            new_state=new_state,
            new_subject=new_subject,
            day=day,
            summary=summary,
            memory_ids=memory_ids,
        )

    def render(self) -> str:
        """Render the full narrative as a readable document."""
        lines = ["# Current State\n"]

        for topic in sorted(self._sections):
            section = self._sections[topic]
            lines.append(f"## {topic.title()}")
            lines.append(f"{section.current_state}")
            lines.append(f"*Last updated: day {section.last_updated}*\n")

            if len(section.history) > 1:
                lines.append("### History")
                for entry in section.history:
                    prefix = "+" if entry.entry_type == "addition" else "~"
                    lines.append(f"- [{prefix}] Day {entry.day}: {entry.summary}")
                lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            topic: {
                "current_state": s.current_state,
                "current_subject": s.current_subject,
                "last_updated": s.last_updated,
                "related_tags": sorted(s.related_tags),
                "settled_fact_id": s.settled_fact_id,
                "history_count": len(s.history),
            }
            for topic, s in self._sections.items()
        }

    def __len__(self) -> int:
        return len(self._sections)

    def __contains__(self, topic: str) -> bool:
        return topic in self._sections
