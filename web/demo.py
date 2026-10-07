"""Build a demo memory pack that shows PRME's connected graph, with no model calls.

Every message goes through the real ``ingest()`` pipeline: grounding, entity
reuse across sessions, repeated-claim merge (#209), named replacements and one
durable publication per message. Only the extraction step is scripted, so the
pack is deterministic and shows what PRME builds from correct extraction
output. It does not measure extraction quality.

The messages name their subject. First-person references such as "I" and "my"
stay local to one message unless the turn names its speaker
(docs/ENTITY-IDENTITY.md), and these turns name none, so a first-person chat
connects across sessions only when it passes ``ingest(speaker=...)``.

    uv run python -m web.demo ./my_memories_demo
    PRME_CHAT_DATA_DIR=./my_memories_demo uv run python -m web.server

Open the explorer and enter the owner ID ``abc_123``.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from prme import MemoryEngine
from prme.config import PRMEConfig
from prme.ingestion.schema import ExtractionResult
from prme.types import LifecycleState

OWNER = "abc_123"

PERSON, ORG = "person", "organization"


def _entity(name, kind):
    return {"name": name, "entity_type": kind}


def _claim(quote, subject, predicate, obj, *, subject_type=PERSON, object_type=None, **extra):
    return {"subject": subject, "subject_entity_type": subject_type, "predicate": predicate,
            "object": obj, "object_entity_type": object_type, "polarity": "positive",
            "evidence_quote": quote, **extra}


# (date, session, role, message, entities, claims). Claims quote the message.
MESSAGES = [
    ("2026-01-05", "onboarding", "user", "Avery works at Northwind Health as a product manager.",
     [_entity("Avery", PERSON), _entity("Northwind Health", ORG)],
     [_claim("Avery works at Northwind Health as a product manager.", "Avery", "works_at", "Northwind Health", object_type=ORG),
      _claim("Avery works at Northwind Health as a product manager.", "Avery", "has_role", "product manager")]),
    ("2026-02-02", "home", "user", "Avery's cleaners, Sparkle Cleaning, come every other Monday.",
     [_entity("Avery", PERSON), _entity("Sparkle Cleaning", ORG)],
     [_claim("Avery's cleaners, Sparkle Cleaning, come every other Monday.", "Avery", "uses_cleaning_service",
             "Sparkle Cleaning", object_type=ORG),
      _claim("Avery's cleaners, Sparkle Cleaning, come every other Monday.", "Sparkle Cleaning", "visits_on",
             "every other Monday", subject_type=ORG)]),
    ("2026-03-09", "work", "user", "Priya is Avery's manager at Northwind Health.",
     [_entity("Priya", PERSON), _entity("Avery", PERSON), _entity("Northwind Health", ORG)],
     [_claim("Priya is Avery's manager at Northwind Health.", "Priya", "manages", "Avery", object_type=PERSON),
      _claim("Priya is Avery's manager at Northwind Health.", "Priya", "works_at", "Northwind Health", object_type=ORG)]),
    ("2026-04-14", "work", "user", "Avery still works at Northwind Health.",
     [_entity("Avery", PERSON), _entity("Northwind Health", ORG)],
     [_claim("Avery still works at Northwind Health.", "Avery", "works_at", "Northwind Health", object_type=ORG)]),
    ("2026-06-01", "career", "user", "Avery left Northwind Health and now works at Brightpath.",
     [_entity("Avery", PERSON), _entity("Northwind Health", ORG), _entity("Brightpath", ORG)],
     [_claim("Avery left Northwind Health and now works at Brightpath.", "Avery", "works_at", "Brightpath", object_type=ORG,
             temporal_intent="update", replaces_object="Northwind Health")]),
    ("2026-06-20", "work", "user", "Avery prefers morning meetings.",
     [_entity("Avery", PERSON)],
     [_claim("Avery prefers morning meetings.", "Avery", "prefers", "morning meetings", fact_type="preference")]),
    ("2026-07-07", "home", "user", "Avery moved Sparkle Cleaning from every other Monday to every other Tuesday.",
     [_entity("Avery", PERSON), _entity("Sparkle Cleaning", ORG)],
     [_claim("Avery moved Sparkle Cleaning from every other Monday to every other Tuesday.", "Sparkle Cleaning",
             "visits_on", "every other Tuesday", subject_type=ORG,
             temporal_intent="update", replaces_object="every other Monday"),
      _claim("Avery moved Sparkle Cleaning from every other Monday to every other Tuesday.", "Avery",
             "rescheduled", "Sparkle Cleaning", object_type=ORG, fact_type="decision")]),
    ("2026-08-18", "family", "user", "Jordan, Avery's partner, also works at Brightpath.",
     [_entity("Jordan", PERSON), _entity("Avery", PERSON), _entity("Brightpath", ORG)],
     [_claim("Jordan, Avery's partner, also works at Brightpath.", "Jordan", "partner_of", "Avery", object_type=PERSON),
      _claim("Jordan, Avery's partner, also works at Brightpath.", "Jordan", "works_at", "Brightpath", object_type=ORG)]),
    ("2026-09-15", "career", "user", "Avery works at Brightpath.",
     [_entity("Avery", PERSON), _entity("Brightpath", ORG)],
     [_claim("Avery works at Brightpath.", "Avery", "works_at", "Brightpath", object_type=ORG)]),
]


class ScriptedExtraction:
    """Return the scripted extraction for each demo message."""

    def __init__(self):
        self._by_text = {text: {"entities": entities, "facts": claims}
                         for _, _, _, text, entities, claims in MESSAGES}

    async def extract(self, content, *, role=None):
        return ExtractionResult.model_validate(self._by_text[content])


async def build(target: Path) -> None:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=False)
    (target / "lexical_index").mkdir()
    config = PRMEConfig(
        db_path=str(target / "memory.duckdb"),
        vector_path=str(target / "vectors.usearch"),
        lexical_path=str(target / "lexical_index"),
        organizer={"opportunistic_enabled": False},
    )
    async with MemoryEngine.open(config) as engine:
        # Keep the configured provider's identity; only its model call is scripted.
        engine._pipeline._extraction_provider.extract = ScriptedExtraction().extract
        for day, session, role, text, _, _ in MESSAGES:
            when = datetime.fromisoformat(day).replace(hour=9, tzinfo=timezone.utc)
            await engine.ingest(text, user_id=OWNER, session_id=session, role=role,
                                event_time=when, wait_for_extraction=True)
        nodes = await engine.query_nodes(user_id=OWNER, lifecycle_states=list(LifecycleState))
    counts = Counter((node.node_type.value, node.lifecycle_state.value) for node in nodes)
    print(f"Built {target} for owner {OWNER}:")
    for (kind, state), count in sorted(counts.items()):
        print(f"  {kind:10} {state:11} {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the PRME explorer demo pack", allow_abbrev=False)
    parser.add_argument("target", type=Path, help="New folder for the pack; must not exist")
    asyncio.run(build(parser.parse_args().target))


if __name__ == "__main__":
    main()
