"""Common benchmark runner infrastructure.

Loads datasets, runs benchmarks against PRME's retrieve() API, collects
metrics, and supports parallel execution. Manages engine lifecycle with
isolated temporary directories per benchmark.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine

from benchmarks.llm_judge import LLMJudgeConfig
from benchmarks.models import BenchmarkResult

logger = logging.getLogger(__name__)


# Registry of known benchmark classes keyed by name.
_BENCHMARK_REGISTRY: dict[str, type] = {}


def _ensure_registry() -> dict[str, type]:
    """Populate the benchmark registry on first call."""
    if not _BENCHMARK_REGISTRY:
        from benchmarks.locomo import LoCoMoBenchmark, LoCoMoRealBenchmark
        from benchmarks.longmemeval import LongMemEvalBenchmark, LongMemEvalRealBenchmark
        from benchmarks.epistemic import EpistemicBenchmark

        _BENCHMARK_REGISTRY["locomo"] = LoCoMoBenchmark
        _BENCHMARK_REGISTRY["longmemeval"] = LongMemEvalBenchmark
        _BENCHMARK_REGISTRY["epistemic"] = EpistemicBenchmark
        _BENCHMARK_REGISTRY["locomo-real"] = LoCoMoRealBenchmark
        _BENCHMARK_REGISTRY["longmemeval-real"] = LongMemEvalRealBenchmark
    return _BENCHMARK_REGISTRY


async def _create_engine(tmp_dir: Path) -> MemoryEngine:
    """Create a MemoryEngine backed by an isolated temp directory.

    Args:
        tmp_dir: Temporary directory for DuckDB, vector, and lexical files.

    Returns:
        Initialized MemoryEngine.
    """
    lexical_dir = tmp_dir / "lexical_index"
    lexical_dir.mkdir(parents=True, exist_ok=True)
    config = PRMEConfig(
        db_path=str(tmp_dir / "memory.duckdb"),
        vector_path=str(tmp_dir / "vectors.usearch"),
        lexical_path=str(lexical_dir),
    )
    return await MemoryEngine.create(config)


async def _run_single_benchmark(
    benchmark_name: str,
    benchmark_cls: type,
    llm_config: LLMJudgeConfig | None = None,
    only_questions: set[str] | None = None,
) -> BenchmarkResult:
    """Run a single benchmark with its own isolated engine.

    Creates a fresh MemoryEngine in a temporary directory, runs the
    benchmark, closes the engine, and returns the result.
    """
    tmp = tempfile.mkdtemp(prefix=f"prme_bench_{benchmark_name}_")
    tmp_dir = Path(tmp)
    engine = await _create_engine(tmp_dir)
    try:
        benchmark = benchmark_cls()
        # Pass llm_config to benchmarks that support it
        if llm_config and llm_config.enabled and hasattr(benchmark, 'run_with_llm'):
            return await benchmark.run_with_llm(engine, llm_config, only_questions=only_questions)
        return await benchmark.run(engine)
    finally:
        await engine.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


class BenchmarkRunner:
    """Orchestrates benchmark execution with optional parallelism.

    Usage::

        runner = BenchmarkRunner()
        results = await runner.run(["locomo", "epistemic"])
        results = await runner.run(["all"])
        results = await runner.run(["locomo"], parallel=False)
    """

    def __init__(self, llm_config: LLMJudgeConfig | None = None) -> None:
        self._registry = _ensure_registry()
        self._llm_config = llm_config

    @property
    def available(self) -> list[str]:
        """List of available benchmark names."""
        return sorted(self._registry.keys())

    # Benchmarks that require downloaded datasets (excluded from "all")
    _REAL_BENCHMARKS = {"locomo-real", "longmemeval-real"}

    def resolve_names(self, names: list[str]) -> list[str]:
        """Resolve benchmark names, expanding groups.

        Special groups:

        - ``"all"`` — synthetic benchmarks only (fast, no downloads needed)
        - ``"all-real"`` — real dataset benchmarks only
        - ``"all-both"`` — everything

        Args:
            names: List of benchmark names or group names.

        Returns:
            Deduplicated list of valid benchmark names.

        Raises:
            ValueError: If an unknown benchmark name is given.
        """
        if "all-both" in names:
            return self.available
        if "all-real" in names:
            return sorted(self._REAL_BENCHMARKS & set(self._registry))
        if "all" in names:
            return sorted(set(self.available) - self._REAL_BENCHMARKS)

        resolved: list[str] = []
        for name in names:
            if name not in self._registry:
                raise ValueError(
                    f"Unknown benchmark: {name!r}. "
                    f"Available: {', '.join(self.available)}"
                )
            if name not in resolved:
                resolved.append(name)
        return resolved

    async def run(
        self,
        names: list[str],
        *,
        parallel: bool = True,
        only_questions: set[str] | None = None,
    ) -> list[BenchmarkResult]:
        """Run the specified benchmarks and return results.

        Args:
            names: Benchmark names to run, or ``["all"]``.
            parallel: If True, run benchmarks concurrently via
                ``asyncio.gather``. If False, run sequentially.
            only_questions: If set, only run questions whose query text
                matches one of these strings (for retrying failures).

        Returns:
            List of BenchmarkResult, one per benchmark.
        """
        resolved = self.resolve_names(names)
        logger.info("Running benchmarks: %s (parallel=%s)", resolved, parallel)

        start = time.monotonic()

        if parallel and len(resolved) > 1:
            tasks = [
                _run_single_benchmark(name, self._registry[name], self._llm_config, only_questions)
                for name in resolved
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Convert exceptions to failed BenchmarkResults
            final: list[BenchmarkResult] = []
            for name, result in zip(resolved, results):
                if isinstance(result, Exception):
                    logger.error("Benchmark %s failed: %s", name, result)
                    final.append(
                        BenchmarkResult(
                            benchmark_name=name,
                            overall_score=0.0,
                            category_scores={},
                            total_queries=0,
                            correct=0,
                            incorrect=0,
                            abstained=0,
                            duration_ms=0.0,
                            details=[],
                        )
                    )
                else:
                    final.append(result)
            results_list = final
        else:
            results_list = []
            for name in resolved:
                try:
                    result = await _run_single_benchmark(
                        name, self._registry[name], self._llm_config, only_questions
                    )
                    results_list.append(result)
                except Exception as exc:
                    logger.error("Benchmark %s failed: %s", name, exc)
                    results_list.append(
                        BenchmarkResult(
                            benchmark_name=name,
                            overall_score=0.0,
                            category_scores={},
                            total_queries=0,
                            correct=0,
                            incorrect=0,
                            abstained=0,
                            duration_ms=0.0,
                            details=[],
                        )
                    )

        elapsed = (time.monotonic() - start) * 1000
        logger.info(
            "Completed %d benchmarks in %.0fms", len(results_list), elapsed
        )
        return results_list
