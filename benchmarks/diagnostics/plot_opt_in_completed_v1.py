"""Export the six complete registered arms and separate packing diagnostic."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, write_new


def main():
    source = study.REPORTS / 'opt-in-successor-v2-analysis-06-complete.json'
    diagnostic = study.REPORTS / 'opt-in-packing-oracle-v1-result.json'
    data, oracle = json.loads(source.read_text()), json.loads(diagnostic.read_text())
    assert oracle['complete'] and oracle['verified_cases'] == 500
    rows = [
        ('Episode routing', 'episode_routing', '#a84632'),
        ('Reranker', 'reranker', '#a84632'),
        ('Query reformulation', 'query_reformulation', '#a84632'),
        ('Evidence augmentation (inactive)', 'evidence_augmentation', '#747b83'),
        ('Evidence projection (inactive)', 'evidence_projection', '#747b83'),
        ('Annotated evidence priority\n(non-deployable diagnostic)', None, '#187e74'),
    ]
    fig, ax = plt.subplots(figsize=(10.8, 5.4))
    fig.subplots_adjust(left=.36, right=.95, top=.81, bottom=.23)
    for y, (label, name, color) in enumerate(rows):
        if name:
            assert data['arms'][name]['status'] == 'complete_verified'
            assert data['arms'][name]['total'] == 500
        stats = data['comparisons'][name] if name else oracle['paired']
        point = 100 * stats['difference']
        low, high = [100 * v for v in stats['question_ci95']]
        ax.errorbar(point, y, xerr=[[point-low], [high-point]], color=color,
            fmt='o' if name else 'D', markersize=6, capsize=4, linewidth=1.8)
        ax.annotate(f'{point:+.1f} [{low:+.1f}, {high:+.1f}]',
            (high, y), xytext=(7, -3), textcoords='offset points', fontsize=9, color=color)
    ax.axvline(0, color='#444b52', linewidth=.9, linestyle='--')
    ax.axhline(4.5, color='#d0d4d8', linewidth=.8)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=10)
    ax.set_xlim(-39, 29)
    ax.set_ylim(5.65, -.65)
    ax.set_xticks([-30, -20, -10, 0, 10, 20])
    ax.set_xlabel('Answer-score difference from baseline (percentage points)', fontsize=10, labelpad=9)
    ax.grid(axis='x', color='#e7e9ec', linewidth=.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis='y', length=0, pad=9)
    for name in ('top', 'right', 'left'):
        ax.spines[name].set_visible(False)
    ax.spines['bottom'].set_color('#bbc1c8')
    fig.suptitle('Completed LongMemEval-S comparisons', x=.055, y=.965,
        ha='left', fontsize=17, weight='bold')
    fig.text(.055, .9, '500 questions per arm · production baseline 437/500 (87.4%)',
        fontsize=11, color='#444b52')
    handles = [Line2D([0], [0], color=c, marker=m, linestyle='None', label=l) for c,m,l in
        [('#a84632','o','Active feature'),('#747b83','o','Identical-input repeat'),
         ('#187e74','D','Annotation-assisted diagnostic')]]
    fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(.5,.065),
        ncol=3, frameon=False, fontsize=9)
    fig.text(.055, .035, 'Paired 95% bootstrap intervals, unadjusted. Development data; no untouched confirmation. Pending arms are unscored.',
        fontsize=8.5, color='#555d66')
    outputs = []
    for extension in ('svg','png'):
        path = study.REPORTS / f'opt-in-completed-comparisons-v1.{extension}'
        if path.exists():
            raise FileExistsError(path)
        fig.savefig(path, dpi=180, facecolor='white')
        outputs.append(path)
    plt.close(fig)
    write_new(study.REPORTS / 'opt-in-completed-comparisons-v1-identity.json', {
        'source_sha256': {p.name: file_sha(p) for p in (source, diagnostic, Path(__file__))},
        'output_sha256': {p.name: file_sha(p) for p in outputs},
        'new_analysis_or_model_calls': False})


if __name__ == '__main__':
    main()
