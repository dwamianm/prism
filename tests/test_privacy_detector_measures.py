"""PV-11 detector measures through the full local sanitizer (#327).

The frozen 69-row synthetic fixture runs through LocalSanitizer.admit, not
only the analyzer, under the balanced preset and again with contact
pseudonymization. The report records removal and blocking, benign-text damage,
key-provider calls, retained bytes and latency. It is local synthetic
evidence for fixed declared examples, not a general accuracy claim.
"""

import json
import os
import statistics
import time
from pathlib import Path
from uuid import uuid4

from prme.privacy import CategoryAction, PrivacyDenied
from prme.privacy.local_analyzer import VERSIONS
from prme.privacy.recognizers import CustomRule
from prme.privacy.sanitizer import LocalSanitizer
from tests.privacy_sanitizer_support import Rules, ScopedTestKeys, admit, setup
from tests.privacy_sanitizer_support import local_analyzer_fixture as local_analyzer_fixture

FIXTURE = Path(__file__).parent / "fixtures/privacy/sanitizer-v1.json"
PSEUDONYMIZED = ("email", "phone", "person")


class CountingKeys(ScopedTestKeys):
    def __init__(self):
        super().__init__()
        self.requests = 0

    async def key_for(self, *args):
        self.requests += 1
        return await super().key_for(*args)


async def measure(analyzer, rows, *, pseudonymize):
    keys, epoch = CountingKeys(), uuid4()
    # The fixture's own custom rules are configured, as a caller would.
    rules = Rules(tuple(dict.fromkeys(CustomRule(**rule) for row in rows for rule in row["rules"])))
    options = {"exclusion_refs": (uuid4(),)}
    if pseudonymize:
        options["category_actions"] = tuple(CategoryAction(category=name, action="pseudonymize")
                                            for name in PSEUDONYMIZED)
    runtime, policy, saved = setup(analyzer, policy_options=options, sanitizer_options={
        "key_provider": keys, "pseudonym_epoch": epoch, "rule_provider": rules})
    keys.provision(policy, epoch)
    sanitizer = runtime.sanitizer
    assert isinstance(sanitizer, LocalSanitizer)
    await admit(sanitizer, saved, "Warm the detector worker.")
    observations = []
    for row in rows:
        before = keys.requests
        started = time.perf_counter()
        try:
            result = await admit(sanitizer, saved, row["text"])
            content, outcome = json.loads(result.fields_json)["content"], "admitted"
            counts = dict(result.action_counts)
        except PrivacyDenied as exc:
            content, outcome, counts = None, f"refused:{exc.code}", {}
        elapsed = (time.perf_counter() - started) * 1000
        values = [row["text"][span["start"]:span["end"]] for span in row["expected_spans"]]
        removed = outcome != "admitted" or all(value not in content for value in values)
        observations.append({
            "id": row["id"], "category": row["category"], "label": row["label"], "outcome": outcome,
            "expected_values_removed": removed if values else None,
            "benign_changed": (row["label"] == "benign" and (outcome != "admitted" or content != row["text"])),
            "action_counts": counts, "key_provider_calls": keys.requests - before,
            "input_bytes": len(row["text"].encode()),
            "retained_bytes": 0 if content is None else len(content.encode()),
            "latency_ms": round(elapsed, 3),
        })
    actions = {item.category: item.action for item in policy.actions()}
    return observations, actions


def summarize(observations, actions):
    by_category = {}
    for item in observations:
        entry = by_category.setdefault(item["category"], {
            "policy_action": actions.get(item["category"], "not_a_policy_category"), "supported_positive": 0, "removed_or_refused": 0, "benign": 0, "benign_changed": 0,
            "unsupported": 0, "unsupported_removed_or_refused": 0})
        if item["label"] == "supported_positive":
            entry["supported_positive"] += 1
            entry["removed_or_refused"] += int(item["expected_values_removed"])
        elif item["label"] == "benign":
            entry["benign"] += 1
            entry["benign_changed"] += int(item["benign_changed"])
        else:
            entry["unsupported"] += 1
            entry["unsupported_removed_or_refused"] += int(bool(item["expected_values_removed"]))
    latencies = sorted(item["latency_ms"] for item in observations)
    admitted = [item for item in observations if item["outcome"] == "admitted"]
    return {
        "categories": by_category,
        "refused_rows": sum(item["outcome"] != "admitted" for item in observations),
        "benign_rows_changed": sum(item["benign_changed"] for item in observations),
        "key_provider_calls": sum(item["key_provider_calls"] for item in observations),
        "input_bytes": sum(item["input_bytes"] for item in observations),
        "retained_bytes_of_admitted_rows": sum(item["retained_bytes"] for item in admitted),
        "input_bytes_of_admitted_rows": sum(item["input_bytes"] for item in admitted),
        "latency_ms": {"median": statistics.median(latencies),
                       "p95": latencies[int(0.95 * (len(latencies) - 1))], "max": latencies[-1]},
    }


async def test_PV11_detector_measures(tmp_path, analyzer):
    rows = json.loads(FIXTURE.read_text())["rows"]
    assert len(rows) == 69
    report = {"schema_version": 1, "fixture": FIXTURE.name, "detector_versions": VERSIONS,
              "scope": ("Local synthetic rows through LocalSanitizer.admit with English declared. "
                        "Not a general precision, recall or anonymity claim."),
              "limits": [
                  "English only; the caller declares the language and no detection runs.",
                  "Recognizers are the pinned local Presidio and spaCy en_core_web_sm models plus the bounded rule registry.",
                  "Contextual sensitivity (a value that is private only in context) is not detected.",
                  "Unsupported formats in the fixture are recorded as such; a miss there is expected, not a pass."]}
    for name, pseudonymize in (("balanced", False), ("pseudonymize_contacts", True)):
        observations, actions = await measure(analyzer, rows, pseudonymize=pseudonymize)
        report[name] = {"summary": summarize(observations, actions), "rows": observations}
    path = Path(os.environ.get("PRME_PV11_DETECTOR_MEASURES", tmp_path / "detector-measures.json"))
    path.write_text(json.dumps(report, indent=2) + "\n")
    # Mandatory forms only, as in test_frozen_detector_rows; the rest is report.
    for name in ("balanced", "pseudonymize_contacts"):
        credential = report[name]["summary"]["categories"]["credential"]
        assert credential["removed_or_refused"] == credential["supported_positive"]
    assert report["balanced"]["summary"]["key_provider_calls"] == 0
