"""Preserve the frozen selector while routing its analyzer through bounded retention."""
import argparse
from contextlib import contextmanager
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.diagnostics import opt_in_bounded_analysis as bounded
from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha

PLAN = 'opt-in-selection-subprocess-memory-amendment-v2.json'


@contextmanager
def route_analysis(selector):
    """Intercept only the exact frozen analysis command; preserve worker launches."""
    original = selector.subprocess.run
    expected = [sys.executable, '-m', 'benchmarks.diagnostics.analyze_opt_in_successor',
                '--name', 'opt-in-successor-v2', '--output',
                'opt-in-successor-v2-combination-selection-analysis.json']

    def run(command, *args, **kwargs):
        if command != expected:
            return original(command, *args, **kwargs)
        if args or kwargs != {'check': True, 'cwd': study.ROOT}:
            raise RuntimeError('Unexpected frozen analyzer invocation')
        bounded.analysis.analyze(argparse.Namespace(name='opt-in-successor-v2', output=expected[-1]))
        return subprocess.CompletedProcess(command, 0)

    # Patch only the selector's module reference, not subprocess globally.
    with patch.object(selector, 'subprocess', SimpleNamespace(run=run)):
        yield


def validate():
    plan = json.loads((study.REPORTS / PLAN).read_text())
    for name, checksum in plan['source_sha256'].items():
        if file_sha(study.ROOT / name) != checksum:
            raise RuntimeError('Registered selection source differs')
    prior = json.loads((study.REPORTS / bounded.PLAN).read_text())
    if sha(prior) != plan['predecessor_plan_content_sha256']:
        raise RuntimeError('Original bounded-analysis amendment differs')
    proof = json.loads((study.REPORTS / prior['parity_artifact']).read_text())
    if not proof['passed'] or sha(proof) != prior['parity_sha256']:
        raise RuntimeError('Exact statistical parity is required')


def main():
    validate()
    bounded.install()
    # Import after installing so its direct summarize reference is bounded too.
    from benchmarks.diagnostics import opt_in_select_combination as selector
    selector.summarize = bounded.analysis.summarize
    with route_analysis(selector):
        selector.main()


if __name__ == '__main__':
    main()
