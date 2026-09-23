from benchmarks.diagnostics.opt_in_select_combination import select


def fixture():
    names = ['inactive', 'positive', 'negative_a', 'negative_b']
    return {'individuals': names}, {'arms': {n: {'latency_seconds': {'cold': {'p95': 2}}} for n in names},
        'comparisons': {n: {'changed_contexts': 1, 'difference': -.1} for n in names}}


def test_complete_coverage_is_required_even_when_a_partial_winner_exists():
    plan, analysis = fixture()
    del analysis['comparisons']['negative_b']
    analysis['comparisons']['positive']['difference'] = .5
    assert select(plan, analysis) is None


def test_inactive_noise_cannot_win_and_negative_results_are_not_hidden():
    plan, analysis = fixture()
    analysis['comparisons']['inactive'].update(changed_contexts=0, difference=.9)
    analysis['comparisons']['positive']['difference'] = .1
    assert select(plan, analysis) == ['positive', 'negative_a']


def test_latency_breaks_equal_quality_ties_before_name():
    plan, analysis = fixture()
    analysis['comparisons']['inactive']['changed_contexts'] = 0
    analysis['arms']['negative_b']['latency_seconds']['cold']['p95'] = 1
    assert select(plan, analysis) == ['negative_b', 'negative_a']
