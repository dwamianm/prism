import json
from types import SimpleNamespace

import pytest

from benchmarks.diagnostics import opt_in_answer_memory_handoff as handoff
from benchmarks.diagnostics.register_opt_in_interactions import sha
from benchmarks.diagnostics.run_opt_in_interactions import validate_complete


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def signed(value):
    return {**value, 'registration_sha256': sha(value)}


def test_exact_tail_does_not_repeat_preparation(tmp_path):
    path = tmp_path / 'source.py'
    path.write_text("""def answer_followup(reg, rows):
    raise AssertionError('Preparation must not execute')
    for name in names.values():
        calls.append(name)
    return reg['value'], selected
""")
    module = SimpleNamespace(__file__=str(path), calls=[])
    result = handoff.execute_tail(module, {'value': 7}, {'control': 'c', 'candidate': 't'}, 'fixed')
    assert module.calls == ['c', 't']
    assert result == (7, 'fixed')
    original = handoff.tail_digest(path)
    path.write_text(path.read_text().replace("raise AssertionError('Preparation must not execute')", 'unused = 123'))
    assert handoff.tail_digest(path) == original


def test_ambiguous_execution_loop_is_rejected(tmp_path):
    path = tmp_path / 'source.py'
    path.write_text('def answer_followup(reg, rows):\n    for name in names.values():\n        pass\n    for name in names.values():\n        pass\n')
    with pytest.raises(ValueError, match='one registered child execution loop'):
        handoff.tail_nodes(path)


@pytest.fixture
def prepared(tmp_path):
    name = 'authored-source-assay'
    private, reports = tmp_path / 'private', tmp_path / 'reports'
    ids = [f'q{i:04d}' for i in range(500)]
    source_reg = signed({'ordered_question_ids': ids})
    save(reports / f'{name}-registration.json', source_reg)
    outcomes = []
    rows = {'control': [], 'candidate': []}
    for i, qid in enumerate(ids):
        contexts = {'baseline': f'control source {i}', 'rank_envelope': f'candidate source {i}'}
        case_path = private / name / 'cases' / f'{qid}.json'
        save(case_path, {'arms': {key: {'context': text} for key, text in contexts.items()}})
        outcomes.append({'question_id': qid, 'status': 'complete', 'case_sha256': handoff.digest(case_path)})
        for role, key in (('control', 'baseline'), ('candidate', 'rank_envelope')):
            text = contexts[key]
            rows[role].append({'question_id': qid, 'context': text,
                              'context_sha256': handoff.hashlib.sha256(text.encode()).hexdigest()})
    execution_path = private / name / 'execution.json'
    save(execution_path, {'complete': True, 'rows': outcomes})
    save(reports / f'{name}-source-result.json', {'complete': True, 'advance_to_answers': True,
        'registration_sha256': source_reg['registration_sha256'], 'execution_sha256': handoff.digest(execution_path)})
    for role in rows:
        child_name = f'{name}-reader-{role}'
        path = private / child_name / 'contexts.json'
        save(path, rows[role])
        save(reports / f'{child_name}-registration.json', signed({
            'source_assay_registration_sha256': source_reg['registration_sha256'],
            'ordered_question_ids': ids, 'role': role, 'prepared_sha256': handoff.digest(path)}))
    return SimpleNamespace(NAME=name, sha=sha, study=SimpleNamespace(PRIVATE=private, REPORTS=reports,
        old=SimpleNamespace(validate_complete=validate_complete)))


def test_authenticates_every_case_and_prepared_context(prepared):
    reg, names, selected, identity = handoff.validate_prepared(prepared)
    assert len(reg['ordered_question_ids']) == 500
    assert list(names) == ['control', 'candidate']
    assert selected is None and len(identity) == 6


@pytest.mark.parametrize('mutation,match', [
    ('started', 'already started'), ('source', 'source case changed'),
    ('rebound_context', 'Prepared context differs'), ('failed_source', 'did not authorize')])
def test_refuses_started_changed_or_failed_stages(prepared, mutation, match):
    private, reports, name = prepared.study.PRIVATE, prepared.study.REPORTS, prepared.NAME
    if mutation == 'started':
        (private / f'{name}-reader-control' / 'q0000').mkdir()
    elif mutation == 'source':
        path = private / name / 'cases/q0000.json'
        path.write_text(path.read_text() + ' ')
    elif mutation == 'failed_source':
        path = reports / f'{name}-source-result.json'
        value = json.loads(path.read_text())
        save(path, {**value, 'complete': False})
    else:
        path = private / f'{name}-reader-candidate/contexts.json'
        value = json.loads(path.read_text())
        value[0]['context'] = 'changed source'
        value[0]['context_sha256'] = handoff.hashlib.sha256(b'changed source').hexdigest()
        save(path, value)
        registration = reports / f'{name}-reader-candidate-registration.json'
        child = json.loads(registration.read_text())
        child.pop('registration_sha256')
        child['prepared_sha256'] = handoff.digest(path)
        save(registration, signed(child))
    with pytest.raises(RuntimeError, match=match):
        handoff.validate_prepared(prepared)
