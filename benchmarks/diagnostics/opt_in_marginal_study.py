"""Full-cohort source assay followed by a conditionally registered answer trial."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
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
from benchmarks.diagnostics import opt_in_marginal_packing as marginal
from benchmarks.diagnostics.analyze_opt_in_successor import describe, load_complete
from benchmarks.diagnostics.longmemeval_s_compact import _bundle_sources
from benchmarks.diagnostics.opt_in_select_combination import settled
from benchmarks.diagnostics.opt_in_source_coverage import literal_coverage
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from prme import MemoryEngine
from prme.retrieval.context_formatter import build_context_guidance
from prme.retrieval.query_analysis import analyze_query
from prme.retrieval.tokenization import count_tokens


NAME = 'opt-in-marginal-packing-v1'


def register():
    parent = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    names = [str(Path(__file__).relative_to(study.ROOT)), 'benchmarks/diagnostics/opt_in_marginal_packing.py',
             'tests/test_opt_in_marginal_packing.py', 'benchmarks/diagnostics/opt_in_source_coverage.py',
             'benchmarks/diagnostics/opt_in_packing_oracle.py']
    reg = {'kind': 'label-free-marginal-packing-source-assay', 'registered_at': study.utc(),
        'parent_registration_sha256': parent['registration_sha256'],
        'source_sha256': {name: file_sha(study.ROOT / name) for name in names},
        'dataset_sha256': file_sha(study.DATASET),
        'ordered_question_ids': parent['longmemeval_s']['ordered_question_ids'],
        'policies': {name: asdict(policy) for name, policy in marginal.POLICIES.items()},
        'hypothesis': 'Own candidate relevance with a bounded 0–10% episode bonus and/or diminishing weight for repeated session members preserves cross-session evidence better than hard episode reservation.',
        'evidence_seen': 'All 500 baseline outcomes, the complete episode-routing outcome, and the full annotation-priority diagnostic. Development only. Prior September 18 source-only episode quota grid was negative.',
        'method': 'Exact full-cohort baseline replay. Protect ordinary instruction/pin priorities and one top-score multipath anchor. Greedy own-score/full-entry-tokens**0.25, optionally divided by 1+.25*selected_same_scope_session_records and multiplied by 1+.10*normalized episode/local BM25 support. No inherited anchor scores or episode priority tier. Whole source, original serializer, exact 3996-token budget, final ordinary reference fallback. No labels, answers or question categories enter routing or packing.',
        'failure_unit': 'All 500 cases and every source policy; any replay, availability or budget failure stops further cases and prevents partial source summaries or selection.',
        'source_selection': 'After every context is finalized, select at most one policy with both more complete source-literal questions and a greater mean source-literal turn fraction than control. Order by descending complete count, descending mean fraction, ascending packing p95, then name. Report all policies, categories and every source loss, including negative findings. Selection is development tuning, not confirmation.',
        'answer_followup': 'If a source candidate qualifies, freeze its 500 contexts and a new 500-case byte-identical baseline-context repeat in separate registrations before inference. Use the unchanged successor reader/judge, prompts, options, 8192/64 output limits and official score. Reuse only the frozen context-to-answer executor (not its annotation-assisted preparation). Primary answer contrast is candidate versus the new control repeat; original 437/500 stays unchanged. Both complete authenticated 500-case arms are required. Report paired seed-20260922 10000-draw intervals, all category changes and every loss. Nominal development intervals cannot support default promotion.',
        'scheduling': 'Wait for every fixed historical retrieval arm to finalize and authenticate. Five source tasks, no provider calls during source assay. If advanced, run control then candidate reader stages, each four provider slots, under disclosed shared load.',
        'production_sources_changed': False, 'promotion_authorized': False}
    reg['registration_sha256'] = sha(reg)
    write_new(study.REPORTS / f'{NAME}-registration.json', reg)
    print(json.dumps({'registered': NAME, 'policies': list(reg['policies'])}))


async def evaluate(case, arm, root):
    qid = case['question_id']
    baseline = study.PRIVATE / 'opt-in-successor-v2/baseline' / qid
    saved = json.loads((baseline / 'capture.json').read_text())
    if study.base._tree_identity(baseline / 'pack') != saved['final_pack']:
        raise study.old.ResearchFailure('Source pack identity differs')
    with tempfile.TemporaryDirectory(prefix='prme-marginal-source-') as temporary:
        pack = Path(temporary) / 'pack'
        await asyncio.to_thread(study.old._clone_pack, baseline / 'pack', pack)
        config = study.old.config_for(arm, pack, None)
        async with MemoryEngine.open(config) as engine:
            reference = study.base._parse_date(case['question_date'])
            response = await engine.retrieve(case['question'], user_id=study.base.USER_ID, reference_time=reference)
            receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=study.base.USER_ID)
            if not response.metadata.receipt_persisted or response.metadata.backend_failures or receipt is None:
                raise study.old.ResearchFailure('Control retrieval unavailable')
            original = saved['retrievals'][0]
            if (response.bundle.render() != original['context'] or
                [str(c.node.id) for c in response.results] != [r['node_id'] for r in original['returned']] or
                [c.composite_score for c in response.results] != [r['composite_score'] for r in original['returned']] or
                receipt.replay_ranking() != tuple(c.node.id for c in response.results)):
                raise study.old.ResearchFailure('Complete baseline replay differs')
            analysis = await analyze_query(case['question'], reference_time=reference)
            guidance = build_context_guidance(case['question'], query_analysis=analysis,
                reference_time=reference, mode=config.packing.context_guidance_mode)
            # This immutable snapshot contains source records/scores, never labels.
            inputs = [c.model_dump(mode='json') for c in response.results]
            write_new(root / 'inputs' / f'{qid}.json', inputs)
            results = {'control': {'context': original['context'], 'tokens': original['context_tokens'],
                                   'packing_seconds': 0.0, 'sources': original['packed']}}
            for name, policy in marginal.POLICIES.items():
                start = time.perf_counter()
                bundle = marginal.pack(response.results, case['question'], config.packing, policy,
                    coverage_notice=response.bundle.coverage_notice, context_guidance=guidance)
                elapsed = time.perf_counter()-start
                if bundle.tokens_used > 3996 or count_tokens(bundle.render(), 'cl100k_base') != bundle.tokens_used:
                    raise study.old.ResearchFailure('Marginal budget accounting failed')
                results[name] = {'context': bundle.render(), 'tokens': bundle.tokens_used,
                                  'packing_seconds': elapsed, 'sources': _bundle_sources(bundle)}
        # Score only after all policies have finalized this case's contexts.
        for result in results.values():
            result['evidence'] = literal_coverage(case, {'context': result['context'], 'returned': original['returned']})
            result['context_sha256'] = hashlib.sha256(result['context'].encode()).hexdigest()
            result['node_evidence'] = study.base._evidence_metrics(case, result['sources'])
    result = {'question_id': qid, 'question_type': case['question_type'], 'status': 'complete',
        'baseline_capture_sha256': file_sha(baseline / 'capture.json'),
        'input_pack_sha256': saved['final_pack']['tree_sha256'], 'inputs_sha256': file_sha(root / 'inputs' / f'{qid}.json'),
        'control_receipt': receipt.model_dump(mode='json'), 'arms': results}
    write_new(root / 'cases' / f'{qid}.json', result)
    return {'question_id': qid, 'status': 'complete', 'case_sha256': file_sha(root / 'cases' / f'{qid}.json')}


def summarize_source(rows):
    summaries = {}
    for name in ('control', *marginal.POLICIES):
        applicable = [r for r in rows if r['arms'][name]['evidence']['applicable']]
        values = [r['arms'][name]['evidence'] for r in applicable]
        category = defaultdict(lambda: Counter(total=0, complete=0))
        losses, gains = [], []
        for row in applicable:
            current = row['arms'][name]['evidence']
            original = row['arms']['control']['evidence']
            category[row['question_type']].update(total=1, complete=current['complete_source_literal_recall'])
            if original['required_source_literals_present'] > current['required_source_literals_present']:
                losses.append(row['question_id'])
            if original['required_source_literals_present'] < current['required_source_literals_present']:
                gains.append(row['question_id'])
        summaries[name] = {'complete_source_questions': sum(v['complete_source_literal_recall'] for v in values),
            'mean_source_fraction': sum(v['required_source_literals_present']/v['required_turns'] for v in values)/len(values),
            'categories': dict(category), 'source_losses': losses, 'source_gains': gains,
            'context_tokens': describe([r['arms'][name]['tokens'] for r in rows]),
            'packing_seconds': describe([r['arms'][name]['packing_seconds'] for r in rows]),
            'changed_contexts': sum(r['arms'][name]['context'] != r['arms']['control']['context'] for r in rows)}
    return summaries


def answer_followup(reg, rows, selected):
    # Reuse the immutable 500-case executor, with this study's independent
    # prepared contexts and explicit label-free classification. Never call its
    # annotation-assisted preparation function.
    baseline = study.PRIVATE / 'opt-in-successor-v2/baseline'
    names = {}
    for kind, source in (('control', 'control'), ('candidate', selected)):
        name = f'{NAME}-reader-{kind}'
        root = study.PRIVATE / name
        root.mkdir(exist_ok=False)
        prepared = [{'question_id': r['question_id'], 'context': r['arms'][source]['context'],
            'context_sha256': r['arms'][source]['context_sha256'], 'context_tokens': r['arms'][source]['tokens'],
            'changed_context': r['arms'][source]['context'] != r['arms']['control']['context'],
            'reason': 'prespecified_label_free_marginal_policy' if kind == 'candidate' else 'new_unchanged_control_repeat',
            'baseline_capture_sha256': r['baseline_capture_sha256'], 'source_pack_tree_sha256': r['input_pack_sha256']}
            for r in rows]
        write_new(root / 'contexts.json', prepared)
        child = {'kind': 'marginal-packing-complete-reader-registration', 'registered_at': study.utc(),
            'source_assay_registration_sha256': reg['registration_sha256'], 'source_sha256': reg['source_sha256'],
            'prepared_sha256': file_sha(root / 'contexts.json'), 'baseline_execution_sha256': file_sha(baseline / 'execution.json'),
            'baseline_verification_sha256': file_sha(baseline / 'verification.json'),
            'dataset_sha256': reg['dataset_sha256'], 'ordered_question_ids': reg['ordered_question_ids'],
            'selected_policy': selected, 'role': kind,
            'candidate_classification': 'Label-free marginal packing; development-selected policy. This child executor compares against the historical control for auditing; the prespecified primary paired answer comparison is candidate versus contemporaneous control repeat.',
            'parent_plan': reg['answer_followup'], 'default_changes': False}
        child['registration_sha256'] = sha(child)
        write_new(study.REPORTS / f'{name}-registration.json', child)
        names[kind] = name
    for name in names.values():
        with (study.PRIVATE / f'{name}.log').open('xb') as log:
            subprocess.run([sys.executable, '-m', 'benchmarks.diagnostics.opt_in_packing_oracle',
                'run', '--name', name], cwd=study.ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    reports = {kind: json.loads((study.REPORTS / f'{name}-result.json').read_text()) for kind, name in names.items()}
    result = {'complete': all(r['complete'] for r in reports.values()), 'selected_policy': selected,
              'source_registration_sha256': reg['registration_sha256'], 'child_results': names,
              'classification': 'Exploratory development; requires untouched confirmation', 'default_changes': False}
    if result['complete']:
        outcomes = {kind: json.loads((study.PRIVATE / name / 'execution.json').read_text())['rows'] for kind, name in names.items()}
        cases = study.base._load_dataset(study.DATASET)
        result.update(control_correct=reports['control']['correct'], candidate_correct=reports['candidate']['correct'],
            paired=study.old.paired_stats(outcomes['control'], outcomes['candidate'], cases),
            category_correct_deltas={k: v['correct']-reports['control']['categories'][k]['correct'] for k, v in reports['candidate']['categories'].items()},
            losses_detail=[c['question_id'] for c, a, b in zip(cases, outcomes['control'], outcomes['candidate']) if a['correct'] and not b['correct']],
            wins_detail=[c['question_id'] for c, a, b in zip(cases, outcomes['control'], outcomes['candidate']) if b['correct'] and not a['correct']])
    else:
        result['answer_metrics'] = None
    write_new(study.REPORTS / f'{NAME}-answer-result.json', result)


async def run(args):
    reg = json.loads((study.REPORTS / f'{NAME}-registration.json').read_text())
    for name, checksum in reg['source_sha256'].items():
        if file_sha(study.ROOT / name) != checksum:
            raise study.old.ResearchFailure('Registered source study code differs')
    parent = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    if args.wait:
        while not all(settled(study.PRIVATE / 'opt-in-successor-v2' / a['id']) for a in parent['arms'] if a['stratum'] == 'historical'):
            await asyncio.sleep(30)
    elif not all(settled(study.PRIVATE / 'opt-in-successor-v2' / a['id']) for a in parent['arms'] if a['stratum'] == 'historical'):
        raise study.old.ResearchFailure('Historical matrix stage is not finalized')
    cases = study.base._load_dataset(study.DATASET)
    study.old.validate_registration(parent, study.ROOT, cases)
    for asset in json.loads((study.REPORTS / 'opt-in-model-assets.json').read_text())['assets']:
        if study.base._tree_identity(Path(asset['root'])) != asset['tree']:
            raise study.old.ResearchFailure('Frozen model assets differ')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    load_complete(study.PRIVATE / 'opt-in-successor-v2/baseline', reg['ordered_question_ids'])
    if file_sha(study.DATASET) != reg['dataset_sha256']:
        raise study.old.ResearchFailure('Source study dataset differs')
    root = study.PRIVATE / NAME
    root.mkdir(exist_ok=False)
    arm = next(a for a in parent['arms'] if a['id'] == 'baseline')
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
                row = await evaluate(case, arm, root)
                if errors:
                    raise study.old.ResearchFailure('Source replay logged a swallowed failure')
            except Exception as exc:
                stop.set()
                row = {'question_id': case['question_id'], 'status': 'failed', 'exception_type': type(exc).__name__,
                       'failure_code': str(exc) if isinstance(exc, study.old.ResearchFailure) else type(exc).__name__,
                       'errors': errors, 'trace_frames': [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                                                        for f in traceback.extract_tb(exc.__traceback__)]}
            finally:
                study.old.ERRORS.reset(token)
            print(json.dumps(row), flush=True)
            return row
    outcomes = await asyncio.gather(*(one(case) for case in cases))
    logging.getLogger('prme').removeHandler(handler)
    complete = all(row['status'] == 'complete' for row in outcomes)
    write_new(root / 'execution.json', {'complete': complete, 'rows': outcomes, 'finished_at': study.utc()})
    result = {'registration_sha256': reg['registration_sha256'], 'complete': complete, 'source_metrics': None,
              'selected_policy': None, 'execution_sha256': file_sha(root / 'execution.json')}
    if complete:
        study.old.validate_complete(reg['ordered_question_ids'], outcomes)
        rows = []
        for row in outcomes:
            path = root / 'cases' / f"{row['question_id']}.json"
            if file_sha(path) != row['case_sha256']:
                raise study.old.ResearchFailure('Source case changed')
            case = json.loads(path.read_text())
            if file_sha(root / 'inputs' / f"{row['question_id']}.json") != case['inputs_sha256']:
                raise study.old.ResearchFailure('Source candidate snapshot changed')
            rows.append(case)
        summaries = summarize_source(rows)
        control = summaries['control']
        eligible = [name for name in marginal.POLICIES if summaries[name]['complete_source_questions'] > control['complete_source_questions']
                    and summaries[name]['mean_source_fraction'] > control['mean_source_fraction']]
        eligible.sort(key=lambda n: (-summaries[n]['complete_source_questions'], -summaries[n]['mean_source_fraction'],
                                    summaries[n]['packing_seconds']['p95'], n))
        result.update(source_metrics=summaries, selected_policy=eligible[0] if eligible else None,
                      verified_cases=len(rows), answer_calls_during_source_assay=0)
    write_new(study.REPORTS / f'{NAME}-source-result.json', result)
    if complete and result['selected_policy']:
        await asyncio.to_thread(answer_followup, reg, rows, result['selected_policy'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['register', 'run'])
    parser.add_argument('--wait', action='store_true')
    args = parser.parse_args()
    register() if args.action == 'register' else asyncio.run(run(args))


if __name__ == '__main__':
    main()
