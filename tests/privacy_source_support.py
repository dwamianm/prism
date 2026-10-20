"""Real storage/sanitizer/crypto with an explicit fixture policy authority.

Synthetic embedding/extraction responses are external model boundaries. The
native OS adapter and PolicyAuthority remain named fixtures; PV-06 owns the
actual current-ledger/fence gate. These runs cannot close the full product case.
"""
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from prme.privacy.sanitizer import LocalSanitizer
from tests.privacy_fakes import isolated_privacy_postgres as isolated_privacy_postgres
from tests.privacy_sanitizer_support import local_analyzer_fixture as local_analyzer_fixture
from tests.test_privacy_keys import native as native
from tests.test_privacy_ciphertext_storage import real_engine

FIXTURE = json.loads((Path(__file__).parent / "fixtures/privacy/source-separation-v1.json").read_text())
SOURCE = FIXTURE["source_only_marker"]
MEMORY = FIXTURE["admitted_marker"]
PROHIBITED = FIXTURE["prohibited_marker"]


@pytest.fixture(params=["directory", "pack", "postgres"], ids=["duckdb", "duckdb_single_file", "postgres"], name="layout")
def backend_layout(request):
    if request.param == "postgres" and not os.getenv("PRME_TEST_DATABASE_URL"):
        pytest.skip("dedicated PostgreSQL required; this is not acceptance success")
    return request.param


async def opened(path, layout, analyzer, *, policy_options=None):
    engine, runtime, policy, custody, evidence, processing = await real_engine(path, layout)
    # Setup precedes admission; preserve actual crypto/registry/backend objects.
    policy = policy.model_copy(update=policy_options or {})
    runtime = replace(runtime, sanitizer=LocalSanitizer(analyzer=analyzer))
    engine._privacy.runtime = runtime
    engine._privacy.default = policy
    engine._config = engine._config.model_copy(update={"privacy_policy": policy})
    return engine, runtime, policy, custody, evidence, processing


async def store_pair(engine, owner, *, session_id=None):
    return await engine.store(
        f"{MEMORY}. {SOURCE}. Contact: {PROHIBITED}.",
        retrieval_content=MEMORY, user_id=owner, session_id=session_id,
        metadata={"private_source": SOURCE},
    )


def assert_no_source(value):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    assert SOURCE not in text and PROHIBITED not in text
