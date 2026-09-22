# Alternate-query signal merging

Date: 2026-09-22. Status: registered, waiting for released execution capacity.
Production sources and defaults remain unchanged.

The completed original reformulation arm scored 434/500 against production's
437/500. Only two packed contexts changed, and all eleven answer disagreements
occurred on unchanged inputs. The pipeline generates alternate-query candidates
but drops their signals when the same ID is already in the original pool.
That is a concrete limitation to investigate; it does not prove that stronger
signals from alternate queries are always relevant or helpful.

The [registered follow-up](opt-in-reformulation-merge-v1-registration.json)
tests one fixed research policy. It merges distinct backend paths and takes the
maximum semantic, normalized lexical and graph-proximity signals before ordinary
scoring. This follows the existing within-query candidate merge convention.
Repeated queries do not count as additional backends. Same-ID node snapshots
must agree exactly; source text, timestamps, owner, scope and content are never
rewritten. No public configuration field or production entry point is added.

The source trial must replay all 500 original baseline and reformulation contexts,
candidate IDs and scores exactly. It reuses each question's recorded cold
reformulations, which were generated without history, answers or source labels.
Local query embeddings and retrieval execute again, but there are no new hosted
reformulation or cross-encoder calls. Reported source timings therefore exclude
the original hosted reformulation expense and cannot establish uncached serving
speed. Complete per-case inputs, contexts, score receipts and source hashes remain
available; the outer experiment identity records the research merge policy.

Answer qualification requires both complete-source count and mean source fraction
to strictly exceed production and original reformulation, with no category's
complete-source count below production. All 470 annotated cases contribute to
source differences and descriptive paired bootstrap intervals; all gains and
losses are retained. This is one fixed policy, with no parameter search.

If it qualifies, all 500 new baseline-repeat contexts and 500 candidate contexts
are frozen before answer inference. Both full arms must complete under the same
official prompts, DeepSeek reader/judge, 8,192/64 output caps, 3,996 memory tokens,
scoring rules and paired analysis. Failed prior arms remain failed. The source
process waits for the current anchor trial to exit, then uses its released local
capacity; any answers acquire the same exclusive four-slot provider lane.

[Validation](reformulation-merge-validation-v1.json) passed nine authored tests
and two DuckDB/live-PostgreSQL tests. They cover signal merging, repeated-query
path counts, immutable source snapshots, finite values, backend failure,
complete authored control replay, frozen-input rejection, scoped retrieval,
receipt replay, feedback and restart. An initial authored executor failure was
fixed before registration and its log is retained. These are operational checks,
not a benchmark gain or release qualification.

All original development outcomes were known at registration. The ongoing anchor
trial's partial quality results have not been inspected. None of this cohort is
untouched confirmation; promotion still requires a separately audited cohort and
the relevant full release checks.
