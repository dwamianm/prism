"""Summarize the frozen #238–241 offline arms without running an answer model.

Input reports must come from product_packing gate. Comparisons refuse different
question sets/labels, datasets, tokenizers, slices or source extraction models.
"""
import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.compare_evidence import paired_statistics
from benchmarks.diagnostics.product_packing import compare_gates, comparison_markdown


def summarize(root: Path) -> dict:
    inputs = {}
    def read(name):
        path = root / (name + '.json')
        raw = path.read_bytes()
        inputs[path.name] = hashlib.sha256(raw).hexdigest()
        report = json.loads(raw)
        if not report.get('complete'):
            raise ValueError(f'Incomplete report: {path}')
        return report
    baseline = read('raw-baseline-4k')
    comparisons = {}
    for arm in ('followup', 'tight-30', 'suppression'):
        result = compare_gates(baseline, read(f'raw-{arm}-4k'))
        comparisons[arm] = result
        (root / f'raw-{arm}-comparison.json').write_text(json.dumps(result, indent=2)+'\n')
        (root / f'raw-{arm}-comparison.md').write_text(comparison_markdown(result).rstrip()+'\n')
    followup = read('raw-followup-4k')
    before = {r['question_id']: r for r in baseline['rows'] if r['benchmark'] == 'locomo'
              and r['category'] == 'multi-hop' and r['evidence'] is not None}
    after = {r['question_id']: r for r in followup['rows'] if r['question_id'] in before}
    recall = paired_statistics([(r['evidence']['packed']/r['evidence']['annotated'],
                                 after[q]['evidence']['packed']/after[q]['evidence']['annotated'])
                                for q,r in before.items()])
    multi = comparisons['followup']['benchmarks']['locomo']['all_evidence_packed_by_category']['multi-hop']
    source_comparisons = {}
    for benchmark in ('locomo', 'longmemeval'):
        source_comparisons[benchmark] = {}
        for budget in ('4k', '8k'):
            control = read(f'ingest-{benchmark}-baseline-{budget}')
            variant = read(f'ingest-{benchmark}-copack-{budget}')
            if control['provenance']['packs']['packs'] != variant['provenance']['packs']['packs']:
                raise ValueError('Source co-packing arms must replay the exact same packs')
            result = compare_gates(control, variant)
            source_comparisons[benchmark][budget] = result
            (root / f'ingest-{benchmark}-copack-{budget}-comparison.json').write_text(json.dumps(result, indent=2)+'\n')
            (root / f'ingest-{benchmark}-copack-{budget}-comparison.md').write_text(comparison_markdown(result).rstrip()+'\n')
    authored = read('authored-supersedence')
    lme_tight = comparisons['tight-30']['benchmarks']['longmemeval']['evidence_recall']['delta']
    orphan_reduced = all(x['benchmarks'][b]['source_fidelity']['after']['claims_missing_source_record_share']
                         < x['benchmarks'][b]['source_fidelity']['before']['claims_missing_source_record_share']
                         for b, budgets in source_comparisons.items() for x in budgets.values())
    source_loss = any(x['benchmarks'][b]['all_evidence_packed']['delta'] < 0
                      for b,budgets in source_comparisons.items() for x in budgets.values())
    suppression = read('raw-suppression-4k')
    before_contexts = {(r['benchmark'], r['question_id']): r['context_sha256']
                       for r in baseline['rows']}
    after_contexts = {(r['benchmark'], r['question_id']): r['context_sha256']
                      for r in suppression['rows']}
    unchanged = before_contexts == after_contexts
    return {'kind': 'prme-four-ticket-offline-decision', 'complete': True, 'inputs_sha256': inputs,
            'decisions': {
                '238': {'offline_gate_passed': recall['delta'] > 0 and multi['delta'] > 0,
                        'multi_hop_evidence_recall': recall, 'multi_hop_all_evidence_packed': multi,
                        'answer_run': 'not_started_offline_gate_failed'},
                '239': {'source_record_fidelity_improved': orphan_reduced, 'evidence_loss_at_a_tested_budget': source_loss,
                        'default_promotion': False, 'answer_run': 'not_started_mixed_offline_evidence'},
                '240': {'tight_limit': 30, 'generous_vector_lexical_limits': 500,
                        'longmemeval_evidence_recall_delta': lme_tight,
                        'stop_before_reranking': lme_tight < 0, 'answer_run': 'not_started_tight_limit_gate_failed',
                        'answer_accuracy': None, 'correct_abstention': None},
                '241': {'benchmark_contexts_unchanged': unchanged, 'authored_context_state': authored['summary'],
                        'answer_run': 'not_started_no_knowledge_update_slice_effect', 'cascade': False}},
            'limits': ['Examined development slices; no answer-quality claims.',
                       'Extraction sample: one LoCoMo conversation and two legacy LongMemEval-S single-session-user histories.',
                       'Folding changes claim denominators; read absolute counts, source tokens and full-source evidence together.',
                       'Recorded gate latency is descriptive; runs shared the host with other work.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path)
    args = parser.parse_args()
    decision = summarize(args.reports)
    (args.reports / 'decision.json').write_text(json.dumps(decision, indent=2)+'\n')
    print(json.dumps(decision['decisions'], indent=2))


if __name__ == '__main__':
    main()
