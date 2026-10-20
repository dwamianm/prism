"""Explicit PV-02 component doubles, never production protection evidence."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest

from prme import PRMEConfig
from prme.privacy import MemoryRetention, PrivacyDenied, PrivacyPolicy, PrivacyRuntime
from prme.privacy.capabilities import ProtectedPayload, RetryCommitment, SanitizedContent


@pytest.fixture(autouse=True)
async def isolated_privacy_postgres(request, monkeypatch):
    """Keep protected component records out of other tests' operator store."""
    if getattr(request.node, "callspec", None) is None or request.node.callspec.params.get("layout") != "postgres":
        yield
        return
    url = os.getenv("PRME_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated PostgreSQL not configured")
    import asyncpg
    conn = await asyncpg.connect(url)
    schema = "privacy_component_" + uuid4().hex
    created = False
    try:
        # Shared extension types must outlive this disposable schema. The
        # schema itself has no public-table fallback, including on reopen.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        parts = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "search_path"]
        query.append(("search_path", schema))
        monkeypatch.setenv("PRME_TEST_DATABASE_URL", urlunsplit(parts._replace(query=urlencode(query))))
        yield
    finally:
        if created:
            await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


class FakeClock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now


class FakeAuthority:
    def __init__(self):
        self.denied = set()
        self.revisions = {}
        self.calls = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def guard(self, snapshot, effect):
        async with self._lock:
            self.calls.append((snapshot, effect))
            if effect in self.denied or any(ref.revision < self.revisions.get(ref.policy_id, 1) for ref in snapshot.references):
                raise PrivacyDenied("privacy_stale_policy")
            yield


class FakeSanitizer:
    def __init__(self):
        self.calls = []
        self.replace_text = None
        self.bad_generated = False

    async def admit(self, request):
        self.calls.append(request)
        if "[reject]" in request.fields_json:
            raise PrivacyDenied()
        fields = request.fields_json
        if self.replace_text:
            fields = fields.replace(*self.replace_text)
        return SanitizedContent(fields_json=fields, source_id=uuid4(), representation_id=uuid4(),
            private_checksum=hashlib.sha256(fields.encode()).hexdigest(), sanitizer_version="prme-sanitizer-v1",
            registry_version="prme-recognizers-v1", detector_versions=("component-double",),
            unambiguous="[ambiguous]" not in fields,
            representation_role="admitted_memory" if request.mode == "generated_output" else "sanitized_source")


class FakeProtector:
    """Reversible encoding to inspect storage effects, explicitly not encryption."""
    def __init__(self):
        self.calls = []
        self.provider_id, self.key_id, self.key_version = uuid4(), uuid4(), uuid4()

    async def ready(self, snapshot, purposes):
        self.calls.append(("ready", purposes))

    @asynccontextmanager
    async def writer_guard(self, payloads):
        yield

    async def seal(self, plaintext, context, *, original_snapshot):
        assert context.policy_id == original_snapshot.policy.policy_id
        self.calls.append(("seal", context.purpose))
        return ProtectedPayload(context=context, envelope=base64.b64encode(plaintext).decode(),
            provider_id=self.provider_id, key_id=self.key_id, key_version=self.key_version)

    async def open(self, payload, access, *, original_snapshot, guard):
        async with self.open_held(payload, access, original_snapshot=original_snapshot, guard=guard) as value:
            return value

    @asynccontextmanager
    async def open_held(self, payload, access, *, original_snapshot, guard):
        assert payload.context.policy_id == original_snapshot.policy.policy_id
        async with guard():
            self.calls.append(("open", payload.context.purpose))
            assert payload.context.purpose == "processing"
            assert payload.context.event_id in access.claimed_event_ids
            yield base64.b64decode(payload.envelope)

    @asynccontextmanager
    async def unwrap_processing_key_held(self, payload, access, *, original_snapshot, guard):
        raise PrivacyDenied("privacy_capability_unavailable")
        yield  # This storage-effect double never supplies key material.

    @asynccontextmanager
    async def hold_evidence_access(self, selections, access, *, guard):
        raise PrivacyDenied("privacy_capability_unavailable")
        yield  # A storage-effect double cannot authorize source disclosure.


class FakeEmbedding:
    model_name = "privacy-component-embedding"
    model_version = "1"
    dimension = 384

    def __init__(self):
        self.sends = []
        self.after_send = None

    async def embed(self, texts):
        return [[float(i % 3) / 3 for i in range(self.dimension)] for _ in texts]

    async def encode(self, texts, snapshot, *, guard):
        async with guard():
            self.sends.append(texts)
            vectors = await self.embed(texts)
        if self.after_send:
            await self.after_send()
        return vectors


class FakeExtractor:
    provider_name = "privacy-component-extractor"

    def __init__(self):
        self.sends = []
        self.fail = False
        self.pause = None
        self.result = {"entities": [], "facts": [{"subject": "Alex", "predicate": "likes", "object": "tea",
                       "polarity": "negative", "condition": "unless it is cold", "epistemic_type": "conditional"}],
                       "relationships": [], "summary": None}

    async def extract(self, content, snapshot, *, guard):
        if self.pause:
            await self.pause()
        async with guard():
            self.sends.append(content.fields_json)
            if self.fail:
                raise RuntimeError("private provider response must never escape")
            return self.result


class FakeCommitter:
    def __init__(self):
        self.keys = {}

    async def commit(self, request, context):
        key_version = uuid4()
        key = os.urandom(32)
        self.keys[key_version] = key
        value = hmac.new(key, context.model_dump_json().encode() + request, "sha256").hexdigest()
        return RetryCommitment(context=context, key_version=key_version, retention_ref=uuid4(), value=value)

    async def verify(self, request, commitment):
        key = self.keys.get(commitment.key_version)
        if key is None:
            raise PrivacyDenied("privacy_retry_unavailable")
        value = hmac.new(key, commitment.context.model_dump_json().encode() + request, "sha256").hexdigest()
        return hmac.compare_digest(value, commitment.value)


def make_runtime():
    return PrivacyRuntime(project_id=uuid4(), authority=FakeAuthority(), sanitizer=FakeSanitizer(),
                          protector=FakeProtector(), embedder=FakeEmbedding(), extractor=FakeExtractor(),
                          retry_committer=FakeCommitter(), clock=FakeClock())


def policy_for(runtime, *, owner=None, **changes):
    values = dict(policy_id=uuid4(), revision=1, project_id=runtime.project_id, owner_id=owner or uuid4(),
                  capture="none", evidence_use="diagnostics_only", language="en",
                  memory_retention=MemoryRetention(mode="until_deleted"))
    values.update(changes)
    return PrivacyPolicy(**values)


def config_for(path, *, layout="directory", policy=None):
    kwargs = dict(db_path=str(path / "memory.duckdb"), vector_path=str(path / "vectors.usearch"),
                  lexical_path=str(path / "lexical"), privacy_policy=policy, extraction_batch_target_tokens=0,
                  enable_mention_links=False, enable_ingest_claim_fixes=False, enable_owner_name_carry=False,
                  enable_owner_group_links=False)
    if layout == "pack":
        kwargs["pack_path"] = str(path / "memory.prme")
    if layout == "postgres":
        kwargs["database_url"] = os.environ["PRME_TEST_DATABASE_URL"]
    return PRMEConfig(**kwargs)


async def open_engine(path, *, runtime=None, policy=None, layout="directory"):
    from prme import MemoryEngine
    runtime = runtime or make_runtime()
    policy = policy or policy_for(runtime)
    config = config_for(path, layout=layout, policy=policy)
    engine = await MemoryEngine.create(config, embedding_provider=runtime.embedder, privacy_runtime=runtime)
    return engine, runtime, policy
