"""Data builders and cases for the contract scorecard.

The scenes come from ``memory_bank/thesis-2026-11-10/embodied_probe.py`` (robot scenes S1, S2, S4, S5, S6 and S8)
and ``chat_check.py`` (the chat). A builder takes a world (the names and sentences a scene is made of) and returns
the items to write, so the visible set (``tests/contract/world.py``) and the hidden set
(``tests/contract_holdout/world.py``) run the same code on different data.

Every builder seeds its own ``random.Random(world.seed)``, as the probe did for each scene, so the visible world
writes the probe's exact sentences in the probe's order. The robot builders return items for ``ingest_fast_many``.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# The shape of every scene. Both worlds use the same numbers, so a world changes the data and never the test.
CHURN_SPACINGS = (("10min", timedelta(minutes=10)), ("3days", timedelta(days=3)))
CHURN_MOVES = (2, 5, 12)
CHURN_SAMPLE = 6          # objects asked about per (spacing, moves); the pack still holds every object
FLOOD_TOTAL = 400         # memories in the flood
FLOOD_REPEATS = 12        # one repeated event spread through the flood, as in the probe
WINDOW_RECORDS = 480      # one sighting every 3 minutes for a day
HOP_ROUTINE = 300
HOP_SAMPLE = 8
UNKNOWN_SEEN = 10
UNKNOWN_ROUTINE = 300
UNKNOWN_ASKED = 6


@dataclass(frozen=True)
class RobotWorld:
    """The names and sentence templates the robot scenes are made of."""

    seed: int
    user: str
    base: datetime
    objects: tuple[str, ...]                    # 20
    places: tuple[str, ...]
    rooms: tuple[str, ...]
    routine: tuple[str, ...]                    # 3 templates over {Room}, {room}, {n1} (1 to 9) and {n2} (18 to 24)
    routine_markers: tuple[str, ...]            # a context line holding one of these is a routine line
    placed: str                                 # "The {obj} is on the {place}."
    where_now: str                              # "Where is the {obj} now?"
    rare: tuple[tuple[str, str, str], ...]      # 8 of (tag, event, question)
    repeated_event: str                         # "... (trip {n})."
    people: tuple[str, ...]                     # 20, paired with objects in order
    request: str                                # "{person} asked the robot to bring the {obj} later."
    thing_asked: str                            # "Where is the thing {person} asked for?"
    unseen: tuple[str, ...]                     # objects never written
    window_day: datetime
    window_hours: tuple[int, int]               # [start, end) in hours of window_day
    window_things: tuple[str, ...]
    window_sentence: str                        # over {thing} and {time} (HH:MM)
    window_question: str                        # names the window in words
    window_plain_question: str                  # asked with event_time_from and event_time_to
    repeat_questions: tuple[tuple[str, str], ...]   # (tag, question)


@dataclass(frozen=True)
class ChatQuestion:
    tag: str
    question: str
    answers: tuple[str, ...]    # every one must rank in the top 3 (case-insensitive substring of a result)
    stale: tuple[str, ...]      # none may rank above the worst-ranked answer


@dataclass(frozen=True)
class RetireItem:
    """A claim the chat later corrects, so it should be retired by the end: the claims extracted from the turn
    containing ``turn`` whose predicate or object contains one of ``words`` (case-insensitive)."""

    tag: str
    turn: str
    words: tuple[str, ...]


@dataclass(frozen=True)
class ChatWorld:
    user: str
    speaker: str
    now: datetime
    sessions: tuple[tuple[datetime, tuple[tuple[str, str], ...]], ...]   # (start, ((role, text), ...))
    questions: tuple[ChatQuestion, ...]
    unanswerable: ChatQuestion
    first: tuple[ChatQuestion, ...] = ()    # through ingest(): the one answer string must be in results[0]
    retire: tuple[RetireItem, ...] = ()     # through ingest(): claims the chat corrects, retired by the end


@dataclass(frozen=True)
class Case:
    """One test id: ``id`` names it and ``data`` holds what the test needs."""

    id: str
    data: tuple


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def item(content: str, when: datetime) -> dict:
    return {"content": content, "event_time": when, "role": "system"}


def routine(world: RobotWorld, rng: random.Random, index: int) -> str:
    # The probe built all three sentences before choosing one, so it drew both numbers every time; so does this.
    room = world.rooms[index % len(world.rooms)]
    n1, n2 = rng.randint(1, 9), rng.randint(18, 24)
    return rng.choice(world.routine).format(room=room, Room=room.capitalize(), n1=n1, n2=n2)


# Builders -------------------------------------------------------------------------------------------------------

def churn(world: RobotWorld, spacing: timedelta, moves: int) -> tuple[list[dict], dict[str, str], datetime]:
    """S1: every object moves ``moves`` times, ``spacing`` apart. Returns items, the newest place, and now."""
    rng = random.Random(world.seed)
    truth: dict[str, str] = {}
    clock = world.base
    items = []
    for _ in range(moves):
        for obj in world.objects:
            place = rng.choice([p for p in world.places if p != truth.get(obj)])
            truth[obj] = place
            items.append(item(world.placed.format(obj=obj, place=place), clock))
        clock += spacing
    return items, truth, clock


def churn_stated(world: RobotWorld, spacing: timedelta, moves: int) -> tuple[list[dict], dict[str, str], datetime]:
    """S1 written as caller-stated facts: the same moves, each an ingest_batch() message with its assertion.

    The message keeps the placement sentence as its content and states the fact beside it: the object
    ``located_in`` the place, status current. Returns the messages, the newest place and now.
    """
    items, truth, now = churn(world, spacing, moves)
    messages = []
    for item in items:
        obj, place = _placement(world, item["content"])
        messages.append({**item, "assertions": [{
            "subject": obj, "subject_type": "product", "relation": "located_in",
            "object": place, "object_type": "location", "status": "current",
        }]})
    return messages, truth, now


def _placement(world: RobotWorld, sentence: str) -> tuple[str, str]:
    """The object and place of a placement sentence this world wrote."""
    for obj in world.objects:
        for place in world.places:
            if world.placed.format(obj=obj, place=place) == sentence:
                return obj, place
    raise ValueError(f"not a placement sentence of this world: {sentence!r}")


def flood(world: RobotWorld) -> tuple[list[dict], datetime]:
    """S2: FLOOD_TOTAL observations 20 seconds apart, mostly routine, with the rare events spread through them."""
    rng = random.Random(world.seed)
    special = {int(FLOOD_TOTAL * (i + 1) / (len(world.rare) + 1)): event for i, (_, event, _) in enumerate(world.rare)}
    for i in range(FLOOD_REPEATS):
        special[int(FLOOD_TOTAL * (i + 0.5) / FLOOD_REPEATS)] = world.repeated_event.format(n=i + 1)
    items = [
        item(special.get(index) or routine(world, rng, index), world.base + timedelta(seconds=20 * index))
        for index in range(FLOOD_TOTAL)
    ]
    return items, world.base + timedelta(seconds=20 * FLOOD_TOTAL)


def window(world: RobotWorld) -> tuple[list[dict], set[str], datetime]:
    """S4: one sighting every 3 minutes for a day. Returns items, the texts inside the window, and now."""
    rng = random.Random(world.seed)
    start, end = world.window_hours
    items, inside = [], set()
    for index in range(WINDOW_RECORDS):
        when = world.window_day + timedelta(minutes=3 * index)
        text = world.window_sentence.format(thing=rng.choice(world.window_things), time=f"{when:%H:%M}")
        if start <= when.hour < end:
            inside.add(text)
        items.append(item(text, when))
    return items, inside, world.window_day + timedelta(days=1, hours=9)


def hops(world: RobotWorld) -> tuple[list[dict], dict[str, tuple[str, str]], datetime]:
    """S5: each person asks for an object, then the object's place is seen. Returns items, person -> pair, now."""
    rng = random.Random(world.seed)
    clock = world.base
    items, pairs = [], {}
    for person, obj in zip(world.people, world.objects):
        first = world.request.format(person=person, obj=obj)
        second = world.placed.format(obj=obj, place=rng.choice(world.places))
        pairs[person] = (first, second)
        items.append(item(first, clock))
        clock += timedelta(minutes=7)
        items.append(item(second, clock))
        clock += timedelta(minutes=7)
    for index in range(HOP_ROUTINE):
        items.append(item(routine(world, rng, index), clock))
        clock += timedelta(seconds=30)
    return items, pairs, clock


def unknown(world: RobotWorld) -> tuple[list[dict], datetime]:
    """S6: ten objects seen once, then routine. The questions ask about objects never seen."""
    rng = random.Random(world.seed)
    clock = world.base
    items = []
    for obj in world.objects[:UNKNOWN_SEEN]:
        items.append(item(world.placed.format(obj=obj, place=rng.choice(world.places)), clock))
        clock += timedelta(minutes=5)
    for index in range(UNKNOWN_ROUTINE):
        items.append(item(routine(world, rng, index), clock))
        clock += timedelta(seconds=30)
    return items, clock


def chat_messages(world: ChatWorld) -> list[dict]:
    """The chat as store() arguments: one minute apart within a session, the owner's turns carry the speaker."""
    messages = []
    for number, (when, turns) in enumerate(world.sessions, start=1):
        for index, (role, text) in enumerate(turns):
            messages.append({
                "content": text, "role": role, "event_time": when.replace(minute=index),
                "session_id": f"s{number}", "speaker": world.speaker if role == "user" else None,
            })
    return messages


def chat_sessions(world: ChatWorld) -> list[tuple[str, list[dict]]]:
    """The chat as ingest_batch() calls, one per session: the same turns, times and speakers as chat_messages."""
    sessions: dict[str, list[dict]] = {}
    for message in chat_messages(world):
        turn = {"content": message["content"], "role": message["role"], "event_time": message["event_time"]}
        if message["speaker"] is not None:
            turn["speaker"] = message["speaker"]
        sessions.setdefault(message["session_id"], []).append(turn)
    return list(sessions.items())


# Cases ----------------------------------------------------------------------------------------------------------

def churn_cases(world: RobotWorld) -> list[Case]:
    cases = []
    for label, spacing in CHURN_SPACINGS:
        for moves in CHURN_MOVES:
            sample = random.Random(f"{world.seed}:churn:{label}:{moves}").sample(world.objects, CHURN_SAMPLE)
            cases += [Case(f"churn-{label}-{moves}moves-{slug(obj)}", (label, moves, obj)) for obj in sample]
    return cases


def churn_stated_cases(world: RobotWorld) -> list[Case]:
    """The churn cases again, for the pack written through assertions (the same sampled objects)."""
    return [Case(case.id.replace("churn-", "stated-", 1), case.data) for case in churn_cases(world)]


def rare_cases(world: RobotWorld) -> list[Case]:
    return [Case(f"flood-rare-{tag}", (event, question)) for tag, event, question in world.rare]


def flood_share_cases(world: RobotWorld) -> list[Case]:
    return [Case("flood-routine-share", ())]


def window_cases(world: RobotWorld) -> list[Case]:
    return [Case("window-natural", ("natural",)), Case("window-explicit", ("explicit",))]


def hop_cases(world: RobotWorld) -> list[Case]:
    picked = sorted(random.Random(f"{world.seed}:hops").sample(range(len(world.people)), HOP_SAMPLE))
    return [Case(f"hops-{slug(world.people[index])}", (world.people[index],)) for index in picked]


def unseen_cases(world: RobotWorld) -> list[Case]:
    return [Case(f"unseen-{slug(obj)}", (obj,)) for obj in world.unseen[:UNKNOWN_ASKED]]


def repeat_cases(world: RobotWorld) -> list[Case]:
    return [Case(f"repeat-{tag}", (question,)) for tag, question in world.repeat_questions]


def chat_answer_cases(world: ChatWorld) -> list[Case]:
    return [Case(f"chat-answer-{q.tag}", (q,)) for q in world.questions if q.answers]


def chat_stale_cases(world: ChatWorld) -> list[Case]:
    return [Case(f"chat-stale-{q.tag}", (q,)) for q in world.questions if q.answers and q.stale]


def chat_unanswerable_cases(world: ChatWorld) -> list[Case]:
    return [Case(f"chat-unanswerable-{world.unanswerable.tag}", (world.unanswerable,))]


def ingest_first_cases(world: ChatWorld) -> list[Case]:
    return [Case(f"chat-first-{q.tag}", (q,)) for q in world.first]


def ingest_ledger_cases(world: ChatWorld) -> list[Case]:
    return [Case(f"chat-ledger-{q.tag}", (q,)) for q in (*world.questions, world.unanswerable)]


def ingest_retire_cases(world: ChatWorld) -> list[Case]:
    return [Case(f"retired-{item.tag}", (item,)) for item in world.retire]


def ingest_true_claim_cases(world: ChatWorld) -> list[Case]:
    return [Case("no-true-claim-retired", ())] if world.retire else []


ROBOT_CASES = {
    "churn_case": churn_cases,
    "stated_case": churn_stated_cases,
    "rare_case": rare_cases,
    "flood_share_case": flood_share_cases,
    "window_case": window_cases,
    "hop_case": hop_cases,
    "unseen_case": unseen_cases,
    "repeat_case": repeat_cases,
}
CHAT_CASES = {
    "chat_answer_case": chat_answer_cases,
    "chat_stale_case": chat_stale_cases,
    "chat_unanswerable_case": chat_unanswerable_cases,
}
# The chat through ingest(): the store() chat's cases under the same ids, plus two of its own. Their expected
# failures are listed apart (XFAIL_INGEST), since the ids repeat.
INGEST_CASES = {
    "ingest_answer_case": chat_answer_cases,
    "ingest_stale_case": chat_stale_cases,
    "ingest_unanswerable_case": chat_unanswerable_cases,
    "ingest_first_case": ingest_first_cases,
    "ingest_ledger_case": ingest_ledger_cases,
    "ingest_retire_case": ingest_retire_cases,
    "ingest_true_claim_case": ingest_true_claim_cases,
}


def case_ids(builders: dict, world) -> set[str]:
    ids = [case.id for build in builders.values() for case in build(world)]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    return set(ids)


def all_case_ids(robot: RobotWorld, chat: ChatWorld) -> set[str]:
    """The ids of the robot and store() chat cases (the ids XFAIL may name)."""
    robot_ids, chat_ids = case_ids(ROBOT_CASES, robot), case_ids(CHAT_CASES, chat)
    assert not robot_ids & chat_ids, "case ids must be unique"
    return robot_ids | chat_ids


# The groups of the end-of-session table: (test file, case id prefix, label), first match wins.
GROUPS = (
    ("test_state_churn.py", "churn-10min", "S1 churn, 10 min apart"),
    ("test_state_churn.py", "churn-3days", "S1 churn, 3 days apart"),
    ("test_state_churn.py", "stated-10min", "S1 churn stated, 10 min apart"),
    ("test_state_churn.py", "stated-3days", "S1 churn stated, 3 days apart"),
    ("test_flood.py", "flood-rare", "S2 flood, rare event in top 5"),
    ("test_flood.py", "flood-routine", "S2 flood, routine share"),
    ("test_time_window.py", "window", "S4 time window"),
    ("test_two_hops.py", "hops", "S5 two hops"),
    ("test_unknown.py", "unseen", "S6 unknown"),
    ("test_determinism.py", "repeat", "S8 determinism"),
    ("test_chat_store.py", "chat-answer", "chat store(), answers in top 3"),
    ("test_chat_store.py", "chat-stale", "chat store(), no stale above"),
    ("test_chat_store.py", "chat-unanswerable", "chat store(), unanswerable"),
    ("test_chat_ingest.py", "chat-answer", "chat ingest(), answers in top 3"),
    ("test_chat_ingest.py", "chat-stale", "chat ingest(), no stale above"),
    ("test_chat_ingest.py", "chat-unanswerable", "chat ingest(), unanswerable"),
    ("test_chat_ingest.py", "chat-first", "chat ingest(), answer first"),
    ("test_chat_ingest.py", "chat-ledger", "chat ingest(), ended never above current"),
    ("test_chat_ingest.py", "retired-", "chat ingest(), corrected claim retired"),
    ("test_chat_ingest.py", "no-true-claim", "chat ingest(), no true claim retired"),
)


def group_of(nodeid: str) -> str:
    """The table group of a test id, or "other"."""
    path, _, name = nodeid.partition("::")
    filename = path.rsplit("/", 1)[-1]
    case = name[name.find("[") + 1:-1] if name.endswith("]") else ""
    return next((label for file, prefix, label in GROUPS if file == filename and case.startswith(prefix)), "other")
