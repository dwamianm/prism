"""PV-05 component harness: real crypto, sanitizer, SQL, claims and publication.

OS get/set/delete and current-policy authority are explicit inherited fixtures.
These checks do not prove PV-06 erasure or the full frozen integration cases.
"""

from contextlib import contextmanager
from datetime import timedelta

from prme.privacy.local_keys import public_registry
from tests.privacy_source_support import opened
from tests.privacy_fakes import isolated_privacy_postgres  # noqa: F401
from tests.privacy_sanitizer_support import local_analyzer_fixture  # noqa: F401
from tests.test_privacy_keys import native  # noqa: F401

LAYOUTS = ["directory", "pack", "postgres"]
MEMORY = "Mara likes tea."
SOURCE_ONLY = "SOURCE_ONLY_COPIED_JOURNAL_324"
SOURCE = f"{MEMORY} {SOURCE_ONLY}."


async def journal_engine(path, layout, analyzer, **policy_options):
    values = await opened(path, layout, analyzer, policy_options=policy_options)
    engine, runtime, *_ = values
    runtime.extractor.result = {
        "entities": [{"name": "Mara", "entity_type": "person"}],
        "facts": [
            {
                "subject": "Mara",
                "predicate": "likes",
                "object": "tea",
                "evidence_quote": SOURCE,
                "polarity": "positive",
            }
        ],
        "relationships": [],
        "summary": None,
    }
    return values


@contextmanager
def processing_keys(runtime, custody, processing):
    with custody.unlocked(
        (processing.ref,), expires_at=runtime.clock() + timedelta(minutes=2)
    ) as private:
        runtime.protector.registry = public_registry(
            custody.path, private_wrapper=private
        )
        try:
            yield
        finally:
            runtime.protector.registry = public_registry(custody.path)


async def operation_bytes(engine):
    rows = await engine._privacy.repository._query("SELECT payload FROM operations", ())
    return repr(rows).encode()
