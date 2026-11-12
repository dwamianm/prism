"""S5 two hops: "Where is the thing Dana asked for?" needs the request and then the object's place.

Twenty people each ask for an object and the object is then seen somewhere, followed by 300 routine lines.
Known answer: both records, the request and the placement, rank in the top 5.
"""

from tests.contract.harness import texts


def test_both_hops_rank_in_top5(hop_case, robot):
    (person,) = hop_case.data
    scene, pairs = robot.hops
    request, placement = pairs[person]
    ranked = texts(scene.ask(robot.world.thing_asked.format(person=person)), 5)
    missing = [text for text in (request, placement) if text not in ranked]
    assert not missing, f"missing from top 5: {missing}; top 5: {ranked}"
