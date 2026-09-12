"""Paired evidence comparisons between methods in one completed frozen run."""

import argparse
import json
from pathlib import Path

from benchmarks.compare_evidence import paired_statistics


def compare_methods(report: dict, *, reference: str = 'prme', samples: int = 2000) -> dict:
    if not report.get('complete') or report.get('errors'):
        raise ValueError('Comparison requires a complete run without errors')
    selected = report['dataset']['selected_question_ids']
    rows = report['details']
    ids = [r['question_id'] for r in rows]
    if len(selected) != len(set(selected)) or len(ids) != len(set(ids)) or set(ids) != set(selected) or any('error' in r for r in rows):
        raise ValueError('Every selected question must have exactly one successful result')
    methods = set(report['summary'])
    if reference not in methods or any(set(r['methods']) != methods for r in rows):
        raise ValueError('Every question must contain all compared methods')
    comparisons = {}
    for baseline in sorted(methods - {reference}):
        pairs = [(r['methods'][baseline], r['methods'][reference]) for r in rows]
        metrics, packing = {}, {}
        for metric in sorted(report['summary'][reference]['metrics']):
            values = [(a['metrics'][metric], b['metrics'][metric]) for a, b in pairs]
            metrics[metric] = paired_statistics([(a, b) for a, b in values if a is not None and b is not None], samples=samples)
        for budget in report['budgets']:
            values = [(a['packing'][str(budget)]['evidence_recall'], b['packing'][str(budget)]['evidence_recall']) for a, b in pairs]
            packing[str(budget)] = paired_statistics([(a, b) for a, b in values if a is not None and b is not None], samples=samples)
        comparisons[baseline] = {'metrics': metrics, 'packed_evidence_recall': packing}
    return {'kind': 'paired-within-run-evidence-comparison', 'run_id': report['run_id'],
            'provenance': report['provenance'], 'dataset': report['dataset'], 'reference': reference,
            'bootstrap_samples': samples, 'bootstrap_seed': 42, 'comparisons': comparisons,
            'limitations': ['Differences are reference minus baseline; evidence recall is not answer accuracy.',
                           'Questions are resampled; shared histories can make them dependent.',
                           'Multiple budgets and metrics are descriptive comparisons, without multiplicity adjustment.',
                           'No isolated latency comparison, competitor product ranking, or before/after software claim.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--reference', default='prme')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare_methods(json.loads(args.report.read_text()), reference=args.reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
