"""S4 time window: a camera logs a sighting every 3 minutes for a day; the question asks for one hour of it.

Known answer: the top 10 results are all inside the hour, whether the hour is named in the question (natural)
or passed as ``event_time_from`` and ``event_time_to`` (explicit).
"""

from datetime import timedelta

from tests.contract.harness import texts


def test_top10_are_inside_the_window(window_case, robot):
    (kind,) = window_case.data
    scene, inside = robot.window
    world = robot.world
    if kind == "natural":
        response = scene.ask(world.window_question)
    else:
        start, end = world.window_hours
        response = scene.ask(world.window_plain_question,
                             event_time_from=world.window_day + timedelta(hours=start),
                             event_time_to=world.window_day + timedelta(hours=end))
    ranked = texts(response, 10)
    outside = [text for text in ranked if text not in inside]
    assert len(ranked) == 10 and not outside, f"{len(ranked)} results, outside the window: {outside}"
