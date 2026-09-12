# Registered fresh PRME / Hindsight raw-memory development study

Registered before dataset retrieval, 2026-09-12. This extends the authored
Hindsight public-API preflight and the earlier Mem0 ranking study. It is not a
leaderboard run, full extraction comparison, independent holdout or default
promotion gate. The 119 development questions have already informed PRME work.

## Fixed inputs and systems

Use all 119 questions selected from `longmemeval_s_cleaned.json` by the existing
`prme-evidence-v1` development split, including abstention cases. All 59,021 source
turns enter each system in order, including four blank turns. Export neutral
positions, roles, exact text and supplied dates only; original dataset session
IDs, answer annotations, answers and question categories go to a separate
reference file. Normalize supplied dates to timezone-aware ISO values using the
existing UTC assumption. Opaque case IDs do not expose abstention suffixes.
Both systems receive the same question reference date via their supported API.

- PRME: installed `b7521bc` package (124 Python files byte-verified), local DuckDB,
  exact vectors, default scoring and density product packing. Explicit raw profile
  disables QA pairing, extraction-triggering features, supersedence, surprise
  gating, neural reranking, reformulation and opportunistic organization. One
  DuckDB worker and a shared caller-owned BGE provider bound local resources.
- Hindsight: installed 0.9.2 at `bde55237f53bf55aacd048b01e29d7dc23b83a85`, verified
  against all 323 Python source files. LLM provider `none` forces raw chunk mode;
  observations and auto-consolidation are off. Use native PostgreSQL text search,
  RRF, public retain batches of 64, and the public high traversal budget. A fresh
  database per case excludes earlier cases and index tombstones. The capture's
  65,536-token recall request counts fact text only; it is a retrieval capture
  allowance, not the reader's context budget. Entities, extra chunks and source
  fact expansions are not requested.

Both use BGE small English v1.5, the same recorded assets, FastEmbed 0.8.0,
ONNX Runtime 1.24.2, NumPy 2.4.2, tiktoken 0.14.0 and Python 3.13.3. Encoding uses
one text per inference batch. Initial PRME authored capture used a newer
ONNX/NumPy environment; keep it as preflight evidence, not matched study evidence.
The subsequent PRME authored run uses a new isolated matched environment.
The earlier seven-input embedding parity check and these asset/runtime pins
support compatibility, not universal cross-platform bitwise determinism.

The authored capture checks long documents, qualifiers, Unicode, whitespace and
blank documents. Both products preserve all nine original documents in three
cases; Hindsight emits no searchable units for blank text. No dataset turn is
removed to accommodate this behavior. A Hindsight configuration warning about
missing sentence-transformers is retained: the explicitly supplied matched
embedding provider handles the verified calls; no fallback model is used.

## Saved returns and budget accounting

Read each admitted document back through public APIs before retrieval. Reject
unknown/duplicate/foreign returned IDs, changed metadata and altered raw text.
Hindsight chunks must be exact source substrings; their returned text is saved
before validation. Do not replace a retrieved chunk with its original document
inside the adapter-rendered context.

Capture PRME's actual candidate objects and reproduce its default 4K public
bundle, plus offline product bundles at 2,048/4,096/8,192 tokens. The PRME reserve
of 100 tokens remains part of its declared product policy. Capture the complete
Hindsight public result and render returned units as JSONL containing unit ID,
source ID, role, supplied source date and exact returned text. Greedily fit whole
records at the same three complete serialized token ceilings; never truncate a
qualifier or infer missing text. This Hindsight renderer is an evaluator adapter,
not a claimed native string renderer. Save all contexts and hashes for a later
common-reader protocol; no answer generation is part of this capture study.

Separately compare the first 100 unique ranked source-document IDs using the
same whole-turn evaluator packer at all three budgets. That joins IDs to original
source turns and isolates source ranking; it is explicitly reconstruction by the
evaluator and must not be confused with actual returned text. The earlier Mem0
run used a different date rendering and older PRME reference, so do not pool its
budget scores directly with this fresh comparison.

## Analysis, failures and limitations

Primary report: descriptive paired labelled-source recall at a 4,096-token shared
whole-turn budget; report 2K/8K and every category alongside it. Five unlabelled
questions have null source scores, never demonstrated abstention. Record wins,
losses and ties. Query-bootstrap intervals are exploratory; also group original
question/abstention IDs and identical histories for a cluster sensitivity check.
Partly overlapping histories can still be dependent; do not claim independent
population superiority from either interval.

For actual contexts, separately report source-document hits and complete original
turn text present inside an individual content-bearing record. The latter is a
conservative diagnostic and can miss complete evidence spread across chunks;
it does not measure annotated answer-span recall or generated-answer accuracy.
Measure all serialized tokens. Preserve unknown IDs and failures, rather than
silently dropping them or assigning perfect scores.

No quality inspection, selection or tuning is allowed until every selected case
has a successful capture and both native processes exit zero. Operational
progress, failure diagnosis and authored preflight data can be inspected earlier.
No outcome-based retries or result merging. A failed run remains failed; any
necessary amended run must have a new registration and artifact path. Complete
comparisons require exact case coverage, source/input/capture hashes, reproduced
contexts and independently observed native exits. Ingestion/readback costs,
embedding request/text/character counts, startup time and retrieval time remain
separate. Native APIs batch differently; concurrent host load and database-server
resources prevent a fair speed ratio. Neither extracted semantic memories,
consolidation, full graph capabilities nor end-to-end product answer quality is
measured by this raw mode.
