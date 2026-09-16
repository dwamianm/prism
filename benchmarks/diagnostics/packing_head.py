"""Fixed one-head development experiment over immutable native captures.

Only the highest-scored ordinary multi-path candidate precedes density ordering.
Instructions and pinned/active tasks retain priority. Never changes defaults.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import inspect
from importlib.metadata import version
import json
from pathlib import Path
import sys
from unittest.mock import patch

from benchmarks.diagnostics.compare_public_captures import cluster_statistics, groups_for
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from prme.retrieval import packing
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.types import NodeType

BUDGETS = (2048, 4096, 8192)


def pack_head(candidates, config):
    """Serial diagnostic only: keep one relevance head, then ordinary density."""
    eligible = [c for c in candidates if c.path_count >= 2
                and c.node.node_type != NodeType.INSTRUCTION
                and not packing._is_pinned_or_active_task(c)]
    if not eligible:
        return packing.pack_context(candidates, config)
    head = min(eligible, key=lambda c: (-c.composite_score, str(c.node.id))).node.id
    original = packing.compute_str
    with patch.object(packing, "compute_str",
                      lambda c: float("inf") if c.node.id == head else original(c)):
        return packing.pack_context(candidates, config)


def measure(bundle, snapshot, config):
    context = bundle.render()
    count = packing.count_tokens(context, config.tokenizer)
    if count != bundle.tokens_used or count > config.token_budget - config.overhead_tokens:
        raise ValueError("Measured context violates budget")
    sources = snapshot['node_sources']
    content = {str(c['node']['id']): c['node']['content'] for c in snapshot['candidates']}
    retained, pointers = [], []
    for line in context.split('\n'):
        if not line.startswith('{'):
            continue
        entry = json.loads(line)
        if entry['representation'] in {'full', 'prose', 'structured'}:
            if content[entry['id']] not in entry['text']:
                raise ValueError('Whole source text lost')
            if content[entry['id']].strip():
                retained.append(sources[entry['id']])
        else:
            pointers.append(sources[entry['id']])
    return {'context': context, 'tokens': count, 'sha256': digest(context.encode()),
            'content_source_ids': retained, 'pointer_source_ids': pointers}


def identity(args):
    files = {name: digest(getattr(args, name).read_bytes())
             for name in ('inputs', 'references', 'source', 'analysis')}
    source = json.loads(args.source.read_bytes())
    analysis = json.loads(args.analysis.read_bytes())
    cases = json.loads(args.inputs.read_bytes())['cases']
    refs = json.loads(args.references.read_bytes())['references']
    ids = [c['case_id'] for c in cases]
    if (not source['complete'] or source['errors'] or not analysis['complete']
            or not analysis['verification_passed'] or len(ids) != 119
            or len(set(ids)) != 119 or ids != [r['case_id'] for r in source['details']]
            or ids != [r['case_id'] for r in analysis['details']]
            or set(ids) != {r['case_id'] for r in refs}
            or analysis['inputs_sha256'] != files['inputs']
            or analysis['source_artifacts']['prme_report'] != files['source']
            or analysis['references_sha256'] != files['references']):
        raise ValueError('Complete fixed development source required')
    inventory = []
    for row in source['details']:
        capture = row['capture']
        if (capture['filename'] != row['case_id'] + '.json'
                or digest((args.captures / capture['filename']).read_bytes()) != capture['sha256']):
            raise ValueError('Immutable capture differs')
        inventory.append(capture)
    return {'files': files, 'captures_sha256': digest(canonical(inventory)),
            'python': sys.version,
            'dependencies': {name: version(name) for name in ('pydantic', 'tiktoken')},
            'modules': {name: digest(Path(inspect.getfile(__import__(name, fromlist=['*']))).read_bytes())
                        for name in ('prme.retrieval.models', 'prme.retrieval.config',
                                     'prme.retrieval.tokenization', 'prme.models.nodes')},
            'implementation_sha256': digest(Path(__file__).read_bytes()),
            'packing_sha256': digest(Path(inspect.getfile(packing)).read_bytes()),
            'statistics_sha256': digest(Path(inspect.getfile(cluster_statistics)).read_bytes())}


def run(args, plan):
    if identity(args) != plan['identity'] or plan['budgets'] != list(BUDGETS):
        raise ValueError('Registered implementation or inputs differ')
    cases = json.loads(args.inputs.read_bytes())['cases']
    references = {r['case_id']: r for r in json.loads(args.references.read_bytes())['references']}
    groups = groups_for(cases, references)
    rows = []
    for case in cases:
        cid = case['case_id']
        snapshot = json.loads((args.captures / (cid + '.json')).read_bytes())
        candidates = [RetrievalCandidate.model_validate(c) for c in snapshot['candidates']]
        before = canonical([c.model_dump(mode='json') for c in candidates])
        config = PackingConfig.model_validate(snapshot['packing_config'])
        if config.multipath_ordering != 'density':
            raise ValueError('Density baseline required')
        arms = {}
        # Context production receives no reference labels or category.
        for budget in BUDGETS:
            cfg = config.model_copy(update={'token_budget': budget})
            for name, fn in [('density', packing.pack_context), ('head1', pack_head)]:
                measured = measure(fn(candidates, cfg), snapshot, cfg)
                if name == 'density' and any(measured[k] != snapshot['contexts'][str(budget)][k]
                                             for k in ('context', 'tokens', 'sha256')):
                    raise ValueError('Exact baseline did not reproduce')
                arms[f'{name}:{budget}'] = measured
        if canonical([c.model_dump(mode='json') for c in candidates]) != before:
            raise ValueError('Candidate inputs mutated')
        gold = set(references[cid]['evidence_source_ids'])
        for measured in arms.values():
            retained = set(measured['content_source_ids'])
            measured['evidence_recall'] = len(retained & gold) / len(gold) if gold else None
        rows.append({'case_id': cid, 'category': references[cid]['category'],
                     'group': groups[cid], 'arms': arms})
        print(f'Completed {len(rows)}/119', flush=True)
    def summarize(selected):
        result = {}
        for budget in BUDGETS:
            valid = [r for r in selected if r['arms'][f'density:{budget}']['evidence_recall'] is not None]
            result[str(budget)] = cluster_statistics(
                [(r['arms'][f'density:{budget}']['evidence_recall'],
                  r['arms'][f'head1:{budget}']['evidence_recall']) for r in valid],
                [r['group'] for r in valid])
        return result
    if identity(args) != plan['identity']:
        raise ValueError('Inputs or implementation changed during execution')
    result = {'complete': True, 'plan_sha256': digest(args.plan.read_bytes()),
              'controls_reproduced': len(rows) * len(BUDGETS), 'details': rows,
              'overall': summarize(rows), 'categories': {
                  cat: summarize([r for r in rows if r['category'] == cat])
                  for cat in sorted({r['category'] for r in rows})}, 'limits': plan['limits']}
    write(args.output, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['register', 'run'])
    for name in ('inputs', 'references', 'source', 'analysis', 'captures', 'plan', 'output'):
        parser.add_argument('--' + name, type=Path, required=name != 'output')
    args = parser.parse_args()
    if args.mode == 'register':
        if args.plan.exists():
            raise ValueError('Refusing to replace registration')
        write(args.plan, {'registered_at': datetime.now(timezone.utc).isoformat(),
                         'identity': identity(args), 'budgets': BUDGETS,
                         'policy': 'Exactly one highest-score ordinary multi-path candidate before density remainder; node-ID ties; existing instruction/pin/task tiers preserved.',
                         'limits': ['Post-hoc development experiment, not independent confirmation.',
                                    'All 119 cases and all three budgets retained; no default promotion.',
                                    'Fixed candidates; source retention is not answer accuracy.',
                                    'Previous 381-question preference regression remains unresolved.']})
    else:
        if args.output is None or args.output.exists():
            raise ValueError('Fresh output required')
        run(args, json.loads(args.plan.read_bytes()))


if __name__ == '__main__':
    main()
