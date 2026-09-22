"""Registered full-cohort replay and answer follow-up for the score-scale repair."""
import argparse
import asyncio
from collections import Counter, defaultdict
from contextvars import ContextVar
import hashlib
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import describe, load_complete
from benchmarks.diagnostics.opt_in_rank_envelope import RankEnvelopeReranker, TRACE
from benchmarks.diagnostics.opt_in_select_combination import settled
from benchmarks.diagnostics.opt_in_source_coverage import literal_coverage
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from prme import MemoryEngine
from prme.retrieval.reranker import CrossEncoderReranker
from prme.retrieval.tokenization import count_tokens

NAME = 'opt-in-rank-envelope-v2'
PARENT = Path('/Users/dwamianm/Sites/prism-opt-in-study-2026-09-22')
CONTROL_ROOT = PARENT / 'data/opt-in-study/opt-in-successor-v2'
RAW_CALLS = ContextVar('rank_envelope_raw_calls', default=None)


class ObservedReranker(CrossEncoderReranker):
    def _predict_sync(self, pairs):
        result = super()._predict_sync(pairs)
        calls = RAW_CALLS.get()
        if calls is None:
            raise RuntimeError('Missing research inference trace')
        calls.append({'pairs_sha256': sha(pairs), 'pairs': pairs, 'scores': result})
        return result


def register():
    parent = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    baseline, legacy = [CONTROL_ROOT / a for a in ('baseline', 'reranker')]
    for folder in (baseline, legacy):
        load_complete(folder, parent['longmemeval_s']['ordered_question_ids'])
    files = [*study.ROOT.glob('src/prme/**/*.py'), *study.ROOT.glob('benchmarks/**/*.py'),
        study.ROOT / 'tests/test_opt_in_rank_envelope.py',
        study.OFFICIAL / 'src/evaluation/evaluate_qa.py']
    reg = {'kind': 'full-cohort-reranker-score-scale-repair', 'registered_at': study.utc(),
        'parent_registration_sha256': parent['registration_sha256'],
        'source_sha256': {str(p.relative_to(study.ROOT)): file_sha(p) for p in files},
        'dependencies': parent['local_validation_environment']['packages'],
        'dataset_sha256': file_sha(study.DATASET), 'ordered_question_ids': parent['longmemeval_s']['ordered_question_ids'],
        'controls': {p.name: {'execution_sha256': file_sha(p / 'execution.json'),
                            'verification_sha256': file_sha(p / 'verification.json')} for p in (baseline, legacy)},
        'configuration': next(a['config'] for a in parent['arms'] if a['id'] == 'reranker'),
        'method': 'Replay all 500 fixed baseline and legacy reranker contexts, candidate IDs, scores and receipts from private copies. Record the actual pinned cross-encoder output once per case. Reuse those exact outputs for the same query/document pairs in one rank-envelope treatment. Sort the original prefix score multiset descending and assign it in the existing neural-blend ranking order; resolve tied assigned scores by UUID. Keep the unscored tail unchanged. Rerun ordinary session expansion, filters, selection and packing. Retain raw neural blend and explicit neural_rank_assignment operations in schema-13 receipts; old/default receipts remain byte-compatible and schema 12.',
        'model_reuse': 'One locked cross-encoder instance shared by source tasks. Treatment reuses the exact control inference for identical pairs; retrieval times disclose that treatment inference was cached, and are not a serving-speed improvement estimate.',
        'evidence_seen': 'All 500 baseline, episode and reranker outcomes; full annotation-priority diagnostic. Post hoc tail audit found 8025/10901 packed reranker records without neural/inherited neural adjustments, with only four annotated turns among those records. Development only.',
        'failure_unit': 'All 500 cases and all three contexts. Any replay, backend, receipt, score, input-pair or budget mismatch stops further cases and prevents partial source metrics or selection. No replacement of the original failed-quality arm.',
        'answer_gate': 'Advance only if both complete-source-question count and mean source fraction improve over the broken legacy reranker across all 500 cases. Report baseline comparison even if worse. Freeze a new baseline-context control repeat and the repair contexts before inference; both complete 500-case arms are required. Same Ollama model, official prompts, 8192/64 output limits, options, 3996 memory tokens and score rule. Primary paired contrast: repair versus new control. Original baseline and failed reranker results remain unchanged. Report all category changes, losses, seed-20260922 10000-draw paired intervals and provider usage. This development repair cannot authorize a default change.',
        'scheduling': 'Amendment v2 starts five local source tasks after authenticating the complete baseline and reranker controls. No hosted calls during source replay. Original five-fresh/two-historical matrix capacity stays unchanged; one additional local source process uses spare host capacity. Freeze answer contexts after full source completion, then wait for all eleven original historical arms to settle before hosted reader stages with four slots each sequentially.',
        'unexecuted_predecessor': 'opt-in-rank-envelope-v1: registered but withdrawn before any source case or answer call; only its idle waiting process was stopped. No algorithm, model, cohort, budget, gate or scoring change.',
        'public_flag_added': False, 'default_changes': False, 'promotion_authorized': False}
    reg['registration_sha256'] = sha(reg)
    write_new(study.REPORTS / f'{NAME}-registration.json', reg)
    print(json.dumps({'registered': NAME, 'registration_sha256': reg['registration_sha256']}))


async def evaluate(case, arm, output, model):
    qid = case['question_id']
    saved = {a: json.loads((CONTROL_ROOT / a / qid / 'capture.json').read_text()) for a in ('baseline', 'reranker')}
    origin = CONTROL_ROOT / 'baseline' / qid
    if study.base._tree_identity(origin / 'pack') != saved['baseline']['final_pack']:
        raise study.old.ResearchFailure('Source pack changed')
    with tempfile.TemporaryDirectory(prefix='prme-rank-envelope-') as temp:
        pack = Path(temp) / 'pack'
        await asyncio.to_thread(study.old._clone_pack, origin / 'pack', pack)
        config = study.old.config_for(arm, pack, None)
        results = {}
        calls, operations = [], []
        call_token, trace_token = RAW_CALLS.set(calls), TRACE.set(operations)
        try:
            async with MemoryEngine.open(config) as engine:
                for name in ('baseline', 'reranker', 'rank_envelope'):
                    if name == 'baseline':
                        engine._retrieval_pipeline._reranker = None
                    elif name == 'reranker':
                        engine._retrieval_pipeline._reranker = model
                    else:
                        if len(calls) != 1:
                            raise study.old.ResearchFailure('Expected one control inference')
                        repair = RankEnvelopeReranker(model_name=config.reranker_model)
                        def cached(pairs):
                            if sha(pairs) != calls[0]['pairs_sha256']:
                                raise study.old.ResearchFailure('Treatment inference inputs differ')
                            return list(calls[0]['scores'])
                        repair._predict_sync = cached
                        engine._retrieval_pipeline._reranker = repair
                    start = time.perf_counter()
                    response = await engine.retrieve(case['question'], user_id=study.base.USER_ID,
                        reference_time=study.base._parse_date(case['question_date']))
                    elapsed = time.perf_counter()-start
                    receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=study.base.USER_ID)
                    if (not response.metadata.receipt_persisted or response.metadata.backend_failures or receipt is None
                            or receipt.replay_ranking() != tuple(c.node.id for c in response.results)):
                        raise study.old.ResearchFailure('Retrieval/receipt failed')
                    context = response.bundle.render()
                    tokens = count_tokens(context, config.packing.tokenizer)
                    if tokens > 3996 or tokens != response.bundle.tokens_used:
                        raise study.old.ResearchFailure('Context budget accounting failed')
                    if name in saved:
                        expected = saved[name]['retrievals'][0]
                        if (context != expected['context']
                            or [str(c.node.id) for c in response.results] != [r['node_id'] for r in expected['returned']]
                            or [c.composite_score for c in response.results] != [r['composite_score'] for r in expected['returned']]):
                            raise study.old.ResearchFailure(f'{name} replay differs')
                    returned, packed = study.base._response_sources(response, receipt)
                    results[name] = {'context': context, 'context_sha256': hashlib.sha256(context.encode()).hexdigest(),
                        'context_tokens': tokens, 'retrieval_seconds': elapsed, 'inference_cached': name == 'rank_envelope',
                        'returned': returned, 'packed': packed, 'receipt': receipt.model_dump(mode='json')}
        finally:
            RAW_CALLS.reset(call_token)
            TRACE.reset(trace_token)
        if len(operations) != 1 or not any(c['score_provenance'] for c in [results['rank_envelope']['receipt']]):
            raise study.old.ResearchFailure('Missing repair trace')
        # No labels or answers are used until all three contexts have finalized.
        for value in results.values():
            value['evidence'] = literal_coverage(case, value)
    record = {'question_id': qid, 'question_type': case['question_type'], 'arms': results,
        'baseline_capture_sha256': file_sha(origin / 'capture.json'),
        'legacy_capture_sha256': file_sha(CONTROL_ROOT / 'reranker' / qid / 'capture.json'),
        'source_pack_tree_sha256': saved['baseline']['final_pack']['tree_sha256'],
        'neural_calls': calls, 'ordinal_operations': operations}
    write_new(output / 'cases' / f'{qid}.json', record)
    return {'question_id': qid, 'status': 'complete', 'case_sha256': file_sha(output / 'cases' / f'{qid}.json')}


def summarize(rows):
    result = {}
    for name in ('baseline', 'reranker', 'rank_envelope'):
        applicable = [r for r in rows if r['arms'][name]['evidence']['applicable']]
        values = [r['arms'][name]['evidence'] for r in applicable]
        categories = defaultdict(lambda: Counter(total=0, complete=0))
        for r in applicable:
            categories[r['question_type']].update(total=1, complete=r['arms'][name]['evidence']['complete_source_literal_recall'])
        result[name] = {'complete_source_questions': sum(v['complete_source_literal_recall'] for v in values),
            'mean_source_fraction': sum(v['required_source_literals_present']/v['required_turns'] for v in values)/len(values),
            'categories': dict(categories),
            'context_tokens': describe([r['arms'][name]['context_tokens'] for r in rows]),
            'retrieval_seconds': describe([r['arms'][name]['retrieval_seconds'] for r in rows]),
            'changed_baseline_contexts': sum(r['arms'][name]['context'] != r['arms']['baseline']['context'] for r in rows),
            'baseline_source_losses': [r['question_id'] for r in applicable if r['arms'][name]['evidence']['required_source_literals_present'] < r['arms']['baseline']['evidence']['required_source_literals_present']],
            'baseline_source_gains': [r['question_id'] for r in applicable if r['arms'][name]['evidence']['required_source_literals_present'] > r['arms']['baseline']['evidence']['required_source_literals_present']]}
    return result


def answer_followup(reg, rows):
    baseline = CONTROL_ROOT / 'baseline'
    names = {}
    for kind, arm in (('control', 'baseline'), ('candidate', 'rank_envelope')):
        name = f'{NAME}-reader-{kind}'
        root = study.PRIVATE / name
        root.mkdir(exist_ok=False)
        contexts = [{'question_id': r['question_id'], 'context': r['arms'][arm]['context'],
            'context_sha256': r['arms'][arm]['context_sha256'], 'context_tokens': r['arms'][arm]['context_tokens'],
            'changed_context': r['arms'][arm]['context'] != r['arms']['baseline']['context'],
            'reason': 'frozen_rank_envelope_policy' if kind == 'candidate' else 'new_unchanged_control_repeat',
            'baseline_capture_sha256': r['baseline_capture_sha256'], 'source_pack_tree_sha256': r['source_pack_tree_sha256']}
            for r in rows]
        write_new(root / 'contexts.json', contexts)
        child = {'kind': 'rank-envelope-complete-answer-registration', 'registered_at': study.utc(),
            'source_assay_registration_sha256': reg['registration_sha256'], 'source_sha256': reg['source_sha256'],
            'prepared_sha256': file_sha(root / 'contexts.json'), 'baseline_execution_sha256': file_sha(baseline / 'execution.json'),
            'baseline_verification_sha256': file_sha(baseline / 'verification.json'),
            'dataset_sha256': reg['dataset_sha256'], 'ordered_question_ids': reg['ordered_question_ids'],
            'role': kind, 'parent_plan': reg['answer_gate'],
            'candidate_classification': 'Label-free score-scale repair, development only. Child output audits the original baseline; primary follow-up contrast uses the new complete control repeat.', 'default_changes': False}
        child['registration_sha256'] = sha(child)
        write_new(study.REPORTS / f'{name}-registration.json', child)
        names[kind] = name
    parent = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    while not all(settled(CONTROL_ROOT / a['id']) for a in parent['arms'] if a['stratum'] == 'historical'):
        time.sleep(30)
    for name in names.values():
        with (study.PRIVATE / f'{name}.log').open('xb') as log:
            subprocess.run([sys.executable, '-m', 'benchmarks.diagnostics.opt_in_packing_oracle', 'run', '--name', name],
                           cwd=study.ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    reports = {k: json.loads((study.REPORTS / f'{v}-result.json').read_text()) for k, v in names.items()}
    result = {'complete': all(v['complete'] for v in reports.values()), 'source_registration_sha256': reg['registration_sha256'],
              'child_results': names, 'classification': 'Development only; untouched confirmation required', 'default_changes': False}
    if result['complete']:
        outcomes = {k: json.loads((study.PRIVATE / v / 'execution.json').read_text())['rows'] for k, v in names.items()}
        cases = study.base._load_dataset(study.DATASET)
        result.update(control_correct=reports['control']['correct'], candidate_correct=reports['candidate']['correct'],
            paired=study.old.paired_stats(outcomes['control'], outcomes['candidate'], cases),
            category_correct_deltas={k: v['correct']-reports['control']['categories'][k]['correct'] for k,v in reports['candidate']['categories'].items()},
            wins=[c['question_id'] for c,a,b in zip(cases,outcomes['control'],outcomes['candidate']) if b['correct'] and not a['correct']],
            losses=[c['question_id'] for c,a,b in zip(cases,outcomes['control'],outcomes['candidate']) if a['correct'] and not b['correct']])
    else:
        result['answer_metrics'] = None
    write_new(study.REPORTS / f'{NAME}-answer-result.json', result)


async def run(args):
    reg = json.loads((study.REPORTS / f'{NAME}-registration.json').read_text())
    for name, checksum in reg['source_sha256'].items():
        if file_sha(study.ROOT / name) != checksum:
            raise study.old.ResearchFailure('Registered source changed')
    for name, pinned in reg['dependencies'].items():
        if version(name) != pinned:
            raise study.old.ResearchFailure('Registered dependency changed')
    if file_sha(study.DATASET) != reg['dataset_sha256']:
        raise study.old.ResearchFailure('Registered dataset changed')
    for name, hashes in reg['controls'].items():
        for kind in ('execution', 'verification'):
            if file_sha(CONTROL_ROOT / name / f'{kind}.json') != hashes[f'{kind}_sha256']:
                raise study.old.ResearchFailure('Registered control changed')
        load_complete(CONTROL_ROOT / name, reg['ordered_question_ids'])
    # Scheduling amendment v2 permits local source replay now. Both complete
    # controls were authenticated above. Hosted answer work still waits below.
    for asset in json.loads((study.REPORTS / 'opt-in-model-assets.json').read_text())['assets']:
        if study.base._tree_identity(Path(asset['root'])) != asset['tree']:
            raise study.old.ResearchFailure('Model asset changed')
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    root = study.PRIVATE / NAME
    root.mkdir(exist_ok=False)
    cases = study.base._load_dataset(study.DATASET)
    if [c['question_id'] for c in cases] != reg['ordered_question_ids']:
        raise study.old.ResearchFailure('Cohort changed')
    arm = {'config': reg['configuration']}
    model = ObservedReranker(model_name=reg['configuration']['reranker_model'])
    semaphore, stop = asyncio.Semaphore(5), asyncio.Event()
    handler = study.old.FailureObserver()
    logging.getLogger('prme').addHandler(handler)
    logging.getLogger('prme').setLevel(logging.DEBUG)
    async def one(case):
        async with semaphore:
            if stop.is_set():
                return {'question_id': case['question_id'], 'status': 'not_started'}
            errors = []
            token = study.old.ERRORS.set(errors)
            try:
                row = await evaluate(case, arm, root, model)
                if errors:
                    raise study.old.ResearchFailure('Swallowed provider/backend failure')
            except Exception as exc:
                stop.set()
                row = {'question_id': case['question_id'], 'status': 'failed', 'exception_type': type(exc).__name__,
                    'failure_code': str(exc) if isinstance(exc, study.old.ResearchFailure) else type(exc).__name__, 'errors': errors,
                    'trace_frames': [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name} for f in traceback.extract_tb(exc.__traceback__)]}
            finally:
                study.old.ERRORS.reset(token)
            print(json.dumps(row), flush=True)
            return row
    outcomes = await asyncio.gather(*(one(c) for c in cases))
    logging.getLogger('prme').removeHandler(handler)
    complete = all(r['status'] == 'complete' for r in outcomes)
    write_new(root / 'execution.json', {'complete': complete, 'rows': outcomes, 'finished_at': study.utc()})
    result = {'complete': complete, 'registration_sha256': reg['registration_sha256'], 'source_metrics': None,
              'advance_to_answers': False, 'execution_sha256': file_sha(root / 'execution.json')}
    if complete:
        study.old.validate_complete(reg['ordered_question_ids'], outcomes)
        rows = []
        for r in outcomes:
            path = root / 'cases' / f"{r['question_id']}.json"
            if file_sha(path) != r['case_sha256']:
                raise study.old.ResearchFailure('Completed case changed')
            rows.append(json.loads(path.read_text()))
        summaries = summarize(rows)
        control, candidate = summaries['reranker'], summaries['rank_envelope']
        advance = (candidate['complete_source_questions'] > control['complete_source_questions']
                   and candidate['mean_source_fraction'] > control['mean_source_fraction'])
        result.update(source_metrics=summaries, advance_to_answers=advance, verified_cases=len(rows),
                      hosted_calls_during_source_assay=0)
    write_new(study.REPORTS / f'{NAME}-source-result.json', result)
    if complete and result['advance_to_answers']:
        await asyncio.to_thread(answer_followup, reg, rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['register', 'run'])
    parser.add_argument('--wait', action='store_true')
    args = parser.parse_args()
    register() if args.action == 'register' else asyncio.run(run(args))


if __name__ == '__main__':
    main()
