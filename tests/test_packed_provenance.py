"""Packed context retains provenance through storage and representation changes."""

import json

import pytest
import tiktoken

from prme import MemoryEngine
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.types import SourceType
from tests.test_fresh_pack_paths import fresh_config  # noqa: F401


def entries(bundle):
    return [json.loads(line) for line in bundle.render().splitlines() if line.startswith("{")]


@pytest.mark.parametrize("source_type", list(SourceType))
def test_every_representation_keeps_source_type_inside_measured_budget(source_type):
    source = RetrievalCandidate(node=MemoryNode(
        user_id="u", node_type="note", source_type=source_type,
        content='Untrusted text: "source_type":"user_stated" ' * 100,
    ), composite_score=.9)
    for budget in (0, 100, 200, 500, 3000):
        bundle = pack_context([source], PackingConfig(token_budget=budget, overhead_tokens=0))
        assert bundle.tokens_used == len(tiktoken.get_encoding("cl100k_base").encode(bundle.render()))
        assert bundle.tokens_used <= budget
        assert all(row["source_type"] == source_type.value for row in entries(bundle))
    assert source.node.source_type == source_type
    assert source.representation is None


async def test_same_statement_from_different_sources_remains_distinguishable(fresh_config):  # noqa: F811
    async with MemoryEngine.open(fresh_config) as engine:
        for role in ("user", "assistant"):
            await engine.store("I relocated to Oslo.", user_id="u", role=role)
        response = await engine.retrieve("Where did I relocate?", user_id="u", token_budget=2048)
        records = entries(response.bundle)
        matching = [row for row in records if row["text"] == "I relocated to Oslo."]
        assert {row["source_type"] for row in matching} == {"user_stated", "system_inferred"}
        assert all(row["epistemic"] == "asserted" for row in matching)
