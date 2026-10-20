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


async def logical_dump(engine, path):
    """Dump all managed logical rows in this test-owned schema, not an export API.

    Native PostgreSQL server WAL/base files and external backup archives are not
    visible to this engine harness. This dump makes no physical erasure claim.
    """
    import json
    import re

    repository = engine._privacy.repository
    tables = await repository._query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=current_schema() AND table_type='BASE TABLE' ORDER BY table_name",
        (),
    )
    lines = []
    for row in tables:
        name = row["table_name"] if isinstance(row, dict) else row[0]
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", name)
        values = await repository._query(f'SELECT * FROM "{name}"', ())
        lines.append(json.dumps({"table": name, "rows": values}, default=str))
    assert any('"table": "operations"' in line for line in lines)
    path.write_text("\n".join(lines))
    return path.read_bytes()
