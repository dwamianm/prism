"""Completed conditional temporal comparison; never fills the failed factorial arm."""
from collections import Counter, defaultdict
import json

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    root = study.PRIVATE/'opt-in-successor-v2'
    cases = study.base._load_dataset(study.DATASET)
    expected = [c['question_id'] for c in cases]
    outcomes, identities = {}, {}
    for arm in ('episode_routing','temporal_episode'):
        folder = root/arm
        verification = json.loads((folder/'verification.json').read_text())
        if not verification['complete'] or file_sha(folder/'execution.json') != verification['execution_sha256']:
            raise RuntimeError('Complete verified arm required')
        outcomes[arm] = json.loads((folder/'execution.json').read_text())['rows']
        study.old.validate_complete(expected,outcomes[arm])
        identities[arm] = {'verification_sha256':file_sha(folder/'verification.json'),
                           'execution_sha256':file_sha(folder/'execution.json')}
    statuses = {'cold':Counter(),'warm':Counter()}
    usage = defaultdict(Counter)
    changed = []
    mismatch = []
    groups = defaultdict(Counter)
    transitions = []
    cold_warm_changes = 0
    for before, after in zip(outcomes['episode_routing'],outcomes['temporal_episode']):
        qid = before['question_id']
        captures, requests = {}, {}
        for arm,row in [('episode_routing',before),('temporal_episode',after)]:
            folder = root/arm/qid
            for kind in ('capture','reader','judge'):
                if file_sha(folder/f'{kind}.json') != row[f'{kind}_sha256']:
                    raise RuntimeError('Case artifact changed')
            captures[arm] = json.loads((folder/'capture.json').read_text())
            requests[arm] = json.loads((folder/'reader.json').read_text())['request']
        a,b = captures['episode_routing']['retrievals'][0],captures['temporal_episode']['retrievals'][0]
        input_changed = requests['episode_routing'] != requests['temporal_episode']
        delta = int(after['correct'])-int(before['correct'])
        cold = b['metadata'].get('temporal_relation')
        status = cold['status'] if cold else 'not_invoked'
        groups['changed_input' if input_changed else 'unchanged_input'].update(
            total=1,wins=delta>0,losses=delta<0,control_correct=before['correct'],candidate_correct=after['correct'])
        if input_changed: changed.append(qid)
        if delta: transitions.append({'question_id':qid,'changed_input':input_changed,'temporal_status':status,
                                       'episode_correct':before['correct'],'temporal_episode_correct':after['correct']})
        if cold and cold.get('control_context_sha256') not in (None,a['context_sha256']):
            mismatch.append(qid)
        if not captures['temporal_episode']['cold_warm_context_equal']: cold_warm_changes += 1
        for retrieval in captures['temporal_episode']['retrievals']:
            metadata = retrieval['metadata'].get('temporal_relation')
            mode = retrieval['mode']
            statuses[mode][metadata['status'] if metadata else 'not_invoked'] += 1
            if not metadata: continue
            if not metadata['confirmation_protocol_aligned'] or metadata['gate_threshold'] != .85:
                raise RuntimeError('Confirmed temporal protocol differs')
            for provider in ('resolver','gate'):
                audit = metadata.get(provider)
                if not audit: continue
                key = mode+'_'+provider
                usage[key]['operations'] += 1
                usage[key]['attempts'] += audit['attempts']
                for field in ('input_tokens','output_tokens'):
                    if audit.get(field) is None: usage[key][field+'_missing_operations'] += 1
                    else: usage[key][field] += audit[field]
                usage[key]['elapsed_ms'] += audit['elapsed_ms']
    output = {'kind':'post-hoc-complete-temporal-effect-conditional-on-episode-routing',
              'at':study.utc(),'arm_identities':identities,'cases':500,
              'conditional_paired':study.old.paired_stats(outcomes['episode_routing'],outcomes['temporal_episode'],cases),
              'input_groups':dict(groups),'changed_input_ids':changed,'all_answer_transitions':transitions,
              'temporal_status_by_retrieval_mode':statuses,'additional_provider_usage':dict(usage),
              'temporal_control_context_mismatches':mismatch,'cold_warm_context_changes':cold_warm_changes,
              'full_factorial_status':'unavailable_failed_temporal_only_arm',
              'new_model_calls':0,'primary_scores_changed':False,
              'limits':'Post-hoc conditional contrast across all 500 cases. It cannot replace the failed temporal-only arm or establish the four-arm interaction. Unchanged-input answer disagreements are reader/judge variation. Token totals with missing-operation counts are reported sums, not complete financial costs.'}
    output['sha256'] = sha(output)
    write_new(study.REPORTS/'opt-in-temporal-episode-audit-v1.json',output)
    print(json.dumps({k:v for k,v in output.items() if k not in ('changed_input_ids','all_answer_transitions')}))


if __name__ == '__main__': main()
