"""Prespecified complete-arm answer, evidence, activation and paired analysis."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json

import numpy as np
from scipy.stats import binomtest

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def load_complete(folder, expected):
    execution=json.loads((folder/'execution.json').read_text())
    if not execution['complete']:
        raise study.old.ResearchFailure('Incomplete arm cannot be scored')
    study.old.validate_complete(expected,execution['rows'])
    verified=json.loads((folder/'verification.json').read_text())
    if (not verified['complete'] or verified['cases']!=len(expected) or
        verified['execution_sha256']!=file_sha(folder/'execution.json')):
        raise study.old.ResearchFailure('Complete verified artifact chain required')
    return execution['rows']


def holm(values, family_size):
    # Failed/unavailable comparisons retain their slots (equivalent to p=1).
    result={}
    maximum=0.0
    for rank,(name,p) in enumerate(sorted(values.items(),key=lambda pair:(pair[1],pair[0]))):
        maximum=max(maximum,min(1.0,(family_size-rank)*p))
        result[name]=maximum
    return result


def describe(values):
    if not values:
        return None
    return dict(mean=float(np.mean(values)),p50=float(np.quantile(values,.5)),p95=float(np.quantile(values,.95)))


def summarize(folder,rows,cases):
    categories=defaultdict(lambda:Counter(total=0,correct=0))
    times=defaultdict(list)
    tokens=[]
    inventory=Counter()
    activation=Counter()
    evidence=Counter()
    provider=Counter()
    captures=[]
    failures=[]
    for case,row in zip(cases,rows):
        location=folder/case['question_id']
        capture=json.loads((location/'capture.json').read_text())
        trace=json.loads((location/'feature-observations.json').read_text())
        if file_sha(location/'feature-observations.json')!=row['feature_observations_sha256']:
            raise study.old.ResearchFailure('Feature observation identity differs')
        captures.append(capture)
        first=capture['retrievals'][0]
        category=categories[case['question_type']]
        category['total']+=1
        category['correct']+=row['correct']
        if case['question_id'].endswith('_abs'):
            categories['abstention']['total']+=1
            categories['abstention']['correct']+=row['correct']
        for retrieval in capture['retrievals']:
            times[retrieval['mode']].append(retrieval['seconds'])
        tokens.append(first['context_tokens'])
        if capture['ingestion']['seconds'] is not None:
            times['ingestion'].append(capture['ingestion']['seconds'])
        inventory.update(capture['ingestion'].get('inventory',{}))
        activation['supersedence_checks']+=trace.get('supersedence_checks',0)
        activation['novelty_calls']+=trace.get('novelty_calls',0)
        activation['reformulation_calls']+=len(trace.get('reformulations',[]))
        activation['nonempty_reformulations']+=sum(bool(x['alternatives']) for x in trace.get('reformulations',[]))
        for prov in first['receipt']['score_provenance'].values():
            activation.update('score_operation_'+x['kind'] for x in prov['adjustments'])
        temporal=first['metadata'].get('temporal_relation')
        if temporal:
            activation['temporal_'+temporal['status']]+=1
        evidence['context_conflict_flags']+=first['conflicts']
        e=first['evidence']
        if e['applicable']:
            evidence['applicable_cases']+=1
            for key in ('complete_turn_recall','any_turn_recall','complete_session_recall','any_session_recall'):
                evidence[key]+=bool(e[key])
        returned=study.base._evidence_metrics(case,first['returned'])
        if not row['correct']:
            reason=('no_annotation' if not e['applicable'] else
                    'annotated_evidence_present_reader_or_annotation_issue' if e['complete_turn_recall'] else
                    'packing_omission' if returned['complete_turn_recall'] else
                    'retrieval_omission')
            failures.append(dict(question_id=case['question_id'],category=case['question_type'],diagnostic=reason,
                                 packed_evidence=e,returned_evidence=returned,context_tokens=first['context_tokens']))
        for role in ('reader','judge'):
            data=json.loads((location/f'{role}.json').read_text())
            provider[role+'_calls']+=1
            provider['http_attempts']+=len(data['attempts'])
            provider['transient_http_errors']+=sum(x['http_status']>=400 for x in data['attempts'])
            provider['input_tokens']+=data['response']['prompt_eval_count']
            provider['output_tokens']+=data['response']['eval_count']
    return dict(total=len(rows),correct=sum(row['correct'] for row in rows),
        accuracy=sum(row['correct'] for row in rows)/len(rows),categories=dict(categories),
        latency_seconds={key:describe(values) for key,values in times.items()},
        ingestion_summed_seconds=sum(times.get('ingestion',[])) if 'ingestion' in times else None,
        monetary_cost=None,context_tokens=describe(tokens),inventory=dict(inventory),
        feature_activation=dict(activation),evidence=dict(evidence),reader_judge_usage=dict(provider),
        error_categories=dict(Counter(x['diagnostic'] for x in failures)),errors=failures),captures


def analyze(args):
    registration=json.loads((study.REPORTS/f'{args.name}-registration.json').read_text())
    cases=study.base._load_dataset(study.DATASET)
    expected=registration['longmemeval_s']['ordered_question_ids']
    if study.file_sha(study.DATASET)!=registration['longmemeval_s']['dataset_sha256']:
        raise study.old.ResearchFailure('Dataset differs')
    output=study.PRIVATE/args.name
    result=dict(kind='opt-in-successor-complete-arm-analysis',analyzed_at=study.utc(),
        registration_sha256=registration['registration_sha256'],arms={},comparisons={},
        all_fixed_arms_complete=False,classification='development; no untouched confirmation',
        default_changes=False,promotion_recommendation=None)
    rows_by_arm={}
    captures={}
    for arm in registration['arms']:
        folder=output/arm['id']
        if not (folder/'execution.json').exists():
            result['arms'][arm['id']]=dict(status='not_complete_or_not_started',answer_metrics=None)
            continue
        execution=json.loads((folder/'execution.json').read_text())
        if not execution['complete']:
            result['arms'][arm['id']]=dict(status='failed_closed',answer_metrics=None,
                counts=dict(Counter(r['status'] for r in execution['rows'])),
                failures=[r for r in execution['rows'] if r['status']=='failed'])
            continue
        rows=load_complete(folder,expected)
        summary,cap=summarize(folder,rows,cases)
        summary.update(status='complete_verified',configuration_sha256=arm['config_sha256'],
            artifact_manifest_sha256=json.loads((folder/'verification.json').read_text())['pack_manifest_sha256'])
        result['arms'][arm['id']]=summary
        rows_by_arm[arm['id']]=rows
        captures[arm['id']]=cap
    p_values={}
    family=[a for a in registration['arms'] if a['id']!=a['control'] and not a['exploratory']]
    for arm in registration['arms']:
        name,control=arm['id'],arm['control']
        if name==control or name not in rows_by_arm or control not in rows_by_arm:
            continue
        stats=study.old.paired_stats(rows_by_arm[control],rows_by_arm[name],cases)
        n=stats['wins']+stats['losses']
        stats['paired_exact_two_sided_p']=float(binomtest(stats['wins'],n,.5).pvalue) if n else 1.0
        if not arm['exploratory']:
            p_values[name]=stats['paired_exact_two_sided_p']
        baseline,candidate=result['arms'][control],result['arms'][name]
        stats['category_correct_deltas']={category:values['correct']-baseline['categories'][category]['correct']
                                           for category,values in candidate['categories'].items()}
        pairs=list(zip(captures[control],captures[name]))
        stats['changed_contexts']=sum(a['retrievals'][0]['context_sha256']!=b['retrievals'][0]['context_sha256'] for a,b in pairs)
        stats['unchanged_context_answer_disagreements']=sum(
            a['retrievals'][0]['context_sha256']==b['retrievals'][0]['context_sha256'] and x['correct']!=y['correct']
            for (a,b),x,y in zip(pairs,rows_by_arm[control],rows_by_arm[name]))
        stats['complete_evidence_losses']=[case['question_id'] for case,(a,b) in zip(cases,pairs)
            if a['retrievals'][0]['evidence'].get('complete_turn_recall') and not b['retrievals'][0]['evidence'].get('complete_turn_recall')]
        stats['losses_detail']=[dict(question_id=c['question_id'],category=c['question_type']) for c,a,b in zip(cases,rows_by_arm[control],rows_by_arm[name]) if a['correct'] and not b['correct']]
        stats['wins_detail']=[dict(question_id=c['question_id'],category=c['question_type']) for c,a,b in zip(cases,rows_by_arm[control],rows_by_arm[name]) if b['correct'] and not a['correct']]
        stats['context_token_delta']=describe([b['retrievals'][0]['context_tokens']-a['retrievals'][0]['context_tokens'] for a,b in pairs])
        stats['cold_latency_delta_seconds']=describe([b['retrievals'][0]['seconds']-a['retrievals'][0]['seconds'] for a,b in pairs])
        stats.update(control=control,exploratory=arm['exploratory'],promotion_status='unassessed_without_untouched_confirmation')
        result['comparisons'][name]=stats
    adjusted=holm(p_values,len(family))
    for name,value in adjusted.items(): result['comparisons'][name]['holm_adjusted_p']=value
    singles=['store_supersedence','qa_pairing','surprise_gating','reranker','query_reformulation','temporal_relations','episode_routing','evidence_augmentation']
    result['all_individuals_comparable']=all(a in result['comparisons'] for a in singles)
    result['all_fixed_arms_complete']=len(rows_by_arm)==len(registration['arms'])
    result['best_combination_selection']=None
    if result['all_individuals_comparable']:
        eligible=[name for name in singles if
            result['comparisons'][name]['full_history_cluster_ci95'][0]>0 and
            result['comparisons'][name]['holm_adjusted_p']<.05 and
            min(result['comparisons'][name]['category_correct_deltas'].values())>=0 and
            not result['comparisons'][name]['complete_evidence_losses']]
        eligible.sort(key=lambda n:(-result['comparisons'][n]['difference'],result['arms'][n]['latency_seconds']['cold']['p95'],n))
        result['best_combination_selection']=eligible[:2]
    result['analysis_sha256']=sha(result)
    write_new(study.REPORTS/args.output,result)
    print(json.dumps(dict(complete_arms=list(rows_by_arm),comparisons={name:{key:value[key] for key in ('difference','wins','losses','question_ci95','changed_contexts','unchanged_context_answer_disagreements')} for name,value in result['comparisons'].items()})))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--output',required=True)
    analyze(parser.parse_args())


if __name__=='__main__': main()
