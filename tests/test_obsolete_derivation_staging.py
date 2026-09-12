"""Known isolation gap which must close before abandoned-stage collection."""
import pytest

from prme import MemoryEngine
from prme.models.extraction_work import StaleExtractionClaimError
from tests import test_durable_ingestion
from tests.test_extraction_work import prepare

config = test_durable_ingestion.config
user = test_durable_ingestion.user


class UnexpectedStaging(AssertionError):
    pass


@pytest.mark.xfail(strict=True, raises=UnexpectedStaging, reason='Obsolete workers can still stage external indexes before graph fencing rejects them')
async def test_obsolete_revision_cannot_write_external_staging(config, user):
    if config.backend != 'duckdb':
        pytest.skip('PostgreSQL writes prepared indexes inside the fenced graph transaction')
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        await engine._event_store.extraction_work.fail(claim, error='StaleDerivationPlanError')
        await engine.retry_extraction(str(event.id), user_id=user, replan=True)
        assert engine._conn.execute('SELECT count(*) FROM vector_staging').fetchone()[0] == 0
        with pytest.raises((ValueError, StaleExtractionClaimError)):
            # Model an old worker resuming with the plan it loaded before its
            # lease expired and a replacement revision was queued.
            await engine._pipeline._publish_plan(plan, claim=claim)
        assert await engine.get_event_nodes(str(event.id), user_id=user) == []
        vectors = engine._conn.execute('SELECT count(*) FROM vector_staging').fetchone()[0]
        documents = engine._lexical_index._index.searcher().num_docs
        if vectors or documents:
            raise UnexpectedStaging(f'Obsolete revision wrote {vectors} vectors and {documents} documents')
