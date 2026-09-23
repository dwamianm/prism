"""Partition untouched LoCoMo conversations without changing product execution."""
import argparse
import ast
import asyncio
from copy import deepcopy
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

from benchmarks.integrations import run_gpt54_comparison as study
from benchmarks.integrations.gpt54_budget import digest, sha, write_new

PLAN = study.PUBLIC/'gpt54-locomo-source-scheduling.json'


class SourceView:
    def __init__(self, records):
        self.records = records

    def read_text(self):
        return json.dumps(self.records)


class CompletionCount(ast.NodeTransformer):
    def __init__(self, count):
        self.count = count
        self.changes = 0

    def visit_Dict(self, node):
        self.generic_visit(node)
        for i, key in enumerate(node.keys):
            value = node.values[i]
            if isinstance(key, ast.Constant) and key.value == 'questions':
                if isinstance(value, ast.Constant) and value.value == 1540:
                    node.values[i] = ast.copy_location(ast.Constant(self.count),value)
                    self.changes += 1
        return node


def child_function(reg, records, private):
    """The exact source function, with only its final cohort-count literal scoped."""
    tree = ast.parse(inspect.getsource(study.prepare_locomo))
    before = sha(ast.dump(tree))
    transform = CompletionCount(len(reg['cohort_ids']['locomo']))
    tree = transform.visit(tree)
    if transform.changes != 1:
        raise ValueError('Unexpected source executor structure')
    ast.fix_missing_locations(tree)
    namespace = dict(study.prepare_locomo.__globals__)
    namespace.update(PRIVATE=private, LOCOMO=SourceView(records), validate=lambda: reg)
    exec(compile(tree,str(study.ROOT/'benchmarks/integrations/run_gpt54_comparison.py'),'exec'),namespace)
    return namespace['prepare_locomo'], {'original_ast_sha256':before,'child_ast_sha256':sha(ast.dump(tree)),
                                       'only_ast_change':'final questions count 1540 to child cohort size'}


def validate_plan():
    plan = json.loads(PLAN.read_text())
    if digest(__file__) != plan['coordinator_sha256'] or digest(study.REG) != plan['registration_sha256']:
        raise ValueError('Scheduling plan changed')
    study.validate()
    return plan


async def worker(cid):
    plan = validate_plan()
    if cid not in plan['child_conversations']:
        raise ValueError('Conversation not assigned to child')
    reg = deepcopy(study.validate())
    records = [c for c in json.loads(study.LOCOMO.read_text()) if c['sample_id']==cid]
    if len(records) != 1:
        raise ValueError('Conversation identity differs')
    ids = [q['question_id'] for q in study.question_rows('locomo') if q['conversation_id']==cid]
    reg['cohort_ids']['locomo'] = ids
    private = study.PRIVATE/'source-children'/cid
    execute, ast_identity = child_function(reg,records,private)
    write_new(private/'claim.json',{'conversation_id':cid,'question_ids':ids,'plan_sha256':digest(PLAN),
                                  'created_at':study.utc(),**ast_identity})
    await execute()
    result = json.loads((private/'locomo/prepared.json').read_text())
    if result['questions'] != len(ids) or [x['question_id'] for x in result['contexts']] != ids:
        raise ValueError('Child source coverage differs')


def alive(pid):
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False


async def coordinate():
    plan = validate_plan()
    parent = study.PRIVATE/'locomo'
    # Own the next untouched conversation before the sequential coordinator
    # reaches it. Its mkdir(exist_ok=False) gives a safe pre-ingestion handoff.
    boundary = parent/'packs'/plan['child_conversations'][0]
    boundary.mkdir()
    write_new(study.PUBLIC/'gpt54-locomo-source-ownership.json',{
        'created_at':study.utc(),'plan_sha256':digest(PLAN),'boundary':str(boundary),
        'meaning':'Expected pre-ingestion FileExistsError after original conversation completes; no source case interrupted.'})
    sem = asyncio.Semaphore(plan['child_workers'])
    async def one(cid):
        async with sem:
            log = study.PRIVATE/f'locomo-source-{cid}.log'
            with log.open('x') as handle:
                process = await asyncio.create_subprocess_exec(sys.executable,'-m',
                    'benchmarks.integrations.gpt54_locomo_sources','worker','--conversation',cid,
                    cwd=study.ROOT,stdout=handle,stderr=subprocess.STDOUT)
                code = await process.wait()
            write_new(study.PUBLIC/f'gpt54-locomo-source-{cid}-exit.json',
                      {'conversation':cid,'returncode':code,'finished_at':study.utc(),'log_sha256':digest(log)})
            return code
    results = await asyncio.gather(*(one(cid) for cid in plan['child_conversations']))
    if any(results):
        raise RuntimeError('A registered source child failed; no replacement')
    while alive(plan['parent_pid']):
        await asyncio.sleep(15)
    parent_log = study.PRIVATE/'locomo-prepare.log'
    if 'FileExistsError' not in parent_log.read_text() or str(boundary) not in parent_log.read_text():
        raise ValueError('Original coordinator did not stop at registered ownership boundary')
    first = plan['parent_conversation']
    first_pack = parent/'packs'/first/'capture-manifest.json'
    packs = [json.loads(first_pack.read_text())]
    manifest = {}
    expected = study.question_rows('locomo')
    for q in (q for q in expected if q['conversation_id']==first):
        dest = parent/'contexts'/(q['question_id']+'.json')
        manifest[q['question_id']] = {'question_id':q['question_id'],'sha256':digest(dest)}
    for cid in plan['child_conversations']:
        child = study.PRIVATE/'source-children'/cid/'locomo'
        prepared = json.loads((child/'prepared.json').read_text())
        packs.extend(prepared['packs'])
        for entry in prepared['contexts']:
            source = child/'contexts'/(entry['question_id']+'.json')
            if entry['question_id'] in manifest or digest(source) != entry['sha256']:
                raise ValueError('Child context changed or duplicate ownership')
            dest = parent/'contexts'/source.name
            with dest.open('xb') as handle:
                handle.write(source.read_bytes())
            if digest(dest) != entry['sha256']:
                raise ValueError('Context copy changed')
            manifest[entry['question_id']] = entry
    if set(manifest) != {q['question_id'] for q in expected} or len(packs) != 10:
        raise ValueError('Full LoCoMo source coverage differs')
    write_new(parent/'prepared.json',{'complete':True,'questions':1540,'registration_sha256':digest(study.REG),
        'contexts':[manifest[q['question_id']] for q in expected], 'packs':packs,
        'scheduling_amendment_sha256':digest(PLAN),'parent_log_sha256':digest(parent_log)})
    write_new(study.PUBLIC/'gpt54-locomo-source-scheduling-result.json',{
        'complete':True,'conversations':10,'questions':1540,'prepared_sha256':digest(parent/'prepared.json'),
        'original_coordinator_exit':'expected pre-ingestion ownership handoff; no failed benchmark case',
        'finished_at':study.utc()})
    print('All 10 conversations and 1540 contexts authenticated',flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['coordinate','worker'])
    p.add_argument('--conversation')
    args = p.parse_args()
    asyncio.run(coordinate() if args.command=='coordinate' else worker(args.conversation))


if __name__ == '__main__':
    main()
