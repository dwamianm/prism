"""Named conversation grouping preserves selected evidence and audit fields."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

import pytest

from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.retrieval.packing import (
    pack_context,
    pack_context_grouped_conversations,
)
from prme.types import (
    EpistemicType,
    LifecycleState,
    NodeType,
    RepresentationLevel,
    Scope,
    SourceType,
)


NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def _candidate(
    index: int,
    *,
    session_id: str | None,
    turn_index: int,
    role: str,
    content: str | None = None,
) -> RetrievalCandidate:
    node = MemoryNode(
        id=UUID(f"00000000-0000-0000-0000-{index:012d}"),
        user_id="owner",
        session_id=session_id,
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        epistemic_type=EpistemicType.ASSERTED,
        lifecycle_state=LifecycleState.TENTATIVE,
        source_type=(
            SourceType.USER_STATED
            if role == "user"
            else SourceType.SYSTEM_INFERRED
        ),
        event_time=NOW,
        valid_from=NOW + timedelta(microseconds=index),
        created_at=NOW + timedelta(seconds=turn_index),
        content=content or (f"Turn {turn_index}. " + "supporting detail " * 25),
        metadata={"source_role": role, "source_turn_index": turn_index},
    )
    return RetrievalCandidate(
        node=node,
        paths=["VECTOR", "LEXICAL"],
        path_count=2,
        composite_score=1 - index / 100,
    )


def _ids(bundle: MemoryBundle) -> set[UUID]:
    return {
        candidate.node.id
        for values in bundle.sections.values()
        for candidate in values
    }


def test_grouping_keeps_named_fields_and_orders_turns() -> None:
    later = _candidate(1, session_id="conversation-1", turn_index=3, role="assistant")
    earlier = _candidate(2, session_id="conversation-1", turn_index=2, role="user")
    config = PackingConfig(
        token_budget=2000,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
        multipath_ordering="score",
        context_guidance_mode="off",
    )

    bundle = pack_context_grouped_conversations([later, earlier], config)
    payload = json.loads(bundle.render().splitlines()[2])

    assert payload["conversation"] == {
        "session_id": "conversation-1",
        "type": "fact",
        "scope": "personal",
        "epistemic": "asserted",
        "memory_lifecycle": "tentative",
        "event_time": NOW.isoformat(),
    }
    assert [turn["turn_index"] for turn in payload["turns"]] == [2, 3]
    assert [turn["role"] for turn in payload["turns"]] == ["user", "assistant"]
    assert [turn["id"] for turn in payload["turns"]] == [
        str(earlier.node.id),
        str(later.node.id),
    ]
    assert all("valid_from" in turn for turn in payload["turns"])
    assert all("source_type" in turn for turn in payload["turns"])
    assert all("text" in turn for turn in payload["turns"])


def test_grouping_preserves_control_and_can_spend_only_saved_space() -> None:
    values = [
        _candidate(
            index,
            session_id="conversation-1" if index <= 8 else f"singleton-{index}",
            turn_index=index,
            role="user" if index % 2 else "assistant",
        )
        for index in range(1, 20)
    ]
    config = PackingConfig(
        token_budget=1450,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
        multipath_ordering="score",
        context_guidance_mode="off",
    )

    control = pack_context(values, config)
    same_set = pack_context_grouped_conversations(values, config)
    filled = pack_context_grouped_conversations(
        values, config, admit_additional=True
    )

    assert _ids(same_set) == _ids(control)
    assert _ids(control) <= _ids(filled)
    assert same_set.tokens_used < control.tokens_used
    assert filled.included_count > control.included_count
    assert filled.tokens_used <= config.token_budget


def test_singletons_keep_the_original_auditable_object() -> None:
    source = _candidate(1, session_id=None, turn_index=0, role="user")
    config = PackingConfig(
        token_budget=1000,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
        context_guidance_mode="off",
    )

    control = pack_context([source], config)
    grouped = pack_context_grouped_conversations([source], config)

    assert grouped.render() == control.render()


def test_grouping_rejects_compact_configuration() -> None:
    with pytest.raises(ValueError, match="auditable context format"):
        pack_context_grouped_conversations(
            [_candidate(1, session_id="s", turn_index=0, role="user")],
            PackingConfig(context_format="compact"),
        )
