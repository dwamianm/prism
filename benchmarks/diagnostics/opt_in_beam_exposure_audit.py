"""Source-identity audit only; never inspect reserved probes or score answers."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def source_map(turns, normalize=False):
    output = {}
    for turn in turns:
        content = turn['content']
        if normalize:
            content = ' '.join(content.split())
        if content:
            output[text_hash(content)] = len(content)
    return output


def describe_overlap(a, b):
    shared = a.keys() & b.keys()
    lengths = [a[k] for k in shared]
    return {'unique_shared_source_strings': len(shared),
            'at_least_80_characters': sum(n >= 80 for n in lengths),
            'at_least_200_characters': sum(n >= 200 for n in lengths),
            'maximum_shared_characters': max(lengths, default=0),
            'shared_source_hashes': sorted(shared)}


def main():
    path = study.PRIVATE / 'beam-inputs-v1/beam_100K.json'
    if file_sha(path) != '6cc7d1e3b68f9d425db836a0343cca567fcabf197cb8adfe4891ba130c1430d3':
        raise RuntimeError('Restored BEAM identity differs')
    rows = json.loads(path.read_text())
    if len(rows) != 20:
        raise RuntimeError('Unexpected BEAM cohort')
    maps = [{mode: source_map((t for session in r['chat'] for t in session), mode == 'whitespace_normalized')
             for mode in ('exact', 'whitespace_normalized')} for r in rows]
    examined = {mode: {**maps[0][mode], **maps[1][mode]} for mode in maps[0]}
    # Content remains in this local process. No probes, reference answers or
    # source strings are displayed, summarized or supplied to any model.
    lme = study.base._load_dataset(study.DATASET)
    comparison = {mode: source_map((t for c in lme for session in c['haystack_sessions'] for t in session),
                                   mode == 'whitespace_normalized') for mode in maps[0]}
    del lme
    seed_ids, profiles = defaultdict(list), defaultdict(list)
    for index, row in enumerate(rows):
        seed_ids[sha(row['conversation_seed'])].append(index)
        profiles[sha(row['user_profile'])].append(index)
    summaries = []
    for index in range(2, 20):
        row = rows[index]
        summaries.append({'conversation_index': index,
            'conversation_identity_sha256': sha(row['conversation_id']),
            'chat_sha256': sha(row['chat']), 'source_turns': sum(len(s) for s in row['chat']),
            'unique_source_strings': {mode: len(value) for mode, value in maps[index].items()},
            'overlap_with_examined_beam_0_1': {mode: describe_overlap(value, examined[mode]) for mode, value in maps[index].items()},
            'overlap_with_examined_longmemeval_s': {mode: describe_overlap(value, comparison[mode]) for mode, value in maps[index].items()}})
    pairs = []
    for a in range(2, 20):
        for b in range(a+1, 20):
            metrics = {mode: describe_overlap(maps[a][mode], maps[b][mode]) for mode in maps[a]}
            if any(v['unique_shared_source_strings'] for v in metrics.values()):
                pairs.append({'indices': [a, b], 'overlap': metrics})
    result = {'kind': 'reserved-beam-source-identity-and-overlap-audit', 'at': study.utc(),
        'dataset_sha256': file_sha(path), 'comparison_dataset_sha256': file_sha(study.DATASET),
        'script_sha256': file_sha(Path(__file__)), 'reserved_indices': list(range(2, 20)),
        'examined_beam_indices': [0, 1], 'conversations': summaries,
        'shared_full_seed_groups': [v for v in seed_ids.values() if len(v) > 1],
        'shared_full_profile_groups': [v for v in profiles.values() if len(v) > 1],
        'reserved_pair_overlaps': pairs, 'new_model_calls': 0,
        'probe_or_reference_content_inspected': False, 'cohort_selected_by_outcome': False,
        'limits': 'Exact and whitespace-normalized source-string/profile/seed identity only, not semantic near-duplicate detection or proof of all human/provider exposure. The 80/200-character buckets are descriptive, not an eligibility rule. No source is excluded here. A later prospective confirmation protocol must declare its cohort and scoring before probe inspection or inference.'}
    result['audit_sha256'] = sha(result)
    write_new(study.REPORTS / 'opt-in-beam-source-exposure-v1.json', result)
    print(json.dumps({'conversations': len(summaries), 'shared_full_seed_groups': result['shared_full_seed_groups'],
        'shared_full_profile_groups': result['shared_full_profile_groups'], 'reserved_pairs_with_any_shared_source': len(pairs),
        'examined_beam_exact_overlaps': sum(r['overlap_with_examined_beam_0_1']['exact']['unique_shared_source_strings'] for r in summaries),
        'examined_lme_exact_overlaps': sum(r['overlap_with_examined_longmemeval_s']['exact']['unique_shared_source_strings'] for r in summaries),
        'examined_normalized_overlaps_at_least_80_chars': sum(r[k]['whitespace_normalized']['at_least_80_characters'] for r in summaries for k in ('overlap_with_examined_beam_0_1', 'overlap_with_examined_longmemeval_s'))}))


if __name__ == '__main__':
    main()
