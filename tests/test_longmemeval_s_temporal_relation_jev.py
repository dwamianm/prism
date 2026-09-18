from benchmarks.diagnostics import longmemeval_s_temporal_relation_jev as trial


def test_threshold_metrics_tracks_false_accepts_and_recall() -> None:
    rows = [
        {"minimum_probability": 0.95, "relation_correct": True},
        {"minimum_probability": 0.91, "relation_correct": True},
        {"minimum_probability": 0.89, "relation_correct": True},
        {"minimum_probability": 0.30, "relation_correct": False},
    ]

    strict = trial.threshold_metrics(rows, 0.9)
    loose = trial.threshold_metrics(rows, 0.2)

    assert strict == {
        "threshold": 0.9,
        "accepted": 2,
        "correct_accepted": 2,
        "false_accepts": 0,
        "precision": 1.0,
        "recall_of_correct_relations": 2 / 3,
    }
    assert loose["false_accepts"] == 1
    assert loose["precision"] == 0.75
