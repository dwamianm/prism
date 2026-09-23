import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarks.diagnostics import opt_in_bounded_selection_v2 as wrapper
from benchmarks.diagnostics import opt_in_select_combination as selector


def command():
    return [sys.executable, '-m', 'benchmarks.diagnostics.analyze_opt_in_successor',
            '--name', 'opt-in-successor-v2', '--output',
            'opt-in-successor-v2-combination-selection-analysis.json']


def test_analyzer_executes_in_the_patched_process_and_restores_reference(monkeypatch):
    analyze = Mock()
    monkeypatch.setattr(wrapper.bounded.analysis, 'analyze', analyze)
    original = Mock()
    module = SimpleNamespace(subprocess=SimpleNamespace(run=original))
    saved = module.subprocess
    with wrapper.route_analysis(module):
        result = module.subprocess.run(command(), check=True, cwd=wrapper.study.ROOT)
    assert module.subprocess is saved
    original.assert_not_called()
    assert result.returncode == 0
    assert analyze.call_args.args[0].name == 'opt-in-successor-v2'
    assert analyze.call_args.args[0].output == command()[-1]


def test_authentication_failure_propagates_without_subprocess_retry(monkeypatch):
    analyze = Mock(side_effect=ValueError('artifact checksum differs'))
    monkeypatch.setattr(wrapper.bounded.analysis, 'analyze', analyze)
    original = Mock()
    module = SimpleNamespace(subprocess=SimpleNamespace(run=original))
    with wrapper.route_analysis(module), pytest.raises(ValueError, match='checksum'):
        module.subprocess.run(command(), check=True, cwd=wrapper.study.ROOT)
    original.assert_not_called()
    assert analyze.call_count == 1


def test_worker_invocation_and_failure_result_are_preserved():
    argv = [sys.executable, '-m', 'benchmarks.diagnostics.opt_in_arm_worker', 'run']
    failure = subprocess.CompletedProcess(argv, 19)
    original = Mock(return_value=failure)
    module = SimpleNamespace(subprocess=SimpleNamespace(run=original))
    with wrapper.route_analysis(module):
        assert module.subprocess.run(argv, cwd=wrapper.study.ROOT) is failure
    original.assert_called_once_with(argv, cwd=wrapper.study.ROOT)


def test_changed_analyzer_invocation_is_not_silently_reinterpreted(monkeypatch):
    analyze = Mock()
    monkeypatch.setattr(wrapper.bounded.analysis, 'analyze', analyze)
    module = SimpleNamespace(subprocess=SimpleNamespace(run=Mock()))
    with wrapper.route_analysis(module), pytest.raises(RuntimeError, match='invocation'):
        module.subprocess.run(command(), check=False, cwd=wrapper.study.ROOT)
    analyze.assert_not_called()


def test_failed_required_arm_still_prevents_subset_selection():
    plan = {'individuals': ['good', 'failed']}
    evidence = {'comparisons': {'good': {'changed_contexts': 500, 'difference': .2}}}
    assert selector.select(plan, evidence) is None


def test_terminal_failure_is_settled_without_becoming_success(tmp_path):
    (tmp_path / 'execution.json').write_text('{"complete": false}')
    assert selector.settled(tmp_path)
    (tmp_path / 'execution.json').write_text('{"complete": true}')
    assert not selector.settled(tmp_path)
    (tmp_path / 'verification.json').write_text('{}')
    assert selector.settled(tmp_path)
