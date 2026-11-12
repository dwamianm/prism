"""S1 state churn: an object moves several times; "Where is it now?" must rank its newest place first.

Twenty objects each move 2, 5 or 12 times, all moves 10 minutes or 3 days apart, in one pack per (spacing, moves).
Six objects per pack are asked about. Known answer: the newest placement sentence is ``results[0]``.

The same moves are also written as caller-stated facts (``ingest_batch()`` with ``assertions``: the object
``located_in`` the place, status current), one pack per (spacing, moves); there the newest placement's sentence
must be ``results[0]``: its message, or its claim, whose text is the same sentence (the placement sentence names
the place, and the claim adds no role or time).
"""

from tests.contract.harness import texts


def test_newest_place_ranks_first(churn_case, robot):
    label, moves, obj = churn_case.data
    scene, truth = robot.churn(label, moves)
    newest = robot.world.placed.format(obj=obj, place=truth[obj])
    ranked = texts(scene.ask(robot.world.where_now.format(obj=obj)), 5)
    assert ranked and ranked[0] == newest, f"newest: {newest!r}; top 5: {ranked}"


def test_newest_stated_place_ranks_first(stated_case, robot):
    label, moves, obj = stated_case.data
    scene, truth = robot.churn_stated(label, moves)
    # The newest move's two records, its message and its claim, both have the placement sentence as their text
    # (prme.ingestion.assertions.stated_text). Either one first is the newest placement.
    sentence = robot.world.placed.format(obj=obj, place=truth[obj])
    ranked = texts(scene.ask(robot.world.where_now.format(obj=obj)), 5)
    assert ranked and ranked[0] == sentence, f"newest: {sentence!r}; top 5: {ranked}"
