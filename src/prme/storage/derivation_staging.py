"""Hold a durable work fence for the duration of an external index write."""
from contextlib import contextmanager

from prme.models.derivation import DerivationPlan, PreparedEmbedding
from prme.models.extraction_work import ExtractionClaim
from prme.storage.extraction_work import validate_duck_claim


class DuckDBStageFence:
    """Separate transaction, same database: index metadata may commit normally.

    The work-row write prevents another connection from advancing the generation
    while the native index call is in flight. Partial index writes remain durable
    on failure; they belong to the immutable plan and must not be compensated by
    deleting entries another retry may need. Caller holds the engine connection
    lock and the relevant index write lock throughout hold().
    """
    def __init__(self, conn, conn_lock, plan: DerivationPlan, claim: ExtractionClaim | None):
        self.conn = conn
        self.conn_lock = conn_lock
        self.plan = DerivationPlan.model_validate_json(plan.model_dump_json())
        self.claim = claim
        self._embeddings = {item.node_id: item for item in self.plan.embeddings}

    def verify_embedding(self, embedding: PreparedEmbedding, user_id: str) -> None:
        if user_id != self.plan.user_id or self._embeddings.get(embedding.node_id) != embedding:
            raise ValueError('Staged embedding does not belong to the fenced plan')

    def verify_plan(self, plan: DerivationPlan) -> None:
        if plan.checksum != self.plan.checksum:
            raise ValueError('Staged documents do not belong to the fenced plan')

    @contextmanager
    def hold(self):
        from prme.storage.event_store import EventStore
        # DuckDB cursor() duplicates the connection with an independent
        # transaction over the same database, including in-memory databases.
        connection = self.conn.cursor()
        try:
            connection.execute('BEGIN TRANSACTION')
            saved = EventStore(connection)._get_derivation_plan_sync(str(self.plan.event_id), self.plan.user_id)
            if saved is None or saved.checksum != self.plan.checksum:
                raise ValueError('Staging requires the exact current journaled derivation plan')
            validate_duck_claim(connection, str(self.plan.event_id), self.plan.user_id,
                                self.claim, plan_id=str(self.plan.id))
            yield
            validate_duck_claim(connection, str(self.plan.event_id), self.plan.user_id,
                                self.claim, plan_id=str(self.plan.id))
            connection.execute('COMMIT')
        except BaseException:
            try:
                connection.execute('ROLLBACK')
            except Exception:
                pass
            raise
        finally:
            connection.close()
