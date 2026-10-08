"""Fast shadow assay of Jev as a gate on proposed owner-memory claims.

Register before collecting; replay cached probabilities without provider calls.
This authored development set does not measure held-out or answer accuracy.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time

import httpx

from benchmarks.diagnostics.memory_admission_cases import cases
from prme.ingestion.extraction import _validate_fact_source_support
from prme.ingestion.schema import ExtractedFact

MODEL = "jev-1.13.0"
API_URL = "https://api.typesafe.ai/v1/systemone"
THRESHOLD = 0.9
QUESTIONS = {
    "supported": {
        "type": "noul",
        "instructions": "Does `source` establish all of `claim` exactly as written, preserving attribution, negation, timing, conditions and whether an action occurred?",
        "criteria": {
            "true": "The entire claim follows from this source alone. A request to be called a name or to change answer style supports that preference.",
            "false": "Any material part is unsupported, misattributed, contradicted, hypothetical, conditional, a question, or changed from an attempt or intention into a completed action.",
        },
    },
    "owner_attributed": {
        "type": "noul",
        "instructions": "Does `source` attribute `claim` to its user-author, rather than another person, an example, pasted document or quoted speaker?",
        "criteria": {
            "true": "Plain first-person statements and requests about the author's own name or answer preferences. A quoted phrase is allowed if the author explicitly endorses it as describing them.",
            "false": "Another person, quoted or pasted text, sample text, or insufficient evidence of attribution to the author. Possessive 'my' does not make a brother's or colleague's property the author's property.",
        },
    },
    "kind": {
        "type": "choice",
        "instructions": "What kind of memory does `claim` express, assuming only for this question that it is supported?",
        "criteria": {
            "fact": "An attribute, state or completed event.",
            "preference": "A desired name, answer style, like or dislike.",
            "decision": "An explicit choice or commitment.",
            "other": "None of these or insufficient information.",
        },
    },
}


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def local_admission(row):
    if row["role"] != "user":
        return False
    try:
        _validate_fact_source_support(
            ExtractedFact.model_validate(row["fact"]), row["source"]
        )
    except ValueError:
        return False
    return True


def payload(row):
    # Labels and IDs never reach the provider.
    return {
        "model": MODEL,
        "questions": QUESTIONS,
        "state": {
            "source": row["source"],
            "claim": row["claim"],
            "evidence_quote": row["fact"]["evidence_quote"],
        },
    }


def registration():
    return {
        "schema_version": 1,
        "kind": "jev-owner-memory-admission-development",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "model": MODEL,
        "endpoint": API_URL,
        "questions": QUESTIONS,
        "cases": cases(),
        "implementation_sha256": digest(
            {
                name: Path(name).read_text()
                for name in (
                    "benchmarks/diagnostics/jev_memory_admission.py",
                    "benchmarks/diagnostics/memory_admission_cases.py",
                    "src/prme/ingestion/extraction.py",
                    "src/prme/ingestion/grounding.py",
                    "src/prme/ingestion/schema.py",
                )
            }
        ),
        "threshold": THRESHOLD,
        "concurrency": 4,
        "timeout_seconds": 30,
        "max_requests": 40,
        "attempts_per_case": 1,
        "screening_gate": {
            "max_false_admissions": 0,
            "min_valid_retention": 0.9,
            "min_supported_kind_accuracy": 0.9,
        },
        "limits": "Authored development contrasts, not held-out accuracy or a default-promotion gate. No memory writes or reader/judge calls.",
    }


def validate_registration(value):
    current = registration()
    for key in current.keys() - {"created_at", "commit"}:
        if value.get(key) != current[key]:
            raise ValueError(f"registration changed: {key}")
    return value


def probability(value):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError("invalid probability")
    return value


def validate_response(value):
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise ValueError("model mismatch")
    answers = value.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
        raise ValueError("answer mismatch")
    for name in ("supported", "owner_attributed"):
        if not isinstance(answers[name], dict) or answers[name].get("type") != "noul":
            raise ValueError("invalid Noul")
        probability(answers[name].get("noul"))
    kind = answers["kind"]
    options = set(QUESTIONS["kind"]["criteria"])
    if (
        not isinstance(kind, dict)
        or kind.get("type") != "choice"
        or kind.get("choice") not in options
    ):
        raise ValueError("invalid Choice")
    distribution = kind.get("probabilities")
    if not isinstance(distribution, dict) or set(distribution) != options:
        raise ValueError("invalid Choice distribution")
    if not math.isclose(
        sum(probability(p) for p in distribution.values()), 1, abs_tol=0.02
    ):
        raise ValueError("invalid Choice sum")
    probability(kind.get("confidence"))
    usage = value.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(k)) is not int or usage[k] < 0
        for k in ("input_tokens", "output_tokens")
    ):
        raise ValueError("invalid usage")
    return value


def cache_identity(reg):
    return digest(reg)


def read_cache(path, reg):
    if not path.exists():
        return {"registration_sha256": cache_identity(reg), "results": {}}
    cache = json.loads(path.read_text())
    if cache.get("registration_sha256") != cache_identity(reg):
        raise ValueError("cache belongs to a different registration")
    expected = {row["id"]: row for row in reg["cases"]}
    if not isinstance(cache.get("results"), dict) or not set(cache["results"]) <= set(
        expected
    ):
        raise ValueError("invalid cache cases")
    for name, entry in cache["results"].items():
        if entry.get("request_sha256") != digest(payload(expected[name])):
            raise ValueError("cached request differs")
        if "response" in entry:
            validate_response(entry["response"])
    return cache


async def collect(reg, path):
    cache = read_cache(path, reg)
    key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if not key:
        from dotenv import dotenv_values

        values = dotenv_values(".env")
        key = values.get("JEV_API_KEY") or values.get("TYPESAFE_API_KEY")
    if not key:
        raise ValueError("set JEV_API_KEY or TYPESAFE_API_KEY")
    semaphore = asyncio.Semaphore(reg["concurrency"])
    write(path, cache)
    async with httpx.AsyncClient(timeout=reg["timeout_seconds"]) as client:

        async def one(row):
            if row["id"] in cache["results"]:
                return
            request = payload(row)
            async with semaphore:
                started = time.perf_counter()
                entry = {"request_sha256": digest(request)}
                try:
                    result = await client.post(
                        API_URL,
                        json=request,
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if result.status_code != 200:
                        entry["error"] = f"http_{result.status_code}"
                    else:
                        entry["response"] = validate_response(result.json())
                except (httpx.HTTPError, ValueError) as exc:
                    entry["error"] = type(exc).__name__
                entry["elapsed_seconds"] = time.perf_counter() - started
            cache["results"][row["id"]] = entry
            write(path, cache)

        await asyncio.gather(*(one(row) for row in reg["cases"]))
    return cache


def metrics(rows, decisions):
    counts = Counter((row["expected_admit"], decisions[row["id"]]) for row in rows)
    tp, fp, fn, tn = (
        counts[key]
        for key in ((True, True), (False, True), (True, False), (False, False))
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "false_admissions": fp,
        "valid_claims_lost": fn,
        "valid_retention": tp / (tp + fn) if tp + fn else None,
        "false_admission_rate": fp / (fp + tn) if fp + tn else None,
    }


def score(reg, cache, threshold=THRESHOLD):
    probability(threshold)
    local, gate, rows = {}, {}, []
    errors, abstentions, kind_correct, kind_total = 0, 0, 0, 0
    elapsed, tokens = [], Counter()
    for row in reg["cases"]:
        name = row["id"]
        local[name] = local_admission(row)
        entry = cache["results"].get(name, {})
        answers = entry.get("response", {}).get("answers")
        gate[name] = False
        if answers is None:
            errors += 1
        else:
            validate_response(entry["response"])
            signals = [answers[k]["noul"] for k in ("supported", "owner_attributed")]
            gate[name] = local[name] and all(p >= threshold for p in signals)
            abstentions += (
                local[name]
                and not gate[name]
                and all(p > 1 - threshold for p in signals)
            )
            if row["expected_admit"]:
                kind_total += 1
                kind_correct += answers["kind"]["choice"] == row["fact"]["fact_type"]
            elapsed.append(entry["elapsed_seconds"])
            tokens.update(entry["response"]["usage"])
        rows.append(
            {
                "id": name,
                "family": row["family"],
                "expected_admit": row["expected_admit"],
                "local_admit": local[name],
                "jev_gate_admit": gate[name],
                "provider_error": answers is None,
            }
        )
    result = metrics(reg["cases"], gate)
    return {
        "registration_sha256": cache_identity(reg),
        "threshold": threshold,
        "exploratory_threshold": threshold != reg["threshold"],
        "local_support_validator": metrics(reg["cases"], local),
        "jev_gate": result,
        "provider_errors_or_missing": errors,
        "abstentions": abstentions,
        "supported_kind_correct": kind_correct,
        "supported_kind_total": kind_total,
        "screening_passed": errors == 0
        and result["false_admissions"] == 0
        and result["valid_retention"] >= 0.9
        and kind_total > 0
        and kind_correct / kind_total >= 0.9,
        "by_family": {
            family: metrics([r for r in reg["cases"] if r["family"] == family], gate)
            for family in sorted({r["family"] for r in reg["cases"]})
        },
        "latency_seconds": {
            "median": sorted(elapsed)[len(elapsed) // 2] if elapsed else None,
            "p95": sorted(elapsed)[math.ceil(len(elapsed) * 0.95) - 1]
            if elapsed
            else None,
        },
        "usage": dict(tokens),
        "cases": rows,
        "limits": reg["limits"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("register", "collect", "score"))
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    args = parser.parse_args()
    if args.command == "register":
        if args.registration.exists():
            raise ValueError("refusing to replace a registration")
        write(args.registration, registration())
        print(f"Registered {len(cases())} authored development cases.")
        return
    reg = validate_registration(json.loads(args.registration.read_text()))
    if args.cache is None or args.output is None:
        parser.error("collect/score require --cache and --output")
    cache = (
        asyncio.run(collect(reg, args.cache))
        if args.command == "collect"
        else read_cache(args.cache, reg)
    )
    report = score(reg, cache, args.threshold)
    write(args.output, report)
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in ("cases", "by_family")
            }
        )
    )
    if report["provider_errors_or_missing"]:
        raise SystemExit(2)
    if not report["screening_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
