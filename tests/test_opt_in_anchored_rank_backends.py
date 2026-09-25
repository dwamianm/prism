"""Reuse the established receipt/restart/owner contract for the new ordering."""
from benchmarks.diagnostics.opt_in_anchored_rank import AnchoredRankEnvelopeReranker
from tests import test_opt_in_rank_envelope as prior

durable_config = prior.durable_config
config = prior.config
user = prior.user


async def test_anchored_receipt_replay_feedback_owner_and_restart(config, user, monkeypatch):
    monkeypatch.setattr(prior, 'RankEnvelopeReranker', AnchoredRankEnvelopeReranker)
    await prior.test_rank_assignment_receipt_and_feedback_survive_backend_restart(config, user)
