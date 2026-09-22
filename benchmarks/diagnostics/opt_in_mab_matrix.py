"""Complete-arm MemoryAgentBench successor on pinned prepared inputs.

The native fast-ingestion control uses the registered adapter's source splitting,
tool role, NOTE nodes, project scope and context-level sessions. Retrieval flags
never change session partitioning. Direct-store arms each ingest fresh sources
under a separate matched direct-store control. No answers enter either ingestor.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from uuid import NAMESPACE_URL, uuid5
from unittest.mock import patch

import duckdb
import httpx
import numpy as np
from dotenv import dotenv_values
from scipy.stats import binomtest

from benchmarks.diagnostics import opt_in_mab_support as support
from benchmarks.diagnostics import opt_in_arm_worker as worker
from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import describe, holm
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new
from prme import FastIngestItem, MemoryEngine, NodeType, Scope
from prme.retrieval.tokenization import count_tokens
from prme.storage.relevance import RelevanceRepository


USER = 'memoryagentbench'
TASKS = ('banking', 'eventqa', 'conflict', 'detective')


@contextmanager
def matched_admission():
    # The LME fixture matches graph clocks and Event.created_at. Event.timestamp
    # is a separate ingestion clock; match that as well in this new protocol.
    original = worker.fixture_identity
    def identity(kind, values):
        result = original(kind, values)
        if kind == 'event' and worker.FRESH_TURN.get() is not None:
            result.setdefault('timestamp', result['created_at'])
        return result
    with patch.object(worker, 'fixture_identity', identity), worker.matched_admission():
        yield


def graph_identity(pack):
    with duckdb.connect(str(pack / 'memory.duckdb'), read_only=True) as db:
        db.execute("SET TimeZone='UTC'")
        return {table: sha(db.execute(f'SELECT to_json(t) FROM {table} t ORDER BY id').fetchall())
                for table in ('events', 'nodes', 'edges')}


def source_items(chunks, sub_dataset, context_id):
    pieces, indices, counts = support.adapter._split_source_chunks(chunks, 6000, sub_dataset=sub_dataset)
    return [FastIngestItem(content=piece, session_id=f'{sub_dataset}:context:{context_id}',
        role='tool', scope=Scope.PROJECT, metadata={'benchmark': 'memoryagentbench',
        'dataset_revision': support.adapter.DATASET_REVISION, 'sub_dataset': sub_dataset,
        'context_id': context_id, 'source_chunk_index': index, 'piece_index': position,
        'piece_count': len(pieces)}) for position, (piece, index) in enumerate(zip(pieces, indices))]


def cohort(prepared):
    result = []
    for context, group in enumerate(prepared['query_groups']):
        for query, answer, qa_pair_id in group:
            result.append({'question_id': f'q{len(result):05d}', 'context_id': context,
                'question': query, 'references': answer, 'qa_pair_id_sha256': sha(qa_pair_id)})
    return result


async def ingest(chunks, sub_dataset, context_id, arm, folder, key, task):
    """Only source chunks and experiment identity cross this boundary."""
    folder.mkdir(exist_ok=False)
    pack = folder / 'pack'
    pack.mkdir()
    config = study.old.config_for(arm, pack, key)
    for field, relative in (('db_path', 'memory.duckdb'), ('vector_path', 'vectors.usearch'),
                            ('lexical_path', 'lexical_index')):
        if Path(getattr(config, field)).resolve() != (pack / relative).resolve():
            raise study.old.ResearchFailure('Ingestion configuration escaped its private pack')
    items = source_items(chunks, sub_dataset, context_id)
    start = time.perf_counter()
    errors, trace = [], {}
    error_token = study.old.ERRORS.set(errors)
    trace_token = worker.FEATURE_TRACE.set(trace)
    try:
        async with MemoryEngine.open(config) as engine:
            if arm['stratum'] == 'fresh':
                for index, item in enumerate(items):
                    token = worker.FRESH_TURN.set({'question_id': f'mab/{task}/{context_id}',
                        'position': 0, 'index': index, 'ordinal': index})
                    try:
                        await engine.store(item.content, user_id=USER, session_id=item.session_id,
                            role=item.role, scope=item.scope, node_type=NodeType.NOTE, metadata=item.metadata)
                    finally:
                        worker.FRESH_TURN.reset(token)
                reference_time = datetime(2026, 9, 22, tzinfo=timezone.utc) + timedelta(microseconds=len(items)-1)
            else:
                event_ids = await engine.ingest_fast_many(items, user_id=USER,
                    request_id=uuid5(NAMESPACE_URL, f'prme-opt-in-mab-v1/{task}/{context_id}/native'))
                while True:
                    status = await engine.process_pending(user_id=USER, budget_ms=300_000)
                    if status.failed:
                        raise study.old.ResearchFailure('Native ingestion materialization failed')
                    if status.pending == 0:
                        break
                    if status.processed == 0:
                        raise study.old.ResearchFailure('Native ingestion made no progress')
                last = await engine.get_event(event_ids[-1], user_id=USER)
                reference_time = last.created_at.astimezone(timezone.utc)
            status = await engine.process_pending(user_id=USER, budget_ms=0)
            if status.pending or status.failed or errors:
                raise study.old.ResearchFailure('Ingestion availability failure')
    finally:
        study.old.ERRORS.reset(error_token)
        worker.FEATURE_TRACE.reset(trace_token)
    result = {'seconds': time.perf_counter()-start, 'source_chunks': len(chunks), 'source_nodes': len(items),
              'reference_time': reference_time.isoformat(), 'inventory': worker.inventory(pack),
              'graph_identity': graph_identity(pack), 'pack': study.base._tree_identity(pack),
              'feature_observations': trace, 'source_chunks_sha256': sha(chunks), 'stratum': arm['stratum']}
    if result['inventory']['events'] != len(items):
        raise study.old.ResearchFailure('Ingestion source count differs')
    write_new(folder / 'ingestion.json', result)
    return result


async def retrieve(engine, case, arm, reference_time):
    query = support.adapter._retrieval_query(case['question'])
    results = []
    for mode in ('first_per_query', 'immediate_repeat'):
        start = time.perf_counter()
        response = await engine.retrieve(query, user_id=USER, scope=Scope.PROJECT,
            reference_time=reference_time, token_budget=4096, limit=100, include_cross_scope=False)
        elapsed = time.perf_counter()-start
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
        if (not response.metadata.receipt_persisted or receipt is None or response.metadata.backend_failures
            or receipt.replay_ranking() != tuple(c.node.id for c in response.results)):
            raise study.old.ResearchFailure('Retrieval receipt or backend failure')
        temporal = response.metadata.temporal_relation
        if temporal and (temporal.status == 'provider_error' or not temporal.confirmation_protocol_aligned):
            raise study.old.ResearchFailure('Temporal provider/protocol failure')
        context = response.bundle.render()
        tokens = count_tokens(context, 'cl100k_base')
        if tokens != response.bundle.tokens_used or tokens > 3996:
            raise study.old.ResearchFailure('Context accounting failed')
        results.append({'mode': mode, 'seconds': elapsed, 'context': context, 'context_tokens': tokens,
            'receipt': receipt.model_dump(mode='json'), 'metadata': response.metadata.model_dump(mode='json'),
            'returned_ids': [str(c.node.id) for c in response.results],
            'conflicts': sum(bool(c.conflict_flag) for c in response.results)})
    if not (arm['config']['enable_query_reformulation'] or arm['config']['temporal_relation']['enabled']):
        if results[0]['context'] != results[1]['context'] or results[0]['returned_ids'] != results[1]['returned_ids']:
            raise study.old.ResearchFailure('Deterministic retrieval differs')
    return {'retrieval_query': query, 'retrievals': results}


def verify_case(case, prepared, folder, pack):
    row = json.loads((folder / 'result.json').read_text())
    capture = json.loads((folder / 'capture.json').read_text())
    reader = json.loads((folder / 'reader.json').read_text())
    expected = support.request_body(support.reader_messages(prepared,
        capture['retrievals'][0]['context'], case['question']), prepared['dataset_config']['generation_max_length'])
    if (row['status'] != 'complete' or reader['status'] != 'complete' or reader['request'] != expected
        or reader['request_sha256'] != sha(expected) or reader['response']['done_reason'] != 'stop'
        or capture['retrieval_query'] != support.adapter._retrieval_query(case['question'])):
        raise study.old.ResearchFailure('Reader or query verification failed')
    for name in ('capture', 'reader', 'feature-observations'):
        if file_sha(folder / f'{name}.json') != row[name.replace('-', '_') + '_sha256']:
            raise study.old.ResearchFailure('Case artifact identity differs')
    metrics = support.score(prepared, reader['response']['message']['content'], case['references'])
    if row['score'] != metrics:
        raise study.old.ResearchFailure('Official rescoring differs')
    with duckdb.connect(str(pack / 'memory.duckdb'), read_only=True) as db:
        for result in capture['retrievals']:
            request_id = result['metadata']['request_id']
            payloads = db.execute("SELECT payload FROM operations WHERE op_type='RETRIEVAL_REQUEST' AND target_id=? AND actor_id=?",
                                  [request_id, USER]).fetchall()
            if len(payloads) != 1:
                raise study.old.ResearchFailure('Durable receipt is missing')
            receipt = RelevanceRepository._read_receipt(payloads[0][0], request_id=request_id, user_id=USER)
            if (receipt.model_dump(mode='json') != result['receipt'] or
                receipt.context_sha256 != hashlib.sha256(result['context'].encode()).hexdigest() or
                result['context_tokens'] != count_tokens(result['context'], 'cl100k_base') or
                tuple(map(str, receipt.replay_ranking())) != tuple(result['returned_ids'])):
                raise study.old.ResearchFailure('Durable receipt replay differs')
    return row


async def run_arm(task, prepared, arm, root, masters, key):
    folder = root / arm['id']
    folder.mkdir(exist_ok=False)
    cases = cohort(prepared)
    records = {c['question_id']: {'question_id': c['question_id'], 'status': 'not_started'} for c in cases}
    stop = asyncio.Event()
    provider = asyncio.Semaphore(4)
    contexts = []
    async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', timeout=300) as client:
        async def answer(case, location, capture, trace):
            row = {'question_id': case['question_id'], 'status': 'failed', 'context_id': case['context_id']}
            try:
                _, artifact = await support.chat(client, provider, support.reader_messages(prepared,
                    capture['retrievals'][0]['context'], case['question']),
                    prepared['dataset_config']['generation_max_length'], location / 'reader.json')
                score = support.score(prepared, artifact['response']['message']['content'], case['references'])
                row.update(status='complete', correct=score['correct'], score=score,
                    capture_sha256=file_sha(location / 'capture.json'), reader_sha256=file_sha(location / 'reader.json'))
            except Exception as exc:
                stop.set()
                row.update(exception_type=type(exc).__name__)
            write_new(location / 'feature-observations.json', trace)
            row['feature_observations_sha256'] = file_sha(location / 'feature-observations.json')
            write_new(location / 'result.json', row)
            records[case['question_id']] = row
            print(json.dumps({'event': 'case_finished', 'task': task, 'arm': arm['id'],
                              'question_id': case['question_id'], 'status': row['status']}), flush=True)
        for context_id, chunks in enumerate(prepared['chunks']):
            if stop.is_set():
                break
            context_folder = folder / f'context-{context_id:03d}'
            pending = []
            try:
                if arm['stratum'] == 'fresh':
                    ingested = await ingest(chunks, prepared['dataset_config']['sub_dataset'], context_id,
                                            arm, context_folder, key, task)
                else:
                    context_folder.mkdir()
                    ingested = json.loads((masters / f'context-{context_id:03d}' / 'ingestion.json').read_text())
                    master = masters / f'context-{context_id:03d}' / 'pack'
                    if study.base._tree_identity(master) != ingested['pack']:
                        raise study.old.ResearchFailure('Native master changed')
                    study.old._clone_pack(master, context_folder / 'pack')
                pack = context_folder / 'pack'
                config = study.old.config_for(arm, pack, key)
                async with MemoryEngine.open(config) as engine:
                    for case in (c for c in cases if c['context_id'] == context_id):
                        if stop.is_set():
                            break
                        location = folder / case['question_id']
                        location.mkdir()
                        errors, trace = [], {}
                        token = study.old.ERRORS.set(errors)
                        feature_token = worker.FEATURE_TRACE.set(trace)
                        try:
                            capture = await retrieve(engine, case, arm, datetime.fromisoformat(ingested['reference_time']))
                            if errors:
                                raise study.old.ResearchFailure('Swallowed feature failure')
                            write_new(location / 'capture.json', capture)
                            pending.append(asyncio.create_task(answer(case, location, capture, trace)))
                            # Bound pending provider work while allowing sequential retrieval.
                            if len(pending) >= 5:
                                await asyncio.gather(*pending)
                                pending.clear()
                        except Exception as exc:
                            stop.set()
                            row = {'question_id': case['question_id'], 'context_id': context_id,
                                   'status': 'failed', 'exception_type': type(exc).__name__, 'errors': errors}
                            write_new(location / 'result.json', row)
                            records[case['question_id']] = row
                        finally:
                            worker.FEATURE_TRACE.reset(feature_token)
                            study.old.ERRORS.reset(token)
                    await asyncio.gather(*pending)
                    pending.clear()
                if graph_identity(pack) != ingested['graph_identity']:
                    raise study.old.ResearchFailure('Retrieval mutated source graph')
                contexts.append({'context_id': context_id, 'ingestion': ingested,
                                 'final_pack': study.base._tree_identity(pack)})
            except Exception as exc:
                stop.set()
                await asyncio.gather(*pending)
                write_new(context_folder / 'failure.json', {'exception_type': type(exc).__name__,
                          'failure_code': str(exc) if isinstance(exc, study.old.ResearchFailure) else type(exc).__name__})
    rows = [records[c['question_id']] for c in cases]
    complete = not stop.is_set() and len(contexts) == len(prepared['chunks']) and all(r['status'] == 'complete' for r in rows)
    state = {'task': task, 'arm': arm['id'], 'complete': complete, 'rows': rows,
             'contexts': contexts, 'finished_at': study.utc()}
    write_new(folder / 'execution.json', state)
    if complete:
        study.old.validate_complete([c['question_id'] for c in cases], rows)
        for case in cases:
            verify_case(case, prepared, folder / case['question_id'], folder / f"context-{case['context_id']:03d}" / 'pack')
        write_new(folder / 'verification.json', {'complete': True, 'cases': len(rows), 'receipts': 2*len(rows),
            'execution_sha256': file_sha(folder / 'execution.json'),
            'artifact_manifest_sha256': sha([c['final_pack'] for c in contexts])})
    print(json.dumps({'event': 'arm_finished', 'task': task, 'arm': arm['id'], 'complete': complete}), flush=True)


def register(args):
    calibration = study.REPORTS / 'opt-in-mab-reader-calibration-v1-result.json'
    if not json.loads(calibration.read_text())['passed']:
        raise study.old.ResearchFailure('MAB reader calibration did not pass')
    source = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    arms = deepcopy(source['arms'])
    # Preserve the registered adapter's context-session-v1 partition in every arm.
    tasks = {}
    for task in TASKS:
        prepared = support.load_task(task)
        identity, count = support.registrar._registered_contexts(prepared['chunks'], prepared['query_groups'],
            max_chunk_chars=6000, sub_dataset=prepared['dataset_config']['sub_dataset'], max_queries=None)
        tasks[task] = {'prepared_sha256': file_sha(support.PREPARED / f'{task}.json'), 'query_count': count,
                      'contexts': identity, 'metric': prepared['metric'], 'dataset_config': prepared['dataset_config'],
                      'agent_config': prepared['agent_config'], 'classification': 'development; prior exposure not excluded'}
    names = ['benchmarks/diagnostics/opt_in_mab_matrix.py', 'benchmarks/diagnostics/opt_in_mab_support.py',
             'benchmarks/diagnostics/opt_in_arm_worker.py', 'benchmarks/diagnostics/opt_in_successor.py',
             'benchmarks/diagnostics/run_opt_in_interactions.py', 'tests/test_opt_in_mab_matrix.py',
             'tests/test_opt_in_mab_support.py', 'benchmarks/integrations/memoryagentbench.py']
    reg = {'kind': 'complete-arm-mab-opt-in-registration', 'registered_at': study.utc(),
        'production_revision': source['production_revision'], 'production_python_sources_sha256': source['production_python_sources_sha256'],
        'source_sha256': {name: file_sha(study.ROOT / name) for name in names},
        'packages': study.old.package_identity(), 'preprocessing': support.registrar._preprocessing_identity(),
        'upstream_sha256': {name: file_sha(support.UPSTREAM / name) for name in
                            ('utils/eval_other_utils.py', 'utils/eval_data_utils.py', 'conversation_creator.py')},
        'calibration_sha256': file_sha(calibration), 'arms': arms, 'tasks': tasks,
        'model_assets_manifest_sha256': file_sha(study.REPORTS / 'opt-in-model-assets.json'),
        'reader': {'model': study.old.MODEL, 'digest': study.old.DIGEST, 'temperature': 0, 'seed': 42,
                   'think': False, 'num_ctx': 65536, 'output_limit': 'Exact task generation_max_length',
                   'judge': 'Pinned official deterministic post_process; no model judge'},
        'ingestion': 'Native control: same task-aware source splitting, tool role, NOTE nodes, PROJECT scope and context-session-v1 as the registered adapter. Retrieval-only arms clone one immutable source pack. Fresh control and ingestion flags call actual store for every same NOTE/tool piece, matched UUID5 and admission clocks. No question or answer enters ingestion. Fresh flags may be inapplicable to these source types; record activation.',
        'episode_partition': 'Always context-session-v1 in all arms, including episode routing. The native adapter convenience flag also changes session partition; this harness deliberately holds ingestion fixed. Source-chunk partition is not included as an unnoticed retrieval change.',
        'failure_unit': 'Every query/context in the complete task arm; retain terminal failure, no partial score, no replacement. Independent arms continue.',
        'analysis': 'Per-task official scores, every context score, paired 10000-draw seed-20260922 question and source-context-cluster intervals. Cluster CI unavailable with one context. Exact discordant test descriptive within these fixed contexts; Holm within each task across all noncontrol/nonexploratory arms, including failed slots. No default recommendation without untouched confirmation.',
        'order': list(TASKS), 'timing': 'One memory engine per context, sequential first/immediate-repeat retrieval, at most four reader requests. Shared host load; first-per-query is not an isolated cold-start measurement.',
        'best_combination': 'Separate preregistration after LongMemEval selection; do not choose favorable MAB results.',
        'promotion_authorized': False}
    reg['registration_sha256'] = sha(reg)
    write_new(study.REPORTS / f'{args.name}-registration.json', reg)
    print(json.dumps({'registered': args.name, 'questions': {t: v['query_count'] for t, v in tasks.items()}}))


async def run(args):
    reg = json.loads((study.REPORTS / f'{args.name}-registration.json').read_text())
    if reg['registration_sha256'] != sha({k: v for k, v in reg.items() if k != 'registration_sha256'}):
        raise study.old.ResearchFailure('Registration differs')
    for name, checksum in {**reg['source_sha256'], **reg['production_python_sources_sha256']}.items():
        if file_sha(study.ROOT / name) != checksum:
            raise study.old.ResearchFailure('Registered code differs')
    for name, checksum in reg['upstream_sha256'].items():
        if file_sha(support.UPSTREAM / name) != checksum:
            raise study.old.ResearchFailure('Official source differs')
    if study.old.package_identity() != reg['packages'] or support.registrar._preprocessing_identity() != reg['preprocessing']:
        raise study.old.ResearchFailure('Dependencies differ')
    assets_path = study.REPORTS / 'opt-in-model-assets.json'
    if file_sha(assets_path) != reg['model_assets_manifest_sha256']:
        raise study.old.ResearchFailure('Model assets manifest differs')
    for asset in json.loads(assets_path.read_text())['assets']:
        if study.base._tree_identity(Path(asset['root'])) != asset['tree']:
            raise study.old.ResearchFailure('Model assets differ')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    # Enforce the requested benchmark stage order before any source or provider work.
    lme = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    for arm in lme['arms']:
        folder = study.PRIVATE / 'opt-in-successor-v2' / arm['id']
        if not (folder / 'execution.json').exists():
            raise study.old.ResearchFailure('LongMemEval stage has not finalized all arms')
        state = json.loads((folder / 'execution.json').read_text())
        if state['complete'] and not (folder / 'verification.json').exists():
            raise study.old.ResearchFailure('LongMemEval complete arm awaits verification')
    output = study.PRIVATE / args.name
    output.mkdir(exist_ok=False)
    key = os.environ.get('JEV_API_KEY') or dotenv_values(study.ORIGINAL / '.env').get('JEV_API_KEY')
    handler = study.old.FailureObserver()
    logging.getLogger('prme').addHandler(handler)
    logging.getLogger('prme').setLevel(logging.DEBUG)
    with matched_admission(), worker.observed_features():
        for task in TASKS:
            if file_sha(support.PREPARED / f'{task}.json') != reg['tasks'][task]['prepared_sha256']:
                raise study.old.ResearchFailure('Prepared task differs')
            prepared = support.load_task(task)
            root = output / task
            root.mkdir()
            masters = root / 'masters'
            masters.mkdir()
            baseline = next(a for a in reg['arms'] if a['id'] == 'baseline')
            for context_id, chunks in enumerate(prepared['chunks']):
                await ingest(chunks, prepared['dataset_config']['sub_dataset'], context_id, baseline,
                             masters / f'context-{context_id:03d}', key, task)
            for arm in reg['arms']:
                await run_arm(task, prepared, arm, root, masters, key)
            analyze(argparse.Namespace(name=args.name, output=f'{args.name}-{task}-stage-results.json'))
    logging.getLogger('prme').removeHandler(handler)


def paired(control, candidate, cases):
    delta = np.array([int(b['correct'])-int(a['correct']) for a, b in zip(control, candidate)])
    rng = np.random.default_rng(20260922)
    question_bootstrap = [float(rng.choice(delta, size=len(delta), replace=True).mean()) for _ in range(10000)]
    groups = defaultdict(list)
    for index, case in enumerate(cases):
        groups[case['context_id']].append(index)
    clusters = list(groups.values())
    interval = None
    if len(clusters) > 1:
        rng = np.random.default_rng(20260922)
        draws = [float(delta[np.concatenate([clusters[i] for i in rng.integers(len(clusters), size=len(clusters))])].mean())
                 for _ in range(10000)]
        interval = np.quantile(draws, [.025, .975]).tolist()
    wins, losses = int((delta == 1).sum()), int((delta == -1).sum())
    return {'difference': float(delta.mean()), 'wins': wins, 'losses': losses,
            'question_ci95': np.quantile(question_bootstrap, [.025, .975]).tolist(),
            'source_context_cluster_ci95': interval, 'source_contexts': len(clusters),
            'cluster_inference_limit': 'No between-source interval with one context; few clusters remain weak external evidence.',
            'discordant_exact_two_sided_p': float(binomtest(wins, wins+losses, .5).pvalue) if wins+losses else 1.0}


def analyze(args):
    reg = json.loads((study.REPORTS / f'{args.name}-registration.json').read_text())
    output = {'registration_sha256': reg['registration_sha256'], 'tasks': {},
              'classification': 'Inspected development; no default promotion', 'analyzed_at': study.utc()}
    for task in TASKS:
        prepared = support.load_task(task)
        cases = cohort(prepared)
        expected = [case['question_id'] for case in cases]
        task_result = {'arms': {}, 'comparisons': {}, 'metric': prepared['metric']}
        all_rows, all_captures = {}, {}
        for arm in reg['arms']:
            folder = study.PRIVATE / args.name / task / arm['id']
            if not (folder / 'execution.json').exists():
                task_result['arms'][arm['id']] = {'status': 'not_complete_or_not_started', 'answer_metrics': None}
                continue
            state = json.loads((folder / 'execution.json').read_text())
            if not state['complete']:
                task_result['arms'][arm['id']] = {'status': 'failed_closed', 'answer_metrics': None,
                    'counts': dict(Counter(r['status'] for r in state['rows'])),
                    'failures': [r for r in state['rows'] if r['status'] == 'failed'],
                    'context_failures': [json.loads(p.read_text()) for p in folder.glob('context-*/failure.json')]}
                continue
            verification = json.loads((folder / 'verification.json').read_text())
            study.old.validate_complete(expected, state['rows'])
            if not verification['complete'] or verification['execution_sha256'] != file_sha(folder / 'execution.json'):
                raise study.old.ResearchFailure('Incomplete verified arm')
            times, metric_values, categories = defaultdict(list), defaultdict(list), defaultdict(lambda: Counter(total=0, correct=0))
            inventory, activation, usage = Counter(), Counter(), Counter()
            captures, tokens = [], []
            for context in state['contexts']:
                pack = folder / f"context-{context['context_id']:03d}" / 'pack'
                if study.base._tree_identity(pack) != context['final_pack']:
                    raise study.old.ResearchFailure('Final context pack differs')
                inventory.update(context['ingestion']['inventory'])
                times['ingestion'].append(context['ingestion']['seconds'])
                for key, value in context['ingestion']['feature_observations'].items():
                    if isinstance(value, int):
                        activation[key] += value
            for case, row in zip(cases, state['rows']):
                location = folder / case['question_id']
                verify_case(case, prepared, location, folder / f"context-{case['context_id']:03d}" / 'pack')
                capture = json.loads((location / 'capture.json').read_text())
                captures.append(capture)
                first = capture['retrievals'][0]
                tokens.append(first['context_tokens'])
                categories[str(case['context_id'])].update(total=1, correct=row['correct'])
                for key, value in row['score']['metrics'].items():
                    metric_values[key].append(float(value))
                for retrieval in capture['retrievals']:
                    times[retrieval['mode']].append(retrieval['seconds'])
                activation['conflict_flags'] += first['conflicts']
                temporal = first['metadata'].get('temporal_relation')
                if temporal:
                    activation['temporal_' + temporal['status']] += 1
                for provenance in first['receipt']['score_provenance'].values():
                    activation.update('score_operation_' + op['kind'] for op in provenance['adjustments'])
                trace = json.loads((location / 'feature-observations.json').read_text())
                activation['reformulation_calls'] += len(trace.get('reformulations', []))
                activation['nonempty_reformulations'] += sum(bool(r['alternatives']) for r in trace.get('reformulations', []))
                reader = json.loads((location / 'reader.json').read_text())
                usage.update(calls=1, input_tokens=reader['response']['prompt_eval_count'],
                    output_tokens=reader['response']['eval_count'], http_attempts=len(reader['attempts']),
                    transient_http_errors=sum(r['http_status'] >= 400 for r in reader['attempts']))
            task_result['arms'][arm['id']] = {'status': 'complete_verified', 'total': len(cases),
                'correct': sum(r['correct'] for r in state['rows']), 'metrics': {k: float(np.mean(v)) for k, v in metric_values.items()},
                'context_scores': dict(categories), 'context_tokens': describe(tokens),
                'latency_seconds': {k: describe(v) for k, v in times.items()},
                'ingestion_summed_seconds': sum(times['ingestion']),
                'ingestion_kind': 'fresh_direct_store' if arm['stratum'] == 'fresh' else 'reused_native_master',
                'monetary_cost': None, 'inventory': dict(inventory), 'activation': dict(activation),
                'reader_usage': dict(usage), 'evidence_omissions': None,
                'evidence_annotation_limit': 'These prepared tasks have no matched source-turn labels; answer overlap is not evidence completeness.',
                'configuration_sha256': arm['config_sha256'], 'artifact_manifest_sha256': verification['artifact_manifest_sha256']}
            all_rows[arm['id']], all_captures[arm['id']] = state['rows'], captures
        p_values = {}
        family_size = sum(a['id'] != a['control'] and not a['exploratory'] for a in reg['arms'])
        for arm in reg['arms']:
            name, control = arm['id'], arm['control']
            if name == control or name not in all_rows or control not in all_rows:
                continue
            stats = paired(all_rows[control], all_rows[name], cases)
            stats.update(control=control, exploratory=arm['exploratory'], promotion_status='opt_in_only_pending_untouched_confirmation',
                losses_detail=[c['question_id'] for c, a, b in zip(cases, all_rows[control], all_rows[name]) if a['correct'] and not b['correct']],
                wins_detail=[c['question_id'] for c, a, b in zip(cases, all_rows[control], all_rows[name]) if b['correct'] and not a['correct']])
            pairs = list(zip(all_captures[control], all_captures[name]))
            stats['changed_contexts'] = sum(a['retrievals'][0]['context'] != b['retrievals'][0]['context'] for a, b in pairs)
            stats['unchanged_input_score_disagreements'] = sum(
                a['retrievals'][0]['context'] == b['retrievals'][0]['context'] and x['correct'] != y['correct']
                for (a, b), x, y in zip(pairs, all_rows[control], all_rows[name]))
            task_result['comparisons'][name] = stats
            if not arm['exploratory']:
                p_values[name] = stats['discordant_exact_two_sided_p']
        for name, value in holm(p_values, family_size).items():
            task_result['comparisons'][name]['holm_adjusted_p'] = value
        output['tasks'][task] = task_result
    output['analysis_sha256'] = sha(output)
    write_new(study.REPORTS / args.output, output)
    print(json.dumps({task: {arm: info.get('correct') for arm, info in values['arms'].items()}
                      for task, values in output['tasks'].items()}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['register', 'run', 'analyze'])
    parser.add_argument('--name', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.action == 'register':
        register(args)
    elif args.action == 'run':
        asyncio.run(run(args))
    else:
        if not args.output:
            parser.error('--output is required for analysis')
        analyze(args)


if __name__ == '__main__':
    main()
