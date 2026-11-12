"""Ids of the hidden set that fail today. Same rules as tests/contract/expected.py.

Set from runs on 2026-11-10 (src/ as at 29a0460d; the same 43 ids failed in four runs).
"""

CHURN_MINUTES = "thesis 2.2 B: recency counts days, so moves minutes apart are not ordered"
CHURN_DAYS = "thesis 2.2 B: no current value; an older placement outranks the newest"
FLOOD_RARE = "thesis 2.2 D: routine observations outrank the rare event"
HOPS = "thesis 2.2 E: one round only; the request is found, the object's place is not"
UNSEEN = "thesis 2.2 D: never says unknown; the budget is filled for an object never seen"

XFAIL: dict[str, str] = {
    # S1 state churn
    **{f"churn-10min-2moves-{obj}": CHURN_MINUTES for obj in ("lunch-box", "cookbook")},
    **{f"churn-10min-5moves-{obj}": CHURN_MINUTES for obj in (
        "lunch-box", "spare-batteries", "baby-monitor", "picture-frame", "pill-organizer", "glue-gun")},
    **{f"churn-10min-12moves-{obj}": CHURN_MINUTES for obj in (
        "sunscreen-bottle", "green-thermos", "picture-frame", "board-game", "extension-cord", "bike-pump")},
    "churn-3days-5moves-hair-dryer": CHURN_DAYS,
    "churn-3days-12moves-headphones": CHURN_DAYS,
    # S2 flood
    **{f"flood-rare-{tag}": FLOOD_RARE for tag in ("fall", "cracked", "fridge", "parrot")},
    "flood-routine-share": "thesis 2.2 D: the context fills with routine lines",
    # S4 time window. No 2.2 letter covers it; the thesis plans it in 4.2 ("between T1 and T2: time filter first").
    "window-natural": "thesis 4.2 (no 2.2 letter): a window named in words is not applied as a filter",
    # S5 two hops
    **{f"hops-{person}": HOPS for person in ("mateo", "amara", "theo", "oscar", "elif", "bruno", "pablo", "hugo")},
    # S6 unknown
    **{f"unseen-{obj}": UNSEEN for obj in (
        "harmonica", "kayak-paddle", "fishing-rod", "rolling-pin", "metronome", "label-maker")},
    # Chat through store()
    "chat-answer-job-now": "thesis 2.2 A: the new job is demoted below session neighbors of a weaker hit",
    "chat-answer-flat-cooking": "thesis 2.2 A: the restrictions are demoted below word matches on cooking",
    "chat-answer-pets": "thesis 2.2 B: the considered pet outranks the pets adopted later",
    "chat-answer-climbing-spend": "thesis 2.2 A: two of the three purchases share no words with the question",
    "chat-answer-suggested-class": "thesis 2.2 A: word matches outrank the assistant's own suggestion",
    "chat-stale-pets": "thesis 2.2 B: the considered pet outranks the pets adopted later",
    "chat-unanswerable-dog": "thesis 2.2 D: never says unknown; the whole chat is packed",
}

# The S1 packs written through ingest(assertions=...), run with --stated. Set from one run in the classic mode
# on 2026-11-10; the stated claims carry no ledger there, so an older placement can still rank first.
STATED = "thesis 2.2 B: without the ledger, an older stated placement outranks the newest"
XFAIL.update({case_id: STATED for case_id in (
    "stated-10min-2moves-lunch-box",
    "stated-10min-2moves-cookbook",
    "stated-10min-5moves-lunch-box",
    "stated-10min-5moves-spare-batteries",
    "stated-10min-5moves-baby-monitor",
    "stated-10min-5moves-picture-frame",
    "stated-10min-5moves-pill-organizer",
    "stated-10min-5moves-glue-gun",
    "stated-10min-12moves-sunscreen-bottle",
    "stated-10min-12moves-green-thermos",
    "stated-10min-12moves-picture-frame",
    "stated-10min-12moves-board-game",
    "stated-10min-12moves-extension-cord",
    "stated-10min-12moves-bike-pump",
    "stated-3days-5moves-spare-batteries",
    "stated-3days-5moves-hair-dryer",
    "stated-3days-12moves-headphones",
    "stated-3days-12moves-lunch-box",
)})

# Expected failures of the chat through ingest() (test_chat_ingest.py), keyed by the same case ids. Set from runs
# on 2026-11-10 of the replayed recording fixtures/chat_extraction.json (made with corrections on) in the classic
# mode (the same 19 ids failed in three runs).
CORRECTIONS_OFF = "thesis 2.2 B: corrections are off in the classic mode, so the corrected claim stays current"
XFAIL_INGEST: dict[str, str] = {
    "chat-answer-climbing-spend": "thesis 2.2 A: two of the three purchases share no words with the question",
    "chat-answer-flat-cooking": "thesis 2.2 A: the restrictions are demoted below word matches",
    "chat-answer-job-now": "thesis 2.2 B: the new job is not known as the current one",
    "chat-answer-pets": "thesis 2.2 A: other claims about the owner outrank the pets",
    "chat-answer-wake-time": "thesis 2.2 B: the old wake-up time ranks above the new one",
    "chat-first-brother-lives": "thesis 2.2 B: the brother's old city ranks above the new one",
    "chat-first-job-now": "thesis 2.2 B: the new job is not known as the current one",
    "chat-stale-brother": "thesis 2.2 B: the brother's old city ranks above the new one",
    "chat-stale-grade": "thesis 2.2 B: an older grade ranks above the newest",
    "chat-stale-job-now": "thesis 2.2 B: the old job ranks above the new one",
    "chat-stale-wake-time": "thesis 2.2 B: the old wake-up time ranks above the new one",
    "chat-unanswerable-dog": "thesis 2.2 D: never says unknown; every record is packed",
    "retired-accountant": CORRECTIONS_OFF,
    "retired-brother-leeds": CORRECTIONS_OFF,
    "retired-climbed-v4": CORRECTIONS_OFF,
    "retired-considering-pet": CORRECTIONS_OFF,
    "retired-four-coffees": CORRECTIONS_OFF,
    "retired-hardest-v2": CORRECTIONS_OFF,
    "retired-wake-7am": CORRECTIONS_OFF,
}

# Known failures under memory_mode="address_first" only (they pass in the classic mode). Same rules as
# tests/contract/expected.py.
XFAIL_INGEST_ADDRESS_FIRST: dict[str, str] = {}
