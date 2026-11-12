"""Engine, packs and fixtures shared by the visible set (``tests/contract``) and the hidden set
(``tests/contract_holdout``). Each directory's ``conftest.py`` star-imports the fixtures below and supplies its
own ``robot_world``, ``chat_world`` and ``chat_recording`` fixtures, its expected failures, a
``pytest_generate_tests`` that calls ``parametrize`` and a ``pytest_terminal_summary`` that calls ``summarize``.

Product defaults. ``tests/conftest.py`` turns several defaults off for every test through two autouse,
function-scoped fixtures (``structure_options_off`` and ``_one_extraction_call_per_turn``). The packs here are
built once per session, before any function-scoped fixture runs, so a ``monkeypatch`` undo would come too late
and would not reach them. Instead:

1. ``structure_options_off`` and ``_one_extraction_call_per_turn`` are overridden below by fixtures of the same
   name that do nothing; pytest resolves a fixture name to the nearest definition, so the parent's never run
   for these tests and ``DEFAULT_SCORING_SETTINGS`` keeps its product values.
2. ``product_config`` builds the configuration inside ``pytest.MonkeyPatch.context()`` with every ``PRME_``
   variable removed and the working directory moved to the empty pack directory, so neither the shell nor the
   checkout's ``.env`` can change a default. The context is undone before the tests run.
3. ``check_product_defaults`` asserts a few alpha-channel defaults on the built configuration, so a change to
   the step above cannot silently test the pre-alpha settings.

Memory mode. ``--memory-mode address_first`` builds every pack with ``memory_mode="address_first"`` and makes
the expected failures non-strict, so an id the mode fixes shows as XPASS and nothing fails for being fixed. The
default, ``classic``, is the product as configured, with strict expected failures.

No model call and no network. Robot scenes use ``ingest_fast_many`` and ``process_pending`` (which never runs an
LLM), the store() chat uses ``store()``, which does not extract by default, and the ingest() chat replays a
recorded extraction (``ExtractionReplay``). ``no_network`` refuses every outbound socket connection while a pack
is built and while each test runs, so an accidental provider call fails loudly.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import os
import shutil
import socket
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pytest

from prme.config import PRMEConfig
from prme.ingestion.errors import ExtractionError
from prme.retrieval.models import RetrievalResponse
from prme.storage.embedding import create_embedding_provider
from prme.storage.engine import MemoryEngine
from prme.types import NodeType
from tests.contract import scenes

PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PRME_EXTRACTION_API_KEY")
MEMORY_MODES = ("classic", "address_first")
# The extraction the chat recordings were made with. A replay names the same provider and model, so the records
# it derives carry the same provenance; the provider's methods are replaced, so it is never called.
RECORDED_EXTRACTION = {"provider": "ollama", "model": "deepseek-v4.1-flash:cloud"}
RECORDING_NAME = "chat_extraction.json"


# Options --------------------------------------------------------------------------------------------------------

def add_memory_mode_option(parser) -> None:
    """Add ``--memory-mode`` and ``--stated``. Both sets' conftests call this, and a run of both adds them once."""
    try:
        parser.addoption(
            "--memory-mode", choices=MEMORY_MODES, default="classic",
            help="Build the contract packs with this memory_mode (address_first makes expected failures non-strict)",
        )
    except ValueError:  # the other set's conftest already added it
        pass
    try:
        parser.addoption(
            "--stated", action="store_true", default=False,
            help="Also build the S1 packs written through ingest(assertions=...) (about 3 minutes per set)",
        )
    except ValueError:  # the other set's conftest already added it
        pass


def memory_mode_of(config) -> str:
    return config.getoption("memory_mode", default="classic")


def stated_of(config) -> bool:
    """Whether to build the S1 packs written as caller-stated facts. They cost about 3 minutes per set (each
    stated fact takes about 200 ms to write), so they run only when asked, to keep the default run short."""
    return bool(config.getoption("stated", default=False))


# Isolation ------------------------------------------------------------------------------------------------------

@contextmanager
def no_network():
    """Refuse outbound socket connections (local Unix sockets, as asyncio uses internally, still work)."""

    def refuse(sock, address, *args, **kwargs):
        if sock.family == socket.AF_UNIX:
            return original(sock, address, *args, **kwargs)
        raise ConnectionRefusedError(f"contract tests make no network call (tried {address!r})")

    def refuse_ex(sock, address, *args, **kwargs):
        if sock.family == socket.AF_UNIX:
            return original_ex(sock, address, *args, **kwargs)
        raise ConnectionRefusedError(f"contract tests make no network call (tried {address!r})")

    def refuse_lookup(host, *args, **kwargs):
        raise socket.gaierror(f"contract tests make no network call (tried to look up {host!r})")

    original, original_ex = socket.socket.connect, socket.socket.connect_ex
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", refuse)
        patch.setattr(socket.socket, "connect_ex", refuse_ex)
        patch.setattr(socket, "getaddrinfo", refuse_lookup)
        patch.setenv("HF_HUB_OFFLINE", "1")
        for key in PROVIDER_KEYS:
            patch.setenv(key, "")
        yield


def product_config(directory: str, *, mode: str = "classic", extraction: dict | None = None,
                   **settings_given) -> PRMEConfig:
    """The configuration a project that sets nothing gets, with its paths in ``directory``.

    The paths are those ``prme.client.config_from_directory`` sets. ``mode`` other than classic is passed as
    ``memory_mode``, and ``extraction`` as the extraction settings (only the ingest() chat passes one). Any other
    keyword is a setting given outright (the recording script uses it to compare correction sources).
    """
    pack = os.path.join(os.path.abspath(directory), "pack")
    lexical = os.path.join(pack, "lexical_index")
    os.makedirs(lexical, exist_ok=True)
    settings: dict = {"db_path": os.path.join(pack, "memory.duckdb"),
                      "vector_path": os.path.join(pack, "vectors.usearch"), "lexical_path": lexical, "pack_path": None,
                      # Named outright: the product default is address_first since 2026-11-12, and the scorecard's
                      # recorded expectations are the classic mode's.
                      "memory_mode": mode}
    if extraction is not None:
        settings["extraction"] = extraction
    settings.update(settings_given)
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if name.startswith("PRME_"):
                patch.delenv(name)
        patch.chdir(directory)
        config = PRMEConfig(**settings)
    check_product_defaults(config, mode)
    return config


def check_product_defaults(config: PRMEConfig, mode: str = "classic") -> None:
    assert config.enable_ingest_claim_fixes and config.enable_mention_links and config.enable_owner_name_carry
    assert config.packing.graph_seed_words and config.packing.session_context_skip_entities
    assert config.scoring.rrf_graph_seed_count and config.scoring.fusion == "rrf"
    assert config.extraction_batch_target_tokens > 0 and not config.enable_store_extraction
    # getattr: the classic mode also runs on a tree from before memory_mode existed, to compare the two.
    assert getattr(config, "memory_mode", "classic") == mode
    if mode == "address_first":
        # The core of the mode; tests/test_belief_state.py checks the rest of its settings. The mode turns on
        # nothing that acts at write time, so typed extraction and corrections are on only when given outright
        # (the recording script gives both).
        assert config.enable_relation_routing and config.enable_belief_state
        for name in ("enable_typed_extraction", "enable_extraction_corrections"):
            assert name in config.model_fields_set or not getattr(config, name)
    else:
        assert not (config.enable_typed_extraction or config.enable_relation_routing
                    or getattr(config, "enable_belief_state", False))


# Recorded extraction ----------------------------------------------------------------------------------------------

def turn_record(turn) -> dict:
    """A batched request's turn as the recording keeps it."""
    return {"index": turn.index, "role": turn.role, "content": turn.content, "speaker": turn.speaker}


def claim_record(claim) -> dict:
    """A pronoun repair claim as the recording keeps it (its place in the extraction is never sent)."""
    record = dataclasses.asdict(claim)
    record.pop("fact_index", None)
    record["fields"] = list(record["fields"])
    return record


def memory_records(memories) -> list[dict]:
    """The earlier claims a batched request showed for correction, as the recording keeps them."""
    return [{"label": memory.label, "claim": memory.claim, "text": memory.text} for memory in memories]


def repair_key(window, claims, speaker) -> str:
    return json.dumps({"window": list(window), "claims": list(claims), "speaker": speaker}, sort_keys=True)


class ExtractionReplay:
    """Answers the extraction provider's calls from a recording, with no model.

    A batch asked exactly as recorded gets the recorded response; any other batch gets each turn's recorded entry
    (as ``tests/test_derivation_version.py`` does). A pronoun repair is answered when its request is the recorded
    one. Anything else is a miss: it is kept in ``misses`` and the call fails, and the pack's fixture fails on any
    miss, so a replay cannot silently derive less than the recording.
    """

    def __init__(self, recording: dict) -> None:
        self.batches: dict[tuple[str, ...], dict] = {}
        self.memories: dict[tuple[str, ...], list[dict]] = {}
        self.entries: dict[str, dict] = {}
        self.repairs: dict[str, dict] = {}
        self.misses: list[tuple[str, object]] = []
        for call in recording["calls"]:
            if call["method"] == "extract_batch":
                contents = tuple(turn["content"] for turn in call["turns"])
                self.batches.setdefault(contents, call["response"])
                if "memories" in call:
                    self.memories.setdefault(contents, call["memories"])
                by_index = {turn["index"]: turn["content"] for turn in call["turns"]}
                for entry in call["response"].get("turns", []):
                    content = by_index.get(entry.get("turn")) if isinstance(entry, dict) else None
                    if content is not None:
                        self.entries.setdefault(content, entry)
            elif call["method"] == "repair_pronouns":
                self.repairs.setdefault(repair_key(call["window"], call["claims"], call["speaker"]), call["response"])

    def install(self, provider) -> None:
        provider.extract_batch = self.extract_batch
        provider.repair_pronouns = self.repair_pronouns
        provider.extract = self.extract

    async def extract_batch(self, turns, **options) -> dict:
        contents = tuple(turn.content for turn in turns)
        # The labels in a recorded answer name the earlier claims its request showed, so a replay that shows
        # other claims would retire the wrong ones: a difference is a miss. A replay that shows none (corrections
        # off, as in the classic mode) drops the labels, so it cannot retire the wrong claim.
        shown = memory_records(options.get("memories", ()))
        if shown and shown != self.memories.get(contents, []):
            self.misses.append(("memories", list(contents)))
        if contents in self.batches:
            return copy.deepcopy(self.batches[contents])
        missing = [turn.content for turn in turns if turn.content not in self.entries]
        if missing:
            self.misses.append(("extract_batch", missing))
        return {"turns": [{**copy.deepcopy(self.entries[turn.content]), "turn": turn.index}
                          for turn in turns if turn.content in self.entries]}

    async def repair_pronouns(self, window, claims, *, speaker=None) -> dict:
        key = repair_key(window, [claim_record(claim) for claim in claims], speaker)
        if key in self.repairs:
            return copy.deepcopy(self.repairs[key])
        self.misses.append(("repair_pronouns", key))
        raise ExtractionError("This pronoun repair is not in the recording", reason_code="ReplayMiss")

    async def extract(self, content, **_options):
        self.misses.append(("extract", content))
        raise ExtractionError("A single-turn extraction is not in the recording", reason_code="ReplayMiss")


# One pack -------------------------------------------------------------------------------------------------------

class Scene:
    """One pack on its own event loop. ``ask`` keeps each answer, so many tests can read one retrieval."""

    def __init__(self, provider, *, user: str, now, mode: str = "classic", items=(), stores=(), sessions=(),
                 replay: ExtractionReplay | None = None, stated=()) -> None:
        self.user, self.now = user, now
        self.directory = tempfile.mkdtemp(prefix="prme-contract-")
        self.loop = asyncio.new_event_loop()
        self.engine = None
        self.replay = replay
        self._answers: dict = {}
        self._retirements: Retirements | None = None
        with no_network():
            config = product_config(self.directory, mode=mode,
                                    extraction=RECORDED_EXTRACTION if replay is not None else None)
            self.engine = self.run(MemoryEngine.create(config, embedding_provider=provider))
            self.run(self._write(list(items), list(stores), list(sessions)))
            if stated:
                self.run(self._state(list(stated)))

    def run(self, awaitable):
        return self.loop.run_until_complete(awaitable)

    async def _write(self, items: list[dict], stores: list[dict], sessions: list[tuple[str, list[dict]]]) -> None:
        engine = self.engine
        for offset in range(0, len(items), 500):
            await engine.ingest_fast_many(items[offset:offset + 500], user_id=self.user)
        while items and (await engine.process_pending(user_id=self.user, budget_ms=5000)).pending:
            pass
        for arguments in stores:
            await engine.store(user_id=self.user, **arguments)
        if sessions:
            assert self.replay is not None, "ingest() needs a recorded extraction to replay"
            self.replay.install(engine._pipeline._extraction_provider)
            for session_id, messages in sessions:
                await engine.ingest_batch(messages, user_id=self.user, session_id=session_id,
                                          wait_for_extraction=True)
            while (status := await engine.process_pending(user_id=self.user, budget_ms=5000)).pending:
                pass
            assert not self.replay.misses, f"the replay could not answer: {self.replay.misses}"
            assert status.failed == 0, f"{status.failed} sources failed to process"
            nodes = await engine.query_nodes(user_id=self.user, limit=1000)
            assert any(node.node_type == NodeType.FACT for node in nodes), "the replay derived no claim"
        # The first retrieve() of a session starts an opportunistic organizer pass in the background, and later
        # retrievals would race it. Run that pass now, inline, so every question sees the same settled pack and a
        # single test id run alone gets the answer it gets in the full run. Its cooldown (an hour) then keeps
        # retrieve() from starting another.
        runner = engine._maintenance_runner
        if runner is not None:
            await runner.drain()
            await runner.maybe_run(user_id=self.user)

    async def _state(self, messages: list[dict]) -> None:
        """Write caller-stated facts (ingest_batch with assertions); a model call would be a miss and fail."""
        engine = self.engine
        guard = ExtractionReplay({"calls": []})
        guard.install(engine._pipeline._extraction_provider)
        for offset in range(0, len(messages), 100):
            await engine.ingest_batch(messages[offset:offset + 100], user_id=self.user, wait_for_extraction=True)
        while (status := await engine.process_pending(user_id=self.user, budget_ms=5000)).pending:
            pass
        assert not guard.misses, f"stated facts called the model: {guard.misses}"
        assert status.failed == 0, f"{status.failed} sources failed to process"
        runner = engine._maintenance_runner
        if runner is not None:
            await runner.drain()
            await runner.maybe_run(user_id=self.user)

    def retirements(self, world: scenes.ChatWorld) -> Retirements:
        """Which claims ended retired, read once (:func:`read_retirements`)."""
        if self._retirements is None:
            self._retirements = self.run(read_retirements(self.engine, self.user, world))
        return self._retirements

    def ask(self, question: str, **kwargs) -> RetrievalResponse:
        key = (question, tuple(sorted(kwargs.items())))
        if key not in self._answers:
            self._answers[key] = self.ask_again(question, **kwargs)
        return self._answers[key]

    def ask_again(self, question: str, **kwargs) -> RetrievalResponse:
        kwargs.setdefault("reference_time", self.now)
        return self.run(self.engine.retrieve(question, user_id=self.user, **kwargs))

    def close(self) -> None:
        try:
            if self.engine is not None:
                self.run(self.engine.close())
        finally:
            self.loop.close()
            shutil.rmtree(self.directory, ignore_errors=True)


class RobotScenes:
    """The robot packs of one world, each built on first use."""

    def __init__(self, world: scenes.RobotWorld, provider, mode: str = "classic") -> None:
        self.world, self.provider, self.mode = world, provider, mode
        self._built: list[Scene] = []
        self._churn: dict = {}

    def _scene(self, items, now) -> Scene:
        scene = Scene(self.provider, user=self.world.user, now=now, mode=self.mode, items=items)
        self._built.append(scene)
        return scene

    def churn(self, label: str, moves: int) -> tuple[Scene, dict[str, str]]:
        if (label, moves) not in self._churn:
            items, truth, now = scenes.churn(self.world, dict(scenes.CHURN_SPACINGS)[label], moves)
            self._churn[label, moves] = (self._scene(items, now), truth)
        return self._churn[label, moves]

    def churn_stated(self, label: str, moves: int) -> tuple[Scene, dict[str, str]]:
        """The churn pack written through ingest(assertions=...) instead of plain observations."""
        key = ("stated", label, moves)
        if key not in self._churn:
            messages, truth, now = scenes.churn_stated(self.world, dict(scenes.CHURN_SPACINGS)[label], moves)
            scene = Scene(self.provider, user=self.world.user, now=now, mode=self.mode, stated=messages)
            self._built.append(scene)
            self._churn[key] = (scene, truth)
        return self._churn[key]

    @cached_property
    def flood(self) -> Scene:
        items, now = scenes.flood(self.world)
        return self._scene(items, now)

    @cached_property
    def window(self) -> tuple[Scene, set[str]]:
        items, inside, now = scenes.window(self.world)
        return self._scene(items, now), inside

    @cached_property
    def hops(self) -> tuple[Scene, dict[str, tuple[str, str]]]:
        items, pairs, now = scenes.hops(self.world)
        return self._scene(items, now), pairs

    @cached_property
    def unknown(self) -> Scene:
        items, now = scenes.unknown(self.world)
        return self._scene(items, now)

    def close(self) -> None:
        for scene in self._built:
            scene.close()


# Fixtures (star-imported by each conftest) ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def structure_options_off():
    """Overrides the fixture of this name in tests/conftest.py: these tests run with the product defaults."""


@pytest.fixture(autouse=True)
def _one_extraction_call_per_turn():
    """Overrides the fixture of this name in tests/conftest.py: these tests run with the product defaults."""


@pytest.fixture(autouse=True)
def _no_network():
    with no_network():
        yield


@pytest.fixture(scope="session")
def embedding_provider():
    """The default local embedding model, loaded once and shared by every pack of the session.

    It is loaded here with the network allowed, before any test's ``no_network`` guard (a session fixture is set
    up before a function one): a cached model makes no request, and a runner without the cache downloads it
    once, as for every other test that uses the default model. After that every pack is built and asked with
    the network refused. The load must not run under the guard: ``huggingface_hub`` reads ``HF_HUB_OFFLINE``
    when it is first imported, so a first import under the guard leaves the process offline for good and the
    download can never happen (the newest-dependencies CI job, 2026-11-11).
    """
    try:
        import huggingface_hub  # noqa: F401  (imported before any guard sets HF_HUB_OFFLINE)
    except ImportError:
        pass
    directory = tempfile.mkdtemp(prefix="prme-contract-")
    try:
        provider = create_embedding_provider(product_config(directory).embedding)
        asyncio.run(provider.embed(["warm up"]))
        yield provider
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(scope="session")
def memory_mode(request) -> str:
    return memory_mode_of(request.config)


@pytest.fixture(scope="session")
def robot(robot_world, embedding_provider, memory_mode):
    packs = RobotScenes(robot_world, embedding_provider, memory_mode)
    yield packs
    packs.close()


@pytest.fixture(scope="session")
def chat(chat_world, embedding_provider, memory_mode):
    scene = Scene(embedding_provider, user=chat_world.user, now=chat_world.now, mode=memory_mode,
                  stores=scenes.chat_messages(chat_world))
    yield scene
    scene.close()


@pytest.fixture(scope="session")
def ingest_chat(chat_world, chat_recording: Path, embedding_provider, memory_mode):
    """The chat through ingest_batch(), one call per session, its extraction replayed from ``chat_recording``."""
    if not chat_recording.exists():
        pytest.skip(f"no recorded extraction at {chat_recording}; "
                    "see tests/contract/fixtures/record_chat_extraction.py")
    replay = ExtractionReplay(json.loads(chat_recording.read_text()))
    scene = Scene(embedding_provider, user=chat_world.user, now=chat_world.now, mode=memory_mode,
                  sessions=scenes.chat_sessions(chat_world), replay=replay)
    yield scene
    scene.close()


# Parametrization ------------------------------------------------------------------------------------------------

def parametrize(metafunc, robot_world, chat_world, expected: dict[str, str], expected_ingest: dict[str, str],
                expected_ingest_address_first: dict[str, str] | None = None) -> None:
    """Give each test its cases, one id per case, with an xfail on the ids listed as failing today.

    The xfail is strict in the classic mode and not strict under ``--memory-mode address_first``, which also marks
    the ingest() chat's ids that fail only under it (``expected_ingest_address_first``).
    """
    strict = memory_mode_of(metafunc.config) == "classic"
    stated = stated_of(metafunc.config)
    ingest_failures = expected_ingest if strict else {**expected_ingest, **(expected_ingest_address_first or {})}
    families = ((scenes.ROBOT_CASES, robot_world, expected), (scenes.CHAT_CASES, chat_world, expected),
                (scenes.INGEST_CASES, chat_world, ingest_failures))
    for builders, world, failures in families:
        for name, build in builders.items():
            if name not in metafunc.fixturenames:
                continue
            skip = [pytest.mark.skip(reason="the stated S1 packs run with --stated")] \
                if name == "stated_case" and not stated else []
            metafunc.parametrize(name, [
                pytest.param(case, id=case.id, marks=skip + (
                    [pytest.mark.xfail(strict=strict, reason=failures[case.id])] if case.id in failures else []
                ))
                for case in build(world)
            ])


def check_expected(robot_world, chat_world, expected: dict[str, str], expected_ingest: dict[str, str],
                   expected_ingest_address_first: dict[str, str] | None = None) -> None:
    """Fail collection when the expected failures name an id that no case has (a typo or a removed case)."""
    unknown = set(expected) - scenes.all_case_ids(robot_world, chat_world)
    ingest_ids = scenes.case_ids(scenes.INGEST_CASES, chat_world)
    unknown |= (set(expected_ingest) | set(expected_ingest_address_first or {})) - ingest_ids
    assert not unknown, f"expected failures name no case: {sorted(unknown)}"


# The end-of-session table -------------------------------------------------------------------------------------------

OUTCOMES = ("passed", "xpassed", "xfailed", "failed")


def summarize(terminalreporter, directory: str, *, by_group: bool) -> None:
    """Print one row per group (or one total row) of the tests under ``directory``: passed, xpassed, xfailed,
    failed, with skipped and errors when there are any.

    The hidden set prints its total only, so its per-group results stay out of sight of whoever works against
    the visible set.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    for outcome in (*OUTCOMES, "skipped", "error"):
        for report in terminalreporter.stats.get(outcome, []):
            nodeid = getattr(report, "nodeid", "")
            if nodeid.startswith(directory):
                counts[scenes.group_of(nodeid) if by_group else "total"][outcome] += 1
    if not counts:
        return
    labels = [label for _file, _prefix, label in scenes.GROUPS if label in counts] if by_group else []
    labels += [label for label in counts if label not in labels and label != "total"]
    total = sum(counts.values(), Counter())
    extra = [outcome for outcome in ("skipped", "error") if total[outcome]]
    columns = (*OUTCOMES, *extra)
    width = max(len(label) for label in [*labels, "total"])
    terminalreporter.write_sep("=", f"{directory} scorecard, memory mode {memory_mode_of(terminalreporter.config)}")
    terminalreporter.write_line(f"{'group':<{width}}  " + "  ".join(f"{column:>7}" for column in columns))
    for label in labels:
        terminalreporter.write_line(f"{label:<{width}}  " + "  ".join(f"{counts[label][c]:>7}" for c in columns))
    terminalreporter.write_line(f"{'total':<{width}}  " + "  ".join(f"{total[c]:>7}" for c in columns))


# Reading answers ------------------------------------------------------------------------------------------------

def texts(response: RetrievalResponse, n: int | None = None) -> list[str]:
    """The ranked results' text, best first."""
    return [candidate.node.content for candidate in response.results[:n]]


def first_rank(ranked: list[str], needle: str) -> int | None:
    """The 1-based rank of the first result containing ``needle`` (case-insensitive), as chat_check.py reads it."""
    for rank, text in enumerate(ranked, start=1):
        if needle.lower() in text.lower():
            return rank
    return None


# Chat checks, shared by the store() and ingest() chats -----------------------------------------------------------

def assert_answers_in_top3(scene: Scene, question: scenes.ChatQuestion) -> None:
    ranked = texts(scene.ask(question.question))
    ranks = {needle: first_rank(ranked, needle) for needle in question.answers}
    assert all(rank is not None and rank <= 3 for rank in ranks.values()), f"ranks: {ranks}; top 3: {ranked[:3]}"


def assert_no_stale_above_answer(scene: Scene, question: scenes.ChatQuestion) -> None:
    ranked = texts(scene.ask(question.question))
    worst = max(first_rank(ranked, needle) or 999 for needle in question.answers)
    stale = {needle: first_rank(ranked, needle) for needle in question.stale}
    above = {needle: rank for needle, rank in stale.items() if rank is not None and rank < worst}
    assert not above, f"stale above the answer (answer rank {worst}): {above}"


def assert_packs_fewer_than_10(scene: Scene, question: scenes.ChatQuestion) -> None:
    packed = scene.ask(question.question).bundle.included_count
    assert packed < 10, f"packed {packed} records"


# Corrections: which claims ended retired ---------------------------------------------------------------------------

@dataclass
class Retirements:
    """The claims of a chat that ended retired, read against its key (``ChatWorld.retire``)."""

    matches: dict[str, list[tuple[str, bool]]]   # key item tag: (claim line, retired) for each claim it matches
    wrong: list[str]                              # retired claims outside the key and not merged into a copy
    retired: list[str]                            # every claim retired other than by merging into a copy


def _statement(node) -> tuple:
    metadata = node.metadata or {}
    return tuple(" ".join(str(metadata.get(key, "")).casefold().replace("_", " ").split())
                 for key in ("subject", "predicate", "object", "polarity"))


def _claim_line(node) -> str:
    metadata = node.metadata or {}
    denied = "not " if metadata.get("polarity") == "negative" else ""
    return f"{metadata.get('subject')} {denied}{metadata.get('predicate')} {metadata.get('object')}"


async def read_retirements(engine, user: str, world: scenes.ChatWorld) -> Retirements:
    """Which claims ended retired: per key item, and those retired wrongly (outside the key, and not merged)."""
    from prme.types import ACTIVE_LIFECYCLE_STATES, LifecycleState

    turns = {str(event.id): event.content for event in await engine.get_events(user_id=user, limit=1000)}
    nodes = await engine._graph_store.query_nodes(user_id=user, lifecycle_states=list(LifecycleState), limit=5000)
    claims = {str(node.id): node for node in nodes if (node.metadata or {}).get("extraction_kind")}

    def retired(node) -> bool:
        return node.lifecycle_state not in ACTIVE_LIFECYCLE_STATES

    def merged(node) -> bool:
        newer = claims.get(str(node.superseded_by)) if node.superseded_by else None
        return newer is not None and _statement(newer) == _statement(node)

    def matches(item: scenes.RetireItem, node) -> bool:
        metadata = node.metadata or {}
        said = " ".join(f"{metadata.get('predicate', '')} {metadata.get('object', '')}".replace("_", " ").split())
        sources = [turns.get(str(ref), "") for ref in node.evidence_refs]
        return (any(item.turn.casefold() in source.casefold() for source in sources)
                and any(word.casefold() in said.casefold() for word in item.words))

    keyed = {item.tag: [node for node in claims.values() if matches(item, node)] for item in world.retire}
    in_key = {str(node.id) for found in keyed.values() for node in found}
    ordered = sorted(claims.values(), key=lambda node: (node.event_time or node.created_at, _claim_line(node)))
    return Retirements(
        matches={tag: [(_claim_line(node), retired(node)) for node in found] for tag, found in keyed.items()},
        wrong=[_claim_line(node) for node in ordered
               if retired(node) and not merged(node) and str(node.id) not in in_key],
        retired=[_claim_line(node) for node in ordered if retired(node) and not merged(node)],
    )
