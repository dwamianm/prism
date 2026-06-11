"""Measurement-rigor utilities for the PRME benchmark suite.

Single benchmark runs cannot distinguish small score differences from run-to-run
judge noise: observed flip noise is ±1.1-1.7pp, the same magnitude as the
remaining headroom toward the accuracy target. These helpers make movement
claims defensible:

- :func:`aggregate_runs` reduces N per-question scores to a stable verdict
  (majority correctness) plus the dispersion (mean ± spread) that quantifies
  how much of any delta is noise.
- :func:`summarize_multi_run` rolls per-question aggregates up to a
  benchmark-level mean and spread across runs.
- :func:`assert_single_run_provenance` / :func:`detect_mixed_run` guard against
  hand-merging retried passes from different runs into one report — a process
  that silently ratchets scores upward by reusing only the lucky verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from benchmarks.llm_judge import is_judge_error

# Correctness threshold shared with the benchmark adapters: a per-run score at
# or above this counts that run as a pass. Kept here so multi-run aggregation
# and the adapters agree on the same cut point.
CORRECT_THRESHOLD: float = 0.5


@dataclass
class QuestionAggregate:
    """Aggregate of one question's scores across multiple runs.

    Attributes:
        scores: The per-run scores that contributed (error runs excluded).
        runs: Number of contributing (non-error) runs.
        mean: Mean score across contributing runs.
        spread: Max minus min score across runs — the run-to-run flip range.
        pass_fraction: Fraction of runs that scored >= CORRECT_THRESHOLD.
        majority_correct: True when more than half the runs passed.
        error_runs: Number of runs that returned an infrastructure-error sentinel.
    """

    scores: list[float]
    runs: int
    mean: float
    spread: float
    pass_fraction: float
    majority_correct: bool
    error_runs: int = 0


def aggregate_runs(scores: list[float], threshold: float = CORRECT_THRESHOLD) -> QuestionAggregate:
    """Reduce one question's per-run scores to a stable, noise-aware verdict.

    Runs whose score is the infrastructure-error sentinel (NaN) are excluded
    from the mean/spread and counted separately — an unmeasured run must not be
    counted as either a pass or a wrong answer.

    Args:
        scores: One score per run for a single question.
        threshold: A run counts as a pass at or above this score.

    Returns:
        A :class:`QuestionAggregate`. When every run errored, ``runs`` is 0,
        ``mean``/``spread`` are 0.0, and ``majority_correct`` is False.
    """
    error_runs = sum(1 for s in scores if _is_error(s))
    valid = [s for s in scores if not _is_error(s)]
    if not valid:
        return QuestionAggregate(
            scores=[],
            runs=0,
            mean=0.0,
            spread=0.0,
            pass_fraction=0.0,
            majority_correct=False,
            error_runs=error_runs,
        )

    passes = sum(1 for s in valid if s >= threshold)
    pass_fraction = passes / len(valid)
    return QuestionAggregate(
        scores=valid,
        runs=len(valid),
        mean=sum(valid) / len(valid),
        spread=max(valid) - min(valid),
        pass_fraction=pass_fraction,
        majority_correct=pass_fraction > 0.5,
        error_runs=error_runs,
    )


@dataclass
class MultiRunSummary:
    """Benchmark-level summary across multiple runs.

    Attributes:
        runs: Number of runs aggregated.
        total_questions: Questions evaluated.
        majority_correct: Questions passing by majority vote across runs.
        mean_accuracy: Mean of per-run accuracies (fraction correct per run).
        accuracy_spread: Max minus min per-run accuracy — the headroom-vs-noise
            yardstick. A delta smaller than this is indistinguishable from noise.
        per_run_accuracy: Accuracy of each individual run.
        unstable_questions: Questions whose pass/fail flipped across runs.
        error_questions: Questions that hit an infrastructure error in at least
            one run — measured imperfectly, surfaced so a degraded run is not
            mistaken for a real regression.
    """

    runs: int
    total_questions: int
    majority_correct: int
    mean_accuracy: float
    accuracy_spread: float
    per_run_accuracy: list[float] = field(default_factory=list)
    unstable_questions: int = 0
    error_questions: int = 0


def summarize_multi_run(
    per_question_scores: dict[str, list[float]],
    threshold: float = CORRECT_THRESHOLD,
) -> MultiRunSummary:
    """Roll per-question, per-run scores up to a benchmark-level summary.

    Args:
        per_question_scores: Mapping of question key to its list of per-run
            scores. Every question is expected to have one score per run; error
            sentinels are tolerated and excluded per-question.
        threshold: Pass threshold for a single run.

    Returns:
        A :class:`MultiRunSummary`. Reports both the majority verdict and the
        per-run accuracy spread so a reader can tell signal from noise.
    """
    if not per_question_scores:
        return MultiRunSummary(
            runs=0,
            total_questions=0,
            majority_correct=0,
            mean_accuracy=0.0,
            accuracy_spread=0.0,
            per_run_accuracy=[],
            unstable_questions=0,
            error_questions=0,
        )

    runs = max(len(s) for s in per_question_scores.values())
    aggregates = {q: aggregate_runs(s, threshold) for q, s in per_question_scores.items()}

    majority_correct = sum(1 for a in aggregates.values() if a.majority_correct)
    unstable = sum(
        1 for a in aggregates.values() if 0.0 < a.pass_fraction < 1.0
    )
    error_questions = sum(1 for a in aggregates.values() if a.error_runs > 0)

    # Per-run accuracy: for each run index, the fraction of questions that
    # passed in that run (ignoring questions that errored in that run).
    per_run_accuracy: list[float] = []
    for i in range(runs):
        graded = 0
        passed = 0
        for scores in per_question_scores.values():
            if i >= len(scores):
                continue
            s = scores[i]
            if _is_error(s):
                continue
            graded += 1
            if s >= threshold:
                passed += 1
        per_run_accuracy.append(passed / graded if graded else 0.0)

    mean_accuracy = sum(per_run_accuracy) / len(per_run_accuracy) if per_run_accuracy else 0.0
    accuracy_spread = (max(per_run_accuracy) - min(per_run_accuracy)) if per_run_accuracy else 0.0

    return MultiRunSummary(
        runs=runs,
        total_questions=len(per_question_scores),
        majority_correct=majority_correct,
        mean_accuracy=mean_accuracy,
        accuracy_spread=accuracy_spread,
        per_run_accuracy=per_run_accuracy,
        unstable_questions=unstable,
        error_questions=error_questions,
    )


class MixedRunReportError(ValueError):
    """Raised when a report appears to splice questions from different runs."""


def detect_mixed_run(report: dict) -> str | None:
    """Return a reason string if *report* looks like a hand-merged mixed run.

    A clean report is produced by one ``run_id``. ``--retry-failed`` merging,
    if it ever wrote results back into an old report, would leave details
    carrying more than one ``run_id`` (or some details tagged and others not).
    That ratchets judge noise upward by keeping only the lucky retried verdicts,
    so we reject it rather than trust the number.

    Args:
        report: A parsed benchmark report dict (as written by report.py).

    Returns:
        ``None`` if the report is single-run (or carries no provenance to
        check); otherwise a human-readable reason the report is mixed.
    """
    run_ids: set[str] = set()
    untagged = False
    for bench in report.get("benchmarks", []):
        for detail in bench.get("details", []):
            rid = detail.get("run_id")
            if rid is None:
                untagged = True
            else:
                run_ids.add(rid)

    if run_ids and untagged:
        return (
            "report mixes tagged and untagged details — some results were "
            "spliced in from a separate run"
        )
    if len(run_ids) > 1:
        return (
            f"report contains details from {len(run_ids)} distinct runs "
            f"({', '.join(sorted(run_ids))}) — results were merged across runs"
        )
    return None


def assert_single_run_provenance(report: dict) -> None:
    """Raise :class:`MixedRunReportError` if *report* merges multiple runs.

    Guardrail for the load path: call before trusting or comparing a report so
    a hand-merged file cannot silently inflate a score.
    """
    reason = detect_mixed_run(report)
    if reason is not None:
        raise MixedRunReportError(reason)


# Single source of truth for the infrastructure-error sentinel test lives in
# benchmarks.llm_judge; re-exported here so the aggregation helpers can filter
# error runs without re-deriving the predicate.
_is_error = is_judge_error
