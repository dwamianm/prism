"""Export the ten complete original arms and distinct completed repair evidence."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, write_new


def main():
    names = ['opt-in-successor-v2-analysis-10-complete.json',
             'opt-in-rank-envelope-v2-reader-candidate-result.json',
             'opt-in-marginal-packing-v2-answer-result.json', 'opt-in-packing-oracle-v1-result.json']
    paths = [study.REPORTS / name for name in names]
    matrix, rank, marginal, oracle = [json.loads(path.read_text()) for path in paths]
    rows = []
    for label, arm, color in [
        ('Reranker', 'reranker', '#aa4736'),
        ('Reranker + reformulation', 'reranker_reformulation', '#aa4736'),
        ('Episode routing', 'episode_routing', '#aa4736'),
        ('Episode + augmentation', 'episode_augmentation', '#aa4736'),
        ('Episode + projection', 'episode_projection', '#aa4736'),
        ('Temporal + episode', 'temporal_episode', '#aa4736'),
        ('Query reformulation', 'query_reformulation', '#aa4736'),
        ('Augmentation · inactive', 'evidence_augmentation', '#78818a'),
        ('Projection · inactive', 'evidence_projection', '#78818a')]:
        result = matrix['arms'][arm]
        assert result['status'] == 'complete_verified' and result['total'] == 500
        rows.append((f'{label}   {result["correct"]}/500', matrix['comparisons'][arm], color, 'o'))
    assert rank['complete'] and rank['verified_cases'] == 500
    assert marginal['complete'] and oracle['complete'] and oracle['verified_cases'] == 500
    rows.extend([
        ('Score-scale repair · 428 vs 437\nSecondary; new primary control failed', rank['paired'], '#36699b', 's'),
        ('Marginal packing · 433 vs 433\nRegistered primary comparison', marginal['paired'], '#36699b', 's'),
        ('Annotation-assisted · 473 vs 437\nNondeployable diagnostic', oracle['paired'], '#187e74', 'D')])
    fig, ax = plt.subplots(figsize=(12, 8.9))
    fig.subplots_adjust(left=.43, right=.97, top=.85, bottom=.18)
    positions = [*range(9), 10, 11.4, 13]
    lows, highs = [], []
    for y, (label, stats, color, marker) in zip(positions, rows):
        point = 100*stats['difference']
        low, high = [100*v for v in stats['question_ci95']]
        lows.append(low); highs.append(high)
        ax.errorbar(point, y, xerr=[[point-low], [high-point]], color=color,
                    fmt=marker, markersize=6, capsize=4, linewidth=1.7)
        ax.annotate(f'{point:+.1f} [{low:+.1f}, {high:+.1f}]', (high,y),
                    xytext=(7,-3), textcoords='offset points', color=color, fontsize=9)
    ax.axvline(0, color='#59616a', linestyle='--', linewidth=.9)
    for y in (8.7, 12.3): ax.axhline(y, color='#d9dde2', linewidth=.8)
    ax.set_yticks(positions, [r[0] for r in rows], fontsize=10)
    ax.set_ylim(13.8,-.8)
    ax.set_xlim(min(lows)-5, max(highs)+21)
    ax.set_xlabel('Paired difference in answer score (percentage points)', fontsize=11, labelpad=12)
    ax.grid(axis='x', color='#e8eaed', linewidth=.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis='y', length=0, pad=10)
    for side in ('top','right','left'): ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color('#bcc3ca')
    fig.suptitle('Completed LongMemEval-S evidence', x=.045, y=.972,
                 ha='left', fontsize=19, weight='bold')
    fig.text(.045,.917,'500 questions per complete arm · original production control: 437/500 (87.4%)',
             fontsize=11, color='#424a53')
    fig.text(.045,.885,'Original feature comparisons use 437/500. Repair reference controls are shown explicitly.',
             fontsize=10, color='#59616a')
    fig.text(.045,.087,'Paired 95% bootstrap intervals, unadjusted. All development evidence; no untouched confirmation.',
             fontsize=9, color='#59616a')
    fig.text(.045,.058,'Temporal-only and the first repair control failed closed and have no quality score. Failed runs are retained.',
             fontsize=9, color='#59616a')
    outputs = []
    for extension in ('png','svg'):
        path = study.REPORTS / f'opt-in-completed-comparisons-v2.{extension}'
        if path.exists(): raise FileExistsError(path)
        fig.savefig(path,dpi=180,facecolor='white')
        outputs.append(path)
    plt.close(fig)
    write_new(study.REPORTS/'opt-in-completed-comparisons-v2-identity.json', {
        'source_sha256': {p.name:file_sha(p) for p in [*paths,Path(__file__)]},
        'output_sha256': {p.name:file_sha(p) for p in outputs}, 'new_model_calls':0,
        'original_and_primary_comparisons_unchanged':True})


if __name__ == '__main__': main()
