"""The hidden set's data: hand-written for the holdout, with its own seed, names, sentences and chat.

It has the same kinds of difficulty as the visible set and none of its text. Do not share it with whoever is
changing the engine against the visible set.
"""

from datetime import datetime, timezone

from tests.contract.scenes import ChatQuestion, ChatWorld, RetireItem, RobotWorld

ROBOT = RobotWorld(
    seed=31415926,
    user="robot-7",
    base=datetime(2026, 10, 27, 6, 30, tzinfo=timezone.utc),
    objects=(
        "green thermos", "bike pump", "garden gloves", "hair dryer", "spare batteries", "cookbook",
        "headphones", "leather wallet", "sunscreen bottle", "claw hammer", "board game", "lunch box",
        "glue gun", "camera bag", "baby monitor", "picture frame", "extension cord", "rain jacket",
        "pill organizer", "chess set",
    ),
    places=(
        "patio table", "workshop bench", "hall console", "attic floor", "mudroom bench", "breakfast bar",
        "nursery dresser", "craft table", "shoe rack", "fireplace mantel", "window seat", "storage bin",
        "ironing board", "spare bed", "freezer lid", "porch swing", "laundry basket", "vanity counter",
        "reading chair", "toy chest", "recycling bin", "media console",
    ),
    rooms=("foyer", "den", "nursery", "workshop", "sunroom", "attic"),
    routine=(
        "{Room} sweep: air quality index {n1}, all quiet.",
        "Routine round in the {room}: windows shut, lights off, {n2} degrees.",
        "{Room} status: unchanged since the previous round.",
    ),
    routine_markers=("sweep:", "Routine round", "status:"),
    placed="The {obj} was left on the {place}.",
    where_now="Where is the {obj} right now?",
    rare=(
        ("fall", "Grandpa slipped on the stairs and sat on the bottom step for five minutes.",
         "Did anyone get hurt or fall?"),
        ("cracked", "The sunroom window was found cracked down the middle.", "Is any glass damaged?"),
        ("fridge", "The fridge door stayed ajar for forty minutes.", "Was food left out of the cold?"),
        ("outage", "Power went out in the whole house for three minutes.", "Was there an electrical outage?"),
        ("tradesperson", "A plumber rang the bell and was let in by Grandpa.", "Did a tradesperson come by?"),
        ("gas", "A strong smell of gas was detected near the stove.", "Is there a dangerous odor?"),
        ("parrot", "The parrot vomited on the floor of its cage.", "Is the bird unwell?"),
        ("car-alarm", "A car alarm went off in the driveway for a minute.", "Did a vehicle make noise outside?"),
    ),
    repeated_event="The robot vacuum returned to its dock after cleaning (cycle {n}).",
    people=(
        "Yuki", "Mateo", "Amara", "Sven", "Leila", "Theo", "Rosa", "Dmitri", "Fatima", "Oscar",
        "Elif", "Bruno", "Chiara", "Kwame", "Astrid", "Pablo", "Noor", "Hugo", "Wen", "Ruth",
    ),
    request="{person} wants the robot to fetch the {obj} this evening.",
    thing_asked="Where can the robot find what {person} wanted?",
    unseen=("harmonica", "kayak paddle", "fishing rod", "rolling pin", "metronome", "label maker"),
    window_day=datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc),
    window_hours=(9, 10),
    window_things=(
        "a school bus", "a fox", "the postman", "a recycling truck", "an ice cream van",
        "a dog walker", "a taxi", "a moving truck", "a skateboarder", "a police car",
    ),
    window_sentence="Porch camera spotted {thing} near the mailbox at {time}.",
    window_question="What did the porch camera spot between 9 and 10 in the morning on November 4?",
    window_plain_question="What did the porch camera spot?",
    repeat_questions=(
        ("den", "Is the den quiet?"),
        ("sunroom-air", "What is the air quality in the sunroom?"),
        ("attic", "Did anything change in the attic?"),
    ),
)

U, A = "user", "assistant"
CHAT = ChatWorld(
    user="elena",
    speaker="Elena",
    now=datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc),
    sessions=(
        (datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc), (
            (U, "Morning! I want help planning workouts and meals. I drink about four coffees a day, for context."),
            (A, "Thanks. For workouts I'd start with the beginner bouldering class at Crux Gym on Tuesdays."),
            (U, "I'm an accountant at Halvorsen & Partners, so my evenings are free after 6."),
            (U, "From now on, please answer me in short bullet points, never long paragraphs."),
            (U, "My brother Marcus lives in Leeds, near our parents. Also, I don't eat pork."),
            (A, "Got it, short bullets from here on."),
        )),
        (datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc), (
            (U, "I bought climbing shoes for 95 pounds today."),
            (U, "I'm considering adopting a rabbit, or maybe a guinea pig, still deciding."),
            (A, "Rabbits need a lot of space; a guinea pig pair might suit a flat better."),
            (U, "My hardest climb so far is a V2."),
            (U, "I wake up at 7 am on weekdays. And I signed up for a bouldering competition in Sheffield on "
                "December 6."),
            (U, "Also got a chalk bag, 18 pounds."),
        )),
        (datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc), (
            (U, "Huge change: I handed in my notice. Next month I join a bakery as an apprentice baker."),
            (U, "I've cut coffee completely, I'm on green tea now."),
            (A, "Great, I'll stop suggesting coffee breaks."),
            (U, "Marcus took a job in Edinburgh and moved there last weekend."),
            (U, "I finally climbed a V4 yesterday."),
            (U, "We got two guinea pigs in the end, Pepper and Biscuit. No rabbit."),
        )),
        (datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc), (
            (U, "Baker hours mean I get up at 4:30 am now."),
            (U, "I also bought a harness for 60 pounds."),
            (U, "My best climb is now a V5."),
            (U, "My flatmate Kai is lactose intolerant, so no milk in shared recipes."),
            (A, "Understood, I'll leave dairy out of shared recipes."),
            (U, "Pepper escaped and hid behind the fridge for an hour."),
        )),
    ),
    questions=(
        ChatQuestion("job-now", "What is Elena's job now?", ("apprentice baker",), ("accountant",)),
        ChatQuestion("coffee", "Does Elena still drink coffee?", ("green tea",), ()),
        ChatQuestion("flat-cooking", "What should I leave out when cooking for Elena's flat?", ("pork", "milk"), ()),
        ChatQuestion("pets", "What pets does Elena have?", ("guinea pigs",), ("considering adopting",)),
        ChatQuestion("brother", "Which city does Elena's brother live in?", ("Edinburgh",), ("Leeds",)),
        ChatQuestion("reply-format", "How should replies to Elena be formatted?", ("bullet",), ()),
        ChatQuestion("climbing-spend", "How much has Elena spent on climbing gear altogether?",
                     ("95 pounds", "18 pounds", "60 pounds"), ()),
        ChatQuestion("grade", "What is the hardest grade Elena can climb now?", ("V5",), ("V2", "V4")),
        ChatQuestion("suggested-class", "Which class did you suggest for Elena's workouts?", ("bouldering class",),
                     ()),
        ChatQuestion("wake-time", "What time does Elena get up on weekdays?", ("4:30",), ("7 am",)),
        ChatQuestion("kai", "Who is Kai?", ("flatmate",), ()),
        ChatQuestion("pepper", "What happened with Pepper?", ("behind the fridge",), ()),
        ChatQuestion("competition", "When and where is the competition?", ("December 6",), ()),
    ),
    unanswerable=ChatQuestion("dog", "Does Elena have a dog?", (), ()),
    first=(
        ChatQuestion("job-now", "What is Elena's job now?", ("apprentice baker",), ()),
        ChatQuestion("brother-lives", "Which city does Elena's brother live in?", ("Edinburgh",), ()),
    ),
    retire=(
        RetireItem("accountant", "accountant at Halvorsen", ("accountant", "halvorsen")),
        RetireItem("four-coffees", "four coffees a day", ("coffee",)),
        RetireItem("considering-pet", "considering adopting a rabbit", ("rabbit", "guinea pig", "adopt")),
        RetireItem("hardest-v2", "hardest climb so far is a V2", ("v2", "hardest")),
        RetireItem("wake-7am", "I wake up at 7 am", ("7 am", "wake")),
        RetireItem("brother-leeds", "Marcus lives in Leeds", ("leeds", "lives")),
        RetireItem("climbed-v4", "finally climbed a V4", ("v4", "climb")),
    ),
)
