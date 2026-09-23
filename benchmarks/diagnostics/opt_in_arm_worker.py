# Execution-only derivative of frozen opt_in_successor.py; see scheduling amendment.
"""Versioned, arm-complete successor to the retained failed opt-in study.

Research only. Production sources and the original registration are immutable.
All 500 cases are required for each reported comparison. A terminal arm failure
is retained and cannot be resumed; other preregistered arms still execute.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time
import traceback
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import duckdb
import httpx
from dotenv import dotenv_values

from benchmarks.diagnostics import run_opt_in_interactions as old
from benchmarks.diagnostics.register_opt_in_interactions import (
    arms, configure, file_sha, sha, write_new,
)
from benchmarks.integrations import run_longmemeval_s_baseline as base
from prme import MemoryEngine, NodeType, Scope
from prme.retrieval.tokenization import count_tokens
from prme.storage.relevance import RelevanceRepository

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = Path('/Users/dwamianm/Sites/prism')
DATASET = ORIGINAL / 'data/benchmarks/longmemeval/longmemeval_s_cleaned.json'
MASTER = ORIGINAL / 'data/benchmarks/longmemeval-s-prme-baseline-v1'
OFFICIAL = ROOT / 'data/opt-in-study/LongMemEval-official'
REPORTS = ROOT / 'benchmarks/results/research/2026-09-22'
PRIVATE = ROOT / 'data/opt-in-study'
READER_LIMIT = 8192
JUDGE_LIMIT = 64
FRESH_TURN: ContextVar[dict | None] = ContextVar('successor_fresh_turn', default=None)
FEATURE_TRACE: ContextVar[dict | None] = ContextVar('successor_feature_trace', default=None)


def utc():
    return datetime.now(timezone.utc).isoformat()


def fixture_identity(kind, kwargs):
    """Only stabilize new IDs/admission clocks; never inject content or labels."""
    turn = FRESH_TURN.get()
    result = dict(kwargs)
    if turn is None:
        return result
    identity = f"prme-opt-in-v2/{turn['question_id']}/{turn['position']}/{turn['index']}/{kind}"
    stamp = datetime(2026, 9, 22, tzinfo=timezone.utc) + timedelta(microseconds=turn['ordinal'])
    for key, value in {'id': uuid5(NAMESPACE_URL, identity), 'created_at': stamp,
                       'updated_at': stamp}.items():
        result.setdefault(key, value)
    if kind != 'event':
        result.setdefault('valid_from', stamp)
        result.setdefault('last_reinforced_at', stamp)
    return result


@contextmanager
def matched_admission():
    # Constructors remain the actual Pydantic models. The wrapper only supplies
    # otherwise random identities and wall-clock defaults during direct store().
    import prme.storage.engine as module
    event_type, node_type = module.Event, module.MemoryNode
    def event(**kwargs):
        return event_type(**fixture_identity('event', kwargs))
    def node(**kwargs):
        kind = 'qa' if (kwargs.get('metadata') or {}).get('qa_pair') else 'source'
        return node_type(**fixture_identity(kind, kwargs))
    with patch.object(module, 'Event', event), patch.object(module, 'MemoryNode', node):
        yield


@contextmanager
def observed_features():
    """Observe actual calls without changing returned values or retry policy."""
    import prme.retrieval.reformulation as reformulation
    from prme.storage.vector_index import VectorIndex
    vector_search = VectorIndex.search
    original = reformulation.reformulate_query
    supersedence = MemoryEngine._check_store_supersedence
    novelty = MemoryEngine._compute_novelty
    async def reformulate(*args, **kwargs):
        alternatives = await original(*args, **kwargs)
        trace = FEATURE_TRACE.get()
        if trace is not None:
            trace.setdefault('reformulations', []).append({'query':args[0], 'alternatives':alternatives})
        return alternatives
    async def supersede(*args, **kwargs):
        trace = FEATURE_TRACE.get()
        if trace is not None:
            trace['supersedence_checks'] = trace.get('supersedence_checks', 0) + 1
        return await supersedence(*args, **kwargs)
    async def surprise(*args, **kwargs):
        trace = FEATURE_TRACE.get()
        if trace is not None:
            trace['novelty_calls'] = trace.get('novelty_calls', 0) + 1
        return await novelty(*args, **kwargs)
    async def checked_vector_search(*args, **kwargs):
        try:
            return await vector_search(*args, **kwargs)
        except Exception as exc:
            errors = old.ERRORS.get()
            if errors is not None:
                errors.append({'logger':'research.vector_search_observer',
                               'exception_type':type(exc).__name__})
            raise
    with patch.object(reformulation, 'reformulate_query', reformulate), \
         patch.object(MemoryEngine, '_check_store_supersedence', supersede), \
         patch.object(MemoryEngine, '_compute_novelty', surprise), \
         patch.object(VectorIndex, 'search', checked_vector_search):
        yield


def inventory(pack):
    with duckdb.connect(str(pack / 'memory.duckdb'), read_only=True) as db:
        columns = {r[0] for r in db.execute('DESCRIBE nodes').fetchall()}
        rows = db.execute('SELECT id, lifecycle_state, metadata, content FROM nodes').fetchall()
        counts = Counter()
        for _, state, metadata, _ in rows:
            counts['nodes'] += 1
            counts['lifecycle_' + state] += 1
            metadata = json.loads(metadata) if isinstance(metadata, str) else metadata or {}
            counts['qa_pairs'] += bool(metadata.get('qa_pair'))
            counts['novelty_scores'] += 'novelty_score' in metadata
        counts['events'] = db.execute('SELECT count(*) FROM events').fetchone()[0]
        counts['superseded_pointers'] = (db.execute('SELECT count(*) FROM nodes WHERE superseded_by IS NOT NULL').fetchone()[0]
                                         if 'superseded_by' in columns else 0)
        return dict(counts)


async def fresh_ingest(case, config, folder):
    start = time.perf_counter()
    count = empty = 0
    # Only these source fields cross the ingestion boundary.
    histories = list(zip(case['haystack_session_ids'], case['haystack_dates'], case['haystack_sessions']))
    async with MemoryEngine.open(config) as engine:
        for position, (session_id, date, session) in enumerate(histories):
            for index, turn in enumerate(session):
                if not turn['content'].strip():
                    empty += 1
                    continue
                token = FRESH_TURN.set(dict(question_id=case['question_id'], position=position,
                                           index=index, ordinal=count))
                try:
                    await engine.store(turn['content'], user_id=base.USER_ID,
                        session_id=f'{position:05d}:{session_id}', role=turn['role'],
                        node_type=NodeType.FACT, scope=Scope.PERSONAL,
                        metadata={'benchmark': 'longmemeval-s', 'source_session_id': session_id,
                                  'source_session_position': position, 'source_turn_index': index,
                                  'source_role': turn['role']}, event_time=base._parse_date(date))
                finally:
                    FRESH_TURN.reset(token)
                count += 1
        status = await engine.process_pending(user_id=base.USER_ID, budget_ms=0)
        if status.pending or status.failed:
            raise old.ResearchFailure('Fresh materialization backlog')
    result = {'kind': 'fresh_matched_admission', 'seconds': time.perf_counter()-start,
              'stored_turns': count, 'empty_turns_omitted': empty,
              'inventory': inventory(folder / 'pack')}
    if result['inventory']['events'] != count:
        raise old.ResearchFailure('Fresh source count mismatch')
    write_new(folder / 'ingestion.json', result)
    return result


async def retrieve_pack(case, arm, folder, key, ingestion, input_tree):
    pack = folder / 'pack'
    config = old.config_for(arm, pack, key)
    start = time.perf_counter()
    engine = await MemoryEngine.create(config)
    open_seconds = time.perf_counter()-start
    responses = []
    try:
        for mode in ('cold', 'warm'):
            start = time.perf_counter()
            response = await engine.retrieve(case['question'], user_id=base.USER_ID,
                reference_time=base._parse_date(case['question_date']))
            elapsed = time.perf_counter()-start
            receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=base.USER_ID)
            if (not response.metadata.receipt_persisted or receipt is None or response.metadata.backend_failures
                or receipt.replay_ranking() != tuple(x.node.id for x in response.results)):
                raise old.ResearchFailure('Retrieval availability/receipt failure')
            outcome = response.metadata.temporal_relation
            if outcome and (outcome.status == 'provider_error' or not outcome.confirmation_protocol_aligned):
                raise old.ResearchFailure('Temporal provider/protocol failure')
            context = response.bundle.render()
            tokens = count_tokens(context, config.packing.tokenizer)
            if tokens != response.bundle.tokens_used or tokens > 3996:
                raise old.ResearchFailure('Context accounting failed')
            returned, packed = base._response_sources(response, receipt)
            responses.append(dict(mode=mode, seconds=elapsed, context=context,
                context_sha256=hashlib.sha256(context.encode()).hexdigest(), context_tokens=tokens,
                receipt=receipt.model_dump(mode='json'), metadata=response.metadata.model_dump(mode='json'),
                returned=returned, packed=packed, evidence=base._evidence_metrics(case, packed),
                conflicts=sum(bool(x.conflict_flag) for x in response.results)))
    finally:
        await engine.close()
    equal = responses[0]['context'] == responses[1]['context']
    if not (config.enable_query_reformulation or config.temporal_relation.enabled):
        if not equal or responses[0]['returned'] != responses[1]['returned']:
            raise old.ResearchFailure('Deterministic cold/warm mismatch')
    result = dict(input_pack=input_tree, final_pack=base._tree_identity(pack), ingestion=ingestion,
                  open_seconds=open_seconds, cold_warm_context_equal=equal, retrievals=responses)
    write_new(folder / 'capture.json', result)
    return result


async def capture(case, arm, folder, key):
    if arm['stratum'] == 'historical':
        return await old.capture(case, arm, folder, MASTER, key)
    pack = folder / 'pack'
    pack.mkdir()
    config = old.config_for(arm, pack, key)
    ingestion = await fresh_ingest(case, config, folder)
    return await retrieve_pack(case, arm, folder, key, ingestion, base._tree_identity(pack))


def calibration_cases():
    result = []
    for n in range(8):
        distractors = '\n'.join(f'2024-02-{j+1:02d}: I watered plant {j+1} and bought {j+2} notebooks.' for j in range(28))
        if n % 4 == 0:
            evidence = f'2024-01-01: I completed project Cedar.\n2024-01-{n+7:02d}: I delivered project Cedar.'
            question, answer, category = 'How many days passed between completing and delivering project Cedar?', str(n+6)+' days', 'temporal-reasoning'
        elif n % 4 == 1:
            evidence = '2024-01-01: My preferred paint color is blue.\n2024-02-01: My preferred paint color is now green; blue is my former preference.'
            question, answer, category = 'What is my current preferred paint color?', 'green', 'knowledge-update'
        elif n % 4 == 2:
            evidence = '\n'.join(f'2024-01-{j+1:02d}: I completed task {chr(65+j)}.' for j in range(20))
            question, answer, category = 'How many tasks did I complete in January?', '20', 'multi-session'
        else:
            evidence = '2024-01-01: I went to a museum. I did not name it or specify the city.'
            question, answer, category = 'What was the exact name of the museum?', 'The museum name is not known.', 'single-session-user'
        result.append(dict(id=f'authored-{n}', context=distractors+'\n'+evidence,
                           question=question, answer=answer, category=category, abstention=n % 4 == 3))
    return result


async def calibrate(args):
    cases = calibration_cases()
    registration = dict(kind='opt-in-successor-authored-calibration', registered_at=utc(),
        source_sha256=file_sha(Path(__file__)), reader_limit=READER_LIMIT, judge_limit=JUDGE_LIMIT,
        model=old.MODEL, digest=old.DIGEST, cases=cases, repetitions=2,
        rationale='8192 allows headroom for the unchanged step-by-step prompt. No dataset answer is used to choose the cap.',
        gate='All calls complete; all positive/negative authored judge controls classified correctly; reader reference equivalence recorded separately.')
    folder = PRIVATE / args.name
    folder.mkdir(parents=True, exist_ok=False)
    write_new(REPORTS / f'{args.name}-registration.json', registration)
    official = base._load_official_prompt_function(OFFICIAL)
    results = []
    async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', timeout=300) as client:
        for repeat in range(2):
            for case in cases:
                stem = f"{case['id']}-{repeat}"
                response, record = await old.chat(client, asyncio.Semaphore(1),
                    base._reader_prompt(case['context'], '2024/03/01 (Fri) 12:00', case['question']),
                    READER_LIMIT, folder / f'{stem}-reader.json')
                verdict, _ = await old.chat(client, asyncio.Semaphore(1),
                    official(case['category'], case['question'], case['answer'], response, abstention=case['abstention']),
                    JUDGE_LIMIT, folder / f'{stem}-judge.json')
                results.append(dict(case=case['id'], repeat=repeat, correct='yes' in verdict.lower(),
                                    output_tokens=record['response']['eval_count']))
        controls = []
        for i, case in enumerate(cases):
            for positive in (True, False):
                candidate = case['answer'] if positive else 'Definitely 8,765 purple elephants.'
                verdict, _ = await old.chat(client, asyncio.Semaphore(1),
                    official(case['category'], case['question'], case['answer'], candidate, abstention=case['abstention']),
                    JUDGE_LIMIT, folder / f'control-{i}-{positive}.json')
                controls.append(dict(case=case['id'], positive=positive, correct=('yes' in verdict.lower()) == positive))
    result = dict(kind='opt-in-successor-calibration-result', completed_at=utc(),
                  passed=all(x['correct'] for x in controls+results), readers=results, judge_controls=controls,
                  artifacts=base._tree_identity(folder))
    write_new(REPORTS / f'{args.name}-result.json', result)
    print(json.dumps(result, default=str))


def register(args):
    cases = base._load_dataset(DATASET)
    previous = json.loads((REPORTS / 'opt-in-interactions-registration.json').read_text())
    calibration = REPORTS / f'{args.calibration}-result.json'
    if not json.loads(calibration.read_text())['passed']:
        raise old.ResearchFailure('Calibration failed')
    defaults = deepcopy(previous['arms'][0]['config'])
    matrix = [a for a in arms(defaults) if a.get('config') and not a.get('alias_of')]
    for arm in matrix:
        arm['stratum'] = 'fresh' if any(arm['ingestion_flags'].values()) else 'historical'
        arm['fresh_ingestion_required'] = arm['stratum'] == 'fresh'
        arm['control'] = 'fresh_baseline' if arm['stratum'] == 'fresh' else 'baseline'
    fresh = deepcopy(matrix[0])
    fresh.update(id='fresh_baseline', stratum='fresh', fresh_ingestion_required=True, control='fresh_baseline')
    matrix.append(fresh)
    projection = deepcopy(matrix[0])
    projection.update(id='evidence_projection', overrides={'packing.evidence_projection_top_k': 50})
    projection['config'] = configure(defaults, projection['overrides'])
    projection['config_sha256'] = sha(projection['config'])
    matrix.append(projection)
    order = ['baseline','episode_routing','evidence_augmentation','evidence_projection',
             'episode_augmentation','episode_projection','reranker','query_reformulation',
             'reranker_reformulation','temporal_relations','temporal_episode',
             'fresh_baseline','store_supersedence','qa_pairing','surprise_gating','full_feature_exploratory']
    matrix.sort(key=lambda a: order.index(a['id']))
    sources = set(previous['source_sha256']) | {
        str(Path(__file__).relative_to(ROOT)), 'tests/test_opt_in_successor.py',
        'benchmarks/diagnostics/analyze_opt_in_successor.py',
        'benchmarks/results/research/2026-09-22/OPT-IN-SUCCESSOR-PROTOCOL.md',
        str(calibration.relative_to(ROOT)),
    }
    result = dict(schema_version=2, kind='prme-opt-in-successor-registration', registered_at=utc(),
        production_revision=previous['production_revision'], research_revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        predecessor_registration_sha256=previous['registration_sha256'], predecessor_status='failed_closed_retained',
        source_sha256={name:file_sha(ROOT/name) for name in sorted(sources)},
        production_python_sources_sha256=previous['production_python_sources_sha256'],
        local_validation_environment={'packages': old.package_identity()},
        longmemeval_s={'ordered_question_ids':[c['question_id'] for c in cases],
                      'dataset_sha256':file_sha(DATASET), 'classification':'previously examined development only'},
        reader_limit=READER_LIMIT, judge_limit=JUDGE_LIMIT, model=old.MODEL, digest=old.DIGEST,
        arms=matrix, aliases={'supersedence_balanced':'store_supersedence','qa_balanced':'qa_pairing','surprise_balanced':'surprise_gating'},
        failure_unit='Complete arm; terminal failures retained, other scheduled arms continue. No partial arm scores.',
        best_combination='Select at most two eligible individuals only after all eight have valid complete comparisons. Freeze new combination before running it.',
        untouched_confirmation='Unavailable in these 500 histories; requires independently audited unused source families before promotion.',
        promotion_authorized=False)
    result['registration_sha256'] = sha(result)
    write_new(REPORTS / f'{args.name}-registration.json', result)
    print(json.dumps({'registration_sha256':result['registration_sha256'], 'arms':len(matrix)}))


def verify_capture(folder, case, official, limits=(READER_LIMIT,JUDGE_LIMIT)):
    row = json.loads((folder/'result.json').read_text())
    if row['status'] != 'complete':
        raise old.ResearchFailure('Incomplete artifact')
    capture = json.loads((folder/'capture.json').read_text())
    if base._tree_identity(folder/'pack') != capture['final_pack']:
        raise old.ResearchFailure('Pack identity differs')
    with duckdb.connect(str(folder/'pack/memory.duckdb'),read_only=True) as db:
        for retrieval in capture['retrievals']:
            request_id = retrieval['metadata']['request_id']
            data = db.execute("SELECT payload FROM operations WHERE op_type='RETRIEVAL_REQUEST' AND target_id=? AND actor_id=?",
                              [request_id,base.USER_ID]).fetchall()
            if len(data) != 1:
                raise old.ResearchFailure('Missing durable receipt')
            receipt = RelevanceRepository._read_receipt(data[0][0],request_id=request_id,user_id=base.USER_ID)
            if (receipt.model_dump(mode='json') != retrieval['receipt'] or
                hashlib.sha256(retrieval['context'].encode()).hexdigest() != receipt.context_sha256 or
                count_tokens(retrieval['context'],'cl100k_base') != retrieval['context_tokens'] or
                tuple(str(x) for x in receipt.replay_ranking()) != tuple(r['node_id'] for r in retrieval['returned'])):
                raise old.ResearchFailure('Receipt/context replay differs')
    reader = json.loads((folder/'reader.json').read_text())
    prompts = [base._reader_prompt(capture['retrievals'][0]['context'],case['question_date'],case['question']),
               official(case['question_type'],case['question'],case['answer'],reader['response']['message']['content'].strip(),
                        abstention=case['question_id'].endswith('_abs'))]
    for role,limit,prompt in zip(('reader','judge'),limits,prompts):
        value = json.loads((folder/f'{role}.json').read_text())
        request = value['request']
        if (value['request_sha256'] != sha(request) or value['status'] != 'complete' or
            request != {'model':old.MODEL,'messages':[{'role':'user','content':prompt}],
                        'options':{'temperature':0,'seed':42,'num_ctx':65536,'num_predict':limit},'think':False,'stream':False} or
            value['response'].get('done_reason') != 'stop' or file_sha(folder/f'{role}.json') != row[f'{role}_sha256']):
            raise old.ResearchFailure('Provider artifact mismatch')
        if role == 'judge' and row['correct'] != ('yes' in value['response']['message']['content'].lower()):
            raise old.ResearchFailure('Verdict differs')
    if file_sha(folder/'capture.json') != row['capture_sha256']:
        raise old.ResearchFailure('Capture hash differs')
    return capture


async def run(args):
    reg = json.loads((REPORTS/f'{args.name}-registration.json').read_text())
    cases = base._load_dataset(DATASET)
    old.validate_registration(reg,ROOT,cases)
    if file_sha(DATASET) != reg['longmemeval_s']['dataset_sha256']:
        raise old.ResearchFailure('Dataset differs')
    base._official_identity(OFFICIAL)
    assets = json.loads((REPORTS/'opt-in-model-assets.json').read_text())
    for asset in assets['assets']:
        if base._tree_identity(Path(asset['root'])) != asset['tree']:
            raise old.ResearchFailure('Model assets differ')
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    output = PRIVATE/args.name
    output.mkdir(parents=True,exist_ok=True)
    selected = [a for a in reg['arms'] if a['id'] == args.arm]
    amendment = json.loads((REPORTS / 'opt-in-successor-scheduling-amendment-v2.json').read_text())
    if file_sha(Path(__file__)) != amendment['worker_sha256'] or len(selected) != 1:
        raise old.ResearchFailure('Arm worker identity or selection differs')
    key = os.environ.get('JEV_API_KEY') or dotenv_values(ORIGINAL/'.env').get('JEV_API_KEY')
    official = base._load_official_prompt_function(OFFICIAL)
    handler = old.FailureObserver()
    logging.getLogger('prme').addHandler(handler)
    logging.getLogger('prme').setLevel(logging.DEBUG)
    async with httpx.AsyncClient(base_url='http://127.0.0.1:11434',timeout=300) as client:
        for arm in selected:
            folder = output/arm['id']
            folder.mkdir(exist_ok=False)
            write_new(folder/'started.json',dict(registration_sha256=reg['registration_sha256'],arm=arm['id'],started_at=utc()))
            print(json.dumps(dict(event='arm_started',arm=arm['id'],at=utc())),flush=True)
            stop = asyncio.Event()
            sem, provider = asyncio.Semaphore(5), asyncio.Semaphore(4)
            # A single factory installation spans every concurrent store task.
            # fresh_ingest uses context-local turn identities.
            async def one(case):
                async with sem:
                    if stop.is_set():
                        return dict(question_id=case['question_id'],status='not_started')
                    destination = folder/case['question_id']
                    destination.mkdir()
                    write_new(destination/'started.json',dict(question_id=case['question_id'],arm=arm['id'],at=utc()))
                    errors=[]
                    token=old.ERRORS.set(errors)
                    trace={}
                    trace_token=FEATURE_TRACE.set(trace)
                    row=dict(question_id=case['question_id'],question_type=case['question_type'],status='failed')
                    try:
                        captured=await capture(case,arm,destination,key)
                        if errors:
                            raise old.ResearchFailure('Feature failure/fallback logged')
                        answer,_=await old.chat(client,provider,base._reader_prompt(captured['retrievals'][0]['context'],case['question_date'],case['question']),reg['reader_limit'],destination/'reader.json')
                        verdict,_=await old.chat(client,provider,official(case['question_type'],case['question'],case['answer'],answer,abstention=case['question_id'].endswith('_abs')),reg['judge_limit'],destination/'judge.json')
                        row.update(status='complete',correct='yes' in verdict.lower(),
                            **{f'{part}_sha256':file_sha(destination/f'{part}.json') for part in ('capture','reader','judge')})
                    except Exception as exc:
                        stop.set()
                        row.update(exception_type=type(exc).__name__,errors=errors,
                            failure_code=str(exc) if isinstance(exc,old.ResearchFailure) else type(exc).__name__,
                            trace_frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(exc.__traceback__)])
                    finally:
                        old.ERRORS.reset(token)
                        FEATURE_TRACE.reset(trace_token)
                    write_new(destination/'feature-observations.json',trace)
                    row['feature_observations_sha256']=file_sha(destination/'feature-observations.json')
                    write_new(destination/'result.json',row)
                    print(json.dumps(dict(event='case_finished',arm=arm['id'],question_id=case['question_id'],status=row['status'])),flush=True)
                    return row
            with matched_admission(), observed_features():
                rows=await asyncio.gather(*(one(c) for c in cases))
            complete=all(r['status']=='complete' for r in rows)
            state=dict(arm=arm['id'],registration_sha256=reg['registration_sha256'],complete=complete,
                       status='complete' if complete else 'failed_closed',rows=rows,finished_at=utc())
            write_new(folder/'execution.json',state)
            if complete:
                old.validate_complete(reg['longmemeval_s']['ordered_question_ids'],rows)
                verified=[]
                for case in cases:
                    verified.append(await asyncio.to_thread(verify_capture,folder/case['question_id'],case,official))
                write_new(folder/'verification.json',dict(complete=True,cases=len(verified),receipts=2*len(verified),
                    pack_manifest_sha256=sha([x['final_pack']['tree_sha256'] for x in verified]),
                    execution_sha256=file_sha(folder/'execution.json'),verified_at=utc()))
            print(json.dumps(dict(event='arm_finished',arm=arm['id'],status=state['status'],at=utc())),flush=True)
    logging.getLogger('prme').removeHandler(handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['calibrate','register','run'])
    parser.add_argument('--name',required=True)
    parser.add_argument('--arm',required=True)
    parser.add_argument('--calibration',default='opt-in-successor-calibration-v1')
    parser.add_argument('--stratum',choices=['historical','fresh','all'],default='all')
    args=parser.parse_args()
    if args.command=='register': register(args)
    else: asyncio.run(calibrate(args) if args.command=='calibrate' else run(args))


if __name__=='__main__':
    main()
