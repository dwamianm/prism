"""Synchronous storage retains explicit classification and TTL during recovery."""
from unittest.mock import AsyncMock

import pytest

from prme import EpistemicType, MemoryClient, NodeType, SourceType
from prme.client import config_from_directory
from tests.test_durable_ingestion import MockEmbeddingProvider


@pytest.mark.parametrize(('options', 'expected_ttl'), [({}, 19), ({'ttl_days': None}, None), ({'ttl_days': 43}, 43)])
def test_sync_store_options_survive_pending_index_recovery(tmp_path, monkeypatch, options, expected_ttl):
    monkeypatch.setattr('prme.storage.engine.create_embedding_provider', lambda _: MockEmbeddingProvider())
    config = config_from_directory(str(tmp_path))
    config.organizer.opportunistic_enabled = False
    config.organizer.default_ttl_days['fact'] = 19
    with MemoryClient(config=config) as client:
        with monkeypatch.context() as fault:
            fault.setattr(client._engine._vector_index, 'index', AsyncMock(side_effect=OSError('Synthetic outage')))
            event_id = client.store('The observatory may use cobalt telescopes', user_id='alice',
                node_type=NodeType.FACT, epistemic_type=EpistemicType.HYPOTHETICAL,
                source_type=SourceType.EXTERNAL_DOCUMENT, **options)
        initial = client.get_event_nodes(event_id, user_id='alice')[0]
        assert client.processing_status(event_id, user_id='alice').status == 'pending'
    with MemoryClient(config=config) as client:
        assert client.process_pending(user_id='alice').processed == 1
        node = client.get_event_nodes(event_id, user_id='alice')[0]
        assert node.id == initial.id
        assert node.epistemic_type == EpistemicType.HYPOTHETICAL
        assert node.source_type == SourceType.EXTERNAL_DOCUMENT
        assert node.ttl_days == initial.ttl_days == expected_ttl
        assert node.confidence == initial.confidence
        assert client.processing_status(event_id, user_id='alice').status == 'complete'
