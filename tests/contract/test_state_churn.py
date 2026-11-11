"""S1 state churn: an object moves several times; "Where is it now?" must rank its newest place first.

Twenty objects each move 2, 5 or 12 times, all moves 10 minutes or 3 days apart, in one pack per (spacing, moves).
Six objects per pack are asked about. Known answer: the newest placement sentence is ``results[0]``.

The same moves are also written as caller-stated facts (``ingest_batch()`` with ``assertions``: the object
``located_in`` the place, status current), one pack per (spacing, moves); there the newest placement claim, or
its sentence, must be ``results[0]``.
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
    newest = robot.world.placed.format(obj=obj, place=truth[obj])
    ranked = texts(scene.ask(robot.world.where_now.format(obj=obj)), 5)
    assert ranked and ranked[0] == newest, f"newest: {newest!r}; top 5: {ranked}"
