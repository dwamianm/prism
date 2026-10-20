"""Synthetic fixtures only; fake custody/provider evidence is labeled as such."""

from dataclasses import replace
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from prme.privacy.admission import _fields
from prme.privacy.capabilities import AdmissionContent, OperationBudget
from prme.privacy.local_analyzer import LocalAnalyzer
from prme.privacy.policy import PrivacyDenied, snapshot
from prme.privacy.sanitizer import LocalSanitizer
from tests.privacy_fakes import make_runtime, policy_for


@pytest.fixture(scope="module", name="analyzer")
def local_analyzer_fixture():
    local = LocalAnalyzer()
    yield local
    local.close()


class ScopedTestKeys:
    """Accepted boundary test double. Native custody integration is PV-03."""

    def __init__(self):
        self.keys = {}
        self.calls = []

    def provision(self, policy, epoch):
        self.keys[(policy.project_id, policy.owner_id, policy.scope.value, epoch)] = (os.urandom(32), uuid4())

    async def key_for(self, project_id, owner_id, scope, epoch):
        boundary = (project_id, owner_id, scope, epoch)
        self.calls.append(boundary)
        if boundary not in self.keys:
            raise PrivacyDenied("privacy_capability_unavailable")
        key, version = self.keys[boundary]
        closed = False

        def use(callback):
            if closed:
                raise PrivacyDenied("privacy_capability_unavailable")
            return callback(key)

        def close():
            nonlocal closed
            closed = True

        return SimpleNamespace(project_id=project_id, owner_id=owner_id, scope=scope,
                               epoch=epoch, key_version=version, purpose="pseudonym", use=use, close=close)


class Rules:
    def __init__(self, rules):
        self.rules = rules

    async def resolve(self, snapshot, references):
        return self.rules


class Schemas:
    def __init__(self, schema):
        self.schema = schema

    async def resolve(self, snapshot, reference):
        return self.schema


def setup(analyzer, *, sanitizer_options=None, policy_options=None):
    runtime = make_runtime()
    policy = policy_for(runtime, **(policy_options or {}))
    sanitizer = LocalSanitizer(analyzer=analyzer, **(sanitizer_options or {}))
    runtime = replace(runtime, sanitizer=sanitizer)
    saved = snapshot(policy, None, runtime.clock())
    return runtime, policy, saved


async def admit(sanitizer, saved, text, *, metadata=None, budget=None, mode="ingress", source=None,
                raw_fields=None, **kwargs):
    return await sanitizer.admit(AdmissionContent(
        fields_json=raw_fields if raw_fields is not None else _fields(text, metadata, **kwargs),
        snapshot=saved, mode=mode, budget=budget or OperationBudget(uuid4()),
        issued_tokens=source.issued_tokens if source else (),
        pseudonym_epoch=source.pseudonym_epoch if source else None, source=source,
        source_role=source.source_role if source else "user", source_time=source.source_time if source else None,
    ))
