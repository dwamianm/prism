"""Launch registered MAB tasks after LongMemEval and combination finalization."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.opt_in_select_combination import NAME as COMBINATION, settled
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    plan = json.loads((study.REPORTS / 'opt-in-mab-launch-plan.json').read_text())
    if file_sha(Path(__file__)) != plan['launcher_sha256']:
        raise RuntimeError('MAB launcher differs from registration')
    lme = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    root = study.PRIVATE / 'opt-in-successor-v2'
    result_path = study.REPORTS / f'{COMBINATION}-result.json'
    while not all(settled(root / a['id']) for a in lme['arms']) or not result_path.exists():
        time.sleep(30)
    combination = json.loads(result_path.read_text())
    name = 'opt-in-mab-matrix-v1'
    reg = json.loads((study.REPORTS / f'{name}-registration.json').read_text())
    child_path = study.REPORTS / f'{COMBINATION}-registration.json'
    if child_path.exists():
        selected = json.loads(child_path.read_text())['arms'][0]
        # Selection was made exclusively on LongMemEval. A failed LME combination
        # remains failed; independent MAB evaluation does not replace it.
        if not any(a['config'] == selected['config'] for a in reg['arms']):
            parent_sha = reg.pop('registration_sha256')
            reg.update(predecessor_registration_sha256=parent_sha, registered_at=study.utc(),
                       combination_selection_sha256=file_sha(child_path),
                       combination_prior_status=combination['status'])
            reg['arms'].append(deepcopy(selected))
            reg['source_sha256'][str(Path(__file__).relative_to(study.ROOT))] = file_sha(Path(__file__))
            reg['source_sha256'][str((study.REPORTS / 'opt-in-mab-launch-plan.json').relative_to(study.ROOT))] = file_sha(study.REPORTS / 'opt-in-mab-launch-plan.json')
            reg['registration_sha256'] = sha(reg)
            name = 'opt-in-mab-matrix-with-combination-v1'
            write_new(study.REPORTS / f'{name}-registration.json', reg)
    write_new(study.REPORTS / 'opt-in-mab-stage-launch.json', {'started_at': study.utc(), 'name': name,
        'registration_sha256': reg['registration_sha256'], 'combination_status': combination['status'],
        'combination_alias': combination.get('alias_of'), 'default_changes': False})
    env = dict(os.environ, PYTHONPATH='data/opt-in-study/mab-preprocessing:src')
    with (study.PRIVATE / f'{name}.log').open('xb') as log:
        execution = subprocess.run([sys.executable, '-m', 'benchmarks.diagnostics.opt_in_mab_matrix',
            'run', '--name', name], cwd=study.ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    write_new(study.REPORTS / 'opt-in-mab-stage-exit.json', {'finished_at': study.utc(), 'name': name,
        'exit_code': execution.returncode, 'complete_execution': execution.returncode == 0,
        'all_arms_successful': None, 'note': 'Every task/arm execution and verification must be checked; exit 0 permits retained failed-closed arms.'})


if __name__ == '__main__':
    main()
