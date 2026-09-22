"""Run frozen repair answers in a released historical provider lane."""
import argparse
import fcntl
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def lane_available(plan, kind, digest, process_alive=alive):
    if process_alive(plan['released_lane_pid']):
        return False
    root = Path(plan['matrix_root'])
    for arm in plan['released_lane_arms']:
        folder = root / arm
        execution_path = folder / 'execution.json'
        if not execution_path.exists():
            return False
        execution = json.loads(execution_path.read_text())
        if execution['complete']:
            verification_path = folder / 'verification.json'
            if not verification_path.exists():
                return False
            verification = json.loads(verification_path.read_text())
            if (not verification['complete'] or verification['cases'] != 500
                    or verification['execution_sha256'] != digest(execution_path)):
                raise RuntimeError('Historical lane verification identity differs')
    if kind == 'marginal':
        predecessor = Path(plan['public_root']) / 'rank-answer-lane-v1-exit.json'
        if not predecessor.exists():
            return False
        state = json.loads(predecessor.read_text())
        if not state['execution_tail_settled'] or process_alive(state['coordinator_pid']):
            return False
    return True


def check_handoff(handoff, plan_path, identity, kind, digest, process_alive=alive):
    if (handoff['lane_plan_sha256'] != digest(plan_path)
            or handoff['kind'] != kind or handoff['prepared_identity'] != identity
            or not handoff['idle_process_stopped']):
        raise RuntimeError('Idle answer ownership handoff differs')
    if process_alive(handoff['stopped_pid']):
        raise RuntimeError('Previous owner remains alive')
    if not handoff['zero_reader_cases_started']:
        raise RuntimeError('A reader run cannot be transferred')


def write_new(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', required=True, choices=['rank', 'marginal'])
    parser.add_argument('--handoff', required=True, type=Path)
    args = parser.parse_args()
    args.handoff = args.handoff.resolve()
    root = Path(__file__).resolve().parents[2]
    public = root / 'benchmarks/results/research/2026-09-22'
    helper_path = root / 'benchmarks/diagnostics/opt_in_answer_memory_handoff.py'
    spec = importlib.util.spec_from_file_location('_frozen_memory_handoff', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    plan_path = public / 'opt-in-answer-lane-scheduling-v1.json'
    plan = json.loads(plan_path.read_text())
    if (helper.digest(__file__) != plan['coordinator_sha256']
            or helper.digest(helper_path) != plan['memory_helper_sha256']):
        raise RuntimeError('Frozen coordinator implementation changed')
    old_plan_path = public / 'opt-in-answer-memory-amendment-v1.json'
    if helper.digest(old_plan_path) != plan['memory_plan_sha256']:
        raise RuntimeError('Original memory handoff plan changed')
    target = json.loads(old_plan_path.read_text())['studies'][args.kind]
    target_root = Path(target['root'])
    sys.path[:0] = [str(target_root), str(target_root / 'src')]
    module = importlib.import_module(target['module'])
    if (helper.digest(module.__file__) != target['source_sha256']
            or helper.tail_digest(module.__file__) != target['tail_ast_sha256']):
        raise RuntimeError('Original registered answer implementation changed')
    reg, names, selected, identity = helper.validate_prepared(module)
    handoff = json.loads(args.handoff.read_text())
    check_handoff(handoff, plan_path, identity, args.kind, helper.digest)
    print(json.dumps({'status': 'waiting_for_released_historical_lane',
                      'kind': args.kind, 'prepared_identity': identity}), flush=True)
    while not lane_available(plan, args.kind, helper.digest):
        time.sleep(30)
    # An OS-held lock also excludes simultaneous coordinators after any crash.
    with (Path(plan['private_root']) / 'answer-lane-v1.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not lane_available(plan, args.kind, helper.digest):
            raise RuntimeError('Provider lane ownership changed')
        reg, names, selected, repeated = helper.validate_prepared(module)
        if identity != repeated:
            raise RuntimeError('Frozen answer contexts changed during wait')
        os.chdir(target_root)
        os.environ['PYTHONPATH'] = 'src'
        write_new(public / f'{args.kind}-answer-lane-v1-start.json', {
            'at': datetime.now(timezone.utc).isoformat(), 'coordinator_pid': os.getpid(),
            'plan_sha256': helper.digest(plan_path), 'handoff_sha256': helper.digest(args.handoff),
            'prepared_identity': identity, 'provider_slots': 4,
            'all_original_historical_arms_required': False,
            'original_answer_execution_ast_sha256': target['tail_ast_sha256']})
        failure = None
        try:
            helper.execute_tail(module, reg, names, selected)
        except BaseException as exc:
            failure = type(exc).__name__
            raise
        finally:
            write_new(public / f'{args.kind}-answer-lane-v1-exit.json', {
                'at': datetime.now(timezone.utc).isoformat(), 'coordinator_pid': os.getpid(),
                'execution_tail_settled': True, 'exception_type': failure,
                'quality_complete': None,
                'note': 'Operational exit only; authenticate the original complete child results for quality. No failed run is retried or replaced.'})


if __name__ == '__main__':
    main()
