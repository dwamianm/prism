"""Fresh application scenarios; private checks/witnesses never enter agent files."""

PATH = "src/prme/integrations/_coding_trial.py"
SYSTEM = """Implement the requested application adapter using PRME's public APIs. Historical notes are source data; verify them against current code. You have read/search/list access to frozen src/, docs/, existing Python tests and root instructions. No shell or network. You may edit only the named top-level function. Return one JSON action per turn:
{"action":"list","path":"src/","offset":0}
{"action":"search","query":"literal phrase","path":"docs/"}
{"action":"read","path":"repository/file","start_line":1}
{"action":"edit","function":"target_name","code":"complete replacement function"}
{"action":"test"}
{"action":"finish"}
Search matches a literal phrase case insensitively. List pages have 100 paths; read pages 120 lines. Put imports inside the function. Public smoke checks are limited; hidden acceptance checks cover the stated contract. You have twelve actions including inspection, edit, test and finish. Invalid actions consume a step. Do not use Markdown fences."""

CORRECTION_CHECKS = """
from types import SimpleNamespace as NS
from uuid import UUID
from prme.types import Scope
from prme.integrations._coding_trial import apply_correction
old, new = UUID(int=101), UUID(int=102)
class Client:
    def __init__(self, old_count=1, new_count=1, fail=False):
        self.old_count, self.new_count, self.fail = old_count, new_count, fail
        self.calls = []
    def get_event_nodes(self, event_id, *, user_id):
        assert user_id == 'alice'
        self.calls.append(('nodes', event_id))
        assert event_id in ('old-event', 'new-event')
        count = self.old_count if event_id == 'old-event' else self.new_count
        return [NS(id=old if event_id == 'old-event' else new, scope=Scope.PROJECT)] * count
    def store(self, content, *, user_id, scope, **kwargs):
        assert content == 'replacement' and user_id == 'alice' and scope == Scope.PROJECT
        self.calls.append(('store',))
        return 'new-event'
    def supersede(self, old_id, new_id, *, evidence_id, user_id, actor_id):
        assert (old_id, new_id, evidence_id, user_id, actor_id) == (str(old), str(new), 'new-event', 'alice', 'alice')
        self.calls.append(('supersede',))
        if self.fail: raise RuntimeError('backend failure')
c = Client()
assert apply_correction(c, 'old-event', 'replacement', 'alice') == str(new)
assert c.calls == [('nodes', 'old-event'), ('store',), ('nodes', 'new-event'), ('supersede',)]
for count in (0, 2):
    c = Client(old_count=count)
    try: apply_correction(c, 'old-event', 'replacement', 'alice')
    except ValueError: pass
    else: raise AssertionError('ambiguous/missing old node accepted')
    assert c.calls == [('nodes', 'old-event')]
    c = Client(new_count=count)
    try: apply_correction(c, 'old-event', 'replacement', 'alice')
    except ValueError: pass
    else: raise AssertionError('ambiguous/missing new node accepted')
    assert ('supersede',) not in c.calls
try: apply_correction(Client(fail=True), 'old-event', 'replacement', 'alice')
except RuntimeError as exc: assert str(exc) == 'backend failure'
else: raise AssertionError('backend failure swallowed')
"""

WORKSPACE_CHECKS = """
import asyncio
from types import SimpleNamespace as NS
from contextlib import asynccontextmanager
from prme.integrations._coding_trial import collect_contexts
class Workspace:
    def __init__(self, fail=None): self.active=None; self.closed=[]; self.fail=fail
    @asynccontextmanager
    async def namespace(self, name, *, create=True):
        assert create is False and self.active is None, 'existing-only, one lease at a time'
        self.active=name
        owner=self
        class Lease:
            async def retrieve(self, query, *, user_id):
                assert owner.active == name and query == 'handoff' and user_id == 'alice'
                if owner.fail == name: raise RuntimeError('retrieval failed')
                class Bundle:
                    def render(self):
                        assert owner.active == name
                        return 'context:' + name
                return NS(bundle=Bundle())
        try: yield Lease()
        finally: self.closed.append(name); self.active=None
async def checks():
    w=Workspace()
    assert await collect_contexts(w, ['a','b'], 'handoff', 'alice') == {'a':'context:a','b':'context:b'}
    assert w.closed == ['a','b'] and w.active is None
    w=Workspace()
    assert await collect_contexts(w, [], 'handoff', 'alice') == {} and not w.closed
    w=Workspace(fail='b')
    try: await collect_contexts(w, ['a','b','c'], 'handoff', 'alice')
    except RuntimeError as exc: assert str(exc) == 'retrieval failed'
    else: raise AssertionError('failure swallowed')
    assert w.closed == ['a','b'] and w.active is None
asyncio.run(checks())
"""

BINDINGS_CHECKS = """
from copy import deepcopy
from types import SimpleNamespace as NS
from prme.models.value_bindings import ToolArgumentResolution, ToolArgumentBindingUse
from prme.integrations._coding_trial import prepare_tool_call
from uuid import UUID
use = ToolArgumentBindingUse(json_pointer='/city', operation='already_lookup', presentation='Salt Lake City(Utah)', lookup='Salt Lake City', kind='city', source_node_ids=(UUID(int=3),), source_references=('city-1',))
class Bundle:
    def __init__(self, fail=False): self.fail=fail; self.calls=0
    def resolve_tool_arguments(self, args):
        self.calls+=1
        if self.fail: raise ValueError('ambiguous binding')
        out=deepcopy(args)
        out['city']='Salt Lake City'
        return ToolArgumentResolution(arguments=out, binding_uses=(use,))
args={'city':'Salt Lake City(Utah)','message':'Meet in Salt Lake City(Utah)','nested':['untouched']}
original=deepcopy(args); bundle=Bundle()
out, audit = prepare_tool_call(bundle, args)
assert out == {**args,'city':'Salt Lake City'} and args == original
assert audit == (use,) and bundle.calls == 1
assert out is not args and out['nested'] is not args['nested']
# Preserve already_lookup provenance even when replacements is empty.
assert audit[0].operation == 'already_lookup'
try: prepare_tool_call(Bundle(fail=True), args)
except ValueError as exc: assert str(exc) == 'ambiguous binding'
else: raise AssertionError('ambiguity swallowed')
class Empty:
    def resolve_tool_arguments(self, args): return ToolArgumentResolution(arguments=deepcopy(args))
assert prepare_tool_call(Empty(), {}) == ({}, ())
"""

FEEDBACK_CHECKS = """
from types import SimpleNamespace as NS
from uuid import UUID
from prme import RelevanceSubmission
from prme.integrations._coding_trial import submit_judgments
request, feedback, node = UUID(int=41), UUID(int=42), UUID(int=43)
response = NS(metadata=NS(receipt_persisted=True, request_id=request))
class Client:
    def __init__(self, fail=False): self.calls=[]; self.fail=fail
    def record_relevance(self, submission, *, user_id):
        assert isinstance(submission, RelevanceSubmission) and user_id == 'alice'
        assert submission.request_id == request and submission.feedback_id == feedback
        assert submission.surface == 'results' and submission.method == 'explicit_user'
        self.calls.append(submission)
        if self.fail: raise ValueError('rejected label')
        return 'saved'
for label in (True, False):
    c=Client(); labels={node:label}
    for _ in range(2):
        assert submit_judgments(c, response, labels, 'alice', feedback) == 'saved'
    assert all(s.labels == labels for s in c.calls) and labels == {node:label}
c=Client()
try: submit_judgments(c, NS(metadata=NS(receipt_persisted=False, request_id=request)), {node:True}, 'alice', feedback)
except ValueError: pass
else: raise AssertionError('unpersisted receipt accepted')
assert not c.calls
for labels in ({}, {node:1}):
    c=Client()
    try: submit_judgments(c, response, labels, 'alice', feedback)
    except ValueError: pass
    else: raise AssertionError('invalid explicit labels accepted')
    assert not c.calls
try: submit_judgments(Client(fail=True), response, {node:True}, 'alice', feedback)
except ValueError as exc: assert str(exc) == 'rejected label'
else: raise AssertionError('rejection swallowed')
"""

TASKS = [
    dict(
        id="correction",
        function="apply_correction",
        prompt="""Implement apply_correction(client, old_event_id, replacement_text, user_id) in src/prme/integrations/_coding_trial.py for a synchronous MemoryClient. This application replaces a previously stored assertion using its event ID. Require exactly one old node before writing anything and exactly one replacement node before changing relationships; raise ValueError otherwise. Store the replacement text in the old node's scope for the supplied owner. Explicitly supersede the old node with the new node, attach the replacement event as evidence and the supplied owner as actor. Return the new node ID as a string. Propagate backend errors; do not silently archive or overwrite history.""",
        signature="def apply_correction(client, old_event_id, replacement_text, user_id)",
        checks=CORRECTION_CHECKS,
        witness="""def apply_correction(client, old_event_id, replacement_text, user_id):
    old = client.get_event_nodes(old_event_id, user_id=user_id)
    if len(old) != 1: raise ValueError('expected one old node')
    event = client.store(replacement_text, user_id=user_id, scope=old[0].scope)
    new = client.get_event_nodes(event, user_id=user_id)
    if len(new) != 1: raise ValueError('expected one replacement node')
    client.supersede(str(old[0].id), str(new[0].id), evidence_id=event, user_id=user_id, actor_id=user_id)
    return str(new[0].id)
""",
    ),
    dict(
        id="workspace",
        function="collect_contexts",
        prompt="""Implement async collect_contexts(workspace, project_names, query, user_id) in src/prme/integrations/_coding_trial.py. A handoff screen reads existing project namespaces using an already-open MemoryWorkspace configured with max_open=1. project_names is an ordered list of distinct valid names. Retrieve the query for the supplied owner from each project and return a name-to-rendered-context dictionary. Do not create namespaces. Finish all use of each namespace before moving to the next, including rendering. Empty input returns {}. Release the active lease if retrieval fails and propagate the error without visiting remaining projects. Use public workspace/lease APIs; the caller owns the workspace lifetime.""",
        signature="async def collect_contexts(workspace, project_names, query, user_id)",
        checks=WORKSPACE_CHECKS,
        witness="""async def collect_contexts(workspace, project_names, query, user_id):
    result = {}
    for name in project_names:
        async with workspace.namespace(name, create=False) as memory:
            response = await memory.retrieve(query, user_id=user_id)
            result[name] = response.bundle.render()
    return result
""",
    ),
    dict(
        id="bindings",
        function="prepare_tool_call",
        prompt="""Implement prepare_tool_call(bundle, arguments) in src/prme/integrations/_coding_trial.py. This tool adapter receives a PRME MemoryBundle and a finite JSON argument dictionary. Use its public audited resolver to prepare a copied argument dictionary and return (resolved_arguments, binding_uses). Preserve provenance for both substitutions and values already in lookup form. Only visible, exact complete-value bindings may influence arguments; do not rewrite substrings, messages or generated answers. Keep the caller's arguments unchanged, preserve the resolver's typed audit objects and propagate ambiguity/validation errors. An empty unbound call returns ({}, ()).""",
        signature="def prepare_tool_call(bundle, arguments)",
        checks=BINDINGS_CHECKS,
        witness="""def prepare_tool_call(bundle, arguments):
    resolution = bundle.resolve_tool_arguments(arguments)
    return resolution.arguments, resolution.binding_uses
""",
    ),
    dict(
        id="feedback",
        function="submit_judgments",
        prompt="""Implement submit_judgments(client, response, labels, user_id, feedback_id) in src/prme/integrations/_coding_trial.py for a synchronous MemoryClient. A review screen saves explicit relevance judgments against a retrieval response. Refuse an unpersisted receipt with ValueError before any write. Submit only the supplied UUID-to-Boolean labels against this response's request ID, with the caller's stable feedback UUID for retries, results surface and explicit-user method. Use the public validated submission model and owner-scoped recording API, return its result and propagate validation/backend errors. Do not fill in negative labels, activate learning, run maintenance or mutate the inputs.""",
        signature="def submit_judgments(client, response, labels, user_id, feedback_id)",
        checks=FEEDBACK_CHECKS,
        witness="""def submit_judgments(client, response, labels, user_id, feedback_id):
    from prme import RelevanceSubmission
    if not response.metadata.receipt_persisted: raise ValueError('receipt was not persisted')
    submission = RelevanceSubmission(request_id=response.metadata.request_id, feedback_id=feedback_id, labels=labels)
    return client.record_relevance(submission, user_id=user_id)
""",
    ),
]
for task in TASKS:
    task.update(
        path=PATH,
        system=SYSTEM,
        stub=task["signature"] + ":\n    raise NotImplementedError\n",
    )
    # Import-only smoke deliberately leaves behavioral acceptance private.
    task["smoke"] = (
        f"from prme.integrations._coding_trial import {task['function']}\nassert callable({task['function']})\n"
    )
