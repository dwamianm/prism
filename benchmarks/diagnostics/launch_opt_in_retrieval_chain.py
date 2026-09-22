"""Run the preregistered second retrieval lane without replacing any arm."""
from pathlib import Path
import json
import os
import subprocess
import sys

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha


def main():
    plan = json.loads((study.REPORTS / 'opt-in-successor-scheduling-amendment-v3.json').read_text())
    if file_sha(Path(__file__)) != plan['launcher_sha256']:
        raise RuntimeError('Launcher identity differs')
    oracle = json.loads((study.REPORTS / 'opt-in-packing-oracle-v1-result.json').read_text())
    if not oracle['complete']:
        raise RuntimeError('Diagnostic provider lane has not completed')
    env = dict(os.environ, PYTHONPATH='src')
    for arm in plan['second_retrieval_lane']:
        destination = study.PRIVATE / 'opt-in-successor-v2' / arm
        if destination.exists():
            raise RuntimeError('Arm already owned; refusing duplicate execution')
        with (study.PRIVATE / f'successor-v2-{arm}.log').open('xb') as log:
            result = subprocess.run([sys.executable, '-m', 'benchmarks.diagnostics.opt_in_arm_worker',
                'run', '--name', 'opt-in-successor-v2', '--arm', arm], cwd=study.ROOT,
                env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'Worker exited before normal finalization: {arm}')
        state = json.loads((destination / 'execution.json').read_text())
        if state['complete'] and not (destination / 'verification.json').exists():
            raise RuntimeError('Complete worker lacks authentication')
        print(json.dumps({'arm': arm, 'status': state['status'], 'finished_at': study.utc()}), flush=True)


if __name__ == '__main__':
    main()
