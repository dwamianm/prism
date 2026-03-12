"""Narrative Rewriter — auto-maintains structured living documents from memory events.

Phase 2 of PRME-X research. After each memory event involving a state change
(migration, switch, deprecation), the rewriter:
1. Detects what changed (topic, old state, new state)
2. Updates the narrative document's topic section
3. Generates and stores a clean "settled fact" back into VSAMemory

This automates the manual settled-fact pattern discovered in Phase 1.
"""

from research.narrative.document import NarrativeDocument, Section, Entry
from research.narrative.rewriter import NarrativeRewriter

__all__ = ["NarrativeDocument", "NarrativeRewriter", "Section", "Entry"]
