"""PRME benchmark suite.

Provides standardized benchmarks for evaluating PRME's memory retrieval:

- **LoCoMo**: Long conversation QA over 300+ turn dialogues.
- **LongMemEval**: Five core abilities (info extraction, multi-session,
  temporal reasoning, knowledge updates, abstention).
- **Epistemic**: Custom PRME-specific tests for supersedence chains,
  confidence calibration, contradiction detection, belief revision,
  and abstention quality.

Run via CLI: ``python -m benchmarks [locomo|longmemeval|epistemic|all]``
"""

from benchmarks.locomo import LoCoMoBenchmark
from benchmarks.longmemeval import LongMemEvalBenchmark
from benchmarks.epistemic import EpistemicBenchmark
from benchmarks.models import BenchmarkResult, QueryResult
from benchmarks.runner import BenchmarkRunner

__all__ = [
    "BenchmarkResult",
    "BenchmarkRunner",
    "EpistemicBenchmark",
    "LoCoMoBenchmark",
    "LongMemEvalBenchmark",
    "QueryResult",
]
