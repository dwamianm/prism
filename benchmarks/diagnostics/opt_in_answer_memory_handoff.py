"""Release completed source-assay memory before its unchanged answer-stage wait.

The original source process must be stopped while idle, after publishing every
source result and both reader registrations, before this coordinator can run.
It executes the exact registered answer function's execution/analysis tail.
It never prepares, selects, rewrites or replaces benchmark contexts or answers.
"""
import argparse
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def tail_nodes(path):
    functions = [n for n in ast.parse(Path(path).read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name == 'answer_followup']
    if len(functions) != 1:
        raise ValueError('Expected one registered answer function')
    body = functions[0].body
    positions = [i for i, n in enumerate(body) if isinstance(n, ast.For)
                 and isinstance(n.iter, ast.Call) and isinstance(n.iter.func, ast.Attribute)
                 and isinstance(n.iter.func.value, ast.Name) and n.iter.func.value.id == 'names'
                 and n.iter.func.attr == 'values' and not n.iter.args and not n.iter.keywords]
    if len(positions) != 1:
        raise ValueError('Expected one registered child execution loop')
    return body[positions[0]:]


def tail_digest(path):
    return hashlib.sha256(ast.dump(ast.Module(body=tail_nodes(path), type_ignores=[]),
                                  include_attributes=False).encode()).hexdigest()


def execute_tail(module, reg, names, selected):
    function = ast.FunctionDef(name='_registered_answer_tail',
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=n) for n in ('reg', 'names', 'selected')],
                           kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=tail_nodes(module.__file__), decorator_list=[])
    tree = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = dict(vars(module))
    exec(compile(tree, module.__file__, 'exec'), namespace)
    return namespace['_registered_answer_tail'](reg, names, selected)


def validate_prepared(module):
    study = module.study
    reg_path = study.REPORTS / f'{module.NAME}-registration.json'
    result_path = study.REPORTS / f'{module.NAME}-source-result.json'
    reg, result = [json.loads(p.read_text()) for p in (reg_path, result_path)]
    if reg['registration_sha256'] != module.sha({k: v for k, v in reg.items() if k != 'registration_sha256'}):
        raise RuntimeError('Source registration changed')
    if (not result['complete'] or result['registration_sha256'] != reg['registration_sha256']
            or not (result.get('advance_to_answers') or result.get('selected_policy'))):
        raise RuntimeError('Complete source result did not authorize the answer stage')
    source_root = study.PRIVATE / module.NAME
    execution = json.loads((source_root / 'execution.json').read_text())
    if not execution['complete'] or digest(source_root / 'execution.json') != result['execution_sha256']:
        raise RuntimeError('Source execution changed')
    expected = reg['ordered_question_ids']
    study.old.validate_complete(expected, execution['rows'])
    if len(expected) != 500:
        raise RuntimeError('Full 500-case cohort is required')
    names = {kind: f'{module.NAME}-reader-{kind}' for kind in ('control', 'candidate')}
    prepared, identity = {}, {'source_result_sha256': digest(result_path), 'source_registration_sha256': digest(reg_path)}
    for kind, name in names.items():
        folder = study.PRIVATE / name
        if {p.name for p in folder.iterdir()} != {'contexts.json'}:
            raise RuntimeError('A reader stage has already started; handoff refused')
        if (study.PRIVATE / f'{name}.log').exists() or (study.REPORTS / f'{name}-result.json').exists():
            raise RuntimeError('A reader execution artifact already exists')
        child_path = study.REPORTS / f'{name}-registration.json'
        child = json.loads(child_path.read_text())
        if (child['registration_sha256'] != module.sha({k: v for k, v in child.items() if k != 'registration_sha256'})
                or child['source_assay_registration_sha256'] != reg['registration_sha256']
                or child['ordered_question_ids'] != expected or child['role'] != kind
                or digest(folder / 'contexts.json') != child['prepared_sha256']):
            raise RuntimeError('Prepared child identity differs')
        prepared[kind] = json.loads((folder / 'contexts.json').read_text())
        if [r['question_id'] for r in prepared[kind]] != expected:
            raise RuntimeError('Prepared question order differs')
        identity[kind + '_registration_sha256'] = digest(child_path)
        identity[kind + '_contexts_sha256'] = digest(folder / 'contexts.json')
    for index, outcome in enumerate(execution['rows']):
        path = source_root / 'cases' / f"{outcome['question_id']}.json"
        if digest(path) != outcome['case_sha256']:
            raise RuntimeError('A source case changed')
        case = json.loads(path.read_text())
        if 'inputs_sha256' in case and digest(source_root / 'inputs' / path.name) != case['inputs_sha256']:
            raise RuntimeError('Source candidate snapshot changed')
        control_key = 'baseline' if 'baseline' in case['arms'] else 'control'
        for kind, key in (('control', control_key), ('candidate', result.get('selected_policy') or 'rank_envelope')):
            context = case['arms'][key]['context']
            row = prepared[kind][index]
            if row['context'] != context or hashlib.sha256(context.encode()).hexdigest() != row['context_sha256']:
                raise RuntimeError('Prepared context differs from its complete source case')
    return reg, names, result.get('selected_policy'), identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['verify', 'run'])
    parser.add_argument('--kind', required=True, choices=['rank', 'marginal'])
    parser.add_argument('--handoff', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    plan_path = root / 'benchmarks/results/research/2026-09-22/opt-in-answer-memory-amendment-v1.json'
    plan = json.loads(plan_path.read_text())
    if digest(__file__) != plan['helper_sha256']:
        raise RuntimeError('Memory coordinator source changed')
    target = plan['studies'][args.kind]
    target_root = Path(target['root'])
    sys.path[:0] = [str(target_root), str(target_root / 'src')]
    module = importlib.import_module(target['module'])
    if digest(module.__file__) != target['source_sha256'] or tail_digest(module.__file__) != target['tail_ast_sha256']:
        raise RuntimeError('Registered answer implementation changed')
    reg, names, selected, identity = validate_prepared(module)
    if args.action == 'verify':
        print(json.dumps(identity), flush=True)
        return
    if args.handoff is None:
        raise RuntimeError('An authenticated idle-source-process handoff is required')
    handoff = json.loads(args.handoff.read_text())
    if (handoff['plan_sha256'] != digest(plan_path) or handoff['kind'] != args.kind
            or handoff['prepared_identity'] != identity or not handoff['idle_source_process_stopped']):
        raise RuntimeError('Idle handoff identity differs')
    try:
        os.kill(handoff['stopped_pid'], 0)
    except ProcessLookupError:
        pass
    else:
        raise RuntimeError('Old source process is still present; refusing duplicate ownership')
    os.chdir(target_root)
    os.environ['PYTHONPATH'] = 'src'
    parent = json.loads((module.study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    print(json.dumps({'status': 'waiting_for_original_historical_gate', 'kind': args.kind,
                      'prepared_identity': identity}), flush=True)
    while not all(module.settled(module.study.PRIVATE / 'opt-in-successor-v2' / a['id'])
                  for a in parent['arms'] if a['stratum'] == 'historical'):
        time.sleep(30)
    # Reauthenticate after waiting; no live source snapshots were retained.
    reg, names, selected, repeated = validate_prepared(module)
    if identity != repeated:
        raise RuntimeError('Prepared stage changed while waiting')
    execute_tail(module, reg, names, selected)


if __name__ == '__main__':
    main()
