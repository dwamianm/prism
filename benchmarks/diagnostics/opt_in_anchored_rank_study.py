"""Registered single-policy follow-up after the completed rank-envelope trial."""
import argparse
import asyncio
from collections import Counter, defaultdict
import fcntl
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

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import describe
from benchmarks.diagnostics.opt_in_anchored_rank import AnchoredRankEnvelopeReranker
from benchmarks.diagnostics.opt_in_rank_envelope import RankEnvelopeReranker, TRACE
from benchmarks.diagnostics.opt_in_source_coverage import literal_coverage
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from prme import MemoryEngine
from prme.retrieval.tokenization import count_tokens

NAME = 'opt-in-anchored-rank-v1'
PARENT = Path('/Users/dwamianm/Sites/prism-opt-in-study-2026-09-22')
PRIOR = study.PRIVATE / 'opt-in-rank-envelope-v2'
BASE = PARENT / 'data/opt-in-study/opt-in-successor-v2/baseline'
ARMS = ('baseline', 'rank_envelope', 'anchored_rank')


def register():
    prior_reg = json.loads((study.REPORTS / 'opt-in-rank-envelope-v2-registration.json').read_text())
    prior_result = study.REPORTS / 'opt-in-rank-envelope-v2-source-result.json'
    source = json.loads(prior_result.read_text())
    if not source['complete'] or source['verified_cases'] != 500:
        raise RuntimeError('Complete prior source trial required')
    files = [*study.ROOT.glob('src/prme/**/*.py'), *study.ROOT.glob('benchmarks/**/*.py'),
             study.ROOT / 'tests/test_opt_in_anchored_rank.py',
             study.OFFICIAL / 'src/evaluation/evaluate_qa.py']
    reg = {'kind': 'registered-single-original-anchor-rank-refinement', 'registered_at': study.utc(),
           'parent_source_registration_sha256': prior_reg['registration_sha256'],
           'prior_source_result_sha256': file_sha(prior_result),
           'prior_source_execution_sha256': file_sha(PRIOR / 'execution.json'),
           'baseline_execution_sha256': file_sha(BASE / 'execution.json'),
           'baseline_verification_sha256': file_sha(BASE / 'verification.json'),
           'source_sha256': {str(p.relative_to(study.ROOT)): file_sha(p) for p in files},
           'dependencies': prior_reg['dependencies'], 'dataset_sha256': prior_reg['dataset_sha256'],
           'ordered_question_ids': prior_reg['ordered_question_ids'], 'configuration': prior_reg['configuration'],
           'model_assets_sha256': file_sha(study.REPORTS / 'opt-in-model-assets.json'),
           'method': 'One fixed policy: find the original highest-score ordinary multi-path candidate using balanced packing eligibility and score/UUID ties. If it is in the reranked prefix, put it first in envelope assignment, followed by the unchanged neural order of the other prefix candidates. Assign the same sorted original prefix score multiset. Leave the unjudged tail unchanged. Equal assigned scores still use UUID ties; this is not unconditional source retention. Ordinary session expansion and balanced packing then run unchanged.',
           'source_trial': 'All 500 cases, exact baseline and previous rank-envelope context/ID/score replay, plus the new policy. Reuse the exact previously recorded neural scores only for byte-identical query/document pairs; no new neural or hosted inference. Authenticate each prior source case, every durable receipt and immutable source pack. Answer labels are used only after all case contexts finalize.',
           'timing_limit': 'Source assay retrieval timings exclude neural inference in both repair variants. They measure cached replay under shared host load, not uncached serving latency or a speed advantage.',
           'source_gate': 'Require both complete-source count and mean source fraction strictly above the prior rank-envelope repair, also strictly above production baseline; require every category complete-source count at least its production baseline. One fixed policy, no parameter search or favorable subset.',
           'answer_gate': 'If the complete source gate passes, freeze all 500 candidate contexts and a new baseline-context repeat before any answers. Both full arms must finish; primary candidate-minus-new-control difference, all categories/losses, 10000 paired seed-20260922 bootstrap intervals. Child original-baseline comparison is secondary. Same original Ollama reader/judge, official prompts, dependency pins, 8192/64 output caps and 3996 memory tokens. Prior failed primary comparison remains failed and is never replaced.',
           'scheduling': 'At most five local source tasks, no source inference. After source completion, answers wait for the existing marginal answer coordinator terminal record and process exit and then acquire its same exclusive answer-lane-v1.lock. One four-slot reader child at a time, control then candidate. No increase in registered provider capacity.',
           'exposure': 'All 500 original controls and nine matrix arms, completed rank candidate 428/500 with failed primary control, and completed marginal primary 433/500 versus 433/500 are known. The four rank complete-source losses were inspected, including two original leading assistant records. Development only; this is a new algorithm trial, not a replacement run or untouched confirmation.',
           'failure_unit': 'Whole 500-case arm; retain failures and unstarted cases with no partial quality score, no restart or replacement.',
           'validation': {'authored_tests': 9, 'log_sha256': file_sha(study.PRIVATE / 'anchored-rank-authored-tests-v2.txt')},
           'public_flag_added': False, 'default_changes': False, 'promotion_authorized': False}
    reg['registration_sha256'] = sha(reg)
    write_new(study.REPORTS / f'{NAME}-registration.json', reg)
    print(json.dumps({'registration_sha256': reg['registration_sha256']}))


def assert_same_retrieval(response, context, expected):
    if (context != expected['context']
            or [str(c.node.id) for c in response.results] != [r['node_id'] for r in expected['returned']]
            or [c.composite_score for c in response.results] != [r['composite_score'] for r in expected['returned']]):
        raise study.old.ResearchFailure('Registered control replay differs')


async def evaluate(case, reg, prior_row, output):
    qid = case['question_id']
    prior_path = PRIOR / 'cases' / f'{qid}.json'
    if file_sha(prior_path) != prior_row['case_sha256']:
        raise study.old.ResearchFailure('Prior source case changed')
    prior = json.loads(prior_path.read_text())
    capture = BASE / qid / 'capture.json'
    if file_sha(capture) != prior['baseline_capture_sha256']:
        raise study.old.ResearchFailure('Baseline capture changed')
    saved = json.loads(capture.read_text())
    master = BASE / qid / 'pack'
    if study.base._tree_identity(master) != saved['final_pack']:
        raise study.old.ResearchFailure('Immutable input pack changed')
    if len(prior['neural_calls']) != 1:
        raise study.old.ResearchFailure('Expected one frozen neural call')
    neural = prior['neural_calls'][0]
    if sha(neural['pairs']) != neural['pairs_sha256']:
        raise study.old.ResearchFailure('Cached pair checksum differs')
    calls = []
    def cached(pairs):
        if sha(pairs) != neural['pairs_sha256']:
            raise study.old.ResearchFailure('Cached inference inputs differ')
        calls.append(neural['pairs_sha256'])
        return list(neural['scores'])
    results, operations = {}, []
    token = TRACE.set(operations)
    try:
        with tempfile.TemporaryDirectory(prefix='prme-anchored-rank-') as temporary:
            pack = Path(temporary) / 'pack'
            await asyncio.to_thread(study.old._clone_pack, master, pack)
            config = study.old.config_for({'config': reg['configuration']}, pack, None)
            async with MemoryEngine.open(config) as engine:
                for arm in ARMS:
                    ranker = None if arm == 'baseline' else (
                        RankEnvelopeReranker() if arm == 'rank_envelope' else AnchoredRankEnvelopeReranker())
                    if ranker is not None:
                        ranker._model_name = config.reranker_model
                        ranker._predict_sync = cached
                    engine._retrieval_pipeline._reranker = ranker
                    started = time.perf_counter()
                    response = await engine.retrieve(case['question'], user_id=study.base.USER_ID,
                        reference_time=study.base._parse_date(case['question_date']))
                    elapsed = time.perf_counter() - started
                    receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=study.base.USER_ID)
                    if (not response.metadata.receipt_persisted or response.metadata.backend_failures or receipt is None
                            or receipt.replay_ranking() != tuple(c.node.id for c in response.results)):
                        raise study.old.ResearchFailure('Retrieval or receipt failed')
                    context = response.bundle.render()
                    tokens = count_tokens(context, config.packing.tokenizer)
                    if tokens > 3996 or tokens != response.bundle.tokens_used:
                        raise study.old.ResearchFailure('Context budget mismatch')
                    if arm != 'anchored_rank':
                        assert_same_retrieval(response, context, prior['arms'][arm])
                    returned, packed = study.base._response_sources(response, receipt)
                    results[arm] = {'context': context, 'context_sha256': hashlib.sha256(context.encode()).hexdigest(),
                        'context_tokens': tokens, 'retrieval_seconds': elapsed, 'neural_inference_cached': arm != 'baseline',
                        'returned': returned, 'packed': packed, 'receipt': receipt.model_dump(mode='json')}
            if len(calls) != 2 or len(operations) != 2:
                raise study.old.ResearchFailure('Expected two cached rankings and traces')
            # Evaluator-only source labels enter after all three retrievals.
            for value in results.values():
                value['evidence'] = literal_coverage(case, value)
    finally:
        TRACE.reset(token)
    if study.base._tree_identity(master) != saved['final_pack']:
        raise study.old.ResearchFailure('Immutable source pack changed during replay')
    record = {'question_id': qid, 'question_type': case['question_type'], 'arms': results,
              'prior_case_sha256': prior_row['case_sha256'], 'baseline_capture_sha256': file_sha(capture),
              'source_pack_tree_sha256': saved['final_pack']['tree_sha256'], 'ordinal_operations': operations,
              'cached_pair_sha256': neural['pairs_sha256'], 'new_model_calls': 0}
    path = output / 'cases' / f'{qid}.json'
    write_new(path, record)
    return {'question_id': qid, 'status': 'complete', 'case_sha256': file_sha(path)}


def finish_source(root, outcomes):
    values = defaultdict(list)
    prepared = {'control': [], 'candidate': []}
    for outcome in outcomes:
        path = root / 'cases' / f"{outcome['question_id']}.json"
        if file_sha(path) != outcome['case_sha256']:
            raise study.old.ResearchFailure('Completed source case changed')
        case = json.loads(path.read_text())
        for arm in ARMS:
            item = case['arms'][arm]
            values[arm].append({'question_type': case['question_type'], 'question_id': case['question_id'],
                               'evidence': item['evidence'], 'context_tokens': item['context_tokens'],
                               'retrieval_seconds': item['retrieval_seconds'],
                               'changed': item['context'] != case['arms']['baseline']['context']})
        for role, arm in [('control', 'baseline'), ('candidate', 'anchored_rank')]:
            item = case['arms'][arm]
            prepared[role].append({'question_id': case['question_id'], 'context': item['context'],
                'context_sha256': item['context_sha256'], 'context_tokens': item['context_tokens'],
                'changed_context': item['context'] != case['arms']['baseline']['context'],
                'reason': 'frozen_original_anchor_policy' if role == 'candidate' else 'new_control_repeat',
                'baseline_capture_sha256': case['baseline_capture_sha256'],
                'source_pack_tree_sha256': case['source_pack_tree_sha256']})
    summary = {}
    for arm, rows in values.items():
        applicable = [r for r in rows if r['evidence']['applicable']]
        categories = defaultdict(Counter)
        for r in applicable:
            categories[r['question_type']].update(total=1, complete=r['evidence']['complete_source_literal_recall'])
        summary[arm] = {'complete_source_questions': sum(r['evidence']['complete_source_literal_recall'] for r in applicable),
            'mean_source_fraction': sum(r['evidence']['required_source_literals_present']/r['evidence']['required_turns'] for r in applicable)/len(applicable),
            'categories': dict(categories), 'context_tokens': describe([r['context_tokens'] for r in rows]),
            'retrieval_seconds': describe([r['retrieval_seconds'] for r in rows]),
            'changed_baseline_contexts': sum(r['changed'] for r in rows)}
    candidate = summary['anchored_rank']
    gate = all(candidate[key] > summary[control][key]
               for control in ('baseline', 'rank_envelope')
               for key in ('complete_source_questions', 'mean_source_fraction'))
    gate = gate and all(row['complete'] >= summary['baseline']['categories'][category]['complete']
                        for category, row in candidate['categories'].items())
    return summary, prepared, gate


def answer_followup(reg, prepared):
    names = {}
    for role, contexts in prepared.items():
        name = f'{NAME}-reader-{role}'
        root = study.PRIVATE / name
        root.mkdir(exist_ok=False)
        write_new(root / 'contexts.json', contexts)
        child = {'kind': 'anchored-rank-complete-answer-registration', 'registered_at': study.utc(),
            'source_assay_registration_sha256': reg['registration_sha256'], 'source_sha256': reg['source_sha256'],
            'prepared_sha256': file_sha(root / 'contexts.json'),
            'baseline_execution_sha256': reg['baseline_execution_sha256'],
            'baseline_verification_sha256': reg['baseline_verification_sha256'],
            'dataset_sha256': reg['dataset_sha256'], 'ordered_question_ids': reg['ordered_question_ids'],
            'role': role, 'parent_plan': reg['answer_gate'], 'default_changes': False,
            'candidate_classification': 'New label-free original-anchor development variant. Original-baseline child contrast is secondary; primary requires both new control and candidate complete. Does not replace the failed prior repair trial.'}
        child['registration_sha256'] = sha(child)
        write_new(study.REPORTS / f'{name}-registration.json', child)
        names[role] = name
    exit_file = PARENT / 'benchmarks/results/research/2026-09-22/marginal-answer-lane-v1-exit.json'
    while True:
        if exit_file.exists():
            prior_exit = json.loads(exit_file.read_text())
            try: os.kill(prior_exit['coordinator_pid'], 0)
            except ProcessLookupError: break
        time.sleep(30)
    with (PARENT / 'data/opt-in-study/answer-lane-v1.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name in names.values():
            with (study.PRIVATE / f'{name}.log').open('xb') as log:
                subprocess.run([sys.executable, '-m', 'benchmarks.diagnostics.opt_in_packing_oracle', 'run', '--name', name],
                               cwd=study.ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    reports = {role: json.loads((study.REPORTS / f'{name}-result.json').read_text()) for role, name in names.items()}
    result = {'complete': all(r['complete'] for r in reports.values()), 'child_results': names,
              'source_registration_sha256': reg['registration_sha256'], 'default_changes': False,
              'classification': 'Development only, untouched confirmation required'}
    if result['complete']:
        rows = {role: json.loads((study.PRIVATE / name / 'execution.json').read_text())['rows'] for role, name in names.items()}
        cases = study.base._load_dataset(study.DATASET)
        result.update(control_correct=reports['control']['correct'], candidate_correct=reports['candidate']['correct'],
            paired=study.old.paired_stats(rows['control'], rows['candidate'], cases),
            category_correct_deltas={k: v['correct']-reports['control']['categories'][k]['correct'] for k,v in reports['candidate']['categories'].items()},
            wins=[a['question_id'] for a,b in zip(rows['control'],rows['candidate']) if b['correct'] and not a['correct']],
            losses=[a['question_id'] for a,b in zip(rows['control'],rows['candidate']) if a['correct'] and not b['correct']])
    else:
        result['answer_metrics'] = None
    write_new(study.REPORTS / f'{NAME}-answer-result.json', result)


async def run():
    reg = json.loads((study.REPORTS / f'{NAME}-registration.json').read_text())
    for name, checksum in reg['source_sha256'].items():
        if file_sha(study.ROOT / name) != checksum: raise study.old.ResearchFailure('Registered source changed')
    for package, pinned in reg['dependencies'].items():
        if version(package) != pinned: raise study.old.ResearchFailure('Dependency changed')
    for path, checksum in [(study.DATASET,reg['dataset_sha256']), (PRIOR/'execution.json',reg['prior_source_execution_sha256']),
                           (study.REPORTS/'opt-in-rank-envelope-v2-source-result.json',reg['prior_source_result_sha256']),
                           (study.REPORTS/'opt-in-model-assets.json',reg['model_assets_sha256']),
                           (BASE/'execution.json',reg['baseline_execution_sha256']), (BASE/'verification.json',reg['baseline_verification_sha256'])]:
        if file_sha(path) != checksum: raise study.old.ResearchFailure('Registered input changed')
    for asset in json.loads((study.REPORTS / 'opt-in-model-assets.json').read_text())['assets']:
        if study.base._tree_identity(Path(asset['root'])) != asset['tree']: raise study.old.ResearchFailure('Model asset changed')
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    cases = study.base._load_dataset(study.DATASET)
    prior_rows = json.loads((PRIOR / 'execution.json').read_text())['rows']
    expected = reg['ordered_question_ids']
    if [c['question_id'] for c in cases] != expected or [r['question_id'] for r in prior_rows] != expected:
        raise study.old.ResearchFailure('Cohort changed')
    study.old.validate_complete(expected, prior_rows)
    root = study.PRIVATE / NAME
    root.mkdir(exist_ok=False)
    semaphore, stop = asyncio.Semaphore(5), asyncio.Event()
    handler = study.old.FailureObserver()
    logging.getLogger('prme').addHandler(handler)
    logging.getLogger('prme').setLevel(logging.DEBUG)
    async def one(case, prior_row):
        async with semaphore:
            if stop.is_set(): return {'question_id': case['question_id'], 'status': 'not_started'}
            errors = []
            token = study.old.ERRORS.set(errors)
            try:
                row = await evaluate(case, reg, prior_row, root)
                if errors: raise study.old.ResearchFailure('Observed backend failure')
            except Exception as exc:
                stop.set()
                row = {'question_id': case['question_id'], 'status': 'failed', 'exception_type': type(exc).__name__,
                       'failure_code': str(exc) if isinstance(exc, study.old.ResearchFailure) else type(exc).__name__, 'errors': errors}
            finally: study.old.ERRORS.reset(token)
            print(json.dumps(row), flush=True)
            return row
    outcomes = await asyncio.gather(*(one(c,r) for c,r in zip(cases,prior_rows)))
    logging.getLogger('prme').removeHandler(handler)
    complete = all(r['status'] == 'complete' for r in outcomes)
    write_new(root / 'execution.json', {'complete': complete, 'rows': outcomes, 'finished_at': study.utc()})
    result = {'complete': complete, 'registration_sha256': reg['registration_sha256'],
              'execution_sha256': file_sha(root/'execution.json'), 'source_metrics': None, 'advance_to_answers': False}
    if complete:
        study.old.validate_complete(expected, outcomes)
        summary, prepared, gate = finish_source(root, outcomes)
        result.update(source_metrics=summary, advance_to_answers=gate, verified_cases=500, new_model_calls=0)
    write_new(study.REPORTS / f'{NAME}-source-result.json', result)
    if complete and result['advance_to_answers']:
        await asyncio.to_thread(answer_followup, reg, prepared)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['register', 'run'])
    args = parser.parse_args()
    register() if args.action == 'register' else asyncio.run(run())
