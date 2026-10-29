"""PV-11 quality sets (#327): 24 storage parity queries and 24 sealed scenarios.

Real sanitizer, crypto and storage with the deterministic fake embedding and
no model call. Each set writes its frozen inputs and every captured response to
the test's directory, so a failure keeps its evidence.
"""

import asyncio
import difflib
import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from prme import MemoryEngine
from prme.privacy.keys import StoredProcessingAuthorizer
from prme.privacy.local_keys import public_registry
from prme.privacy.retry_commitments import NativeRetryCommitter
from prme.storage.privacy_export import export_protected_pack, restore_protected_export
from prme.types import NodeType, RetrievalMode, Scope
from tests.privacy_journal_support import isolated_privacy_postgres, journal_engine, local_analyzer_fixture  # noqa: F401
from tests.privacy_journal_support import native as native_fixture  # noqa: F401
from tests.privacy_journal_support import processing_keys
from tests.privacy_fakes import FakeEmbedding
from tests.privacy_portability_support import fresh_config

FAST_CYCLE = Path(__file__).parent.parent / "benchmarks/fixtures/fast-cycle"
CLOCK = "2026-10-20T12:00:00+00:00"


def parity_inputs():
    """The 24 fixed existing queries over one fixed corpus.

    store-v1's 17 questions in order, then the first 7 questions of
    store-heldout-v1, which holds the same 75 memories.
    """
    main = json.loads((FAST_CYCLE / "store-v1.json").read_text())
    heldout = json.loads((FAST_CYCLE / "store-heldout-v1.json").read_text())
    assert [item["content"] for item in main["memories"]] == [item["content"] for item in heldout["memories"]]
    queries = [item["question"] for item in main["key"]["questions"]]
    queries += [item["question"] for item in heldout["key"]["questions"][:7]]
    assert len(queries) == 24 and len(set(queries)) == 24
    hashes = {name: hashlib.sha256((FAST_CYCLE / name).read_bytes()).hexdigest()
              for name in ("store-v1.json", "store-heldout-v1.json")}
    return main["memories"], queries, hashes


async def store_corpus(engine, memories, owner):
    # As the fast cycle stores them by default: no session ID, which a
    # protected store accepts only as a UUID; the session stays in metadata.
    # One saved policy covers one scope, so every memory is stored in the
    # policy's personal scope, in the plain baseline too.
    for memory in memories:
        await engine.store(memory["content"], user_id=owner, role=memory["role"], speaker=memory.get("speaker"),
                           node_type=NodeType(memory["node_type"]), scope=Scope.PERSONAL,
                           metadata={"source": "synthetic-demo", "session": memory["session"]})


async def capture(memory, queries, owner, clock, *, access=None, scope=Scope.PERSONAL):
    saved = {}
    for query in queries:
        response = await asyncio.wait_for(memory.retrieve(
            query, user_id=owner, scope=scope, reference_time=clock, include_cross_scope=False,
            source_access=access), 30)
        assert not response.metadata.backend_failures and not response.metadata.embedding_mismatch
        saved[query] = {
            "result_ids": [str(item.node.id) for item in response.results],
            "scores": [[str(item.node.id), item.composite_score, sorted(item.paths),
                        None if item.score_provenance is None else item.score_provenance.model_dump(mode="json")]
                       for item in response.results],
            "packed_sections": [[name, [str(item.node.id) for item in section]]
                                for name, section in response.bundle.sections.items()],
            "packed_text": [item.node.content for section in response.bundle.sections.values() for item in section],
            "context": response.bundle.render(),
        }
    return saved


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


class WordEmbedding(FakeEmbedding):
    """Deterministic word-hash vectors, so vector scores depend on content.

    The shared fake gives every text one vector, which makes every vector score
    a tie decided by index internals; parity would then compare tie order.
    """

    model_name = "privacy-quality-word-embedding"

    async def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % self.dimension] += 1.0 if digest[4] % 2 else -1.0
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


# Each edge keeps the storage backend, whose search engines differ by design:
# a DuckDB directory to an encrypted single-file pack, and a PostgreSQL schema
# restored into another PostgreSQL namespace.
@pytest.mark.parametrize("source_layout,target_layout", [("directory", "pack"), ("postgres", "postgres")],
                         ids=["duckdb", "postgres"])
async def test_PV11_storage_parity_24_fixed_queries(tmp_path, analyzer, native, source_layout, target_layout):
    """Byte-preserving encryption and restore keep selection and packed text."""
    import os

    if target_layout == "postgres" and not os.getenv("PRME_TEST_DATABASE_URL"):
        pytest.skip("dedicated PostgreSQL required; not a parity pass")
    memories, queries, hashes = parity_inputs()
    template, runtime, policy, custody, evidence, _ = await journal_engine(
        tmp_path / "template", "directory", analyzer, evidence_use="runtime")
    base = template._config.model_copy(update={
        "organizer": template._config.organizer.model_copy(update={"opportunistic_enabled": False})})
    await template.close()
    runtime.clock.now = datetime.fromisoformat(CLOCK)
    runtime = replace(runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock),
                      embedder=WordEmbedding())
    owner = str(policy.owner_id)
    clock = runtime.clock()
    write(tmp_path / "parity-frozen-inputs.json", {
        "fixture_sha256": hashes, "queries": queries, "clock": CLOCK, "edge": [source_layout, target_layout],
        "embedding": [runtime.embedder.model_name, runtime.embedder.model_version, runtime.embedder.dimension],
        "policy": policy.model_dump(mode="json"), "packing": base.packing.model_dump(mode="json"),
        "scoring": base.scoring.model_dump(mode="json")})

    async with fresh_config(base, tmp_path / "plain", "directory") as plain, \
            fresh_config(base, tmp_path / "standard", source_layout) as standard, \
            fresh_config(base, tmp_path / "target", target_layout, restore=True) as target:
        if target_layout == "pack":
            target = type(target).model_validate({**target.model_dump(), "encryption_enabled": True,
                                                  "encryption_key": "passphrase:task-owned-parity-327"})
        # Pre-sanitization baseline: the same raw input in a plain store.
        async with MemoryEngine.open(plain.model_copy(update={"privacy_policy": None}),
                                     embedding_provider=runtime.embedder) as memory:
            await store_corpus(memory, memories, owner)
            baseline = await capture(memory, queries, owner, clock)
        write(tmp_path / "parity-plain-baseline.json", baseline)
        artifact = tmp_path / "parity-export.json"
        with custody.unlocked((evidence.ref,), expires_at=clock + timedelta(minutes=5)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            try:
                async with MemoryEngine.open(standard, privacy_runtime=runtime,
                                             embedding_provider=runtime.embedder) as source:
                    await store_corpus(source, memories, owner)
                    records = await source._privacy.repository._read("1=1", ())
                    assert len(records) == len(memories)
                    assert all(record.snapshot.policy == policy for record in records)
                    assert all(record.snapshot.evidence_deadline > clock for record in records)
                    access = await runtime.protector.authority.grant(
                        "runtime", tuple((record.evidence.context, record.snapshot) for record in records),
                        expires_at=clock + timedelta(minutes=5))
                    before = await capture(source, queries, owner, clock, access=access)
                    await export_protected_pack(source, artifact)
                write(tmp_path / "parity-before.json", before)
                restored = await restore_protected_export(artifact, target, privacy_runtime=runtime,
                                                          embedding_provider=runtime.embedder)
                assert restored["state"] == "complete"
                async with MemoryEngine.open(target, privacy_runtime=runtime,
                                             embedding_provider=runtime.embedder) as memory:
                    if target_layout == "pack":
                        assert memory._config.encryption_enabled and Path(memory._config.pack_path).is_file()
                    after = await capture(memory, queries, owner, clock, access=access)
                write(tmp_path / "parity-after.json", after)
            finally:
                runtime.protector.registry = public_registry(custody.path)

    # Sanitization-induced differences are recorded against the plain
    # baseline, separately from the parity check, and never relabel it.
    sanitization = {query: {
        "plain_packed_text": baseline[query]["packed_text"], "protected_packed_text": before[query]["packed_text"],
        "context_diff": "".join(difflib.unified_diff(
            baseline[query]["context"].splitlines(True), before[query]["context"].splitlines(True),
            fromfile="plain", tofile="protected"))} for query in queries}
    write(tmp_path / "parity-sanitization-differences.json", sanitization)
    differences = {query: "".join(difflib.unified_diff(
        before[query]["context"].splitlines(True), after[query]["context"].splitlines(True),
        fromfile=f"protected-{source_layout}", tofile=f"protected-{target_layout}")) for query in queries}
    write(tmp_path / "parity-differences.json", differences)
    for query in queries:
        assert before[query]["result_ids"], query
        assert after[query]["result_ids"] == before[query]["result_ids"], query
        assert after[query]["packed_sections"] == before[query]["packed_sections"], query
        assert after[query]["context"].encode() == before[query]["context"].encode(), query


SEALED = Path(__file__).parent / "fixtures/privacy/sealed-behavior-v1.json"
QUALIFIERS = Path(__file__).parent / "fixtures/privacy/source-separation-v1.json"


def qualifier_claim(row):
    claim = {key: row[key] for key in ("subject", "predicate", "object", "polarity", "condition") if key in row}
    claim["evidence_quote"] = row["source"]
    claim["epistemic_type"] = "conditional" if row.get("condition") else "asserted"
    if row["id"] == "Q04":
        claim["temporal_ref"] = "October 21"
    if row.get("relationship"):
        names = {"subject": "source_entity", "object": "target_entity", "predicate": "relationship_type"}
        claim = {names.get(key, key): value for key, value in claim.items()}
    return {"entities": [{"name": "Alex", "entity_type": "person"}],
            "facts": [] if row.get("relationship") else [claim],
            "relationships": [claim] if row.get("relationship") else [], "summary": None}


async def load_sealed_cases(engine, runtime, owner, cases, unlock):
    qualifiers = {row["id"]: row for row in json.loads(QUALIFIERS.read_text())["qualifiers"]}
    clock = datetime.fromisoformat(json.loads(QUALIFIERS.read_text())["clock"])
    for case in cases:
        if case["group"] == "conditions_or_corrections":
            row = qualifiers[case["qualifier"]]
            runtime.extractor.result = qualifier_claim(row)
            # As CL05: accept the input, then process it with the processing key.
            await engine.ingest(row["source"], user_id=owner, event_time=clock)
            with unlock():
                assert (await engine.process_extractions(user_id=owner)).processed == 1
            case["required"] = [row["expected"]]
            continue
        for memory in case["memories"]:
            await engine.store(f"{memory} {case['source_only']}", retrieval_content=memory, user_id=owner)


async def sealed_answers(engine, owner, cases, *, access=None):
    answers = {}
    for case in cases:
        mode = RetrievalMode(case.get("retrieval_mode", "default"))
        response = await engine.retrieve(case["query"], user_id=owner, source_access=access, retrieval_mode=mode)
        answers[case["id"]] = {
            "retrieval_mode": mode.value, "context": response.bundle.render(),
            "response": response.model_dump(mode="json"),
            "source_limitation": response.metadata.source_limitation,
            "availability": [item.state for item in response.metadata.source_availability or ()],
        }
        if mode != RetrievalMode.DEFAULT:
            default = await engine.retrieve(case["query"], user_id=owner, source_access=access)
            answers[case["id"]]["default_context"] = default.bundle.render()
    return answers


async def test_PV11_sealed_behavior_24_frozen_scenarios(tmp_path, analyzer, native):
    """Admitted facts and qualifiers survive the sealed profile; sources stay unavailable."""
    fixture = json.loads(SEALED.read_text())
    cases = fixture["cases"]
    assert len(cases) == 24 and {case["group"] for case in cases} == {
        "simple_facts", "conditions_or_corrections", "multi_source", "source_only_or_quotation"}
    write(tmp_path / "sealed-frozen-inputs.json", {
        "fixture_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (SEALED, QUALIFIERS)}})
    results = {}
    for profile, use in (("sealed", "diagnostics_only"), ("standard", "runtime")):
        template, runtime, policy, custody, evidence, processing = await journal_engine(
            tmp_path / profile / "template", "directory", analyzer, evidence_use=use)
        await template.close()
        # Reopen with content-dependent vectors; the embedding is fixed at open.
        runtime = replace(runtime, embedder=WordEmbedding(),
                          retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
        engine = await MemoryEngine.create(template._config.model_copy(update={
            "db_path": str(tmp_path / profile / "memory.duckdb"),
            "vector_path": str(tmp_path / profile / "vectors.usearch"),
            "lexical_path": str(tmp_path / profile / "lexical")}),
            embedding_provider=runtime.embedder, privacy_runtime=runtime)
        runtime.protector.authority.processing = StoredProcessingAuthorizer(
            engine._privacy.repository, engine._event_store.extraction_work)
        owner = str(policy.owner_id)
        try:
            assert policy.profile == profile
            selected = [dict(case) for case in cases]
            await load_sealed_cases(engine, runtime, owner, selected,
                                    lambda: processing_keys(runtime, custody, processing))
            access = None
            if profile == "standard":
                records = await engine._privacy.repository._read("1=1", ())
                access = await runtime.protector.authority.grant(
                    "runtime", tuple((record.evidence.context, record.snapshot) for record in records
                                     if record.evidence is not None),
                    expires_at=runtime.clock() + timedelta(minutes=5))
                with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=5)) as private:
                    runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
                    try:
                        results[profile] = await sealed_answers(engine, owner, selected, access=access)
                    finally:
                        runtime.protector.registry = public_registry(custody.path)
            else:
                results[profile] = await sealed_answers(engine, owner, selected)
                required = {case["id"]: case["required"] for case in selected}
        finally:
            await engine.close()
    write(tmp_path / "sealed-answers.json", results)
    # Standard and sealed differences are reported, never compared as a
    # benchmark: a granted standard read may also pack source passages.
    write(tmp_path / "sealed-standard-differences.json", {case["id"]: "".join(difflib.unified_diff(
        results["sealed"][case["id"]]["context"].splitlines(True),
        results["standard"][case["id"]]["context"].splitlines(True),
        fromfile="sealed", tofile="standard")) for case in cases})
    failures = {}
    for case in cases:
        answer = results["sealed"][case["id"]]
        text = json.dumps(answer["response"])
        problems = [f"missing {value!r}" for value in required[case["id"]] if value not in answer["context"]]
        marker = case.get("source_only")
        if marker and marker in text:
            problems.append("source-only value disclosed")
        if case.get("expect_source_unavailable") and (
                not answer["source_limitation"] or "available" in answer["availability"]):
            problems.append("source not reported unavailable")
        if problems:
            failures[case["id"]] = problems
    write(tmp_path / "sealed-failures.json", failures)
    assert failures == {}
