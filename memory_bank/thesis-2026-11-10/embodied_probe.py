"""Embodied memory probe: a fast check of store() and retrieve() on robot-shaped work.

Written for memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md. It makes no model
call (local embedding only), needs no dataset, and runs in a few minutes.
It is a diagnostic, not a benchmark: the scenes are synthetic, each has a known
answer, and one run is one sample of a deterministic system.

Run:
    PYTHONPATH=. uv run python memory_bank/thesis-2026-11-10/embodied_probe.py \
        --out memory_bank/thesis-2026-11-10/embodied_probe_results.json

Scenes (S1 to S8):
    S1 state churn      an object moves K times; "Where is it now?" must rank the newest place first
    S2 flood and rare   thousands of routine observations hide a few rare events
    S3 counting         "How many times ...?" needs every one of N events in the context
    S4 time window      "between 15:00 and 16:00" must return only records in the window
    S5 two hops         the answer needs two records that share no words with the question
    S6 abstention       a question about a thing never seen should look different from a real hit
    S7 scale            write rate and retrieve latency as the store grows
    S8 determinism      the same question twice must return the same records in the same order
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone

from prme.client import MemoryClient

USER = "robot-1"
BASE = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)

OBJECTS = [
    "red mug", "house keys", "blue notebook", "phone charger", "reading glasses", "tv remote",
    "black umbrella", "dog leash", "screwdriver set", "first aid kit", "yoga mat", "water bottle",
    "laptop bag", "sewing kit", "flashlight", "tape measure", "car keys", "passport folder",
    "wool scarf", "guitar tuner",
]
PLACES = [
    "kitchen counter", "dishwasher", "living room table", "office desk", "hallway shelf",
    "bedroom nightstand", "garage workbench", "bathroom cabinet", "front porch", "laundry room",
    "dining table", "sofa armrest", "bookshelf", "entry bench", "pantry", "balcony",
    "coat closet", "study windowsill", "basement shelf", "kids room floor", "guest room dresser",
    "piano top",
]
ROOMS = ["hallway", "kitchen", "garage", "living room", "office", "bedroom"]


def new_client() -> tuple[MemoryClient, str]:
    directory = tempfile.mkdtemp(prefix="prme-embodied-")
    return MemoryClient(os.path.join(directory, "pack")), directory


def ids(response, n: int) -> list[str]:
    return [candidate.node.content for candidate in response.results[:n]]


def pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


# S1 -------------------------------------------------------------------------------------------
def s1_state_churn(spacing: timedelta, updates: int, rng: random.Random) -> dict:
    client, _ = new_client()
    truth: dict[str, str] = {}
    clock = BASE
    try:
        for step in range(updates):
            for obj in OBJECTS:
                place = rng.choice([p for p in PLACES if p != truth.get(obj)])
                truth[obj] = place
                client.store(f"The {obj} is on the {place}.", user_id=USER, event_time=clock, role="system")
            clock += spacing
        top1 = top3_all_newest = context_first = 0
        for obj in OBJECTS:
            response = client.retrieve(f"Where is the {obj} now?", user_id=USER, reference_time=clock)
            answer = f"The {obj} is on the {truth[obj]}."
            ranked = ids(response, 5)
            top1 += bool(ranked) and ranked[0] == answer
            context = response.bundle.rendered_context
            lines = [line for line in context.splitlines() if obj in line]
            context_first += bool(lines) and truth[obj] in lines[0]
        return {
            "updates": updates, "spacing": str(spacing), "questions": len(OBJECTS),
            "newest_ranked_first": top1, "newest_is_first_context_line_for_object": context_first,
            "newest_ranked_first_pct": pct(top1, len(OBJECTS)),
        }
    finally:
        client.close()


# S2 and S7 ------------------------------------------------------------------------------------
RARE = [
    ("Water is pooling under the kitchen sink.", "Is there a leak anywhere?"),
    ("The front door was left open for eleven minutes.", "Was any entrance left unlocked or open?"),
    ("Smoke detector in the garage chirped: low battery.", "Does any alarm need maintenance?"),
    ("Grandma took her blood pressure pill at the dining table.", "Did she take her medication?"),
    ("A package was dropped on the front porch by a courier.", "Did a delivery arrive?"),
    ("The stove burner was on with no pot on it.", "Was any appliance left running unsafely?"),
    ("The cat knocked a vase off the bookshelf and it broke.", "Did anything get broken?"),
    ("An unknown person stood at the back gate for two minutes.", "Was there a stranger near the house?"),
]


def routine(rng: random.Random, index: int) -> str:
    room = ROOMS[index % len(ROOMS)]
    return rng.choice([
        f"{room.capitalize()} camera: no people, no obstacles, light level {rng.randint(1, 9)}.",
        f"Patrol pass through the {room}: floor clear, doors as before, temperature {rng.randint(18, 24)} C.",
        f"{room.capitalize()} check: nothing moved since the last pass.",
    ])


def s2_flood(total: int, rng: random.Random) -> dict:
    client, _ = new_client()
    try:
        rare_at = {int(total * (i + 1) / (len(RARE) + 1)): text for i, (text, _) in enumerate(RARE)}
        dog_events = 12
        for i in range(dog_events):
            rare_at[int(total * (i + 0.5) / dog_events)] = f"The dog went out to the back yard through the pet door (trip {i + 1})."
        started = time.perf_counter()
        store_times = []
        for index in range(total):
            text = rare_at.get(index) or routine(rng, index)
            one = time.perf_counter()
            client.store(text, user_id=USER, event_time=BASE + timedelta(seconds=20 * index), role="system")
            store_times.append(time.perf_counter() - one)
        write_seconds = time.perf_counter() - started
        end = BASE + timedelta(seconds=20 * total)
        top5 = in_context = 0
        latencies, routine_lines, lines_total = [], 0, 0
        for text, question in RARE:
            one = time.perf_counter()
            response = client.retrieve(question, user_id=USER, reference_time=end)
            latencies.append((time.perf_counter() - one) * 1000)
            top5 += text in ids(response, 5)
            context = response.bundle.rendered_context
            in_context += text in context
            body = [line for line in context.splitlines() if line.strip()]
            lines_total += len(body)
            routine_lines += sum(("camera:" in line or "Patrol pass" in line or "check:" in line) for line in body)
        count = client.retrieve("How many times did the dog go outside today?", user_id=USER, reference_time=end)
        dogs_found = count.bundle.rendered_context.count("The dog went out to the back yard")
        return {
            "memories": total, "rare_events": len(RARE),
            "count_question_events": dog_events, "count_question_events_in_context": dogs_found,
            "count_question_records_packed": count.bundle.included_count,
            "rare_in_top5": top5, "rare_in_context": in_context,
            "context_lines_that_are_routine_pct": pct(routine_lines, lines_total),
            "store_per_second": round(total / write_seconds, 1),
            "store_p50_ms": round(statistics.median(store_times) * 1000, 1),
            "store_p95_ms": round(sorted(store_times)[int(len(store_times) * 0.95)] * 1000, 1),
            "retrieve_p50_ms": round(statistics.median(latencies), 1),
            "retrieve_max_ms": round(max(latencies), 1),
        }
    finally:
        client.close()


def s7_fast_path(total: int, rng: random.Random) -> dict:
    """The deferred write path: accept events now, index them later."""
    client, _ = new_client()
    try:
        items = [
            {"content": routine(rng, index), "event_time": BASE + timedelta(seconds=20 * index), "role": "system"}
            for index in range(total)
        ]
        started = time.perf_counter()
        for offset in range(0, total, 500):
            client.ingest_fast_many(items[offset:offset + 500], user_id=USER)
        accept_seconds = time.perf_counter() - started
        started = time.perf_counter()
        while True:
            result = client.process_pending(user_id=USER, budget_ms=5000)
            if not getattr(result, "pending", 0):
                break
        index_seconds = time.perf_counter() - started
        one = time.perf_counter()
        client.retrieve("Is the hallway clear?", user_id=USER, reference_time=BASE + timedelta(days=2))
        return {
            "memories": total,
            "accept_per_second": round(total / accept_seconds, 1),
            "index_per_second": round(total / index_seconds, 1) if index_seconds else None,
            "process_result": str(result)[:200],
            "first_retrieve_ms": round((time.perf_counter() - one) * 1000, 1),
        }
    finally:
        client.close()


# S3 -------------------------------------------------------------------------------------------
def s3_counting(counts: list[int], rng: random.Random) -> list[dict]:
    rows = []
    for count in counts:
        client, _ = new_client()
        try:
            total = 600
            slots = set(rng.sample(range(total), count))
            for index in range(total):
                when = BASE + timedelta(minutes=2 * index)
                if index in slots:
                    text = f"The dog went out to the back yard through the pet door at {when:%H:%M}."
                else:
                    text = routine(rng, index)
                client.store(text, user_id=USER, event_time=when, role="system")
            response = client.retrieve(
                "How many times did the dog go outside today?", user_id=USER,
                reference_time=BASE + timedelta(minutes=2 * total),
            )
            context = response.bundle.rendered_context
            found = context.count("The dog went out to the back yard")
            rows.append({
                "events": count, "in_context": found, "all_in_context": found >= count,
                "aggregation_coverage": str(response.metadata.aggregation_coverage)[:160],
                "tokens_used": response.bundle.tokens_used,
            })
        finally:
            client.close()
    return rows


# S4 -------------------------------------------------------------------------------------------
def s4_time_window(rng: random.Random) -> dict:
    client, _ = new_client()
    try:
        day = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
        things = ["a delivery van", "the neighbor's cat", "a cyclist", "the mail carrier", "a garbage truck",
                  "two children", "a jogger", "a white sedan", "a delivery drone", "a street sweeper"]
        inside = []
        for index in range(480):
            when = day + timedelta(minutes=3 * index)
            text = f"Garage camera saw {rng.choice(things)} pass the driveway at {when:%H:%M}."
            if 15 <= when.hour < 16:
                inside.append(text)
            client.store(text, user_id=USER, event_time=when, role="system")
        reference = day + timedelta(days=1, hours=9)
        natural = client.retrieve(
            "What did the garage camera see between 15:00 and 16:00 on November 3?",
            user_id=USER, reference_time=reference,
        )
        explicit = client.retrieve(
            "What did the garage camera see?", user_id=USER, reference_time=reference,
            event_time_from=day + timedelta(hours=15), event_time_to=day + timedelta(hours=16),
        )

        def score(response) -> dict:
            top = ids(response, 10)
            context = response.bundle.rendered_context
            return {
                "top10_in_window": sum(text in inside for text in top),
                "window_records_in_context": sum(text in context for text in inside),
                "results": len(response.results),
            }
        return {"records_in_window": len(inside), "natural_language": score(natural), "explicit_filter": score(explicit)}
    finally:
        client.close()


# S5 -------------------------------------------------------------------------------------------
PEOPLE = ["Dana", "Ravi", "Mei", "Tomas", "Aisha", "Jonas", "Priya", "Leo", "Nora", "Omar",
          "Ines", "Felix", "Hana", "Marco", "Zoe", "Ivan", "Lena", "Kofi", "Sara", "Ben"]


def s5_two_hops(rng: random.Random) -> dict:
    client, _ = new_client()
    try:
        pairs = []
        clock = BASE
        for person, obj in zip(PEOPLE, OBJECTS):
            place = rng.choice(PLACES)
            first = f"{person} asked the robot to bring the {obj} later."
            second = f"The {obj} is on the {place}."
            pairs.append((person, first, second))
            client.store(first, user_id=USER, event_time=clock, role="system")
            clock += timedelta(minutes=7)
            client.store(second, user_id=USER, event_time=clock, role="system")
            clock += timedelta(minutes=7)
        for index in range(300):
            client.store(routine(rng, index), user_id=USER, event_time=clock, role="system")
            clock += timedelta(seconds=30)
        both5 = both_context = first_only = 0
        for person, first, second in pairs:
            response = client.retrieve(
                f"Where is the thing {person} asked for?", user_id=USER, reference_time=clock,
            )
            top = ids(response, 5)
            context = response.bundle.rendered_context
            both5 += first in top and second in top
            first_only += first in top and second not in top
            both_context += first in context and second in context
        return {"questions": len(pairs), "both_hops_in_top5": both5, "only_first_hop_in_top5": first_only,
                "both_hops_in_context": both_context}
    finally:
        client.close()


# S6 -------------------------------------------------------------------------------------------
def s6_abstention(rng: random.Random) -> dict:
    client, _ = new_client()
    try:
        clock = BASE
        seen = OBJECTS[:10]
        for obj in seen:
            client.store(f"The {obj} is on the {rng.choice(PLACES)}.", user_id=USER, event_time=clock, role="system")
            clock += timedelta(minutes=5)
        for index in range(300):
            client.store(routine(rng, index), user_id=USER, event_time=clock, role="system")
            clock += timedelta(seconds=30)
        unseen = ["violin case", "ski boots", "wedding album", "soldering iron", "tennis racket",
                  "bird feeder", "fondue pot", "telescope", "snow shovel", "typewriter"]

        def probe(obj: str) -> tuple[float, float, int]:
            response = client.retrieve(f"Where is the {obj} now?", user_id=USER, reference_time=clock)
            trace = response.results[0] if response.results else None
            semantic = getattr(trace, "semantic_score", 0.0) if trace else 0.0
            composite = getattr(trace, "composite_score", 0.0) if trace else 0.0
            return round(semantic, 3), round(composite, 4), response.bundle.included_count

        known = [probe(obj) for obj in seen]
        unknown = [probe(obj) for obj in unseen]
        return {
            "seen_top_semantic": [row[0] for row in known], "unseen_top_semantic": [row[0] for row in unknown],
            "seen_top_composite": [row[1] for row in known], "unseen_top_composite": [row[1] for row in unknown],
            "seen_records_packed": [row[2] for row in known], "unseen_records_packed": [row[2] for row in unknown],
            "composite_separates": min(row[1] for row in known) > max(row[1] for row in unknown),
            "semantic_separates": min(row[0] for row in known) > max(row[0] for row in unknown),
        }
    finally:
        client.close()


# S8 -------------------------------------------------------------------------------------------
def s8_determinism(rng: random.Random) -> dict:
    client, _ = new_client()
    try:
        clock = BASE
        for index in range(400):
            client.store(routine(rng, index), user_id=USER, event_time=clock, role="system")
            clock += timedelta(seconds=30)
        same = 0
        questions = ["Is the hallway clear?", "What is the temperature in the office?", "Did anything move in the garage?"]
        for question in questions:
            first = client.retrieve(question, user_id=USER, reference_time=clock)
            second = client.retrieve(question, user_id=USER, reference_time=clock)
            same += ([str(c.node.id) for c in first.results] == [str(c.node.id) for c in second.results]
                     and first.bundle.rendered_context == second.bundle.rendered_context)
        return {"questions": len(questions), "identical_twice": same}
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=None)
    parser.add_argument("--flood", type=int, default=3000, help="memories in the S2 flood")
    parser.add_argument("--seed", type=int, default=20261110)
    args = parser.parse_args()
    started = time.perf_counter()
    report: dict = {"seed": args.seed}

    def run(name: str, fn):
        one = time.perf_counter()
        report[name] = fn()
        print(f"{name}: {time.perf_counter() - one:.1f}s\n{json.dumps(report[name], indent=1)}", flush=True)

    run("S1_state_churn", lambda: [
        s1_state_churn(spacing, updates, random.Random(args.seed))
        for spacing in (timedelta(minutes=10), timedelta(days=3)) for updates in (2, 5, 12)
    ])
    run("S3_counting", lambda: s3_counting([3, 8, 20, 50], random.Random(args.seed)))
    run("S4_time_window", lambda: s4_time_window(random.Random(args.seed)))
    run("S5_two_hops", lambda: s5_two_hops(random.Random(args.seed)))
    run("S6_abstention", lambda: s6_abstention(random.Random(args.seed)))
    run("S8_determinism", lambda: s8_determinism(random.Random(args.seed)))
    run("S2_flood", lambda: s2_flood(args.flood, random.Random(args.seed)))
    run("S7_fast_path", lambda: s7_fast_path(args.flood, random.Random(args.seed)))
    report["seconds"] = round(time.perf_counter() - started, 1)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(report, handle, indent=1)
            handle.write("\n")
    print(f"total {report['seconds']}s")


if __name__ == "__main__":
    main()
