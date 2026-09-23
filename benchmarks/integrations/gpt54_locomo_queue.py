"""Run the already registered LoCoMo arm after fixed prerequisites complete."""
import json
from pathlib import Path
import subprocess
import sys
import time

from benchmarks.integrations import run_gpt54_comparison as study
from benchmarks.integrations.gpt54_budget import digest, write_new


def main():
    plan_path = study.PUBLIC/'gpt54-locomo-queue-plan.json'
    plan = json.loads(plan_path.read_text())
    if digest(__file__) != plan['queue_sha256'] or digest(study.REG) != plan['registration_sha256']:
        raise ValueError('Queue registration changed')
    prepared = study.PRIVATE/'locomo/prepared.json'
    lme_result = study.PUBLIC/'gpt54-longmemeval-v1-result.json'
    print('Waiting for complete LongMemEval answers and all 1540 LoCoMo captures',flush=True)
    while not (prepared.exists() and lme_result.exists()):
        time.sleep(15)
    value = json.loads(lme_result.read_text())
    if not value['complete'] or value['completed'] != 500:
        raise ValueError('LongMemEval failed; independent LoCoMo requires an explicit recorded handoff')
    value = json.loads(prepared.read_text())
    if not value['complete'] or value['questions'] != 1540:
        raise ValueError('LoCoMo contexts incomplete')
    if (study.PRIVATE/'locomo/execution').exists():
        raise ValueError('LoCoMo already has an execution owner')
    if digest(Path(plan['loader'])) != plan['loader_sha256']:
        raise ValueError('Amended loader changed')
    write_new(study.PUBLIC/'gpt54-locomo-queue-handoff.json', {
        'started_at':study.utc(),'queue_plan_sha256':digest(plan_path),
        'prepared_sha256':digest(prepared),'longmemeval_result_sha256':digest(lme_result),
        'command':plan['command'],
    })
    print('Starting full registered LoCoMo answer/judge arm',flush=True)
    completed = subprocess.run(plan['command'],cwd=study.ROOT)
    write_new(study.PUBLIC/'gpt54-locomo-queue-exit.json',
              {'finished_at':study.utc(),'returncode':completed.returncode})
    sys.exit(completed.returncode)


if __name__ == '__main__':
    main()
