"""Execute the prospective observed-top-two development combination plan."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import load_complete, summarize
from benchmarks.diagnostics.register_opt_in_interactions import configure, file_sha, sha, write_new


NAME = 'opt-in-successor-top-two-v1'
PLAN = 'opt-in-exploratory-combination-plan.json'


def select(plan, analysis):
    if not all(name in analysis['comparisons'] for name in plan['individuals']):
        return None
    active = [name for name in plan['individuals'] if analysis['comparisons'][name]['changed_contexts'] > 0]
    active.sort(key=lambda name: (-analysis['comparisons'][name]['difference'],
                                 analysis['arms'][name]['latency_seconds']['cold']['p95'], name))
    return active[:2]


def settled(folder):
    if not (folder / 'execution.json').exists():
        return False
    state = json.loads((folder / 'execution.json').read_text())
    return not state['complete'] or (folder / 'verification.json').exists()


def run(args):
    plan = json.loads((study.REPORTS / PLAN).read_text())
    source = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    required = set(plan['individuals']) | {'baseline', 'fresh_baseline'}
    root = study.PRIVATE / 'opt-in-successor-v2'
    while not all(settled(root / name) for name in required):
        if not args.wait:
            raise RuntimeError('Complete individual comparison coverage is not yet available')
        time.sleep(30)
    # Avoid taking the primary analyzer snapshot during another arm's verification.
    while any((root / a['id'] / 'execution.json').exists() and not settled(root / a['id']) for a in source['arms']):
        time.sleep(30)
    analysis_name = 'opt-in-successor-v2-combination-selection-analysis.json'
    subprocess.run([sys.executable, '-m', 'benchmarks.diagnostics.analyze_opt_in_successor',
        '--name', 'opt-in-successor-v2', '--output', analysis_name], check=True, cwd=study.ROOT)
    analysis = json.loads((study.REPORTS / analysis_name).read_text())
    chosen = select(plan, analysis)
    selection = {'plan_sha256': plan['plan_sha256'], 'selected_at': study.utc(),
                 'source_analysis_sha256': file_sha(study.REPORTS / analysis_name),
                 'selected_individuals': chosen, 'original_gated_selection': analysis['best_combination_selection'],
                 'classification': 'Exploratory outcome-selected development combination; no default promotion'}
    if chosen is None:
        selection.update(status='unavailable_incomplete_individual_comparisons', answer_metrics=None)
        write_new(study.REPORTS / f'{NAME}-result.json', selection)
        return
    arms = {a['id']: a for a in source['arms']}
    changes = {}
    for name in chosen:
        changes.update(arms[name]['overrides'])
    configuration = configure(arms['baseline']['config'], changes)
    same = next((a for a in source['arms'] if a['config'] == configuration), None)
    if same is not None:
        selection.update(alias_of=same['id'], configuration_sha256=same['config_sha256'])
        write_new(study.REPORTS / f'{NAME}-selection.json', selection)
        while not settled(root / same['id']):
            time.sleep(30)
        state = json.loads((root / same['id'] / 'execution.json').read_text())
        selection.update(status='alias_complete' if state['complete'] else 'alias_failed_closed',
                         alias_execution_sha256=file_sha(root / same['id'] / 'execution.json'),
                         no_additional_replication=True)
        write_new(study.REPORTS / f'{NAME}-result.json', selection)
        return
    combined = deepcopy(arms['baseline'])
    flags = {key: configuration[key] for key in arms['baseline']['ingestion_flags']}
    fresh = any(flags.values())
    combined.update(id='exploratory_top_two', overrides=changes, config=configuration,
        config_sha256=sha(configuration), ingestion_flags=flags, ingestion_group=sha(flags),
        fresh_ingestion_required=fresh, stratum='fresh' if fresh else 'historical',
        control='fresh_baseline' if fresh else 'baseline', exploratory=True)
    registration = deepcopy(source)
    registration.pop('registration_sha256')
    registration.update(kind='additional-exploratory-combination-registration', registered_at=study.utc(),
        predecessor_registration_sha256=source['registration_sha256'], arms=[combined], aliases={},
        selection=selection, control_root=str(root), source_selection_plan_sha256=plan['plan_sha256'])
    for path in (str(Path(__file__).relative_to(study.ROOT)),
                 'tests/test_opt_in_select_combination.py', str((study.REPORTS / PLAN).relative_to(study.ROOT))):
        registration['source_sha256'][path] = file_sha(study.ROOT / path)
    registration['registration_sha256'] = sha(registration)
    write_new(study.REPORTS / f'{NAME}-registration.json', registration)
    write_new(study.REPORTS / f'{NAME}-selection.json', selection)
    while not all(settled(root / a['id']) for a in source['arms'] if a['stratum'] == 'historical'):
        time.sleep(30)
    with (study.PRIVATE / f'{NAME}.log').open('xb') as log:
        completed = subprocess.run([sys.executable, '-m', 'benchmarks.diagnostics.opt_in_arm_worker',
            'run', '--name', NAME, '--arm', combined['id']], cwd=study.ROOT,
            env=dict(os.environ, PYTHONPATH='src'), stdout=log, stderr=subprocess.STDOUT)
    folder = study.PRIVATE / NAME / combined['id']
    if completed.returncode or not (folder / 'execution.json').exists():
        selection.update(status='worker_or_authentication_failed', answer_metrics=None, exit_code=completed.returncode)
        write_new(study.REPORTS / f'{NAME}-result.json', selection)
        return
    state = json.loads((folder / 'execution.json').read_text())
    if not state['complete']:
        selection.update(status='failed_closed', answer_metrics=None,
                         failures=[r for r in state['rows'] if r['status'] == 'failed'])
        write_new(study.REPORTS / f'{NAME}-result.json', selection)
        return
    expected = registration['longmemeval_s']['ordered_question_ids']
    rows = load_complete(folder, expected)
    control = load_complete(root / combined['control'], expected)
    cases = study.base._load_dataset(study.DATASET)
    summary, captures = summarize(folder, rows, cases)
    stats = study.old.paired_stats(control, rows, cases)
    stats['losses_detail'] = [c['question_id'] for c, a, b in zip(cases, control, rows) if a['correct'] and not b['correct']]
    stats['wins_detail'] = [c['question_id'] for c, a, b in zip(cases, control, rows) if b['correct'] and not a['correct']]
    stats['category_correct_deltas'] = {name: values['correct'] - analysis['arms'][combined['control']]['categories'][name]['correct']
                                       for name, values in summary['categories'].items()}
    control_captures = [json.loads((root / combined['control'] / c['question_id'] / 'capture.json').read_text()) for c in cases]
    stats['complete_evidence_losses'] = [c['question_id'] for c, a, b in zip(cases, control_captures, captures)
        if a['retrievals'][0]['evidence'].get('complete_turn_recall') and not b['retrievals'][0]['evidence'].get('complete_turn_recall')]
    stats['changed_contexts'] = sum(a['retrievals'][0]['context'] != b['retrievals'][0]['context'] for a, b in zip(control_captures, captures))
    stats['unchanged_input_score_disagreements'] = sum(a['retrievals'][0]['context'] == b['retrievals'][0]['context'] and x['correct'] != y['correct']
        for a, b, x, y in zip(control_captures, captures, control, rows))
    selection.update(status='complete_verified', summary=summary, paired=stats,
        control=combined['control'], configuration_sha256=combined['config_sha256'],
        verification_sha256=file_sha(folder / 'verification.json'),
        artifact_manifest_sha256=json.loads((folder / 'verification.json').read_text())['pack_manifest_sha256'])
    write_new(study.REPORTS / f'{NAME}-result.json', selection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
