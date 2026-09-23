"""Full-cohort label-assisted packing diagnostic, outside the feature matrix.

This deliberately uses evidence annotations to prioritize already returned source
turns. It is not deployable retrieval, a promotion candidate, or an upper bound.
No annotation or reference answer enters PRME or a temporal/Jev call.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import time

import duckdb
import httpx

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import load_complete
from benchmarks.diagnostics.opt_in_source_coverage import literal_coverage, required_turns, source_key
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import _render_entry
from prme.retrieval.tokenization import count_tokens
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.types import RepresentationLevel


def priority_context(case, capture, full_entries):
    original = capture['context']
    wanted = required_turns(case)
    coverage = literal_coverage(case, capture)
    if not wanted or coverage['complete_source_literal_recall']:
        return original, 'unchanged_complete_or_unannotated'
    selected = [row['node_id'] for row in capture['returned'] if source_key(row) in wanted]
    if len(selected) != len(wanted) or len(set(selected)) != len(wanted):
        return original, 'unchanged_required_source_not_returned'
    lines = original.split('\n')
    if lines.count('[stable_facts]') != 1 or any(line.startswith('[') and line != '[stable_facts]' for line in lines):
        raise study.old.ResearchFailure('Unexpected oracle section contract')
    header, body = original.split('[stable_facts]\n', 1)
    prefix = header + '[stable_facts]\n'
    chosen = [full_entries[node_id] for node_id in selected]
    context = prefix + '\n'.join(chosen)
    if count_tokens(context, 'cl100k_base') > 3996:
        return original, 'unchanged_required_full_sources_exceed_budget'
    for line in body.split('\n'):
        if json.loads(line)['id'] in selected:
            continue
        trial = context + '\n' + line
        if count_tokens(trial, 'cl100k_base') <= 3996:
            context = trial
    return context, 'annotation_priority_with_original_remainder'


def prepare(args):
    source_reg = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    cases = study.base._load_dataset(study.DATASET)
    study.old.validate_registration(source_reg, study.ROOT, cases)
    baseline = study.PRIVATE / 'opt-in-successor-v2' / 'baseline'
    rows = load_complete(baseline, source_reg['longmemeval_s']['ordered_question_ids'])
    destination = study.PRIVATE / args.name
    destination.mkdir(exist_ok=False)
    prepared = []
    reasons = Counter()
    for case, result in zip(cases, rows):
        start = time.perf_counter()
        location = baseline / case['question_id']
        source = json.loads((location / 'capture.json').read_text())
        if file_sha(location / 'capture.json') != result['capture_sha256']:
            raise study.old.ResearchFailure('Baseline capture differs')
        if study.base._tree_identity(location / 'pack') != source['final_pack']:
            raise study.old.ResearchFailure('Baseline source pack differs')
        capture = source['retrievals'][0]
        entries = {}
        if not case['question_id'].endswith('_abs') and not literal_coverage(case, capture)['complete_source_literal_recall']:
            graph = object.__new__(DuckPGQGraphStore)
            with duckdb.connect(str(location / 'pack/memory.duckdb'), read_only=True) as db:
                wanted = required_turns(case)
                for row in capture['returned']:
                    if source_key(row) not in wanted:
                        continue
                    raw = db.execute('SELECT * FROM nodes WHERE id=?', [row['node_id']]).fetchone()
                    node = graph._row_to_node(raw)
                    if node.content != wanted[source_key(row)]:
                        raise study.old.ResearchFailure('Source literal differs from original turn')
                    entries[row['node_id']] = _render_entry(RetrievalCandidate(node=node,
                        representation=RepresentationLevel.FULL, rendered_text=node.content))
        context, reason = priority_context(case, capture, entries)
        reasons[reason] += 1
        prepared.append({'question_id': case['question_id'], 'context': context,
            'context_sha256': hashlib.sha256(context.encode()).hexdigest(),
            'context_tokens': count_tokens(context, 'cl100k_base'), 'reason': reason,
            'changed_context': context != capture['context'], 'baseline_capture_sha256': result['capture_sha256'],
            'source_pack_tree_sha256': source['final_pack']['tree_sha256'],
            'preparation_seconds': time.perf_counter() - start})
    write_new(destination / 'contexts.json', prepared)
    sources = [str(Path(__file__).relative_to(study.ROOT)),
               'benchmarks/diagnostics/opt_in_source_coverage.py', 'tests/test_opt_in_packing_oracle.py']
    reg = {'kind': 'full-cohort-label-assisted-packing-diagnostic', 'registered_at': study.utc(),
        'source_registration_sha256': source_reg['registration_sha256'],
        'source_sha256': {path: file_sha(study.ROOT / path) for path in sources},
        'prepared_sha256': file_sha(destination / 'contexts.json'),
        'baseline_execution_sha256': file_sha(baseline / 'execution.json'),
        'baseline_verification_sha256': file_sha(baseline / 'verification.json'),
        'dataset_sha256': file_sha(study.DATASET), 'ordered_question_ids': [c['question_id'] for c in cases],
        'baseline_already_inspected': True, 'outcome_selection': 'None: all 500 cases, regardless of original correctness.',
        'method': 'Keep already full-source-complete or unannotated contexts. Otherwise prioritize all annotated whole turns in original returned order, only if all were returned and together fit 3996 tokens. Fill remaining space with original packed entries in original order. Preserve header and source text. On absent candidate or over-budget required set, retain the original context. Never truncate.',
        'annotation_boundary': 'Evidence labels used only by this offline diagnostic selector. No annotations or answers sent to PRME, temporal relations, or Jev. Official reference remains judge-only.',
        'provider': {'model': study.old.MODEL, 'digest': study.old.DIGEST,
                     'reader_limit': study.READER_LIMIT, 'judge_limit': study.JUDGE_LIMIT,
                     'prompts_options_scoring': 'identical to successor-v2; four provider slots'},
        'prepared_reasons': dict(reasons), 'changed_contexts': sum(x['changed_context'] for x in prepared),
        'failure_unit': 'All 500; any terminal failure retains artifacts and publishes no partial answer score.',
        'analysis': 'All-case paired difference and 10000-draw seed-20260922 intervals; separately describe changed versus unchanged inputs, retaining primary baseline. Development diagnostic only, no promotion test.',
        'candidate_classification': 'Non-deployable annotation-assisted diagnostic; not an achievable upper bound or feature-arm result.',
        'default_changes': False}
    reg['registration_sha256'] = sha(reg)
    write_new(study.REPORTS / f'{args.name}-registration.json', reg)
    print(json.dumps({'registered': args.name, 'reasons': reasons, 'changed_contexts': reg['changed_contexts']}))


async def run(args):
    reg = json.loads((study.REPORTS / f'{args.name}-registration.json').read_text())
    for path, checksum in reg['source_sha256'].items():
        if file_sha(study.ROOT / path) != checksum:
            raise study.old.ResearchFailure('Registered diagnostic source differs')
    root = study.PRIVATE / args.name
    if file_sha(root / 'contexts.json') != reg['prepared_sha256'] or file_sha(study.DATASET) != reg['dataset_sha256']:
        raise study.old.ResearchFailure('Diagnostic input differs')
    cases = study.base._load_dataset(study.DATASET)
    prepared = json.loads((root / 'contexts.json').read_text())
    expected = reg['ordered_question_ids']
    if [c['question_id'] for c in cases] != expected or [c['question_id'] for c in prepared] != expected:
        raise study.old.ResearchFailure('Diagnostic cohort differs')
    output = root / 'execution'
    output.mkdir(exist_ok=False)
    official = study.base._load_official_prompt_function(study.OFFICIAL)
    semaphore, provider, stop = asyncio.Semaphore(5), asyncio.Semaphore(4), asyncio.Event()
    async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', timeout=300) as client:
        async def one(case, context):
            async with semaphore:
                row = {'question_id': case['question_id'], 'status': 'not_started'}
                if stop.is_set():
                    return row
                folder = output / case['question_id']
                folder.mkdir()
                try:
                    answer, _ = await study.old.chat(client, provider,
                        study.base._reader_prompt(context['context'], case['question_date'], case['question']),
                        study.READER_LIMIT, folder / 'reader.json')
                    verdict, _ = await study.old.chat(client, provider,
                        official(case['question_type'], case['question'], case['answer'], answer,
                                 abstention=case['question_id'].endswith('_abs')),
                        study.JUDGE_LIMIT, folder / 'judge.json')
                    row.update(status='complete', correct='yes' in verdict.lower(),
                        reader_sha256=file_sha(folder / 'reader.json'), judge_sha256=file_sha(folder / 'judge.json'))
                except Exception as exc:
                    stop.set()
                    row.update(status='failed', exception_type=type(exc).__name__)
                write_new(folder / 'result.json', row)
                print(json.dumps({'event': 'case_finished', **row}), flush=True)
                return row
        rows = await asyncio.gather(*(one(case, context) for case, context in zip(cases, prepared)))
    complete = all(row['status'] == 'complete' for row in rows)
    write_new(root / 'execution.json', {'complete': complete, 'rows': rows, 'finished_at': study.utc()})
    if not complete:
        write_new(study.REPORTS / f'{args.name}-result.json', {'complete': False, 'answer_metrics': None,
            'counts': dict(Counter(row['status'] for row in rows)), 'registration_sha256': reg['registration_sha256']})
        return
    study.old.validate_complete(expected, rows)
    baseline = study.PRIVATE / 'opt-in-successor-v2' / 'baseline'
    if file_sha(baseline / 'execution.json') != reg['baseline_execution_sha256']:
        raise study.old.ResearchFailure('Baseline execution differs')
    control = load_complete(baseline, expected)
    categories = defaultdict(lambda: Counter(total=0, correct=0))
    groups = defaultdict(lambda: Counter(total=0, control_correct=0, candidate_correct=0, wins=0, losses=0))
    usage = Counter()
    for case, context, row, old_row in zip(cases, prepared, rows, control):
        folder = output / case['question_id']
        answer = json.loads((folder / 'reader.json').read_text())['response']['message']['content'].strip()
        prompts = [study.base._reader_prompt(context['context'], case['question_date'], case['question']),
                   official(case['question_type'], case['question'], case['answer'], answer,
                            abstention=case['question_id'].endswith('_abs'))]
        for role, prompt, limit in zip(('reader', 'judge'), prompts, (study.READER_LIMIT, study.JUDGE_LIMIT)):
            artifact = json.loads((folder / f'{role}.json').read_text())
            request = {'model': study.old.MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                       'options': {'temperature': 0, 'seed': 42, 'num_ctx': 65536, 'num_predict': limit},
                       'think': False, 'stream': False}
            if (file_sha(folder / f'{role}.json') != row[f'{role}_sha256'] or artifact['request'] != request
                or artifact['request_sha256'] != sha(request) or artifact['status'] != 'complete'
                or artifact['response']['done_reason'] != 'stop'):
                raise study.old.ResearchFailure('Diagnostic provider chain differs')
            if role == 'judge' and row['correct'] != ('yes' in artifact['response']['message']['content'].lower()):
                raise study.old.ResearchFailure('Diagnostic score differs')
            usage['calls'] += 1
            usage['input_tokens'] += artifact['response']['prompt_eval_count']
            usage['output_tokens'] += artifact['response']['eval_count']
            usage['http_attempts'] += len(artifact['attempts'])
        categories[case['question_type']].update(total=1, correct=row['correct'])
        if case['question_id'].endswith('_abs'):
            categories['abstention'].update(total=1, correct=row['correct'])
        group = groups['changed_input' if context['changed_context'] else 'unchanged_input_repeat']
        group.update(total=1, control_correct=old_row['correct'], candidate_correct=row['correct'],
                     wins=row['correct'] and not old_row['correct'], losses=old_row['correct'] and not row['correct'])
    result = {'complete': True, 'verified_cases': len(rows), 'total': len(rows),
              'correct': sum(row['correct'] for row in rows), 'categories': dict(categories),
              'paired': study.old.paired_stats(control, rows, cases), 'input_groups': dict(groups),
              'provider_usage': dict(usage), 'context_tokens_total': sum(c['context_tokens'] for c in prepared),
              'registration_sha256': reg['registration_sha256'], 'execution_sha256': file_sha(root / 'execution.json'),
              'classification': reg['candidate_classification'], 'primary_baseline_replaced': False}
    write_new(study.REPORTS / f'{args.name}-result.json', result)
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    prepare(args) if args.action == 'prepare' else asyncio.run(run(args))


if __name__ == '__main__':
    main()
