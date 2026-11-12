"""S6 unknown: a question about an object that was never seen.

Ten objects are seen once, then 300 routine lines. Known answer: nothing in the store is about the asked
object, so the response packs fewer than 10 records rather than filling the budget.
"""


def test_unseen_object_packs_fewer_than_10_records(unseen_case, robot):
    (obj,) = unseen_case.data
    packed = robot.unknown.ask(robot.world.where_now.format(obj=obj)).bundle.included_count
    assert packed < 10, f"packed {packed} records"
