"""A text-bearing fidelity floor keeps text-free fallbacks out of every context format."""

import hashlib
import json
import warnings
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.models import MemoryNode
from prme.models.relevance import ReceiptCandidate
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate, ScoreTrace
from prme.retrieval.packing import (
    _render_representation,
    pack_context,
    pack_context_monotonic_compact,
    requires_memory_text,
)
from prme.types import (
    TEXT_REPRESENTATIONS,
    NodeType,
    RepresentationLevel,
    Scope,
    has_memory_text,
)
from tests import test_durable_ingestion
from tests.previous_defaults import previous_defaults

config = test_durable_ingestion.config
user = test_durable_ingestion.user

FORMATS = ("auditable", "compact", "reader")
TEXT_FLOORS = ("full", "prose", "structured")
POINTERS = frozenset(RepresentationLevel) - TEXT_REPRESENTATIONS
LONG_TEXT = "A long source with a qualifier. " * 60


def candidate(index: int, content: str, *, score: float = .8) -> RetrievalCandidate:
    node = MemoryNode(
        id=f"00000000-0000-0000-0000-{index:012d}",
        user_id="owner",
        node_type=NodeType.FACT,
        content=content,
        event_time=datetime(2023, 5, 28, 18, 12, tzinfo=timezone.utc),
    )
    trace = ScoreTrace(semantic_similarity=score, composite_score=score)
    return RetrievalCandidate(node=node, composite_score=score, score_trace=trace)


def packing(context_format: str, min_fidelity: str, **updates) -> PackingConfig:
    values = dict(token_budget=4096, overhead_tokens=0, context_format=context_format,
                  min_fidelity=min_fidelity, context_guidance_mode="off",
                  multipath_ordering="score")
    values.update(updates)
    return PackingConfig(**values)


def packed(bundle) -> list[RetrievalCandidate]:
    return [item for group in bundle.sections.values() for item in group]


def rendered_texts(bundle) -> list[str]:
    """The record text each rendered entry carries, parsed from the context itself."""
    texts = []
    for line in bundle.render().splitlines():
        if bundle.context_format == "auditable" and line.startswith("{"):
            texts.append(json.loads(line)["text"])
        elif bundle.context_format == "compact" and line.startswith('["'):
            texts.append(json.loads(line)[-1])
        elif bundle.context_format == "reader" and line.startswith("- "):
            texts.append(json.loads(line[line.index('"'):]))
    return texts


def near_full_budget(context_format: str) -> int:
    """Room for the short record and a pointer to the long one, never for its text."""
    short = candidate(2, "Short evidence.")
    return pack_context([short], packing(context_format, "reference")).tokens_used + 300


def assert_only_memory_text(bundle) -> None:
    for item in packed(bundle):
        assert item.representation in TEXT_REPRESENTATIONS
        assert has_memory_text(item.node.content)
        assert item.node.content in item.rendered_text
    texts = rendered_texts(bundle)
    assert len(texts) == bundle.included_count
    assert all(has_memory_text(text) for text in texts)


def test_text_representations_are_the_levels_that_render_the_stored_text():
    item = candidate(1, "The telescope is blue.")
    for level in RepresentationLevel:
        rendered = _render_representation(item, level)
        assert (item.node.content in rendered) == (level in TEXT_REPRESENTATIONS)


def test_the_default_floor_is_unchanged():
    assert PackingConfig().min_fidelity == RepresentationLevel.REFERENCE
    assert not requires_memory_text(PackingConfig())
    assert requires_memory_text(PackingConfig(context_format="reader"))
    for floor in TEXT_FLOORS:
        assert requires_memory_text(PackingConfig(min_fidelity=floor))


@pytest.mark.parametrize("context_format", ["auditable", "compact"])
def test_default_floor_still_packs_a_pointer_when_only_a_pointer_fits(context_format):
    long, short = candidate(1, LONG_TEXT, score=.9), candidate(2, "Short evidence.", score=.1)
    settings = packing(context_format, "reference",
                       token_budget=near_full_budget(context_format))

    bundle = pack_context([long, short], settings)

    [pointer] = [item for item in packed(bundle) if item.node.id == long.node.id]
    assert pointer.representation in POINTERS
    assert LONG_TEXT not in bundle.render()
    assert str(long.node.id) in bundle.render()


@pytest.mark.parametrize("min_fidelity", TEXT_FLOORS)
@pytest.mark.parametrize("context_format", FORMATS)
def test_text_floor_excludes_a_record_that_fits_only_as_a_pointer(context_format, min_fidelity):
    long, short = candidate(1, LONG_TEXT, score=.9), candidate(2, "Short evidence.", score=.1)
    settings = packing(context_format, min_fidelity,
                       token_budget=near_full_budget(context_format))

    bundle = pack_context([long, short], settings)

    assert [item.node.id for item in packed(bundle)] == [short.node.id]
    assert bundle.excluded_ids == [long.node.id]
    assert str(long.node.id) not in bundle.render()
    assert "fact:" not in bundle.render() and "confidence:" not in bundle.render()
    assert rendered_texts(bundle) == ["Short evidence."]
    assert_only_memory_text(bundle)


@pytest.mark.parametrize("min_fidelity", TEXT_FLOORS)
@pytest.mark.parametrize("context_format", FORMATS)
def test_text_floor_excludes_blank_records_even_with_room(context_format, min_fidelity):
    blank, empty = candidate(1, "   \n ", score=.9), candidate(2, "", score=.8)
    kept = candidate(3, "Evidence.", score=.1)

    bundle = pack_context([blank, empty, kept], packing(context_format, min_fidelity))

    assert [item.node.id for item in packed(bundle)] == [kept.node.id]
    assert set(bundle.excluded_ids) == {blank.node.id, empty.node.id}
    assert rendered_texts(bundle) == ["Evidence."]


@pytest.mark.parametrize("context_format", ["auditable", "compact"])
def test_default_floor_still_packs_blank_records(context_format):
    blank, kept = candidate(1, "", score=.9), candidate(2, "Evidence.", score=.1)
    bundle = pack_context([blank, kept], packing(context_format, "reference"))
    assert {item.node.id for item in packed(bundle)} == {blank.node.id, kept.node.id}
    assert sorted(rendered_texts(bundle)) == ["", "Evidence."]


@pytest.mark.parametrize("context_format", FORMATS)
def test_every_budget_packs_only_memory_text_and_text_floors_agree(context_format):
    values = [candidate(index, f"Evidence {index}. " * (index * 7), score=round(1 - index / 100, 2))
              for index in range(1, 25)]
    values.append(candidate(99, " ", score=.95))
    pointer_budgets = 0
    for budget in range(0, 2400, 61):
        default = pack_context(values, packing(context_format, "reference", token_budget=budget))
        pointer_budgets += any(item.representation in POINTERS for item in packed(default))
        bundles = [pack_context(values, packing(context_format, floor, token_budget=budget))
                   for floor in TEXT_FLOORS]
        for bundle in bundles:
            assert_only_memory_text(bundle)
            ids = [item.node.id for item in packed(bundle)] + bundle.excluded_ids
            assert sorted(ids) == sorted(item.node.id for item in values)
        # Neither PROSE nor STRUCTURED is shorter than FULL, so every text floor
        # packs the same context.
        assert len({bundle.render() for bundle in bundles}) == 1
    # The sweep only means something if the default floor packs pointers in it.
    assert pointer_budgets > 0 or context_format == "reader"


def test_text_floor_is_deterministic():
    values = [candidate(1, LONG_TEXT, score=.9), candidate(2, "Short evidence.", score=.1),
              candidate(3, "", score=.5)]
    settings = packing("auditable", "full", token_budget=near_full_budget("auditable"))

    first, second = pack_context(values, settings), pack_context(list(reversed(values)), settings)

    assert first.render() == second.render()
    assert first.excluded_ids == second.excluded_ids == [values[0].node.id, values[2].node.id]


def test_a_plain_string_floor_from_model_copy_is_honored():
    # The pipeline's per-request override used to reach the packer unvalidated.
    long, short = candidate(1, LONG_TEXT, score=.9), candidate(2, "Short evidence.", score=.1)
    settings = packing("auditable", "reference", token_budget=near_full_budget("auditable"))

    bundle = pack_context([long, short], settings.model_copy(update={"min_fidelity": "full"}))

    assert bundle.excluded_ids == [long.node.id]
    assert_only_memory_text(bundle)


def test_blank_records_do_not_take_the_balanced_head():
    blank = candidate(1, "", score=.99)
    best = candidate(2, "The best multi-path record. " * 20, score=.9)
    short = candidate(3, "Short and dense.", score=.85)
    for item in (blank, best, short):
        item.paths = ["VECTOR", "LEXICAL"]
        item.path_count = 2
    only_best = pack_context([best], packing("auditable", "full", multipath_ordering="balanced"))

    bundle = pack_context(
        [blank, best, short],
        packing("auditable", "full", multipath_ordering="balanced",
                token_budget=only_best.tokens_used),
    )

    # Without the reserved head, the shorter, denser record would go first and
    # leave no room for the best one.
    assert [item.node.id for item in packed(bundle)] == [best.node.id]
    assert set(bundle.excluded_ids) == {blank.node.id, short.node.id}


def test_monotonic_compact_keeps_memory_text_under_a_text_floor():
    values = [candidate(index, f"Evidence {index}. " * (index * 9), score=round(1 - index / 100, 2))
              for index in range(1, 20)]
    blank = candidate(50, "", score=.99)
    values.append(blank)
    for budget in (300, 700, 1500):
        bundle = pack_context_monotonic_compact(
            values, packing("compact", "full", token_budget=budget)
        )
        assert bundle.context_format == "compact"
        assert packed(bundle)
        assert blank.node.id in bundle.excluded_ids
        assert_only_memory_text(bundle)


@pytest.mark.parametrize("level", sorted(POINTERS))
@pytest.mark.parametrize("context_format", FORMATS)
def test_reserved_pointer_levels_are_refused_when_text_is_required(context_format, level):
    item = candidate(1, "The telescope is blue.")

    refused = pack_context([item], packing(context_format, "full"),
                           _required=((item.node.id, level),))

    assert refused.excluded_ids == [item.node.id] and not packed(refused)
    if context_format != "reader":
        allowed = pack_context([item], packing(context_format, "reference"),
                               _required=((item.node.id, level),))
        assert [entry.representation for entry in packed(allowed)] == [level]


@pytest.mark.parametrize("level", list(RepresentationLevel))
def test_receipt_content_credit_uses_the_same_text_levels(level):
    fields = dict(node_id=candidate(1, "x").node.id, scope=Scope.PERSONAL,
                  content_sha256="0" * 64, score=.5, trace=None, representation=level,
                  in_context=True, has_content=True)
    if level in TEXT_REPRESENTATIONS:
        assert ReceiptCandidate(**fields).has_content
    else:
        with pytest.raises(ValidationError, match="content-bearing"):
            ReceiptCandidate(**fields)


def test_environment_sets_the_floor(monkeypatch):
    monkeypatch.setenv("PRME_PACKING__MIN_FIDELITY", "full")
    assert PRMEConfig().packing.min_fidelity == RepresentationLevel.FULL
    monkeypatch.setenv("PRME_PACKING__MIN_FIDELITY", "bogus")
    with pytest.raises(ValidationError):
        PRMEConfig()


async def test_per_request_text_floor_keeps_pointers_out_and_records_them(config, user):
    # The auditable format packs text-free fallbacks at the default floor; the reader format never does.
    config = previous_defaults(config)
    long_text = "The telescope manual covers every lens and mount. " * 80
    async with MemoryEngine.open(config) as engine:
        await engine.store(long_text, user_id=user, scope=Scope.PROJECT)
        await engine.store("The telescope is blue.", user_id=user, scope=Scope.PROJECT)
        request = dict(user_id=user, scope=Scope.PROJECT, min_score=0,
                       include_cross_scope=False, token_budget=700)

        default = await engine.retrieve("telescope", **request)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            # A plain string is converted before packing and receipt serialization.
            floored = await engine.retrieve("telescope", min_fidelity="full", **request)

        [long_node] = [item.node for item in default.results if item.node.content == long_text]
        pointers = [item for item in packed(default.bundle) if item.representation in POINTERS]
        assert [item.node.id for item in pointers] == [long_node.id]

        assert floored.bundle.min_fidelity == RepresentationLevel.FULL
        assert_only_memory_text(floored.bundle)
        assert long_node.id in floored.bundle.excluded_ids
        assert str(long_node.id) not in floored.bundle.render()
        assert "The telescope is blue." in floored.bundle.render()

        receipt = await engine.get_retrieval_receipt(
            str(floored.metadata.request_id), user_id=user
        )
        context = floored.bundle.render()
        assert receipt.packing.min_fidelity == RepresentationLevel.FULL
        assert receipt.context_sha256 == hashlib.sha256(context.encode()).hexdigest()
        in_context = [item for item in receipt.candidates if item.in_context]
        assert in_context and all(item.has_content for item in in_context)
        [excluded] = [item for item in receipt.candidates if item.node_id == long_node.id]
        assert not excluded.in_context and excluded.representation is None
        assert receipt.replay_ranking() == tuple(item.node.id for item in floored.results)

        with pytest.raises(ValueError):
            await engine.retrieve("telescope", min_fidelity="bogus", **request)


async def test_blank_exclusions_are_not_reported_as_a_budget_limit(config, user):
    # The auditable format packs blank turns at the default floor; the reader format never does.
    config = previous_defaults(config)
    async with MemoryEngine.open(config) as engine:
        for index in range(5):
            await engine.store(f"User asked about hiking trail {index}", user_id=user,
                               session_id="s1", role="user")
            # A tool-call-only chat turn has no text.
            await engine.store("", user_id=user, session_id="s1", role="assistant")
        query = "How many hiking trails did I ask about?"

        default = await engine.retrieve(query, user_id=user)
        floored = await engine.retrieve(query, user_id=user,
                                        min_fidelity=RepresentationLevel.FULL)

        blank_ids = {item.node.id for item in floored.results
                     if not has_memory_text(item.node.content)}
        assert blank_ids and blank_ids <= set(floored.bundle.excluded_ids)
        assert set(floored.bundle.excluded_ids) == blank_ids
        coverage = floored.metadata.aggregation_coverage
        assert coverage is not None
        assert coverage.status != "context_limited"
        assert "token_budget" not in coverage.limitations
        # The default floor packs the blank turns and reports the same coverage.
        assert not default.bundle.excluded_ids
        assert default.metadata.aggregation_coverage.status == coverage.status
