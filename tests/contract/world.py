"""The visible set's data: the sentences, names and questions of the thesis scripts, unchanged.

Robot data is ``memory_bank/thesis-2026-11-10/embodied_probe.py`` (seed 20261110, its default). The chat is
``chat_check.py``'s SESSIONS and QUESTIONS; its stale strings are kept as written there.
"""

from datetime import datetime, timezone

from tests.contract.scenes import ChatQuestion, ChatWorld, RetireItem, RobotWorld

ROBOT = RobotWorld(
    seed=20261110,
    user="robot-1",
    base=datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc),
    objects=(
        "red mug", "house keys", "blue notebook", "phone charger", "reading glasses", "tv remote",
        "black umbrella", "dog leash", "screwdriver set", "first aid kit", "yoga mat", "water bottle",
        "laptop bag", "sewing kit", "flashlight", "tape measure", "car keys", "passport folder",
        "wool scarf", "guitar tuner",
    ),
    places=(
        "kitchen counter", "dishwasher", "living room table", "office desk", "hallway shelf",
        "bedroom nightstand", "garage workbench", "bathroom cabinet", "front porch", "laundry room",
        "dining table", "sofa armrest", "bookshelf", "entry bench", "pantry", "balcony",
        "coat closet", "study windowsill", "basement shelf", "kids room floor", "guest room dresser",
        "piano top",
    ),
    rooms=("hallway", "kitchen", "garage", "living room", "office", "bedroom"),
    routine=(
        "{Room} camera: no people, no obstacles, light level {n1}.",
        "Patrol pass through the {room}: floor clear, doors as before, temperature {n2} C.",
        "{Room} check: nothing moved since the last pass.",
    ),
    routine_markers=("camera:", "Patrol pass", "check:"),
    placed="The {obj} is on the {place}.",
    where_now="Where is the {obj} now?",
    rare=(
        ("leak", "Water is pooling under the kitchen sink.", "Is there a leak anywhere?"),
        ("door-open", "The front door was left open for eleven minutes.", "Was any entrance left unlocked or open?"),
        ("alarm", "Smoke detector in the garage chirped: low battery.", "Does any alarm need maintenance?"),
        ("medication", "Grandma took her blood pressure pill at the dining table.", "Did she take her medication?"),
        ("delivery", "A package was dropped on the front porch by a courier.", "Did a delivery arrive?"),
        ("stove", "The stove burner was on with no pot on it.", "Was any appliance left running unsafely?"),
        ("broken", "The cat knocked a vase off the bookshelf and it broke.", "Did anything get broken?"),
        ("stranger", "An unknown person stood at the back gate for two minutes.",
         "Was there a stranger near the house?"),
    ),
    repeated_event="The dog went out to the back yard through the pet door (trip {n}).",
    people=(
        "Dana", "Ravi", "Mei", "Tomas", "Aisha", "Jonas", "Priya", "Leo", "Nora", "Omar",
        "Ines", "Felix", "Hana", "Marco", "Zoe", "Ivan", "Lena", "Kofi", "Sara", "Ben",
    ),
    request="{person} asked the robot to bring the {obj} later.",
    thing_asked="Where is the thing {person} asked for?",
    unseen=(
        "violin case", "ski boots", "wedding album", "soldering iron", "tennis racket",
        "bird feeder", "fondue pot", "telescope", "snow shovel", "typewriter",
    ),
    window_day=datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc),
    window_hours=(15, 16),
    window_things=(
        "a delivery van", "the neighbor's cat", "a cyclist", "the mail carrier", "a garbage truck",
        "two children", "a jogger", "a white sedan", "a delivery drone", "a street sweeper",
    ),
    window_sentence="Garage camera saw {thing} pass the driveway at {time}.",
    window_question="What did the garage camera see between 15:00 and 16:00 on November 3?",
    window_plain_question="What did the garage camera see?",
    repeat_questions=(
        ("hallway", "Is the hallway clear?"),
        ("office-temperature", "What is the temperature in the office?"),
        ("garage", "Did anything move in the garage?"),
    ),
)

U, A = "user", "assistant"
CHAT = ChatWorld(
    user="sam",
    speaker="Sam",
    now=datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc),
    sessions=(
        (datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc), (
            (U, "Hi, I'm planning meals for the week. I'm vegetarian, by the way."),
            (A, "Got it. I'd suggest a lentil curry on Monday and a mushroom risotto on Wednesday."),
            (U, "Sounds good. My sister Priya is visiting on Friday and she can't eat gluten."),
            (A, "Then for Friday I recommend rice noodles with tofu."),
            (U, "Please always give me quantities in metric units. I never know what a cup is."),
            (U, "I work as a nurse at St. Mary's hospital, mostly night shifts, so dinner for me is at about 6 am."),
        )),
        (datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc), (
            (U, "I've started training for a half marathon. The race is on November 22 in Lisbon."),
            (A, "Nice. I suggest a 12-week plan with three runs a week and one long run on Sundays."),
            (U, "My longest run so far is 9 km. My knee hurt a bit after it."),
            (U, "I bought new shoes yesterday, Brooks Ghost, they cost 140 euros."),
            (A, "Good pick. Plan to replace them after about 700 km."),
            (U, "Oh and I'm thinking about getting a dog, maybe a greyhound, but I haven't decided."),
        )),
        (datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc), (
            (U, "Big news: I quit the hospital. I start at a pediatric clinic on November 2, day shifts only."),
            (U, "Also I've started eating fish again, so I'm not strictly vegetarian anymore. Still no meat."),
            (A, "Understood, I'll include fish in your plans from now on."),
            (U, "Did 16 km on Sunday. Knee was fine this time."),
            (U, "Priya moved to Toronto last week, so no more Friday dinners with her."),
            (U, "We adopted a dog after all! Not a greyhound though, a beagle called Miso."),
        )),
        (datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc), (
            (U, "The new job is great. I get home at 5:30 pm now."),
            (U, "I spent 60 euros on running gels and another 85 euros on a race belt and socks."),
            (U, "Longest run is now 19 km."),
            (U, "My partner Alex is allergic to peanuts, so keep that in mind for recipes."),
            (A, "Noted. I'll keep peanuts out of every recipe."),
            (U, "Miso chewed through my old running shoes, ha."),
        )),
    ),
    questions=(
        ChatQuestion("work-now", "What does Sam do for work now?", ("pediatric clinic",), ("St. Mary",)),
        ChatQuestion("vegetarian", "Is Sam vegetarian?", ("fish",), ()),
        ChatQuestion("household-restrictions",
                     "What food restrictions should I respect when cooking for Sam's household?",
                     ("peanut", "meat"), ()),
        ChatQuestion("pet", "What pet does Sam have?", ("beagle",), ("thinking about getting a dog",)),
        ChatQuestion("sister", "Where does Sam's sister live?", ("Toronto",), ()),
        ChatQuestion("units", "How should I format ingredient amounts?", ("metric",), ()),
        ChatQuestion("running-spend", "How much has Sam spent on running gear in total?",
                     ("140", "60 euros", "85"), ()),
        ChatQuestion("run-distance", "How far can Sam run at the moment?", ("19 km",), ("is 9 km", "16 km")),
        ChatQuestion("race", "When is the race and where?", ("November 22",), ()),
        ChatQuestion("friday-dinner", "What did you recommend for the Friday dinner?", ("rice noodles",), ()),
        ChatQuestion("dinner-time", "What time does Sam usually eat dinner?", ("5:30",), ("6 am",)),
        ChatQuestion("alex", "Who is Alex?", ("partner",), ()),
        ChatQuestion("old-shoes", "What happened to Sam's old running shoes?", ("chewed",), ()),
    ),
    unanswerable=ChatQuestion("cat", "Does Sam own a cat?", (), ()),
    first=(
        ChatQuestion("work-now", "What does Sam do for work now?", ("pediatric clinic",), ()),
        ChatQuestion("sister-lives", "Where does Sam's sister live?", ("Toronto",), ()),
    ),
    # What a later turn corrects, so it should be retired by the end of the chat (the lead's key).
    retire=(
        RetireItem("nurse-st-marys", "nurse at St. Mary", ("nurse", "mary", "hospital")),
        RetireItem("night-shifts", "nurse at St. Mary", ("night",)),
        RetireItem("dinner-6am", "nurse at St. Mary", ("6 am", "dinner")),
        RetireItem("vegetarian", "I'm vegetarian, by the way", ("vegetarian",)),
        RetireItem("considering-dog", "thinking about getting a dog", ("dog", "greyhound")),
        RetireItem("priya-visiting", "Priya is visiting on Friday", ("visit", "friday")),
        RetireItem("longest-run-9km", "longest run so far is 9 km", ("9 km", "longest", "longest run")),
    ),
)
