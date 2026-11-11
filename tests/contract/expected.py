"""Ids that fail today, each with the thesis finding it shows (``memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md``).

Each listed id is a strict xfail: when a change makes it pass, the run fails until the id is removed from here.
The list was set from runs of this suite on 2026-11-10 (branch address-first-memory, src/ as at 29a0460d; the
same 47 ids failed in four runs), not from the thesis numbers.
"""

CHURN_MINUTES = "thesis 2.2 B: recency counts days, so moves minutes apart are not ordered"
CHURN_DAYS = "thesis 2.2 B: no current value; an older placement outranks the newest"
FLOOD_RARE = "thesis 2.2 D: routine observations outrank the rare event"
HOPS = "thesis 2.2 E: one round only; the request is found, the object's place is not"
UNSEEN = "thesis 2.2 D: never says unknown; the budget is filled for an object never seen"

XFAIL: dict[str, str] = {
    # S1 state churn
    **{f"churn-10min-2moves-{obj}": CHURN_MINUTES
       for obj in ("wool-scarf", "yoga-mat", "dog-leash", "sewing-kit", "water-bottle")},
    **{f"churn-10min-5moves-{obj}": CHURN_MINUTES
       for obj in ("sewing-kit", "guitar-tuner", "screwdriver-set", "blue-notebook")},
    **{f"churn-10min-12moves-{obj}": CHURN_MINUTES
       for obj in ("tape-measure", "tv-remote", "black-umbrella", "blue-notebook", "water-bottle")},
    "churn-3days-5moves-guitar-tuner": CHURN_DAYS,
    **{f"churn-3days-12moves-{obj}": CHURN_DAYS
       for obj in ("tv-remote", "tape-measure", "screwdriver-set", "wool-scarf")},
    # S2 flood
    "flood-rare-broken": FLOOD_RARE,
    "flood-rare-stranger": FLOOD_RARE,
    "flood-routine-share": "thesis 2.2 D: the context fills with routine lines",
    # S4 time window. No 2.2 letter covers it; the thesis plans it in 4.2 ("between T1 and T2: time filter first").
    "window-natural": "thesis 4.2 (no 2.2 letter): a window named in words is not applied as a filter",
    # S5 two hops
    **{f"hops-{person}": HOPS for person in ("dana", "ravi", "aisha", "priya", "ines", "zoe", "sara", "ben")},
    # S6 unknown
    **{f"unseen-{obj}": UNSEEN for obj in (
        "violin-case", "ski-boots", "wedding-album", "soldering-iron", "tennis-racket", "bird-feeder")},
    # Chat through store()
    "chat-answer-work-now": "thesis 2.2 A: the new employer is demoted below session neighbors of a weaker hit",
    "chat-answer-household-restrictions": "thesis 2.2 A: the 'no meat' record is demoted below word matches",
    "chat-answer-pet": "thesis 2.2 A: function words vote; 'what a cup is' outranks the beagle",
    "chat-answer-sister": "thesis 2.2 E: one round only; the sister is found, her new city is not",
    "chat-answer-running-spend": "thesis 2.2 A: the shoe purchase shares no words with the question",
    "chat-answer-friday-dinner": "thesis 2.2 A: word matches on 'Friday dinner' outrank the recommendation",
    "chat-answer-dinner-time": "thesis 2.2 B: the new dinner time is not known as the current one",
    "chat-stale-work-now": "thesis 2.2 B: the old hospital job ranks above the new clinic job",
    "chat-stale-dinner-time": "thesis 2.2 B: the old night-shift dinner time ranks above the new one",
    "chat-unanswerable-cat": "thesis 2.2 D: never says unknown; the whole chat is packed",
}

# The S1 packs written through ingest(assertions=...), run with --stated. Set from two runs in the classic mode
# on 2026-11-10; the stated claims carry no ledger there, so an older placement can still rank first.
STATED = "thesis 2.2 B: without the ledger, an older stated placement outranks the newest"
XFAIL.update({case_id: STATED for case_id in (
    "stated-10min-2moves-wool-scarf",
    "stated-10min-2moves-yoga-mat",
    "stated-10min-2moves-dog-leash",
    "stated-10min-2moves-sewing-kit",
    "stated-10min-2moves-water-bottle",
    "stated-10min-5moves-sewing-kit",
    "stated-10min-5moves-guitar-tuner",
    "stated-10min-5moves-red-mug",
    "stated-10min-5moves-screwdriver-set",
    "stated-10min-5moves-blue-notebook",
    "stated-10min-12moves-tape-measure",
    "stated-10min-12moves-tv-remote",
    "stated-10min-12moves-black-umbrella",
    "stated-10min-12moves-blue-notebook",
    "stated-10min-12moves-water-bottle",
    "stated-3days-2moves-tape-measure",
    "stated-3days-2moves-first-aid-kit",
    "stated-3days-5moves-car-keys",
    "stated-3days-5moves-guitar-tuner",
    "stated-3days-12moves-tv-remote",
    "stated-3days-12moves-tape-measure",
    "stated-3days-12moves-car-keys",
    "stated-3days-12moves-screwdriver-set",
    "stated-3days-12moves-wool-scarf",
)})

# Expected failures of the chat through ingest() (test_chat_ingest.py), keyed by the same case ids. Set from runs
# on 2026-11-10 of the replayed recording fixtures/chat_extraction.json (made with corrections on) in the classic
# mode (the same 16 ids failed in three runs).
CORRECTIONS_OFF = "thesis 2.2 B: corrections are off in the classic mode, so the corrected claim stays current"
XFAIL_INGEST: dict[str, str] = {
    "chat-answer-dinner-time": "thesis 2.2 B: the old night-shift dinner time is first and the new one far down",
    "chat-answer-friday-dinner": "thesis 2.2 A: word matches outrank the recommendation",
    "chat-answer-household-restrictions": "thesis 2.2 A: the 'no meat' claim is demoted below word matches",
    "chat-answer-pet": "thesis 2.2 C: the extracted records outrank the beagle",
    "chat-answer-running-spend": "thesis 2.2 A: the shoe purchase shares no words with the question",
    "chat-answer-sister": "thesis 2.2 E: one round only; the sister is found, her new city is not",
    "chat-answer-units": "thesis 2.2 A: the split sentence 'I never know what a cup is' outranks the instruction",
    "chat-first-sister-lives": "thesis 2.2 E: one round only; the sister's new city is not reached",
    "chat-stale-dinner-time": "thesis 2.2 B: the old night-shift dinner time ranks above the new one",
    "chat-unanswerable-cat": "thesis 2.2 D: never says unknown; every record is packed",
    "retired-considering-dog": CORRECTIONS_OFF,
    "retired-dinner-6am": CORRECTIONS_OFF,
    "retired-longest-run-9km": CORRECTIONS_OFF,
    "retired-night-shifts": CORRECTIONS_OFF,
    "retired-nurse-st-marys": CORRECTIONS_OFF,
    "retired-priya-visiting": CORRECTIONS_OFF,
}

# Known failures under memory_mode="address_first" only (they pass in the classic mode), applied with
# --memory-mode address_first. Each needs a design decision; see README.md.
XFAIL_INGEST_ADDRESS_FIRST: dict[str, str] = {
    # Failed with the earlier recording (corrections showing the claims on the turn's entities) and passes with
    # the present one; the cause stays in the data, so it is kept as known.
    "chat-stale-pet": "claim text split across two records: the ledger leads with 'We adopted a dog after all!' and "
                      "the beagle is named in the next sentence, another record, so the considered dog can rank above",
    # Fails in about half of the runs: not a stable result, so not a pass.
    "chat-first-work-now": "not deterministic: 'I start at a pediatric clinic' and 'Sam works schedule day shifts "
                           "only' come from one turn and share a stated time at works_at, and the ledger breaks the "
                           "tie on node ids, which are random for every build",
}
