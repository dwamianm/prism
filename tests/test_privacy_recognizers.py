"""Frozen synthetic detector observations, separate from product coverage."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from prme.privacy.local_analyzer import CompletePattern, LocalAnalyzer, RecognizerIncomplete, VERSIONS
from prme.privacy.recognizers import CustomRule, bounded_spans

FIXTURES = Path(__file__).parent / "fixtures" / "privacy"


@pytest.mark.asyncio
async def test_frozen_detector_rows(tmp_path):
    fixture = FIXTURES / "sanitizer-v1.json"
    manifest = json.loads((FIXTURES / "sanitizer-v1-artifacts.json").read_text())
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == manifest["fixture_sha256"]
    rows = json.loads(fixture.read_text())["rows"]
    assert len(rows) == 69
    analyzer = LocalAnalyzer()
    observations = []
    counts = {}
    try:
        await analyzer.ready()
        for row in rows:
            result = await analyzer.analyze((row["text"],), seconds=2)
            assert result.complete and result.versions == VERSIONS
            rules = tuple(CustomRule(**rule) for rule in row["rules"])
            spans = result.spans[0] + tuple(bounded_spans(row["text"], rules))
            actual = {(span.start, span.end) for span in spans if span.category == row["category"]}
            expected = {(span["start"], span["end"]) for span in row["expected_spans"]}
            matrix = counts.setdefault(row["category"], {"tp": 0, "fp": 0, "fn": 0, "tn": 0,
                                                        "unsupported_detected": 0, "unsupported_missed": 0})
            if row["label"] == "unsupported":
                matrix["unsupported_detected" if actual else "unsupported_missed"] += 1
            else:
                matrix["tp"] += len(actual & expected)
                matrix["fp"] += len(actual - expected)
                matrix["fn"] += len(expected - actual)
                matrix["tn"] += int(not actual and not expected)
            observations.append({"id": row["id"], "category": row["category"], "label": row["label"],
                                 "expected_spans": sorted(expected), "actual_spans": sorted(actual),
                                 "other_categories": sorted({span.category for span in spans} - {row["category"]})})
    finally:
        analyzer.close()
    report = {"fixture_sha256": manifest["fixture_sha256"], "detector_versions": VERSIONS,
              "rows": observations, "counts": counts, "scope": "local synthetic recognition only"}
    path = Path(os.environ.get("PRME_SANITIZER_DETECTOR_REPORT", tmp_path / "detector-results.json"))
    path.write_text(json.dumps(report, indent=2) + "\n")
    # Deterministic credential/literal forms are mandatory. NLP observations are
    # retained as precision/recall evidence, without tuning a new threshold.
    assert counts["credential"]["fn"] == counts["credential"]["fp"] == 0
    assert counts["custom_identifier"]["fn"] == counts["custom_identifier"]["fp"] == 0
    assert counts["sensitive_topic"]["fn"] == counts["sensitive_topic"]["fp"] == 0


@pytest.mark.parametrize("during_iteration", [False, True])
def test_early_pattern_timeout_is_not_swallowed(during_iteration):
    import re
    from presidio_analyzer import Pattern, PatternRecognizer

    class EarlyTimeout:
        def finditer(self, text, *, timeout):
            if not during_iteration:
                raise TimeoutError("synthetic private pattern detail")
            return self.iterate()

        def iterate(self):
            yield re.match("synthetic", "synthetic input")
            raise TimeoutError("synthetic private pattern detail")

    pattern = Pattern("injected timeout", "synthetic", 1)
    recognizer = PatternRecognizer(supported_entity="EMAIL_ADDRESS", patterns=[pattern])
    pattern.compiled_regex = CompletePattern(EarlyTimeout())
    pattern.compiled_with_flags = recognizer.global_regex_flags
    with pytest.raises(RecognizerIncomplete, match="privacy_capability_unavailable") as error:
        recognizer.analyze("synthetic input", entities=["EMAIL_ADDRESS"])
    assert not isinstance(error.value, TimeoutError)
    assert error.value.__cause__ is None


def test_offline_suffix_configuration_has_no_network_or_cache(tmp_path, monkeypatch):
    import socket
    import tldextract

    def forbidden(*args, **kwargs):
        pytest.fail("offline suffix adapter attempted a network/cache effect")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(tldextract.cache.DiskCache, "set", forbidden)
    suffixes = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=(),
                                   fallback_to_snapshot=True, include_psl_private_domains=False)
    assert suffixes("sample.person@example.com").fqdn == "example.com"
    assert not suffixes("invalid.invalidtld").fqdn
    assert not list(tmp_path.iterdir())


def test_complete_registry_rejects_missing_recognizer(monkeypatch):
    from presidio_analyzer.recognizer_registry import RecognizerRegistry
    from prme.privacy.local_analyzer import build_engine

    analyze = build_engine()
    original = RecognizerRegistry.get_recognizers

    def missing(self, *args, **kwargs):
        return [item for item in original(self, *args, **kwargs) if item.name != "UsSsnRecognizer"]

    monkeypatch.setattr(RecognizerRegistry, "get_recognizers", missing)
    with pytest.raises(RecognizerIncomplete, match="privacy_capability_unavailable"):
        analyze("The cup is on the table.")


def _stalled_worker(connection):
    import time

    connection.send(("ready", VERSIONS))
    connection.recv()
    time.sleep(1)
    connection.send(("complete", ((),)))


@pytest.mark.asyncio
async def test_supervisor_discards_late_worker_result(monkeypatch):
    import prme.privacy.local_analyzer as local
    from prme.privacy.policy import PrivacyDenied

    monkeypatch.setattr(local, "_worker", _stalled_worker)
    analyzer = LocalAnalyzer()
    try:
        await analyzer.ready()
        with pytest.raises(PrivacyDenied, match="privacy_capability_unavailable"):
            await analyzer.analyze(("The cup is on the table.",), seconds=0.01)
        assert analyzer._connection is None and analyzer._process is None
    finally:
        analyzer.close()
