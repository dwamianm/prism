"""A "We" claim is linked to the people it speaks for (prism-monorepo issue 275).

With ``enable_owner_group_links``, a claim a user turn states about we, us or our keeps its group subject and
gains a ``group_member`` link to the owner (#315) and, when the session names exactly one group with the owner
("My partner Jordan and I"), to each named person. The same "We" claim repeated in two user turns of one session
merges into one current record (#209). The chat is the defect probe's (``benchmarks/conversations/defects-v1.json``),
with a scripted extraction in place of the model. Every test runs on DuckDB and, with PRME_TEST_DATABASE_URL set,
on PostgreSQL.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from prme import MemoryEngine
from prme.ingestion.claim_merge import claim_key, find_repeated_group_claims
from prme.ingestion.group_links import (
    GROUP_LINK_METADATA,
    _candidate_names,
    is_group_link,
    link_group_claims,
    named_groups,
    session_group_members,
)
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.ingestion.schema import ExtractionResult
from prme.types import EdgeType, LifecycleState, NodeType, Scope
from tests import test_durable_ingestion
from tests.test_mention_links import claim, person, place
from tests.test_owner_name_carry import on

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 10, 5, 19, 0, tzinfo=timezone.utc)
OWNER = "Alex Rivera"

TRIP = "Hi, I am Alex Rivera. My partner Jordan and I are planning a trip to Japan in late March next year."
REPLY = "That is cherry blossom season. Which places are you thinking about?"
CITIES_TURN = "We chose Tokyo, Kyoto, Kanazawa, and Osaka. We dropped Hiroshima to save time."
CITIES = "We chose Tokyo, Kyoto, Kanazawa, and Osaka."
DROPPED = "We dropped Hiroshima to save time."
WORK_TURN = "We chose Postgres partitioning over TimescaleDB for the shipment table. We dropped the idea of self-hosting."
POSTGRES = "We chose Postgres partitioning over TimescaleDB for the shipment table."
SELF_HOSTING = "We dropped the idea of self-hosting."
INTRODUCTION = "Hi, I am Alex Rivera."
HIKE_JORDAN = "Jordan and I went hiking on Saturday."
COOK_SAM = "Sam and I cooked dinner for the neighbors."
HIKE_WIFE = "My wife and I went hiking on Saturday."
HIKE_RILEY = "Riley and I went hiking on Saturday."
PASTA = "We chose the pasta place."
TOKYO = "We chose Tokyo."
FLIGHTS = "Jordan and I booked the flights."
DANA_INTRODUCTION = "Hi, I am Dana."
HIKE_DANA = "Dana and I went hiking on Saturday."
COLLEAGUE = "My colleague Lee reviewed the runbook."
HIKE_JORDAN_LEE = "Jordan Lee and I went hiking on Saturday."
KYOTO_NAMED = "Alex Rivera and the team: we chose Kyoto."

TRIP_SENTENCE = "My partner Jordan and I are planning a trip to Japan in late March next year."
RESULTS = {
    TRIP: {"entities": [person(OWNER), person("Jordan"), place("Japan")],
           "facts": [claim("I", "planning_trip_to", "Japan in late March next year", TRIP_SENTENCE)]},
    REPLY: {"entities": [], "facts": []},
    CITIES_TURN: {"entities": [place(city) for city in ("Tokyo", "Kyoto", "Kanazawa", "Osaka", "Hiroshima")],
                  "facts": [claim("We", "chose", "Tokyo, Kyoto, Kanazawa, and Osaka", CITIES),
                            claim("We", "dropped", "Hiroshima", DROPPED, object_entity_type="location")]},
    WORK_TURN: {"entities": [{"name": "Postgres partitioning", "entity_type": "concept"},
                             {"name": "TimescaleDB", "entity_type": "product"}],
                "facts": [claim("We", "chose", "Postgres partitioning over TimescaleDB for the shipment table",
                                POSTGRES),
                          claim("We", "dropped_idea_of", "self-hosting", SELF_HOSTING)]},
    INTRODUCTION: {"entities": [person(OWNER)], "facts": [claim("I", "name", OWNER, INTRODUCTION)]},
    HIKE_JORDAN: {"entities": [person("Jordan")], "facts": [claim("Jordan", "went", "hiking", HIKE_JORDAN)]},
    COOK_SAM: {"entities": [person("Sam")], "facts": [claim("Sam", "cooked", "dinner", COOK_SAM)]},
    HIKE_WIFE: {"entities": [], "facts": [claim("I", "went", "hiking", HIKE_WIFE)]},
    HIKE_RILEY: {"entities": [], "facts": [claim("I", "went", "hiking", HIKE_RILEY)]},
    PASTA: {"entities": [], "facts": [claim("We", "chose", "the pasta place", PASTA)]},
    TOKYO: {"entities": [place("Tokyo")],
            "facts": [claim("We", "chose", "Tokyo", TOKYO, object_entity_type="location")]},
    FLIGHTS: {"entities": [person("Jordan")], "facts": [claim("Jordan", "booked", "the flights", FLIGHTS)]},
    DANA_INTRODUCTION: {"entities": [person("Dana")], "facts": [claim("I", "name", "Dana", DANA_INTRODUCTION)]},
    HIKE_DANA: {"entities": [person("Dana")], "facts": [claim("Dana", "went", "hiking", HIKE_DANA)]},
    COLLEAGUE: {"entities": [person("Lee")], "facts": [claim("Lee", "reviewed", "the runbook", COLLEAGUE)]},
    HIKE_JORDAN_LEE: {"entities": [], "facts": [claim("I", "went", "hiking", HIKE_JORDAN_LEE)]},
    KYOTO_NAMED: {"entities": [person(OWNER), place("Kyoto")],
                  "facts": [claim("We", "chose", "Kyoto", KYOTO_NAMED, object_entity_type="location")]},
}

# The defect probe's chat, without the turns that state nothing about a group.
DEFECTS = {"s1-trip": [TRIP, REPLY, CITIES_TURN], "s3-work": [WORK_TURN]}


def linked(base, **updates):
    """The group links on, with the owner's name carried, as the alpha channel's defaults have them."""
    return on(base, **{"enable_owner_group_links": True, **updates})


def role(content):
    return "assistant" if content == REPLY else "user"


async def chat(engine, owner, sessions, *, batched=False):
    """Ingest each session's turns in order, one extraction per turn or one batched request per session."""
    pipeline = engine._pipeline
    if batched:
        async def extract_batch(turns):
            return {"turns": [{**RESULTS[turn.content], "turn": turn.index} for turn in turns]}

        pipeline._extraction_provider.extract_batch = extract_batch
    else:
        async def extract(content, **_):
            return ExtractionResult.model_validate(RESULTS[content])

        pipeline._extraction_provider.extract = extract
    minute = 0
    for session_id, turns in sessions.items():
        messages = []
        for content in turns:
            messages.append({"content": content, "role": role(content), "event_time": T0 + timedelta(minutes=minute)})
            minute += 2
        if batched:
            await engine.ingest_batch(messages, user_id=owner, session_id=session_id, wait_for_extraction=True)
        else:
            for message in messages:
                await engine.ingest(message["content"], user_id=owner, role=message["role"], session_id=session_id,
                                    event_time=message["event_time"], wait_for_extraction=True)


async def group_links(engine, owner):
    """(claim text, person name) for every group link of the owner's memory."""
    nodes = {node.id: node for node in await engine.query_nodes(
        user_id=owner, lifecycle_states=list(LifecycleState), limit=1000)}
    edges = await engine._graph_store.get_edges(node_ids=[str(node_id) for node_id in nodes])
    return sorted((nodes[edge.source_id].content, nodes[edge.target_id].content)
                  for edge in edges if is_group_link(edge))


def per_claim(found):
    """The names each claim is linked to."""
    names = {}
    for text, name in found:
        names.setdefault(text, set()).add(name)
    return names


class TestTheGroupsATurnNames:
    @pytest.mark.parametrize(("text", "groups"), [
        (TRIP, [("Jordan",)]),
        ("Jordan and I chose Tokyo.", [("Jordan",)]),
        ("Jordan, Sam and I chose Tokyo.", [("Jordan", "Sam")]),
        ("Jordan, Sam, and I chose Tokyo.", [("Jordan", "Sam")]),
        ("Training update: Mel and I did a 10 mile long run on Saturday.", [("Mel",)]),
        ("When Jordan arrived, Sam and I left.", [("Sam",)]),
        ("I called my mom, Jordan and I ate dinner.", [("Jordan",)]),
        ("My best friend Dana and I run.", [("Dana",)]),
        ("It'll be just me and Biscuit.", [("Biscuit",)]),
        ("Me and Jordan went to the store.", [("Jordan",)]),
        ("Me and Jordan, Sam and Dana went out.", [("Jordan", "Sam", "Dana")]),
        ("She gave the book to Jordan and me.", [("Jordan",)]),
        ("Hi, Jordan and I are planning a trip.", [("Jordan",)]),
        ("On Saturday, Jordan and I hiked.", [("Jordan",)]),
        # A member after a comma alone starts another clause; one before "and me" counts by itself.
        ("It'll be just me and Biscuit, Sam can't come.", [("Biscuit",)]),
        ("I told Sam, Jordan and me to wait.", [("Jordan",)]),
        # A member without a name: any words after "my" right next to "and I", a known relation further along.
        ("My wife and I went to Paris.", [(None,)]),
        ("My bandmates and I played a show.", [(None,)]),
        ("It was me and my bandmates.", [(None,)]),
        ("After my run, Jordan and I got coffee.", [("Jordan",)]),
        ("Yesterday my partner and I went out.", [(None,)]),
        ("Jordan, my sister and I went out.", [("Jordan", None)]),
        # "and I" that joins two clauses, not a group.
        ("I got the offer from Brightpath and I accepted it!", []),
        ("Two weeks in at Brightpath and I already like my team.", []),
        ("I met Jordan and I liked him.", []),
        ("We're moving to Denver instead, and I start at a hospital there in July.", []),
        ("Jordan and I'm happy.", []),
        (CITIES_TURN, []),
        ("I live in Denver with my partner Sam and our golden retriever, Biscuit.", []),
    ])
    def test_a_group_is_the_speaker_and_the_members_joined_to_them(self, text, groups):
        assert named_groups(text) == groups


class TestMembers:
    async def test_a_we_claim_links_to_the_owner_and_the_partner_the_session_names(self, config, user):
        """Criterion 1: the trip's two "We" claims are linked to both Alex Rivera and Jordan."""
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, DEFECTS)
            found = per_claim(await group_links(engine, user))
            [cities] = [node for node in await engine.query_nodes(user_id=user) if node.content == CITIES]
            edges = await engine._graph_store.get_edges(node_ids=[str(cities.id)])
            [subject] = [edge for edge in edges if edge.edge_type == EdgeType.HAS_FACT]
            group = await engine.get_node(str(subject.source_id), user_id=user)
            [intro_event] = [event for event in await engine.get_events(user) if event.content == TRIP]
        assert found[CITIES] == found[DROPPED] == {OWNER, "Jordan"}
        # The claim keeps its literal we and its group subject; the member link names the turn that named the group.
        assert cities.metadata["subject"] == "We" and group.content == "We"
        assert group.metadata["identity_status"] == "unresolved_reference"
        members = [edge for edge in edges if is_group_link(edge)]
        assert sorted(edge.metadata.get("named_in", "") for edge in members) == ["", str(intro_event.id)]

    async def test_a_session_that_names_no_group_links_the_owner_only(self, config, user):
        """Criterion 2: the work session's "We" claims are linked to Alex Rivera and not to Jordan."""
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, DEFECTS)
            found = per_claim(await group_links(engine, user))
        assert found[POSTGRES] == found[SELF_HOSTING] == {OWNER}

    @pytest.mark.parametrize("named", [[HIKE_JORDAN, COOK_SAM], [HIKE_WIFE], [HIKE_RILEY]],
                             ids=["two-groups", "member-without-a-name", "name-without-a-person"])
    async def test_two_groups_or_a_member_it_cannot_name_link_the_owner_only(self, config, user, named):
        """Criterion 3: two different groups, "my wife", or a name no person entity has."""
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, *named, PASTA]})
            found = per_claim(await group_links(engine, user))
        assert found == {PASTA: {OWNER}}

    async def test_the_same_group_named_twice_is_one_group(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, HIKE_JORDAN, FLIGHTS, PASTA]})
            found = per_claim(await group_links(engine, user))
        assert found == {PASTA: {OWNER, "Jordan"}}

    async def test_a_group_named_after_the_claim_does_not_count_and_both_paths_agree(self, config, user):
        """Only the turns at or before the claim's turn count, so batching the session changes nothing."""
        sessions = {"s1": [INTRODUCTION, PASTA, HIKE_JORDAN, TOKYO]}
        found = {}
        for batched in (False, True):
            owner = f"{user}-{batched}"
            async with MemoryEngine.open(linked(config, extraction_batch_target_tokens=2048 if batched else 0)) as engine:
                await chat(engine, owner, sessions, batched=batched)
                found[batched] = per_claim(await group_links(engine, owner))
        assert found[False] == found[True] == {PASTA: {OWNER}, TOKYO: {OWNER, "Jordan"}}

    async def test_the_defect_chat_gives_the_same_links_batched(self, config, user):
        found = {}
        for batched in (False, True):
            owner = f"{user}-{batched}"
            async with MemoryEngine.open(linked(config, extraction_batch_target_tokens=2048 if batched else 0)) as engine:
                await chat(engine, owner, DEFECTS, batched=batched)
                found[batched] = await group_links(engine, owner)
        assert found[False] == found[True] and len(found[True]) == 6

    async def test_a_group_another_speaker_names_is_not_the_owners(self, config, user):
        """The caller names each turn's speaker; Dana's "Jordan and I" says nothing about Alex's we."""
        async def extract(content, **_):
            return ExtractionResult.model_validate(RESULTS[content])

        async with MemoryEngine.open(linked(config)) as engine:
            engine._pipeline._extraction_provider.extract = extract
            for minute, (content, speaker) in enumerate([(HIKE_JORDAN, "Dana"), (PASTA, OWNER)]):
                await engine.ingest(content, user_id=user, role="user", speaker=speaker, session_id="s1",
                                    event_time=T0 + timedelta(minutes=minute), wait_for_extraction=True)
            found = per_claim(await group_links(engine, user))
        assert found == {PASTA: {OWNER}}

    async def test_a_failed_member_lookup_still_links_the_owner(self, config, user, monkeypatch):
        async def fail(*args, **kwargs):
            raise RuntimeError("lookup failed")

        monkeypatch.setattr("prme.ingestion.group_links.session_group_members", fail)
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, DEFECTS)
            found = per_claim(await group_links(engine, user))
        assert found == {CITIES: {OWNER}, DROPPED: {OWNER}, POSTGRES: {OWNER}, SELF_HOSTING: {OWNER}}

    async def test_off_nothing_is_linked(self, config, user):
        async with MemoryEngine.open(linked(config, enable_owner_group_links=False)) as engine:
            await chat(engine, user, DEFECTS)
            assert await group_links(engine, user) == []

    async def test_it_reads_only_the_session_up_to_the_turn(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, PASTA, HIKE_JORDAN], "s2": [HIKE_RILEY]})
            events = {event.content: event for event in await engine.get_events(user)}
            [owner] = [node for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)
                       if node.content == OWNER]
            before = await session_group_members(events[PASTA], owner, graph_store=engine._graph_store,
                                                 event_store=engine._event_store)
            after = await session_group_members(events[HIKE_JORDAN], owner, graph_store=engine._graph_store,
                                                event_store=engine._event_store)
            listed = await engine.get_events(user, session_id="s1", until=events[PASTA].timestamp)
        assert before == ([], None)
        assert [node.content for node in after[0]] == ["Jordan"] and after[1] == str(events[HIKE_JORDAN].id)
        assert [event.content for event in listed] == [PASTA, INTRODUCTION]


class TestRepeatedWeClaims:
    async def test_the_same_we_claim_in_two_user_turns_of_a_session_merges(self, config, user):
        """Criterion 4: as a named speaker's repeated I claim does."""
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, TOKYO, REPLY, TOKYO]})
            nodes = await engine.query_nodes(user_id=user, lifecycle_states=list(LifecycleState), limit=1000)
            copies = [node for node in nodes if node.content == TOKYO and node.node_type != NodeType.NOTE]
            found = per_claim(await group_links(engine, user))
        [current] = [node for node in copies if node.lifecycle_state in (LifecycleState.TENTATIVE,
                                                                         LifecycleState.STABLE)]
        [earlier] = [node for node in copies if node.id != current.id]
        assert earlier.lifecycle_state == LifecycleState.SUPERSEDED
        assert current.metadata["merged_claim_ids"] == [str(earlier.id)]
        assert len(current.evidence_refs) == 2 and found == {TOKYO: {OWNER}}

    async def test_it_merges_on_the_batched_path_too(self, config, user):
        async with MemoryEngine.open(linked(config, extraction_batch_target_tokens=2048)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, TOKYO, REPLY, TOKYO]}, batched=True)
            active = [node for node in await engine.query_nodes(user_id=user)
                      if node.content == TOKYO and node.node_type != NodeType.NOTE]
        assert len(active) == 1 and len(active[0].metadata["merged_claim_ids"]) == 1

    async def test_the_same_claim_in_another_session_stays_separate(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, TOKYO], "s2": [TOKYO]})
            active = [node for node in await engine.query_nodes(user_id=user)
                      if node.content == TOKYO and node.node_type != NodeType.NOTE]
        assert len(active) == 2 and not any(node.metadata.get("merged_claim_ids") for node in active)

    @pytest.mark.parametrize("updates", [{"enable_owner_group_links": False}, {"enable_claim_merge": False}],
                             ids=["no-group-links", "no-claim-merge"])
    async def test_without_the_group_links_or_claim_merge_both_stay(self, config, user, updates):
        async with MemoryEngine.open(linked(config, **updates)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, TOKYO, REPLY, TOKYO]})
            active = [node for node in await engine.query_nodes(user_id=user)
                      if node.content == TOKYO and node.node_type != NodeType.NOTE]
        assert len(active) == 2


class TestGuards:
    """The review's cases: another speaker's claim, a partial name, a failed link, a long roster, the lookup cost."""

    async def test_another_speakers_identical_we_claim_does_not_retire_the_owners(self, config, user):
        async def extract(content, **_):
            return ExtractionResult.model_validate(RESULTS[content])

        turns = [(DANA_INTRODUCTION, "Dana"), (INTRODUCTION, OWNER), (HIKE_DANA, OWNER), (TOKYO, OWNER), (TOKYO, "Dana")]
        async with MemoryEngine.open(linked(config)) as engine:
            engine._pipeline._extraction_provider.extract = extract
            for minute, (content, speaker) in enumerate(turns):
                await engine.ingest(content, user_id=user, role="user", speaker=speaker, session_id="s1",
                                    event_time=T0 + timedelta(minutes=minute), wait_for_extraction=True)
            nodes = await engine.query_nodes(user_id=user, lifecycle_states=list(LifecycleState), limit=1000)
            copies = [node for node in nodes if node.content == TOKYO and node.node_type != NodeType.NOTE]
            found = await group_links(engine, user)
        assert len(copies) == 2 and all(node.lifecycle_state != LifecycleState.SUPERSEDED for node in copies)
        assert not any(node.metadata.get("merged_claim_ids") for node in copies)
        # Alex's claim links Alex and Dana, whom Alex named; Dana's links Dana only.
        assert found == sorted([(TOKYO, OWNER), (TOKYO, "Dana"), (TOKYO, "Dana")])

    async def test_a_partial_name_does_not_link_another_person(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, {"s0": [INTRODUCTION, COLLEAGUE], "s1": [HIKE_JORDAN_LEE, TOKYO]})
            found = per_claim(await group_links(engine, user))
        assert found == {TOKYO: {OWNER}}

    def test_only_a_weekday_month_or_title_is_dropped_from_the_front_of_a_name(self):
        assert _candidate_names("Jordan Lee") == ["Jordan Lee"]
        assert _candidate_names("Sunday Jordan") == ["Sunday Jordan", "Jordan"]
        assert _candidate_names("Coach Taylor") == ["Coach Taylor", "Taylor"]
        assert _candidate_names("June Lee") == ["June Lee"]

    async def test_a_failed_member_link_costs_no_claim_its_owner_link(self, config, user):
        async with MemoryEngine.open(linked(config, enable_owner_group_links=False)) as engine:
            await chat(engine, user, DEFECTS)
            claims = [node for node in await engine.query_nodes(user_id=user) if node.content in (CITIES, DROPPED)]
            [event_id] = {str(ref) for node in claims for ref in node.evidence_refs}
            [jordan] = [node for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)
                        if node.content == "Jordan"]

            async def create_edge(edge):
                if edge.target_id == jordan.id:
                    raise RuntimeError("write failed")
                return await engine._graph_store.create_edge(edge)

            made = await link_group_claims(event_id, claims, user_id=user, graph_store=engine._graph_store,
                                           event_store=engine._event_store, create_edge=create_edge)
            found = per_claim(await group_links(engine, user))
        assert made == 2 and found == {CITIES: {OWNER}, DROPPED: {OWNER}}

    async def test_an_event_of_another_user_gets_no_link(self, config, user):
        async with MemoryEngine.open(linked(config, enable_owner_group_links=False)) as engine:
            await chat(engine, user, DEFECTS)
            claims = [node for node in await engine.query_nodes(user_id=user) if node.content in (CITIES, DROPPED)]
            [event_id] = {str(ref) for node in claims for ref in node.evidence_refs}
            made = await link_group_claims(event_id, claims, user_id=f"{user}-other", graph_store=engine._graph_store,
                                           event_store=engine._event_store,
                                           create_edge=engine._graph_store.create_edge)
            assert made == 0 and await group_links(engine, user) == []

    async def test_a_claim_whose_sentence_names_the_owner_still_merges(self, config, user):
        async with MemoryEngine.open(linked(config, enable_mention_links=True)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, KYOTO_NAMED, REPLY, KYOTO_NAMED]})
            active = [node for node in await engine.query_nodes(user_id=user)
                      if node.content == KYOTO_NAMED and node.node_type != NodeType.NOTE]
        assert len(active) == 1 and len(active[0].metadata["merged_claim_ids"]) == 1

    async def test_a_time_without_a_zone_is_read_as_utc(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, PASTA]})
            first = min(await engine.get_events(user), key=lambda event: event.timestamp)
            naive = first.timestamp.astimezone(timezone.utc).replace(tzinfo=None)
            listed = await engine.get_events(user, until=naive)
        assert [event.id for event in listed] == [first.id]

    def test_a_long_roster_is_read_in_bounded_time_and_names_no_group(self):
        started = time.perf_counter()
        assert named_groups("Jordan, " * 1000 + "and I went home.") == []
        assert named_groups("Jordan, " * 40_000 + "and I went home.") == []
        assert named_groups(", ".join(["Jordan"] * 15) + " and I went home.") == [tuple(["Jordan"] * 15)]
        assert time.perf_counter() - started < 2.0


class _Graph:
    """The two reads the merge lookup makes, counted."""

    def __init__(self, edges, nodes):
        self.edges, self.nodes = edges, {str(node.id): node for node in nodes}
        self.edge_reads, self.node_reads = 0, []

    async def get_edges(self, *, source_id=None, target_id=None, edge_type=None):
        self.edge_reads += 1
        return [edge for edge in self.edges
                if str(edge.source_id if source_id else edge.target_id) == (source_id or target_id)]

    async def get_nodes(self, node_ids):
        self.node_reads.extend(node_ids)
        return [self.nodes[node_id] for node_id in node_ids if node_id in self.nodes]


def _claim(subject="We", session="s1"):
    return MemoryNode(user_id="owner", scope=Scope.PERSONAL, node_type=NodeType.DECISION, content=TOKYO,
                      session_id=session, metadata={"subject": subject, "subject_entity_type": "person",
                                                    "predicate": "chose", "object": "Tokyo"},
                      event_time=T0, valid_from=T0)


def _link(claim, owner, event_id, **extra):
    return MemoryEdge(source_id=claim.id, target_id=owner.id, edge_type=EdgeType.MENTIONS, user_id="owner",
                      provenance_event_id=event_id, metadata={**GROUP_LINK_METADATA, **extra})


class TestMergeLookup:
    async def test_a_claim_not_about_we_reads_nothing(self):
        graph, new = _Graph([], []), _claim(subject="Alex")
        found = await find_repeated_group_claims(graph, "owner-id", new, claim_key(new, None), None,
                                                 session_events={"e1"}, exclude=set())
        assert found == [] and graph.edge_reads == 0 and graph.node_reads == []

    async def test_it_loads_only_owner_links_made_in_the_session(self):
        owner, here, elsewhere, named = _claim(subject="Owner"), _claim(), _claim(session="s2"), _claim()
        session_event, other_event = here.id, elsewhere.id  # any distinct IDs stand for the two turns
        graph = _Graph([
            _link(here, owner, session_event),
            _link(elsewhere, owner, other_event),
            _link(named, owner, session_event, named_in="intro"),
        ], [here, elsewhere, named])
        new = _claim()
        found = await find_repeated_group_claims(graph, str(owner.id), new, claim_key(new, None), None,
                                                 session_events={str(session_event)}, exclude=set())
        assert graph.node_reads == [str(here.id)] and [node.id for node in found] == [here.id]


class TestReingest:
    """``reingest()`` plans through the same function as ``ingest()``, so a re-derived source gets the same links."""

    @staticmethod
    def reingesting(base):
        return linked(base, enable_reingest=True, extraction_batch_target_tokens=2048)

    async def test_a_reingested_chat_gets_the_links_a_fresh_ingest_gives(self, config, user):
        async with MemoryEngine.open(linked(config, enable_owner_group_links=False)) as engine:
            await chat(engine, user, DEFECTS)
            await engine.process_pending(user_id=user)
            assert await group_links(engine, user) == []
        async with MemoryEngine.open(self.reingesting(config)) as engine:
            async def extract_batch(turns):
                return {"turns": [{**RESULTS[turn.content], "turn": turn.index} for turn in turns]}

            engine._pipeline._extraction_provider.extract_batch = extract_batch
            result = await engine.reingest(user)
            reingested = per_claim(await group_links(engine, user))
        async with MemoryEngine.open(linked(config)) as engine:
            fresh_owner = f"{user}-fresh"
            await chat(engine, fresh_owner, DEFECTS)
            fresh = per_claim(await group_links(engine, fresh_owner))
        assert result.processed == 4
        assert reingested == fresh == {CITIES: {OWNER, "Jordan"}, DROPPED: {OWNER, "Jordan"},
                                       POSTGRES: {OWNER}, SELF_HOSTING: {OWNER}}

    async def test_a_reingested_session_merges_a_repeated_we_claim_as_ingest_does(self, config, user):
        async with MemoryEngine.open(linked(config, enable_owner_group_links=False)) as engine:
            await chat(engine, user, {"s1": [INTRODUCTION, TOKYO, REPLY, TOKYO]})
            await engine.process_pending(user_id=user)
            before = [node for node in await engine.query_nodes(user_id=user)
                      if node.content == TOKYO and node.node_type != NodeType.NOTE]
        async with MemoryEngine.open(self.reingesting(config)) as engine:
            async def extract_batch(turns):
                return {"turns": [{**RESULTS[turn.content], "turn": turn.index} for turn in turns]}

            engine._pipeline._extraction_provider.extract_batch = extract_batch
            await engine.reingest(user)
            active = [node for node in await engine.query_nodes(user_id=user)
                      if node.content == TOKYO and node.node_type != NodeType.NOTE]
        # Without the group links the two copies stayed apart; re-derived with them, one current record holds
        # both turns, as after ingest() with the links on. It also folds the second turn's own earlier claim.
        assert len(before) == 2
        assert len(active) == 1 and {str(ref) for ref in active[0].evidence_refs} == {
            str(ref) for node in before for ref in node.evidence_refs}
