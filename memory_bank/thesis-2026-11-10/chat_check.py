"""Chat check: one hand-written 24-turn chat with 14 questions, through store() and through ingest().

Written for memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md. It is a small look at behavior, not a benchmark:
one conversation, one run. The store() side needs no model. The ingest() side makes a few extraction calls
through the local Ollama server (deepseek-v4.1-flash:cloud, the model the project's probes use).

    PYTHONPATH=. PRME_EXTRACTION__PROVIDER=ollama PRME_EXTRACTION__MODEL=deepseek-v4.1-flash:cloud \
      uv run python memory_bank/thesis-2026-11-10/chat_check.py --out memory_bank/thesis-2026-11-10/chat_check_results.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import tempfile
import time
from datetime import datetime, timezone

from prme.client import MemoryClient

U, A = "user", "assistant"
SESSIONS = [
    (datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc), [
        (U, "Hi, I'm planning meals for the week. I'm vegetarian, by the way."),
        (A, "Got it. I'd suggest a lentil curry on Monday and a mushroom risotto on Wednesday."),
        (U, "Sounds good. My sister Priya is visiting on Friday and she can't eat gluten."),
        (A, "Then for Friday I recommend rice noodles with tofu."),
        (U, "Please always give me quantities in metric units. I never know what a cup is."),
        (U, "I work as a nurse at St. Mary's hospital, mostly night shifts, so dinner for me is at about 6 am."),
    ]),
    (datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc), [
        (U, "I've started training for a half marathon. The race is on November 22 in Lisbon."),
        (A, "Nice. I suggest a 12-week plan with three runs a week and one long run on Sundays."),
        (U, "My longest run so far is 9 km. My knee hurt a bit after it."),
        (U, "I bought new shoes yesterday, Brooks Ghost, they cost 140 euros."),
        (A, "Good pick. Plan to replace them after about 700 km."),
        (U, "Oh and I'm thinking about getting a dog, maybe a greyhound, but I haven't decided."),
    ]),
    (datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc), [
        (U, "Big news: I quit the hospital. I start at a pediatric clinic on November 2, day shifts only."),
        (U, "Also I've started eating fish again, so I'm not strictly vegetarian anymore. Still no meat."),
        (A, "Understood, I'll include fish in your plans from now on."),
        (U, "Did 16 km on Sunday. Knee was fine this time."),
        (U, "Priya moved to Toronto last week, so no more Friday dinners with her."),
        (U, "We adopted a dog after all! Not a greyhound though, a beagle called Miso."),
    ]),
    (datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc), [
        (U, "The new job is great. I get home at 5:30 pm now."),
        (U, "I spent 60 euros on running gels and another 85 euros on a race belt and socks."),
        (U, "Longest run is now 19 km."),
        (U, "My partner Alex is allergic to peanuts, so keep that in mind for recipes."),
        (A, "Noted. I'll keep peanuts out of every recipe."),
        (U, "Miso chewed through my old running shoes, ha."),
    ]),
]
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)

# question, strings that must all be found (the answer), strings that are stale or wrong if ranked above the answer
QUESTIONS = [
    ("What does Sam do for work now?", ["pediatric clinic"], ["St. Mary"]),
    ("Is Sam vegetarian?", ["fish"], []),
    ("What food restrictions should I respect when cooking for Sam's household?", ["peanut", "meat"], []),
    ("What pet does Sam have?", ["beagle"], ["thinking about getting a dog"]),
    ("Where does Sam's sister live?", ["Toronto"], []),
    ("How should I format ingredient amounts?", ["metric"], []),
    ("How much has Sam spent on running gear in total?", ["140", "60 euros", "85"], []),
    ("How far can Sam run at the moment?", ["19 km"], ["is 9 km", "16 km"]),
    ("When is the race and where?", ["November 22"], []),
    ("What did you recommend for the Friday dinner?", ["rice noodles"], []),
    ("What time does Sam usually eat dinner?", ["5:30"], ["6 am"]),
    ("Who is Alex?", ["partner"], []),
    ("Does Sam own a cat?", [], []),
    ("What happened to Sam's old running shoes?", ["chewed"], []),
]


def first_rank(texts: list[str], needle: str) -> int | None:
    for index, text in enumerate(texts, start=1):
        if needle.lower() in text.lower():
            return index
    return None


FIRST_PERSON = {
    "What does Sam do for work now?": "What do I do for work now?",
    "Is Sam vegetarian?": "Am I vegetarian?",
    "What food restrictions should I respect when cooking for Sam's household?":
        "What food restrictions apply when cooking for my household?",
    "What pet does Sam have?": "What pet do I have?",
    "Where does Sam's sister live?": "Where does my sister live?",
    "How much has Sam spent on running gear in total?": "How much have I spent on running gear in total?",
    "How far can Sam run at the moment?": "How far can I run at the moment?",
    "What time does Sam usually eat dinner?": "What time do I usually eat dinner?",
    "Does Sam own a cat?": "Do I own a cat?",
    "What happened to Sam's old running shoes?": "What happened to my old running shoes?",
}


def run(path: str, *, profiles: bool = False, first_person: bool = False, min_score: float | None = None) -> dict:
    client = MemoryClient(os.path.join(tempfile.mkdtemp(prefix="prme-chat-"), "pack"))
    started = time.perf_counter()
    try:
        for number, (when, turns) in enumerate(SESSIONS, start=1):
            messages = [
                {"content": text, "role": role, "event_time": when.replace(minute=index),
                 **({"speaker": "Sam"} if role == U else {})}
                for index, (role, text) in enumerate(turns)
            ]
            if path == "ingest":
                client.ingest_batch(messages, user_id="sam", session_id=f"s{number}")
            else:
                for message in messages:
                    client.store(message["content"], user_id="sam", session_id=f"s{number}", role=message["role"],
                                 speaker=message.get("speaker"), event_time=message["event_time"])
        write_seconds = time.perf_counter() - started
        profiles_built = None
        if profiles:
            # The sync client does not pass include_speaker_turns (the engine does), so call the engine.
            profiles_built = client._run(client._engine.consolidate_knowledge(
                user_id="sam", entity_names=["Sam", "Priya", "Alex", "Miso"], include_speaker_turns=True))
        nodes = client.query_nodes(user_id="sam", limit=1000)
        kinds = collections.Counter(node.node_type.value for node in nodes)
        rows = []
        for question, answers, stale in QUESTIONS:
            asked_as = FIRST_PERSON.get(question, question) if first_person else question
            response = client.retrieve(asked_as, user_id="sam", reference_time=NOW, min_score=min_score,
                                       **({"speaker": "Sam"} if first_person else {}))
            texts = [candidate.node.content for candidate in response.results]
            context = response.bundle.rendered_context
            ranks = {needle: first_rank(texts, needle) for needle in answers}
            stale_ranks = {needle: first_rank(texts, needle) for needle in stale}
            worst = max((rank or 999) for rank in ranks.values()) if ranks else None
            rows.append({
                "question": question, "answer_ranks": ranks, "stale_ranks": stale_ranks,
                "all_answers_in_top3": bool(ranks) and worst <= 3,
                "all_answers_in_context": all(needle.lower() in context.lower() for needle in answers),
                "stale_above_answer": any(rank and worst and rank < worst for rank in stale_ranks.values()),
                "records_packed": response.bundle.included_count, "tokens": response.bundle.tokens_used,
                "top3": [text[:110] for text in texts[:3]],
                "top3_kinds": [candidate.node.node_type.value for candidate in response.results[:3]],
            })
        asked = [row for row in rows if row["answer_ranks"]]
        return {
            "path": path, "profiles_built": profiles_built,
            "questions_with_a_summary_in_top3": sum("summary" in row["top3_kinds"] for row in rows),
            "cat_question_records": rows[12]["records_packed"], "write_seconds": round(write_seconds, 1), "nodes": dict(kinds),
            "questions_with_an_answer": len(asked),
            "all_answers_in_top3": sum(row["all_answers_in_top3"] for row in asked),
            "all_answers_in_context": sum(row["all_answers_in_context"] for row in asked),
            "stale_above_answer": sum(row["stale_above_answer"] for row in asked),
            "mean_tokens": round(sum(row["tokens"] for row in rows) / len(rows)),
            "derived_sample": sorted(
                f"{node.node_type.value}: {node.content[:120]}" for node in nodes if node.node_type.value != "note"
            )[:80],
            "rows": rows,
        }
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--paths", default="store,ingest")
    parser.add_argument("--profiles", action="store_true", help="build entity profiles after writing")
    parser.add_argument("--first-person", action="store_true", help="ask as the owner (I, my) with speaker=Sam")
    parser.add_argument("--min-score", type=float, default=None)
    args = parser.parse_args()
    report = {path: run(path, profiles=args.profiles, first_person=args.first_person, min_score=args.min_score)
              for path in args.paths.split(",")}
    for path, result in report.items():
        print(f"== {path}: write {result['write_seconds']}s nodes {result['nodes']} top3 {result['all_answers_in_top3']}"
              f"/{result['questions_with_an_answer']} context {result['all_answers_in_context']}"
              f"/{result['questions_with_an_answer']} stale-above {result['stale_above_answer']} tokens {result['mean_tokens']}"
              f" profiles {result['profiles_built']} summary-in-top3 {result['questions_with_a_summary_in_top3']}"
              f" cat-records {result['cat_question_records']}")
        for row in result["rows"]:
            print(f"  {row['question'][:52]:52} ranks={row['answer_ranks']} stale={row['stale_ranks']} packed={row['records_packed']}")
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(report, handle, indent=1)
            handle.write("\n")


if __name__ == "__main__":
    main()
