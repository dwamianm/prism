from benchmarks.integrations import run_wice_grounding_model as grounding
from benchmarks.integrations import run_wice_claim_verification as wice


def test_quality_gates_keep_fully_unsupported_slice_separate():
    registration = {
        "evaluation": {
            "gates": {
                "claims_evaluated_min": 3,
                "supported_precision_min": 0.5,
                "supported_recall_min": 0.5,
                "balanced_accuracy_min": 0.5,
                "false_support_rate_max": 0.5,
                "not_supported_false_support_rate_max": 0.0,
            }
        }
    }
    samples = [
        {"label": "supported", "predicted_supported": True},
        {"label": "partially_supported", "predicted_supported": False},
        {"label": "not_supported", "predicted_supported": True},
    ]
    metrics = wice._binary_metrics(samples, prediction="predicted_supported")

    result = grounding._quality_gates(registration, samples, metrics)

    assert result["passed"] is False
    assert result["results"]["not_supported_false_support_rate_max"] == {
        "required": 0.0,
        "observed": 1.0,
        "passed": False,
    }


def test_device_selection_prefers_cuda_then_mps():
    class Availability:
        def __init__(self, available):
            self.available = available

        def is_available(self):
            return self.available

    class Torch:
        cuda = Availability(False)

        class backends:
            mps = Availability(True)

    assert grounding._select_device(Torch) == "mps"
    Torch.cuda = Availability(True)
    assert grounding._select_device(Torch) == "cuda"
    Torch.cuda = Availability(False)
    Torch.backends.mps = Availability(False)
    assert grounding._select_device(Torch) == "cpu"
