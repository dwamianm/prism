"""Load the exact pinned official prompt function without unrelated CLI imports.

Prospective execution amendment after an authored calibration import failure.
The benchmark protocol, prompt function AST, source capture and score rule stay
unchanged. No benchmark reader case had started at amendment registration.
"""
import argparse
import ast
import asyncio
import json
from pathlib import Path

from benchmarks.integrations import run_gpt54_comparison as study
from benchmarks.integrations.gpt54_budget import digest, write_new


def load_prompt(root):
    path = Path(root) / 'src/evaluation/evaluate_qa.py'
    tree = ast.parse(path.read_text())
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == 'get_anscheck_prompt']
    if len(matches) != 1 or matches[0].decorator_list:
        raise ValueError('Unexpected official prompt function')
    module = ast.Module(body=matches, type_ignores=[])
    namespace = {}
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace['get_anscheck_prompt']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['calibrate', 'longmemeval', 'locomo'])
    args = parser.parse_args()
    amend = json.loads((study.PUBLIC/'gpt54-official-loader-amendment.json').read_text())
    if digest(__file__) != amend['loader_sha256'] or digest(study.REG) != amend['registration_sha256']:
        raise ValueError('Loader amendment changed')
    official = study.OFFICIAL/'src/evaluation/evaluate_qa.py'
    if digest(official) != amend['official_source_sha256']:
        raise ValueError('Official source changed')
    study.lme._load_official_prompt_function = load_prompt
    if args.command == 'calibrate':
        original = study.PRIVATE
        ledger_type = study.Ledger
        study.PRIVATE = original/'loader-calibration-v2'
        study.Ledger = lambda _path: ledger_type(original/'spending.json')
        try:
            asyncio.run(study.calibrate())
        finally:
            study.PRIVATE = original
            study.Ledger = ledger_type
        source = original/'loader-calibration-v2/authored-calibration/result.json'
        gate = json.loads(source.read_text())
        gate.update(kind='authored-calibration-gate-after-loader-amendment',
                    successful_calibration=str(source), successful_calibration_sha256=digest(source),
                    retained_failure='calibration.log and original authored-calibration reader records')
        write_new(original/'authored-calibration/result.json',gate)
    else:
        asyncio.run(study.run(args.command))


if __name__ == '__main__':
    main()
