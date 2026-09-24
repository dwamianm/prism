"""Registered source-gated query-reformulation signal-merge experiment."""
import argparse
import asyncio
from collections import Counter, defaultdict
from contextvars import ContextVar
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import MethodType
from unittest.mock import patch

import numpy as np

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics import opt_in_arm_worker as worker
from benchmarks.diagnostics.analyze_opt_in_successor import describe
from benchmarks.diagnostics.opt_in_reformulation_merge import expand_with_merge
from benchmarks.diagnostics.opt_in_source_coverage import literal_coverage
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from prme import MemoryEngine
from prme.retrieval.tokenization import count_tokens

NAME = 'opt-in-reformulation-merge-v1'
BASE = study.PRIVATE / 'opt-in-successor-v2'
CACHED = ContextVar('frozen_reformulation')
ARMS = ('baseline', 'original_reformulation', 'merged_reformulation')


# Where a request would be sent, with which credential, for how long and through
# which client cache does not change what a cached replay returns, and the
# recorded options never included them. Compare only the recorded request.
UNRECORDED_OPTIONS = frozenset({'api_key', 'base_url', 'timeout', 'client_cache'})


async def cached_reformulation(query, **kwargs):
    value = CACHED.get()
    request = {key: option for key, option in kwargs.items() if key not in UNRECORDED_OPTIONS}
    if query != value['query'] or request != value['options']:
        raise study.old.ResearchFailure('Cached reformulation request differs')
    value['calls'] += 1
    return list(value['alternatives'])


def register():
    original = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    arms = {a['id']: a for a in original['arms']}
    captures, input_cases = {}, {}
    for arm in ('baseline', 'query_reformulation'):
        if not (BASE / arm / 'verification.json').exists():
            raise RuntimeError('Complete authenticated inputs required')
        captures[arm] = {'execution': file_sha(BASE / arm / 'execution.json'),
                        'verification': file_sha(BASE / arm / 'verification.json')}
        execution = json.loads((BASE / arm / 'execution.json').read_text())
        study.old.validate_complete(original['longmemeval_s']['ordered_question_ids'], execution['rows'])
        input_cases[arm] = {row['question_id']: {k: row[k] for k in
            ('capture_sha256', 'feature_observations_sha256')} for row in execution['rows']}
    files = [*study.ROOT.glob('src/prme/**/*.py'), *study.ROOT.glob('benchmarks/**/*.py'),
             study.ROOT / 'tests/test_opt_in_reformulation_merge.py',
             study.ROOT / 'tests/test_opt_in_reformulation_merge_backends.py',
             study.OFFICIAL / 'src/evaluation/evaluate_qa.py']
    reg = {'kind': 'registered-alternate-query-signal-merge', 'registered_at': study.utc(),
        'parent_registration_sha256': original['registration_sha256'],
        'dataset_sha256': original['longmemeval_s']['dataset_sha256'],
        'ordered_question_ids': original['longmemeval_s']['ordered_question_ids'],
        'configuration': arms['baseline']['config'],
        'reformulation_configuration': arms['query_reformulation']['config'],
        'research_policy_configuration': {'id': 'alternate_query_max_signal_merge_v1',
            'paths': 'sorted distinct backend union', 'signals': 'componentwise maximum',
            'query_multiplicity_bonus': False, 'parameters_tuned': False},
        'dependencies': study.old.package_identity(), 'inputs': captures, 'input_cases': input_cases,
        'source_sha256': {str(p.relative_to(study.ROOT)): file_sha(p) for p in files},
        'model_assets_sha256': file_sha(study.REPORTS / 'opt-in-model-assets.json'),
        'method': 'One fixed research method, no new public flag. Before normal scoring, merge alternate-query hits into existing candidates using union of backend paths and maximum semantic, normalized lexical and graph-proximity signals, matching within-query candidate merging. Repeated queries never add backend count. Same-ID source snapshots must agree. New IDs retain their ordinary source records. No source content, time window, owner, scope, scorer, renderer or budget changes.',
        'source_protocol': 'All 500 histories. Reuse the exact recorded cold reformulations, originally generated from the question alone. Require exact baseline and original-reformulation context, candidate ID and score replay. Candidate calls use the same cached strings. No fresh hosted reformulation or cross-encoder calls; ordinary local query embedding and retrieval are re-executed. No answers or source annotations in retrieval. Whole-arm failure on any replay, backend, receipt, budget, artifact or case-coverage failure.',
        'source_gate': 'Both complete-source count and mean source fraction strictly exceed baseline and original reformulation; every category complete-source count must be at least baseline. One policy, no parameter sweep. Evaluate whole-source annotations only after retrieval finalizes. Report complete-source and mean-fraction paired differences with descriptive 10000 seed-20260922 bootstrap intervals on all 470 annotated cases, plus every source gain/loss.',
        'answer_protocol': 'If source gate passes, freeze 500 new control-repeat and 500 candidate contexts before answers. Official unchanged DeepSeek Ollama reader/judge, 8192/64 output limits, 3996 memory tokens, exact official prompts/scoring. Primary requires both arms complete. Report all categories, wins, losses, original-baseline secondary comparison, paired 10000 seed-20260922 bootstrap intervals and provider failures. No replacement of previous failures.',
        'scheduling': {'wait_for_anchor_pid': 18445,
            'source': 'Wait for the existing anchor study process to exit, then at most five local tasks. This reuses released source capacity. No partial outcome inspection.',
            'answers': 'Acquire the same exclusive answer-lane-v1.lock; one four-slot reader child at a time, control then candidate. No increase in provider concurrency.'},
        'exposure': 'All 500 LongMemEval-S histories are previously examined development. Original baseline 437, original reformulation 434 with only two changed contexts; all ten completed original arms, failed temporal, failed rank primary with candidate 428, and marginal primary tie 433 were known. Anchor source trial still running and its partial quality metrics have not been inspected. Not untouched confirmation.',
        'timing_limit': 'Cached reformulation source timings exclude the hosted reformulation call. They are shared-host replay timings, not a serving-speed claim. Fresh-ingestion cost is zero; historical shared ingestion and prior reformulation expense remain separate.',
        'validation': {'authored_tests_passed': 9, 'authored_log_sha256': file_sha(study.PRIVATE / 'reformulation-merge-tests-v4.txt'),
            'initial_authored_failure': 'The new assay read nonexistent top-level reformulation provider/model fields. Authored integration failed before any benchmark execution. Corrected to the existing extraction provider/model fields; initial 8-pass/1-fail log retained.',
            'initial_log_sha256': file_sha(study.PRIVATE / 'reformulation-merge-tests-v1.txt'),
            'backend_tests_passed': 2, 'backend_log_sha256': file_sha(study.PRIVATE / 'reformulation-merge-backends-v1.txt')},
        'default_changes': False, 'promotion_authorized': False}
    reg['registration_sha256'] = sha(reg)
    write_new(study.REPORTS / f'{NAME}-registration.json', reg)
    print(json.dumps({'registration_sha256': reg['registration_sha256']}))


def assert_replay(response, expected):
    if (response.bundle.render() != expected['context']
        or [str(c.node.id) for c in response.results] != [r['node_id'] for r in expected['returned']]
        or [c.composite_score for c in response.results] != [r['composite_score'] for r in expected['returned']]):
        raise study.old.ResearchFailure('Registered retrieval replay differs')


async def evaluate(case, reg, root):
    qid = case['question_id']
    folders = {a: BASE / a / qid for a in ('baseline', 'query_reformulation')}
    captures = {a: json.loads((f / 'capture.json').read_text()) for a, f in folders.items()}
    for arm, folder in folders.items():
        result = json.loads((folder / 'result.json').read_text())
        frozen = reg['input_cases'][arm][qid]
        if (result['status'] != 'complete' or file_sha(folder / 'capture.json') != frozen['capture_sha256']
            or file_sha(folder / 'feature-observations.json') != frozen['feature_observations_sha256']):
            raise study.old.ResearchFailure('Input capture changed')
    observations = json.loads((folders['query_reformulation'] / 'feature-observations.json').read_text())
    record = observations['reformulations'][0]
    config_values = reg['reformulation_configuration']
    cached = {'query': record['query'], 'alternatives': record['alternatives'], 'calls': 0,
        'options': {'provider': config_values['extraction']['provider'],
                    'model': config_values['extraction']['model'],
                    'count': config_values['query_reformulation_count']}}
    if cached['query'] != case['question'] or not cached['alternatives']:
        raise study.old.ResearchFailure('Recorded reformulation missing')
    token = CACHED.set(cached)
    pack_source = folders['baseline'] / 'pack'
    if study.base._tree_identity(pack_source) != captures['baseline']['final_pack']:
        raise study.old.ResearchFailure('Immutable source differs')
    results = {}
    try:
        with tempfile.TemporaryDirectory(prefix='prme-reformulation-merge-') as temp:
            pack = Path(temp) / 'pack'
            await asyncio.to_thread(study.old._clone_pack, pack_source, pack)
            config = study.old.config_for({'config': reg['configuration']}, pack, None)
            async with MemoryEngine.open(config) as engine:
                pipeline = engine._retrieval_pipeline
                original = pipeline._expand_reformulated_queries
                pipeline._research_merge_trace = []
                for arm in ARMS:
                    pipeline._enable_query_reformulation = arm != 'baseline'
                    pipeline._query_reformulation_provider = cached['options']['provider']
                    pipeline._query_reformulation_model = cached['options']['model']
                    pipeline._query_reformulation_count = cached['options']['count']
                    pipeline._expand_reformulated_queries = (
                        MethodType(expand_with_merge, pipeline) if arm == 'merged_reformulation' else original)
                    start = time.perf_counter()
                    response = await engine.retrieve(case['question'], user_id=study.base.USER_ID,
                        reference_time=study.base._parse_date(case['question_date']))
                    elapsed = time.perf_counter() - start
                    receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=study.base.USER_ID)
                    if (response.metadata.backend_failures or not response.metadata.receipt_persisted or receipt is None
                        or receipt.replay_ranking() != tuple(c.node.id for c in response.results)):
                        raise study.old.ResearchFailure('Backend or receipt failed')
                    context = response.bundle.render()
                    tokens = count_tokens(context, config.packing.tokenizer)
                    if tokens != response.bundle.tokens_used or tokens > 3996:
                        raise study.old.ResearchFailure('Budget mismatch')
                    if arm != 'merged_reformulation':
                        saved = captures['baseline' if arm == 'baseline' else 'query_reformulation']['retrievals'][0]
                        assert_replay(response, saved)
                    returned, packed = study.base._response_sources(response, receipt)
                    results[arm] = {'context': context, 'context_sha256': hashlib.sha256(context.encode()).hexdigest(),
                        'context_tokens': tokens, 'retrieval_seconds': elapsed, 'reformulation_cached': arm != 'baseline',
                        'returned': returned, 'packed': packed, 'receipt': receipt.model_dump(mode='json')}
                trace = pipeline._research_merge_trace
                if cached['calls'] != 2 or len(trace) != 1:
                    raise study.old.ResearchFailure('Cached request count differs')
            for result in results.values():
                result['evidence'] = literal_coverage(case, result)
    finally:
        CACHED.reset(token)
    if study.base._tree_identity(pack_source) != captures['baseline']['final_pack']:
        raise study.old.ResearchFailure('Immutable pack changed')
    record = {'question_id': qid, 'question_type': case['question_type'], 'arms': results,
        'source_capture_sha256': {a: file_sha(f / 'capture.json') for a, f in folders.items()},
        'observations_sha256': file_sha(folders['query_reformulation'] / 'feature-observations.json'),
        'source_pack_tree_sha256': captures['baseline']['final_pack']['tree_sha256'],
        'policy': 'alternate_query_max_signal_merge_v1', 'merge_trace': trace,
        'new_hosted_model_calls': 0, 'new_cross_encoder_calls': 0, 'local_query_embeddings_reexecuted': True}
    path = root / 'cases' / f'{qid}.json'
    write_new(path, record)
    return {'question_id': qid, 'status': 'complete', 'case_sha256': file_sha(path)}


def finish_source(root, outcomes):
    rows = defaultdict(list)
    prepared = {'control': [], 'candidate': []}
    for outcome in outcomes:
        path = root / 'cases' / f"{outcome['question_id']}.json"
        if file_sha(path) != outcome['case_sha256']:
            raise study.old.ResearchFailure('Completed source changed')
        case = json.loads(path.read_text())
        for arm, result in case['arms'].items():
            rows[arm].append({'question_id': case['question_id'], 'question_type': case['question_type'],
                'evidence': result['evidence'], 'context_tokens': result['context_tokens'],
                'retrieval_seconds': result['retrieval_seconds'],
                'changed': result['context'] != case['arms']['baseline']['context']})
        for role, arm in [('control', 'baseline'), ('candidate', 'merged_reformulation')]:
            result = case['arms'][arm]
            prepared[role].append({'question_id': case['question_id'], 'context': result['context'],
                'context_sha256': result['context_sha256'], 'context_tokens': result['context_tokens'],
                'changed_context': result['context'] != case['arms']['baseline']['context'],
                'reason': 'alternate_query_signal_merge' if role == 'candidate' else 'new_control_repeat',
                'baseline_capture_sha256': case['source_capture_sha256']['baseline'],
                'source_pack_tree_sha256': case['source_pack_tree_sha256']})
    summary = {}
    for arm, values in rows.items():
        applicable = [v for v in values if v['evidence']['applicable']]
        categories = defaultdict(Counter)
        for value in applicable:
            categories[value['question_type']].update(total=1, complete=value['evidence']['complete_source_literal_recall'])
        summary[arm] = {'complete_source_questions': sum(v['evidence']['complete_source_literal_recall'] for v in applicable),
            'mean_source_fraction': sum(v['evidence']['required_source_literals_present']/v['evidence']['required_turns'] for v in applicable)/len(applicable),
            'categories': dict(categories), 'context_tokens': describe([v['context_tokens'] for v in values]),
            'retrieval_seconds': describe([v['retrieval_seconds'] for v in values]),
            'changed_baseline_contexts': sum(v['changed'] for v in values)}
    candidate = summary['merged_reformulation']
    gate = all(candidate[key] > summary[control][key] for control in ('baseline', 'original_reformulation')
               for key in ('complete_source_questions', 'mean_source_fraction'))
    gate = gate and all(v['complete'] >= summary['baseline']['categories'][k]['complete']
                        for k, v in candidate['categories'].items())
    paired = {}
    for control in ('baseline', 'original_reformulation'):
        pairs = [(a, b) for a, b in zip(rows[control], rows['merged_reformulation']) if a['evidence']['applicable']]
        def fractions(r):
            e = r['evidence']
            return e['required_source_literals_present'] / e['required_turns']
        differences = {
            'complete_source': [int(b['evidence']['complete_source_literal_recall']) - int(a['evidence']['complete_source_literal_recall']) for a,b in pairs],
            'mean_source_fraction': [fractions(b) - fractions(a) for a,b in pairs]}
        paired[control] = {}
        for metric, delta in differences.items():
            vector = np.array(delta)
            rng = np.random.default_rng(20260922)
            bootstrap = vector[rng.integers(0, len(delta), size=(10000, len(delta)))].mean(axis=1)
            paired[control][metric] = {'difference': float(vector.mean()),
                'ci95': np.quantile(bootstrap, [.025, .975]).tolist(), 'questions': len(delta)}
        paired[control]['complete_source_gains'] = [a['question_id'] for a,b in pairs
            if not a['evidence']['complete_source_literal_recall'] and b['evidence']['complete_source_literal_recall']]
        paired[control]['complete_source_losses'] = [a['question_id'] for a,b in pairs
            if a['evidence']['complete_source_literal_recall'] and not b['evidence']['complete_source_literal_recall']]
    return summary, prepared, gate, paired


def answer_followup(reg, prepared):
    names = {}
    for role, contexts in prepared.items():
        name = f'{NAME}-reader-{role}'
        root = study.PRIVATE / name
        root.mkdir(exist_ok=False)
        write_new(root / 'contexts.json', contexts)
        child = {'kind': 'reformulation-signal-merge-answer-registration', 'registered_at': study.utc(),
            'source_assay_registration_sha256': reg['registration_sha256'], 'source_sha256': reg['source_sha256'],
            'prepared_sha256': file_sha(root / 'contexts.json'),
            'baseline_execution_sha256': reg['inputs']['baseline']['execution'],
            'baseline_verification_sha256': reg['inputs']['baseline']['verification'],
            'dataset_sha256': reg['dataset_sha256'], 'ordered_question_ids': reg['ordered_question_ids'],
            'role': role, 'parent_plan': reg['answer_protocol'], 'default_changes': False,
            'candidate_classification': 'New signal-merge development study. Child original-baseline contrast is secondary; primary requires both new arms. No failed run replacement.'}
        child['registration_sha256'] = sha(child)
        write_new(study.REPORTS / f'{name}-registration.json', child)
        names[role] = name
    with (study.PRIVATE / 'answer-lane-v1.lock').open('a') as lock:
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


def validate_inputs(reg):
    if reg['registration_sha256'] != sha({k: v for k, v in reg.items() if k != 'registration_sha256'}):
        raise study.old.ResearchFailure('Registration changed')
    for name, checksum in reg['source_sha256'].items():
        if file_sha(study.ROOT / name) != checksum: raise study.old.ResearchFailure('Registered source changed')
    if study.old.package_identity() != reg['dependencies']: raise study.old.ResearchFailure('Dependencies changed')
    if file_sha(study.DATASET) != reg['dataset_sha256']: raise study.old.ResearchFailure('Dataset changed')
    for arm, inputs in reg['inputs'].items():
        for name, checksum in inputs.items():
            if file_sha(BASE / arm / f'{name}.json') != checksum: raise study.old.ResearchFailure('Verified arm changed')
    if file_sha(study.REPORTS / 'opt-in-model-assets.json') != reg['model_assets_sha256']:
        raise study.old.ResearchFailure('Model identities changed')
    for asset in json.loads((study.REPORTS / 'opt-in-model-assets.json').read_text())['assets']:
        if study.base._tree_identity(Path(asset['root'])) != asset['tree']:
            raise study.old.ResearchFailure('Model asset changed')


async def run():
    reg = json.loads((study.REPORTS / f'{NAME}-registration.json').read_text())
    validate_inputs(reg)
    print(json.dumps({'status': 'waiting_for_released_anchor_capacity'}), flush=True)
    while True:
        try: os.kill(reg['scheduling']['wait_for_anchor_pid'], 0)
        except ProcessLookupError: break
        await asyncio.sleep(30)
    validate_inputs(reg)
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    cases = study.base._load_dataset(study.DATASET)
    if [c['question_id'] for c in cases] != reg['ordered_question_ids']:
        raise study.old.ResearchFailure('Cohort changed')
    root = study.PRIVATE / NAME
    root.mkdir(exist_ok=False)
    semaphore, stop = asyncio.Semaphore(5), asyncio.Event()
    observer = study.old.FailureObserver()
    logging.getLogger('prme').addHandler(observer)
    logging.getLogger('prme').setLevel(logging.DEBUG)
    async def one(case):
        async with semaphore:
            if stop.is_set(): return {'question_id': case['question_id'], 'status': 'not_started'}
            errors = []
            token = study.old.ERRORS.set(errors)
            try:
                row = await evaluate(case, reg, root)
                if errors: raise study.old.ResearchFailure('Observed backend failure')
            except Exception as exc:
                stop.set()
                row = {'question_id': case['question_id'], 'status': 'failed', 'exception_type': type(exc).__name__,
                    'failure_code': str(exc) if isinstance(exc, study.old.ResearchFailure) else type(exc).__name__, 'errors': errors}
            finally: study.old.ERRORS.reset(token)
            print(json.dumps(row), flush=True)
            return row
    with worker.observed_features(), patch('prme.retrieval.reformulation.reformulate_query', cached_reformulation):
        outcomes = await asyncio.gather(*(one(c) for c in cases))
    logging.getLogger('prme').removeHandler(observer)
    complete = all(r['status'] == 'complete' for r in outcomes)
    write_new(root / 'execution.json', {'complete': complete, 'rows': outcomes, 'finished_at': study.utc()})
    result = {'complete': complete, 'registration_sha256': reg['registration_sha256'],
        'execution_sha256': file_sha(root / 'execution.json'), 'source_metrics': None, 'advance_to_answers': False}
    if complete:
        study.old.validate_complete(reg['ordered_question_ids'], outcomes)
        summary, prepared, gate, paired = finish_source(root, outcomes)
        result.update(source_metrics=summary, advance_to_answers=gate, verified_cases=500,
                      source_paired=paired, new_hosted_model_calls=0, new_cross_encoder_calls=0,
                      local_query_embeddings_reexecuted=True)
    write_new(study.REPORTS / f'{NAME}-source-result.json', result)
    if complete and result['advance_to_answers']:
        await asyncio.to_thread(answer_followup, reg, prepared)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['register', 'run'])
    args = parser.parse_args()
    register() if args.action == 'register' else asyncio.run(run())
