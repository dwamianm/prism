"""S8 determinism: the same question asked twice of the same pack returns the same records and context.

Asked of the flood pack (S2), without reusing a kept answer.
"""


def test_same_question_twice_gives_the_same_answer(repeat_case, robot):
    (question,) = repeat_case.data
    first, second = robot.flood.ask_again(question), robot.flood.ask_again(question)
    assert [str(result.node.id) for result in first.results] == [str(result.node.id) for result in second.results]
    assert first.bundle.rendered_context == second.bundle.rendered_context
