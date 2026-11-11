"""S2 flood: 400 observations, almost all routine, hide eight rare events.

Known answers: each rare event ranks in the top 5 for its question, and routine lines are under half of the
context lines packed for those eight questions taken together.
"""

from tests.contract.harness import texts


def test_rare_event_ranks_in_top5(rare_case, robot):
    event, question = rare_case.data
    ranked = texts(robot.flood.ask(question), 5)
    assert event in ranked, f"{event!r} not in top 5: {ranked}"


def test_routine_lines_are_under_half_of_the_context(flood_share_case, robot):
    markers = robot.world.routine_markers
    lines = routine = 0
    for _, _, question in robot.world.rare:
        body = [line for line in robot.flood.ask(question).bundle.rendered_context.splitlines() if line.strip()]
        lines += len(body)
        routine += sum(any(marker in line for marker in markers) for line in body)
    assert lines and routine < 0.5 * lines, f"{routine} of {lines} context lines are routine"
