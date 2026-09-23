"""Post hoc accounting of model-scored evidence and unscored packed records."""
from collections import Counter
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.analyze_opt_in_successor import describe, load_complete
from benchmarks.diagnostics.opt_in_source_coverage import required_turns, source_key
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    name = 'opt-in-reranker-score-scale-audit-v1'
    folder = study.PRIVATE / 'opt-in-successor-v2/reranker'
    reg = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    cases = study.base._load_dataset(study.DATASET)
    outcomes = load_complete(folder, reg['longmemeval_s']['ordered_question_ids'])
    totals, evidence, shares, questions = Counter(), Counter(), [], []
    for case, outcome in zip(cases, outcomes):
        path = folder / case['question_id'] / 'capture.json'
        if file_sha(path) != outcome['capture_sha256']:
            raise study.old.ResearchFailure('Frozen capture changed')
        capture = json.loads(path.read_text())['retrievals'][0]
        provenance = capture['receipt']['score_provenance']
        def neural(node_id):
            return any(op['kind'] == 'neural_blend' for op in provenance[node_id]['adjustments'])
        packed = capture['packed']
        no_neural = sum(not neural(row['node_id']) for row in packed)
        local = Counter(packed_records=len(packed), packed_without_neural_or_inherited_neural=no_neural,
            first_returned_without_neural=int(not neural(capture['returned'][0]['node_id'])),
            first_packed_without_neural=int(not neural(packed[0]['node_id'])))
        totals.update(local)
        shares.append(no_neural / len(packed))
        wanted, exposed = required_turns(case), {row['node_id'] for row in packed}
        local_evidence = Counter()
        for row in capture['returned']:
            if source_key(row) in wanted:
                key = ('packed' if row['node_id'] in exposed else 'omitted') + ('_neural' if neural(row['node_id']) else '_no_neural')
                local_evidence[key] += 1
        evidence.update(local_evidence)
        questions.append({'question_id': case['question_id'], 'capture_sha256': outcome['capture_sha256'],
                          'counts': dict(local), 'annotated_turns': dict(local_evidence)})
    result = {'kind': 'post-hoc-reranker-score-scale-audit', 'complete': True, 'cases': len(cases),
        'evidence_already_seen': 'All current reranker outcomes and the first descriptive count calculation. This repeat freezes the auditable derivation, not a prospective hypothesis test.',
        'source_sha256': file_sha(Path(__file__)), 'dataset_sha256': file_sha(study.DATASET),
        'parent_registration_sha256': reg['registration_sha256'],
        'execution_sha256': file_sha(folder / 'execution.json'), 'verification_sha256': file_sha(folder / 'verification.json'),
        'definition': 'neural means the final candidate score lineage contains neural_blend, whether direct or inherited through a session operation. No-neural means neither. Counts use first retrieval and original dataset turn annotations, never reader or judge correctness.',
        'totals': dict(totals), 'annotated_turns': dict(evidence), 'per_question_no_neural_fraction': describe(shares),
        'questions': questions, 'limitations': 'Association plus inspected code path, not an isolated causal estimate, semantic sufficiency test or evidence that an untested repair improves answers.'}
    result['result_sha256'] = sha(result)
    write_new(study.REPORTS / f'{name}-result.json', result)
    print(json.dumps({'totals': totals, 'annotated_turns': evidence}))


if __name__ == '__main__':
    main()
