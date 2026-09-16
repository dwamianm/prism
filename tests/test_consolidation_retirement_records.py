"""Retirement records preserve legacy metadata rather than silently nulling it."""
import math

import pytest

from prme import MemoryEngine
from prme.organizer.consolidation import consolidate_cluster, forget_consolidated
from prme.storage.consolidation_retirement import read_record
from tests.test_consolidation_safety import _cluster
from tests.test_consolidation_retirement_atomic import _records
from tests.test_durable_ingestion import config, user  # noqa: F401


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
async def test_retirement_preserves_nonfinite_legacy_metadata(config, user, value):  # noqa: F811
    if config.database_url:
        pytest.skip('Legacy DuckDB JSON non-finite values; PostgreSQL JSONB rejects their storage')
    async with MemoryEngine.open(config) as engine:
        cluster, nodes = await _cluster(engine, user)
        await engine._graph_store.update_node(str(nodes[0].id), metadata={'legacy': value})
        summary = await consolidate_cluster(engine, cluster)
        cluster.member_ids = [str(nodes[0].id)]
        assert await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0) == 1
        record = read_record((await _records(engine, user))[0])
        for node in [record.before, record.after]:
            actual = node.metadata['legacy']
            if math.isnan(value):
                assert math.isnan(actual)
            else:
                assert actual == value
