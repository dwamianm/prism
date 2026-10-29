"""SN01–SN12, exactly two real backends. Pending routes remain explicit gates."""

from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.models.speaker import SPEAKER_METADATA_KEY
from prme.privacy import CategoryAction, PrivacyDenied
from prme.privacy.capabilities import SourceOnlyRegion
from prme.privacy.local_analyzer import Detection
from prme.privacy.recognizers import CustomRule, MAX_INPUT_BYTES, Span
from prme.privacy.sanitizer import LocalSanitizer, canonical
from prme.privacy.strict_schema import ClosedSchema, ValueValidator
from prme.types import Scope
from tests.privacy_fakes import config_for, fresh_postgres_store, open_engine
from tests.privacy_fakes import isolated_privacy_postgres as isolated_privacy_postgres
from tests.privacy_sanitizer_support import Rules, Schemas, ScopedTestKeys, admit, setup
from tests.privacy_sanitizer_support import local_analyzer_fixture as local_analyzer_fixture
from tests.test_privacy_sanitizer import NoInference

FIXTURES = json.loads((Path(__file__).parent / "fixtures/privacy/sanitizer-v1.json").read_text())["rows"]
SOURCE = "John Smith does not like tea unless it is cold."


@pytest.fixture(params=["directory", "postgres"], ids=["duckdb", "postgres"], name="layout")
def backend_layout(request):
    if request.param == "postgres" and not os.getenv("PRME_TEST_DATABASE_URL"):
        pytest.skip("dedicated PostgreSQL required; not a sanitizer success")
    return request.param


async def opened(tmp_path, layout, analyzer, *, policy_options=None, sanitizer_options=None):
    runtime, policy, saved = setup(analyzer, policy_options=policy_options, sanitizer_options=sanitizer_options)
    runtime.extractor.result = {
        "entities": [], "facts": [{"subject": "John Smith", "predicate": "likes", "object": "tea",
                                 "evidence_quote": SOURCE, "polarity": "negative",
                                 "condition": "unless it is cold", "epistemic_type": "conditional"}],
        "relationships": [], "summary": None,
    }
    if layout == "postgres":
        # Each call opens an independent store, bound to its own runtime project.
        await fresh_postgres_store()
    engine, _, _ = await open_engine(tmp_path, layout=layout, runtime=runtime, policy=policy)
    return engine, runtime, policy, saved


async def no_writes(engine, runtime, owner):
    assert await engine._event_store.get_by_user(owner) == []
    assert await engine._graph_store.query_nodes(user_id=owner) == []
    assert await engine._privacy.repository.for_owner(owner) == ()
    assert not runtime.embedder.sends and not runtime.extractor.sends


async def scan_copies(engine, runtime, owner, prohibited, path, caplog, capsys):
    objects = []
    objects.extend(node.model_dump_json() for node in await engine._graph_store.query_nodes(user_id=owner))
    objects.extend(event.model_dump_json() for event in await engine._event_store.get_by_user(owner))
    objects.extend(record.model_dump_json() for record in await engine._privacy.repository.for_owner(owner))
    objects.extend(json.dumps(value) for value in runtime.embedder.sends)
    objects.extend(runtime.extractor.sends)
    captured = capsys.readouterr()
    objects.extend((captured.out, captured.err, caplog.text))
    repository = engine._privacy.repository
    if repository.pool is not None:
        async with repository.pool.acquire() as connection:
            objects.extend(row["payload"] for row in await connection.fetch("SELECT payload::text AS payload FROM operations"))
    else:
        async with repository.lock:
            objects.extend(row[0] for row in repository.conn.execute("SELECT payload FROM operations").fetchall())
    for marker in prohibited:
        assert all(marker not in value for value in objects)
    await engine.close()
    for file in path.rglob("*"):
        if file.is_file():
            value = file.read_bytes()
            assert all(marker.encode() not in value for marker in prohibited)


async def test_SN01_recognized_credential(tmp_path, layout, analyzer, caplog, capsys):
    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    positives = [row["text"] for row in FIXTURES if row["category"] == "credential" and row["label"] == "supported_positive"]
    try:
        for text in positives:
            for body, metadata in ((text, None), ("The cup is on the table.", {"detail": text})):
                with pytest.raises(PrivacyDenied):
                    await engine.store(body, user_id=owner, metadata=metadata)
        with pytest.raises(PrivacyDenied):
            await engine.store("The cup is on the table.", user_id=owner, metadata={"password": "synthetic-value"})
        await no_writes(engine, runtime, owner)
        for row in FIXTURES:
            if row["category"] == "credential" and row["label"] == "benign":
                identifier = await engine.store(row["text"], user_id=owner)
                assert (await engine.get_event_nodes(identifier, user_id=owner))[0].content == row["text"]
        await scan_copies(engine, runtime, owner, positives, tmp_path, caplog, capsys)
    finally:
        await engine.close()


async def test_SN02_contact_actions_and_profiles(tmp_path, layout, analyzer, caplog, capsys):
    contacts = [row for row in FIXTURES if row["category"] in {"email", "phone"}
                and row["label"] == "supported_positive" and row["id"] in {"email-01", "phone-01", "phone-02"}]
    for use in ("runtime", "diagnostics_only"):
        path = tmp_path / use
        engine, runtime, policy, _ = await opened(path, layout, analyzer, policy_options={
            "capture": "full", "evidence_use": use, "evidence_lifetime_seconds": 3600})
        owner = str(policy.owner_id)
        try:
            removed = []
            for row in contacts:
                identifier = await engine.store(row["text"], user_id=owner)
                content = (await engine.get_event_nodes(identifier, user_id=owner))[0].content
                span = row["expected_spans"][0]
                marker = row["text"][span["start"]:span["end"]]
                removed.append(marker)
                assert marker not in content and f"[{row['category'].upper()}_REMOVED]" in content
                record = await engine._privacy.repository.get(identifier, user_id=owner)
                assert record.sanitization.detector_versions and record.sanitization.action_counts
            assert await engine.store("There are 42 boxes.", user_id=owner)
            await scan_copies(engine, runtime, owner, removed, path, caplog, capsys)
        finally:
            await engine.close()
    for action in ("allow", "block"):
        engine, runtime, policy, _ = await opened(tmp_path / action, layout, analyzer, policy_options={
            "category_actions": (CategoryAction(category="email", action=action),)})
        try:
            if action == "allow":
                identifier = await engine.store(contacts[0]["text"], user_id=str(policy.owner_id))
                assert (await engine.get_event_nodes(identifier, user_id=str(policy.owner_id)))[0].content == contacts[0]["text"]
            else:
                with pytest.raises(PrivacyDenied):
                    await engine.store(contacts[0]["text"], user_id=str(policy.owner_id))
                await no_writes(engine, runtime, str(policy.owner_id))
        finally:
            await engine.close()


async def test_CL01_metadata_keys_and_display_fields(tmp_path, layout, analyzer, caplog, capsys):
    import hashlib

    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    email = next(row for row in FIXTURES if row["id"] == "email-01")
    span = email["expected_spans"][0]
    address = email["text"][span["start"]:span["end"]]
    try:
        # A credential in a metadata key or a display field is refused like
        # body text, on the single and the fast batch path.
        for metadata, speaker in (({"password=synthetic-value": "x"}, None),
                                  ({"contact": {"password=synthetic-value": "x"}}, None),
                                  (None, "password=synthetic-value")):
            with pytest.raises(PrivacyDenied):
                await engine.store("The cup is on the table.", user_id=owner, metadata=metadata, speaker=speaker)
            with pytest.raises(PrivacyDenied):
                await engine.ingest_fast_many([{"content": "The cup is on the table.", "metadata": metadata,
                                                "speaker": speaker}], user_id=owner)
        await no_writes(engine, runtime, owner)
        # A removed contact in a metadata key, a nested key and a display field
        # is replaced before storage; the original never becomes durable.
        identifier = await engine.store("The cup is on the table.", user_id=owner, speaker=address,
                                        metadata={address: "primary", "contact": {address: "work"}})
        batch = await engine.ingest_fast_many([{"content": "There are 42 boxes.", "speaker": address,
                                                 "metadata": {address: "primary"}}], user_id=owner)
        assert (await engine.process_pending(user_id=owner)).processed == 1
        events = await engine._event_store.get_by_user(owner)
        nodes = [node for item in (identifier, *batch) for node in await engine.get_event_nodes(item, user_id=owner)]
        assert len(events) == 2 and len(nodes) == 2
        assert all(node.metadata[SPEAKER_METADATA_KEY] == "[EMAIL_REMOVED]" for node in nodes)
        assert all(address not in value.model_dump_json() for value in (*events, *nodes))
        record = await engine._privacy.repository.get(identifier, user_id=owner)
        assert record.sanitization.action_counts
        await scan_copies(engine, runtime, owner, (address, hashlib.sha256(address.encode()).hexdigest()),
                          tmp_path, caplog, capsys)
    finally:
        await engine.close()


async def test_SN03_placeholder_continuity(tmp_path, layout, analyzer):
    keys, epoch = ScopedTestKeys(), uuid4()
    engine, runtime, policy, saved = await opened(tmp_path, layout, analyzer, sanitizer_options={
        "key_provider": keys, "pseudonym_epoch": epoch}, policy_options={
        "category_actions": (CategoryAction(category="email", action="pseudonymize"),)})
    keys.provision(policy, epoch)
    owner = str(policy.owner_id)
    try:
        tokens = []
        for _ in range(2):
            identifier = await engine.store("sample.person@example.com", user_id=owner)
            tokens.append((await engine.get_event_nodes(identifier, user_id=owner))[0].content)
        assert tokens[0] == tokens[1] and tokens[0].startswith("[PRME_PSEUDONYM_V1:")
        spoof = await engine.store(tokens[0], user_id=owner)
        assert tokens[0] not in (await engine.get_event_nodes(spoof, user_id=owner))[0].content
        keys.keys.clear()
        before = len(runtime.embedder.sends)
        with pytest.raises(PrivacyDenied):
            await engine.store("sample.person@example.com", user_id=owner)
        assert len(runtime.embedder.sends) == before
        next_epoch = uuid4()
        keys.provision(policy, next_epoch)
        new = LocalSanitizer(analyzer=analyzer, key_provider=keys, pseudonym_epoch=next_epoch)
        source = await admit(new, saved, "sample.person@example.com")
        assert source.issued_tokens[0] != tokens[0]
    finally:
        await engine.close()


async def test_SN04_boundary_isolation(tmp_path, layout, analyzer):
    keys, epoch = ScopedTestKeys(), uuid4()
    runtime, policy, saved = setup(analyzer, sanitizer_options={"key_provider": keys, "pseudonym_epoch": epoch},
                                   policy_options={"category_actions": (CategoryAction(category="email", action="pseudonymize"),)})
    policies = (policy, policy.model_copy(update={"owner_id": uuid4()}),
                policy.model_copy(update={"project_id": uuid4()}), policy.model_copy(update={"scope": Scope.PROJECT}))
    tokens = []
    source = None
    for index, selected in enumerate(policies):
        keys.provision(selected, epoch)
        chosen_runtime = replace(runtime, project_id=selected.project_id)
        if layout == "postgres":
            await fresh_postgres_store()
        engine = await MemoryEngine.create(config_for(tmp_path / str(index), layout=layout, policy=selected),
                                           embedding_provider=runtime.embedder, privacy_runtime=chosen_runtime)
        try:
            identifier = await engine.store("sample.person@example.com", user_id=str(selected.owner_id), scope=selected.scope)
            tokens.append((await engine.get_event_nodes(identifier, user_id=str(selected.owner_id)))[0].content)
            if index == 0:
                source = await admit(runtime.sanitizer, saved, "sample.person@example.com")
            else:
                from prme.privacy.policy import snapshot
                foreign = snapshot(selected, None, runtime.clock())
                with pytest.raises(PrivacyDenied):
                    await admit(runtime.sanitizer, foreign, "", mode="generated_output", source=source,
                                raw_fields=canonical({"summary": tokens[0]}))
        finally:
            await engine.close()
    assert len(set(tokens)) == 4


async def test_SN05_custom_rules(tmp_path, layout, analyzer):
    rules = Rules((CustomRule("literal_topic", "synthetic violet orchard"),
                   CustomRule("literal_identifier", "SYNTH-ID-482")))
    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer, sanitizer_options={"rule_provider": rules},
                                            policy_options={"exclusion_refs": (uuid4(),)})
    owner = str(policy.owner_id)
    try:
        with pytest.raises(PrivacyDenied):
            await engine.store("synthetic violet orchard", user_id=owner)
        await no_writes(engine, runtime, owner)
        identifier = await engine.store("SYNTH-ID-482", user_id=owner)
        assert (await engine.get_event_nodes(identifier, user_id=owner))[0].content == "[CUSTOM_IDENTIFIER_REMOVED]"
        inspection = json.dumps(await engine.inspect_privacy_policy(user_id=owner))
        assert "synthetic violet orchard" not in inspection and "SYNTH-ID-482" not in inspection
        for invalid in ((CustomRule("regex", "(a+)+$"),), (CustomRule("literal_topic", "x" * 257),),
                        tuple(CustomRule("literal_topic", "x") for _ in range(65))):
            rules.rules = invalid
            with pytest.raises(PrivacyDenied):
                await engine.store("The public agenda is ready.", user_id=owner)
        rules.rules = ()
        assert await engine.store("The public agenda is ready.", user_id=owner)
    finally:
        await engine.close()


async def test_SN06_conditions_negation_and_strict_values(tmp_path, layout, analyzer):
    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        identifier = await engine.ingest(SOURCE, speaker="John Smith", event_time=runtime.clock(),
                                         user_id=owner, wait_for_extraction=True)
        nodes = await engine.get_event_nodes(identifier, user_id=owner)
        fact = next(node for node in nodes if node.metadata.get("polarity") == "negative")
        assert fact.metadata["condition"] == "unless it is cold" and "John Smith" in fact.content
        assert fact.content.startswith("It is not true that ") and fact.event_time == runtime.clock()
        assert fact.metadata[SPEAKER_METADATA_KEY] == "John Smith" and "evidence_quote" not in fact.metadata
        timed = json.loads((Path(__file__).parent / "fixtures/privacy/source-separation-v1.json").read_text())["qualifiers"][3]
        prior = runtime.extractor.result
        runtime.extractor.result = {"entities": [], "relationships": [], "summary": None, "facts": [{
            "subject": timed["subject"], "predicate": timed["predicate"], "object": timed["object"],
            "polarity": timed["polarity"], "evidence_quote": timed["source"]}]}
        before = len(runtime.embedder.sends)
        with pytest.raises(PrivacyDenied, match="privacy_evidence_ambiguous"):
            await engine.ingest(timed["source"], user_id=owner, wait_for_extraction=True)
        assert len(runtime.embedder.sends) == before
        runtime.extractor.result = prior
        runtime.extractor.result["facts"][0]["polarity"] = "positive"
        before = len(runtime.embedder.sends)
        with pytest.raises(PrivacyDenied, match="privacy_evidence_ambiguous"):
            await engine.ingest(SOURCE, user_id=owner, wait_for_extraction=True)
        assert len(runtime.embedder.sends) == before
    finally:
        await engine.close()
    reference = uuid4()
    schema = ClosedSchema(reference, (("enabled", ValueValidator("boolean")),))
    engine, runtime, policy, _ = await opened(tmp_path / "strict", layout, analyzer, sanitizer_options={
        "schema_provider": Schemas(schema)}, policy_options={
        "strict_schema_ref": reference, "strict_schema_version": "prme-strict-schema-v1"})
    try:
        for value in ({"enabled": "true"}, {"enabled": True, "notes": SOURCE}):
            with pytest.raises(PrivacyDenied):
                await engine.store(canonical(value), user_id=str(policy.owner_id))
        await no_writes(engine, runtime, str(policy.owner_id))
        assert await engine.store(canonical({"enabled": True}), user_id=str(policy.owner_id))
    finally:
        await engine.close()


async def test_SN07_language_declaration(tmp_path, layout, analyzer):
    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    try:
        from prme.privacy import PrivacyPolicy
        for language in ("es", "unknown", None):
            values = policy.model_dump()
            if language is None:
                values.pop("language")
            else:
                values["language"] = language
            with pytest.raises(ValueError):
                PrivacyPolicy.model_validate(values)
        await no_writes(engine, runtime, str(policy.owner_id))
        # Declared English is not automatic language detection. This known
        # limitation is an observation, never an unsupported-language pass.
        identifier = await engine.store("La reunión es mañana.", user_id=str(policy.owner_id))
        assert await engine.get_event_nodes(identifier, user_id=str(policy.owner_id))
        inspection = await engine.inspect_privacy_policy(user_id=str(policy.owner_id))
        assert inspection["sanitizer_coverage"]["language_detection"] == "caller_declared_only"
    finally:
        await engine.close()


async def test_SN08_obfuscation_offsets(tmp_path, layout, analyzer):
    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        for row in FIXTURES:
            if row["id"] in {"email-02", "email-03", "email-04", "email-05"}:
                identifier = await engine.store("Before " + row["text"] + " after.", user_id=owner)
                assert (await engine.get_event_nodes(identifier, user_id=owner))[0].content == "Before [EMAIL_REMOVED] after."
        with pytest.raises(PrivacyDenied):
            await engine.store("password \t= \t'synthetic-value'", user_id=owner)
        # Unsupported bytes remain unchanged; the detector report keeps them
        # outside recall/true-negative denominators.
        for row in FIXTURES:
            if row["id"] in {"credential-17", "credential-19"}:
                identifier = await engine.store(row["text"], user_id=owner)
                assert (await engine.get_event_nodes(identifier, user_id=owner))[0].content == row["text"]
    finally:
        await engine.close()


async def test_SN09_detector_failures(tmp_path, layout, analyzer, monkeypatch, caplog, capsys):
    class Fault(NoInference):
        def __init__(self, mode):
            self.mode = mode

        async def analyze(self, texts, *, seconds):
            if self.mode == "exception":
                raise RuntimeError("synthetic private detector error")
            if self.mode == "offset":
                return Detection(tuple((Span("person", 0, len(text) + 1, "injected"),) for text in texts))
            if self.mode == "missing_recognizer":
                return Detection(tuple(() for _ in texts), complete=False)
            from prme.privacy.local_analyzer import verify_artifacts
            import prme.privacy.local_analyzer as local
            with monkeypatch.context() as patch:
                patch.setattr(local.metadata, "version", lambda name: "unavailable")
                verify_artifacts()

    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    try:
        for mode in ("exception", "missing_model", "missing_recognizer", "offset"):
            runtime.sanitizer.analyzer = Fault(mode)
            with pytest.raises(PrivacyDenied) as denied:
                await engine.store("The cup is on the table.", user_id=str(policy.owner_id))
            assert str(denied.value).startswith("privacy_")
        await no_writes(engine, runtime, str(policy.owner_id))
        captured = capsys.readouterr()
        assert "synthetic private detector error" not in caplog.text + captured.out + captured.err
        runtime.sanitizer.analyzer = analyzer
        assert await engine.store("The cup is on the table.", user_id=str(policy.owner_id))
    finally:
        await engine.close()


async def test_SN10_deadline_and_aggregate_bounds(tmp_path, layout, analyzer, monkeypatch):
    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        runtime.sanitizer.analyzer = NoInference()
        with pytest.raises(PrivacyDenied):
            await engine.ingest_fast_many([{"content": "x" * (MAX_INPUT_BYTES // 2)},
                                          {"content": "x" * (MAX_INPUT_BYTES // 2 + 1)}], user_id=owner)
        await no_writes(engine, runtime, owner)
        from types import SimpleNamespace
        import prme.privacy.sanitizer as sanitizer
        import prme.privacy.recognizers as recognizers
        ticks = iter((0, 0))
        clock = SimpleNamespace(monotonic_ns=lambda: next(ticks, 2_000_000_001))
        with monkeypatch.context() as patch:
            patch.setattr(sanitizer, "time", clock)
            patch.setattr(recognizers, "time", clock)
            with pytest.raises(PrivacyDenied):
                await engine.store("The cup is on the table.", user_id=owner)
        await no_writes(engine, runtime, owner)
        with pytest.raises(PrivacyDenied, match="privacy_capability_unavailable"):
            recognizers.bounded_spans("x" * MAX_INPUT_BYTES,
                                     (CustomRule("literal_identifier", "x"), CustomRule("literal_identifier", "xx")))
        runtime.sanitizer.analyzer = analyzer
        assert await engine.store("The cup is on the table.", user_id=owner)
    finally:
        await engine.close()


async def test_SN11_prewrite_effects_and_query_gate(tmp_path, layout, analyzer, caplog, capsys):
    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    marker = "password=synthetic-value"
    email = next(row for row in FIXTURES if row["id"] == "email-01")
    span = email["expected_spans"][0]
    address = email["text"][span["start"]:span["end"]]
    try:
        # A fast batch and the supported raw-record import both refuse the
        # whole batch when a later item fails, before any earlier item writes.
        with pytest.raises(PrivacyDenied):
            await engine.ingest_fast_many([{"content": "The cup is on the table."},
                                          {"content": marker}], user_id=owner)
        await no_writes(engine, runtime, owner)
        for messages in ([{"content": "The cup is on the table.", "role": "user"}, {"content": marker, "role": "user"}],
                         [{"content": "The cup is on the table.", "role": "user", "speaker": marker}]):
            with pytest.raises(PrivacyDenied):
                await engine.ingest_batch(messages, user_id=owner)
            await no_writes(engine, runtime, owner)
        identifiers = await engine.ingest_fast_many([{"content": "The cup is on the table."},
                                                    {"content": "There are 42 boxes."}], user_id=owner)
        assert len(identifiers) == 2
        result = await engine.process_pending(user_id=owner)
        assert result.processed == 2
        for identifier in identifiers:
            assert await engine.get_event_nodes(identifier, user_id=owner)
        # A display name is content: a removed contact is replaced before it
        # is stored, and only the sanitized text is embedded.
        identifier = await engine.store("The cup is on the table.", speaker=address, user_id=owner)
        node = (await engine.get_event_nodes(identifier, user_id=owner))[0]
        assert node.metadata[SPEAKER_METADATA_KEY] == "[EMAIL_REMOVED]"

        # Query gate: a credential query is refused before any provider send,
        # and a removed contact is replaced before transmission and receipts.
        sends = len(runtime.embedder.sends)
        with pytest.raises(PrivacyDenied):
            await engine.retrieve(f"tea {marker}", user_id=owner)
        assert len(runtime.embedder.sends) == sends
        response = await engine.retrieve(f"tea for {address}", user_id=owner, min_score=0)
        sent = json.dumps(runtime.embedder.sends[sends:])
        assert "[EMAIL_REMOVED]" in sent and address not in sent
        assert response.metadata.receipt_persisted
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=owner)
        assert address not in receipt.model_dump_json()
        await scan_copies(engine, runtime, owner, (marker, address), tmp_path, caplog, capsys)
    finally:
        await engine.close()


async def test_SN12_generated_reintroduction_and_publication_gate(tmp_path, layout, analyzer, monkeypatch,
                                                                 caplog, capsys):
    from prme.types import NodeType
    from prme.organizer import profiles, summarization
    from prme.organizer.summarization import SummarizationLevel
    from tests.privacy_journal_support import operation_bytes

    engine, runtime, policy, _ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    marker = "password=synthetic-value"
    try:
        runtime.extractor.result["summary"] = marker
        with pytest.raises(PrivacyDenied):
            await engine.ingest(SOURCE, user_id=owner, wait_for_extraction=True)
        assert not runtime.embedder.sends and await engine._event_store.get_by_user(owner) == []
        runtime.extractor.result["summary"] = None

        class SourceOnly:
            async def classify(self, fields_json, snapshot):
                # Exact fixture declaration: trusted engine context classifies
                # the source paragraph as source-only, independent of its words.
                return (SourceOnlyRegion(("content",), 0, len(json.loads(fields_json)["content"])),)

        runtime.sanitizer._classifier = SourceOnly()
        with pytest.raises(PrivacyDenied, match="privacy_evidence_ambiguous"):
            await engine.ingest(SOURCE, user_id=owner, wait_for_extraction=True)
        assert not runtime.embedder.sends and await engine._event_store.get_by_user(owner) == []
        runtime.sanitizer._classifier = None
        # A placeholder this source never issued is refused at the plan
        # boundary, before any write or vector.
        # A protected profile needs exactly one admitted entity node.
        clean = {**runtime.extractor.result, "entities": [{"name": "John Smith", "entity_type": "person"}]}
        fact = clean["facts"][0]
        unissued = f"[PRME_PSEUDONYM_V1:email:{uuid4()}:{'A' * 43}]"
        runtime.extractor.result = {**clean, "facts": [{**fact, "object": unissued}]}
        with pytest.raises(PrivacyDenied, match="privacy_boundary_mismatch"):
            await engine.ingest(SOURCE, user_id=owner, wait_for_extraction=True)
        assert not runtime.embedder.sends and await engine._event_store.get_by_user(owner) == []
        # A field outside the extraction schema is withheld: the fact publishes
        # without it, and its value reaches no inventoried copy (scanned below).
        extra = "SN12_UNDECLARED_FIELD_VALUE"
        runtime.extractor.result = {**clean, "facts": [{**fact, "private_note": extra}]}
        identifier = await engine.ingest(SOURCE, user_id=owner, wait_for_extraction=True)
        nodes = await engine.get_event_nodes(identifier, user_id=owner)
        assert nodes and all(extra not in node.model_dump_json() for node in nodes)
        runtime.extractor.result = clean
        record = await engine._privacy.repository.get(identifier, user_id=owner)
        assert record.sanitization.action_counts and marker not in record.model_dump_json()

        # Organizer output: generated summary and profile text that carries a
        # removed value is withheld before its plan, vector or node exists.
        build_summary, build_profile = summarization._build_summary_content, profiles.build_profile

        def leaking_profile(*args, **kwargs):
            built = build_profile(*args, **kwargs)
            return None if built is None else (f"{built[0]}\n{marker}", *built[1:])

        monkeypatch.setattr(summarization, "_build_summary_content",
                            lambda *args: f"{build_summary(*args)}\n{marker}")
        monkeypatch.setattr(profiles, "build_profile", leaking_profile)
        sends = len(runtime.embedder.sends)
        with pytest.raises(PrivacyDenied) as denied:
            await summarization._materialize_summary_node(engine, SummarizationLevel.DAILY, "2026-10-20",
                                                          nodes, owner)
        assert denied.value.code == "privacy_denied"
        with pytest.raises(PrivacyDenied) as denied:
            await engine.consolidate_knowledge(user_id=owner, scope=Scope.PERSONAL, entity_names=["John Smith"])
        assert denied.value.code == "privacy_denied"
        assert len(runtime.embedder.sends) == sends and await engine.profile_jobs(user_id=owner) == []
        assert await engine._graph_store.query_nodes(user_id=owner, node_type=NodeType.SUMMARY) == []
        assert marker.encode() not in await operation_bytes(engine)

        # The same paths publish through the guard once the generated text is clean.
        monkeypatch.setattr(summarization, "_build_summary_content", build_summary)
        monkeypatch.setattr(profiles, "build_profile", build_profile)
        daily, created = await summarization._materialize_summary_node(
            engine, SummarizationLevel.DAILY, "2026-10-20", nodes, owner)
        assert created and "John Smith" in daily.content
        assert await engine.consolidate_knowledge(user_id=owner, scope=Scope.PERSONAL,
                                                  entity_names=["John Smith"]) == 1
        published = await engine._graph_store.query_nodes(user_id=owner, node_type=NodeType.SUMMARY)
        assert {str(daily.id)} < {str(node.id) for node in published}
        assert any(node.metadata.get("entity_profile") for node in published)
        await scan_copies(engine, runtime, owner, (marker, unissued, extra), tmp_path, caplog, capsys)
    finally:
        await engine.close()

    # Under a closed schema, generated output with an undeclared field is
    # refused against the admitted source; the declared shape is admitted.
    reference = uuid4()
    schema = ClosedSchema(reference, (("enabled", ValueValidator("boolean")),))
    engine, runtime, policy, saved = await opened(tmp_path / "strict", layout, analyzer, sanitizer_options={
        "schema_provider": Schemas(schema)}, policy_options={
        "strict_schema_ref": reference, "strict_schema_version": "prme-strict-schema-v1"})
    try:
        source = await admit(runtime.sanitizer, saved, canonical({"enabled": True}))
        with pytest.raises(PrivacyDenied):
            await admit(runtime.sanitizer, saved, "", mode="generated_output", source=source,
                        raw_fields=canonical({"enabled": True, "notes": "kept"}))
        admitted = await admit(runtime.sanitizer, saved, "", mode="generated_output", source=source,
                               raw_fields=canonical({"enabled": True}))
        assert json.loads(admitted.fields_json) == {"enabled": True}
    finally:
        await engine.close()
