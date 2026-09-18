from benchmarks.integrations import run_wice_atomic_grounding as atomic


def test_all_subclaims_must_be_supported():
    parents = [
        {"id": "case-a", "label": "supported"},
        {"id": "case-b", "label": "partially_supported"},
    ]
    subclaims = [
        {
            "id": "case-a-0",
            "label": "supported",
            "support_probability": 0.9,
            "predicted_supported": True,
        },
        {
            "id": "case-a-1",
            "label": "supported",
            "support_probability": 0.8,
            "predicted_supported": True,
        },
        {
            "id": "case-b-0",
            "label": "supported",
            "support_probability": 0.95,
            "predicted_supported": True,
        },
        {
            "id": "case-b-1",
            "label": "not_supported",
            "support_probability": 0.2,
            "predicted_supported": False,
        },
    ]

    result = atomic._aggregate_parent_samples(parents, subclaims)

    assert result[0]["predicted_supported"] is True
    assert result[0]["support_probability"] == 0.8
    assert result[0]["oracle_decomposition_supported"] is True
    assert result[1]["predicted_supported"] is False
    assert result[1]["support_probability"] == 0.2
    assert result[1]["oracle_decomposition_supported"] is False


def test_subclaims_are_ordered_by_numeric_suffix():
    parents = [{"id": "case", "label": "supported"}]
    subclaims = [
        {
            "id": "case-10",
            "label": "supported",
            "support_probability": 0.9,
            "predicted_supported": True,
        },
        {
            "id": "case-2",
            "label": "supported",
            "support_probability": 0.8,
            "predicted_supported": True,
        },
    ]

    result = atomic._aggregate_parent_samples(parents, subclaims)

    assert [item["id"] for item in result[0]["subclaims"]] == [
        "case-2",
        "case-10",
    ]
