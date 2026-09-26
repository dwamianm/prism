"""Frozen authored regressions in real PRME functions, not an untouched benchmark.

Checks never enter model prompts or the memory corpus. Every task must pass
against the pinned original source and fail against its mutation before a run.
"""

TASKS = [
    {
        "id": "metadata-admission",
        "path": "src/prme/storage/metadata.py",
        "function": "snapshot_metadata",
        "prompt": "Repair metadata admission: nested caller metadata can currently lose values or admit non-portable data. Preserve the existing public snapshot_metadata API and repository compatibility contract.",
        "mutation": "def snapshot_metadata(metadata: dict | None) -> dict | None:\n    return json.loads(json.dumps(metadata))\n",
        "smoke": "from prme.storage.metadata import snapshot_metadata\nx = {'nested': [1, None]}\ny = snapshot_metadata(x)\nassert y == x and y is not x\n",
        "checks": """from prme.storage.metadata import snapshot_metadata
assert snapshot_metadata(None) is None
assert snapshot_metadata({'a': (1, 2), 3: 'three'}) == {'a': [1, 2], '3': 'three'}
x = {'a': [{'b': 1}]}
y = snapshot_metadata(x)
x['a'][0]['b'] = 2
assert y['a'][0]['b'] == 1
for value in [float('nan'), float('inf'), float('-inf'), object(), {1, 2}]:
    try:
        snapshot_metadata({'nested': [value]})
    except ValueError as exc:
        assert str(exc) == 'Metadata must contain finite JSON-serializable values'
    else:
        raise AssertionError('invalid metadata accepted')
for value in [{1: 'a', '1': 'b'}, {None: 'a', 'null': 'b'}, {True: 'a', 'true': 'b'}]:
    try:
        snapshot_metadata({'nested': [value]})
    except ValueError as exc:
        assert str(exc) == 'Metadata object keys collide after JSON serialization'
    else:
        raise AssertionError('colliding metadata accepted')
assert snapshot_metadata([{'1': 'a'}, {'1': 'b'}]) == [{'1': 'a'}, {'1': 'b'}]
cycle = {}; cycle['x'] = cycle
try:
    snapshot_metadata(cycle)
except ValueError:
    pass
else:
    raise AssertionError('cycle accepted')
""",
    },
    {
        "id": "scope-validation",
        "path": "src/prme/retrieval/scope.py",
        "function": "normalize_scope",
        "prompt": "Repair normalize_scope: some invalid caller inputs unexpectedly broaden retrieval to every scope. Preserve the public API, accept supported scope forms, and protect a pending request from caller mutation.",
        "mutation": "def normalize_scope(scope: ScopeInput) -> list[Scope] | None:\n    if isinstance(scope, str):\n        return [Scope(scope)]\n    return None\n",
        "smoke": "from prme.retrieval.scope import normalize_scope\nfrom prme.types import Scope\nassert normalize_scope('project') == [Scope.PROJECT]\nassert normalize_scope(None) is None\n",
        "checks": """from prme.retrieval.scope import normalize_scope
from prme.types import Scope
assert normalize_scope(None) is None
assert normalize_scope(Scope.PROJECT) == [Scope.PROJECT]
assert normalize_scope('personal') == [Scope.PERSONAL]
x = [Scope.PROJECT, 'personal']; y = normalize_scope(x); x.clear()
assert y == [Scope.PROJECT, Scope.PERSONAL]
assert normalize_scope(('project', 'project')) == [Scope.PROJECT, Scope.PROJECT]
for value in [[], (), '', 'invalid', ['project', 'invalid'], ['project', None], b'project', bytearray(b'project'), 0, False, {}, {'project'}]:
    try:
        normalize_scope(value)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid scope accepted: ' + repr(value))
""",
    },
    {
        "id": "legacy-snapshot",
        "path": "src/prme/storage/_snapshot_json.py",
        "function": "dumps",
        "prompt": "Repair the journal dumps serializer: legacy metadata containing non-finite floats currently emits invalid JSON. Keep finite historical record bytes unchanged and preserve the legacy values when the existing loads reader reconstructs a snapshot.",
        "mutation": "def dumps(value: dict, *, sort_keys: bool = False) -> str:\n    return json.dumps(value, default=_default, sort_keys=sort_keys)\n",
        "smoke": "from prme.storage import _snapshot_json as s\nassert s.loads(s.dumps({'a': [1, None]})) == {'a': [1, None]}\n",
        "checks": """from prme.storage import _snapshot_json as s
from datetime import datetime, timezone
from uuid import UUID
import json, math
finite = {'z': [1, None, 'NaN', True], 'a': {'x': 1.25}}
for sort_keys in [False, True]:
    assert s.dumps(finite, sort_keys=sort_keys) == json.dumps(finite, default=s._default, allow_nan=False, sort_keys=sort_keys)
value = {'payload': [float('nan'), float('inf'), float('-inf'), None, 'NaN'], 'nested': {'a/b~': float('nan')}, 'ordinary': {'kind': 'nan', 'path': ['x']}, 'date': datetime(2026, 1, 1, tzinfo=timezone.utc), 'id': UUID(int=1)}
raw = s.dumps(value)
def reject(x): raise AssertionError('bare nonfinite constant: ' + x)
encoded = json.loads(raw, parse_constant=reject)
assert encoded['_snapshot_encoding'] == 'prme-special-floats-v1'
assert set(encoded) == {'_snapshot_encoding', 'value', 'nonfinite'}
back = s.loads(raw)
assert math.isnan(back['payload'][0])
assert back['payload'][1:5] == [float('inf'), float('-inf'), None, 'NaN']
assert math.isnan(back['nested']['a/b~'])
assert back['ordinary'] == value['ordinary']
assert back['date'] == value['date'].isoformat() and back['id'] == str(value['id'])
assert s.dumps({'x': float('inf')}, sort_keys=True) == s.dumps({'x': float('inf')}, sort_keys=True)
""",
    },
    {
        "id": "assertion-normalization",
        "path": "src/prme/retrieval/aggregation.py",
        "function": "normalize_assertion_value",
        "prompt": "Repair exact assertion selector normalization: equivalent Unicode, whitespace and predicate separators currently produce inconsistent groups. Preserve the documented distinction between exact normalized matching and semantic equivalence, and keep non-predicate field behavior distinct.",
        "mutation": "def normalize_assertion_value(field_name: AssertionField, value: str) -> str:\n    return value.strip().lower().replace(' ', '_')\n",
        "smoke": "from prme.retrieval.aggregation import normalize_assertion_value as n\nassert n('predicate', 'Raised') == 'raised'\n",
        "checks": """from prme.retrieval.aggregation import normalize_assertion_value as n
assert n('subject', '  Alice   SMITH  ') == 'alice smith'
assert n('subject', 'Straße') == 'strasse'
assert n('object', 'ＡＢＣ') == 'abc'
assert n('object', 'a-b') == 'a-b'
assert n('object', 'x\\ty\\nz') == 'x y z'
assert n('predicate', '  Raised - Total  ') == 'raised_total'
assert n('predicate', 'Raised\\tTotal') == 'raised_total'
assert n('predicate', 'raised_total') == 'raised_total'
assert n('polarity', ' NEGATIVE ') == 'negative'
assert n('predicate', 'fundraised') != n('predicate', 'raised')
assert n('object', '$12') != n('object', '12 USD')
assert n('predicate', 'raised__total') == 'raised__total'
""",
    },
]
