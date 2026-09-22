import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.diagnostics.opt_in_answer_lane import check_handoff, lane_available


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepared(tmp_path):
    folder = tmp_path / 'baseline'
    folder.mkdir()
    (folder / 'execution.json').write_text(json.dumps({'complete': True}))
    (folder / 'verification.json').write_text(json.dumps({'complete': True, 'cases': 500,
        'execution_sha256': digest(folder / 'execution.json')}))
    return {'released_lane_pid': 101, 'released_lane_arms': ['baseline'],
            'matrix_root': str(tmp_path), 'public_root': str(tmp_path)}


def test_live_lane_owner_blocks_even_verified_arms(tmp_path):
    assert not lane_available(prepared(tmp_path), 'rank', digest, lambda _: True)


def test_dead_owner_and_verified_full_arm_release_lane(tmp_path):
    assert lane_available(prepared(tmp_path), 'rank', digest, lambda _: False)


def test_missing_or_tampered_verification_never_releases_lane(tmp_path):
    plan = prepared(tmp_path)
    path = tmp_path / 'baseline/verification.json'
    saved = json.loads(path.read_text())
    path.unlink()
    assert not lane_available(plan, 'rank', digest, lambda _: False)
    saved['execution_sha256'] = 'different'
    path.write_text(json.dumps(saved))
    with pytest.raises(RuntimeError, match='identity'):
        lane_available(plan, 'rank', digest, lambda _: False)


def test_failed_closed_arm_releases_capacity_without_a_quality_score(tmp_path):
    plan = prepared(tmp_path)
    (tmp_path / 'baseline/execution.json').write_text(json.dumps({'complete': False}))
    (tmp_path / 'baseline/verification.json').unlink()
    assert lane_available(plan, 'rank', digest, lambda _: False)


def test_marginal_waits_for_rank_exit_and_dead_rank_owner(tmp_path):
    plan = prepared(tmp_path)
    assert not lane_available(plan, 'marginal', digest, lambda _: False)
    (tmp_path / 'rank-answer-lane-v1-exit.json').write_text(json.dumps({
        'execution_tail_settled': True, 'coordinator_pid': 202}))
    assert not lane_available(plan, 'marginal', digest, lambda pid: pid == 202)
    assert lane_available(plan, 'marginal', digest, lambda _: False)


def test_ownership_handoff_binds_plan_contexts_and_idle_owner(tmp_path):
    plan = tmp_path / 'plan.json'
    plan.write_text('{}')
    identity = {'context_sha': 'frozen'}
    handoff = {'lane_plan_sha256': digest(plan), 'kind': 'rank',
        'prepared_identity': identity, 'idle_process_stopped': True,
        'stopped_pid': 303, 'zero_reader_cases_started': True}
    check_handoff(handoff, plan, identity, 'rank', digest, lambda _: False)
    with pytest.raises(RuntimeError, match='remains alive'):
        check_handoff(handoff, plan, identity, 'rank', digest, lambda _: True)
    with pytest.raises(RuntimeError, match='differs'):
        check_handoff(handoff, plan, {'context_sha': 'changed'}, 'rank', digest, lambda _: False)
    handoff['zero_reader_cases_started'] = False
    with pytest.raises(RuntimeError, match='cannot be transferred'):
        check_handoff(handoff, plan, identity, 'rank', digest, lambda _: False)
