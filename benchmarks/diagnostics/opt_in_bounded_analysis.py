"""Exact-statistics execution wrapper with bounded cross-arm capture retention."""
import argparse
import json
from pathlib import Path
import sys
import time

from benchmarks.diagnostics import analyze_opt_in_successor as analysis
from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha

PLAN = 'opt-in-analysis-memory-amendment-v1.json'


def install():
    if getattr(analysis.summarize, '_bounded_capture_retention', False):
        return
    original = analysis.summarize
    def summarize(*args, **kwargs):
        # The frozen implementation still reads/checks every complete artifact
        # and computes all metrics. Retain precisely the fields used later by
        # paired analysis; complete originals remain in immutable captures.
        result, captures = original(*args, **kwargs)
        retained = [{'retrievals': [{key: capture['retrievals'][0][key] for key in
                    ('context', 'context_sha256', 'context_tokens', 'seconds', 'evidence')}]} for capture in captures]
        return result, retained
    summarize._bounded_capture_retention = True
    analysis.summarize = summarize


def watch():
    root = study.PRIVATE / 'opt-in-successor-v2'
    registration = json.loads((study.REPORTS / 'opt-in-successor-v2-registration.json').read_text())
    while True:
        verified, pending = 0, False
        for arm in registration['arms']:
            folder = root / arm['id']
            if (folder / 'verification.json').exists():
                verified += 1
            elif (folder / 'execution.json').exists():
                pending |= json.loads((folder / 'execution.json').read_text())['complete']
        target = f'opt-in-successor-v2-analysis-{verified:02d}-complete.json'
        if verified and not pending and not (study.REPORTS / target).exists():
            try:
                analysis.analyze(argparse.Namespace(name='opt-in-successor-v2', output=target))
            except FileNotFoundError as exc:
                # An independent arm may finalize between the inventory and
                # analysis. Its validation must finish before any score is
                # published. No other failed check is retried or suppressed.
                if Path(exc.filename or '').name != 'verification.json':
                    raise
                if (study.REPORTS / target).exists():
                    raise
                print(json.dumps({'event': 'analysis_waits_for_new_verification'}), flush=True)
        if verified == len(registration['arms']):
            return
        time.sleep(30)


def main():
    plan = json.loads((study.REPORTS / PLAN).read_text())
    if plan['wrapper_sha256'] != file_sha(Path(__file__)):
        raise RuntimeError('Registered analysis wrapper differs')
    if file_sha(study.ROOT / 'benchmarks/diagnostics/analyze_opt_in_successor.py') != plan['original_analyzer_sha256']:
        raise RuntimeError('Original statistical implementation differs')
    proof = json.loads((study.REPORTS / plan['parity_artifact']).read_text())
    if not proof['passed'] or sha(proof) != plan['parity_sha256']:
        raise RuntimeError('Exact statistical parity is required')
    install()
    mode = sys.argv.pop(1)
    if mode == 'watch':
        watch()
    elif mode == 'analyze':
        analysis.main()
    elif mode == 'select':
        # The original plan, selector and gates stay byte-identical. Its
        # analysis function reads the same now-bounded summarize global.
        from benchmarks.diagnostics import opt_in_select_combination
        opt_in_select_combination.main()
    else:
        raise ValueError('Choose watch, analyze or select')


if __name__ == '__main__':
    main()
