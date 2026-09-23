"""Audited, budget-reserved Responses calls for registered benchmark runs."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

import httpx

MODEL = "gpt-5.4-2026-03-05"
# The user enabled auto top-up and authorized both full cohorts on 2026-09-23.
# Request count and token caps remain bounded by the frozen protocol.
CAP_NANODOLLARS = None
READER_LIMIT = 8192
JUDGE_LIMIT = 2048


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


class BudgetExhausted(RuntimeError):
    pass


def usage_cost(body):
    usage = body["usage"]
    inp, out = usage["input_tokens"], usage["output_tokens"]
    cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
    if any(type(v) is not int or v < 0 for v in (inp, out, cached)) or cached > inp:
        raise ValueError("Invalid provider usage")
    rates = (1250, 125, 7500) if body.get("service_tier") == "flex" else (2500, 250, 15000)
    return (inp - cached) * rates[0] + cached * rates[1] + out * rates[2]


class Ledger:
    """One locked study ledger; unknown requests keep their full reservation."""

    def __init__(self, path, cap=CAP_NANODOLLARS):
        self.path = Path(path)
        self.cap = cap
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def update(self, key=None, reserve=None, actual=None):
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = json.loads(self.path.read_text()) if self.path.exists() else {
                "cap_nanodollars": self.cap, "entries": {},
            }
            if value["cap_nanodollars"] != self.cap:
                raise ValueError("Budget identity differs")
            entries = value["entries"]
            if reserve is not None:
                if key in entries:
                    raise ValueError("Request already reserved; never replay an uncertain request")
                if self.cap is not None and sum(v["charge"] for v in entries.values()) + reserve > self.cap:
                    raise BudgetExhausted("Study spending cap reached before request")
                entries[key] = {"charge": reserve, "reservation": reserve, "settled": False}
            if actual is not None:
                row = entries[key]
                if row["settled"] or actual < 0 or actual > row["reservation"]:
                    raise ValueError("Invalid cost settlement")
                row.update(charge=actual, settled=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(value, indent=2) + "\n")
            os.replace(temp, self.path)
            return value


def response_text(body):
    if body.get("model") != MODEL or body.get("status") != "completed":
        raise ValueError("Incomplete response or changed model")
    if body.get("error") or body.get("incomplete_details"):
        raise ValueError("Provider response failed")
    text = "\n".join(c["text"] for item in body.get("output", [])
                     for c in item.get("content", []) if c.get("type") == "output_text").strip()
    if not text:
        raise ValueError("Missing answer text")
    return text


async def call(client, semaphore, ledger, prompt, limit, path):
    path = Path(path)
    body = {"model": MODEL, "input": prompt, "reasoning": {"effort": "medium"},
            "service_tier": "flex", "max_output_tokens": limit, "store": False}
    write_new(path.with_suffix(".request.json"), body)
    # UTF-8 bytes dominate tokenizer count. Extra 1024 covers request framing.
    reservation = (len(prompt.encode()) + 1024) * 2500 + limit * 15000
    started = time.perf_counter()
    async with semaphore:
        for attempt in range(1, 5):
            key = str(path.resolve()) + f":{attempt}"
            ledger.update(key, reserve=reservation)
            stamp = time.perf_counter()
            try:
                r = await client.post("/responses", json=body)
                value = r.json()
            except Exception as exc:
                write_new(path.with_suffix(f".attempt-{attempt}.json"), {
                    "exception_type": type(exc).__name__, "unknown_charge_reserved": reservation,
                })
                raise RuntimeError("Ambiguous provider failure retained without retry") from None
            record = {"http_status": r.status_code, "response": value,
                      "seconds": time.perf_counter() - stamp, "request_sha256": sha(body)}
            write_new(path.with_suffix(f".attempt-{attempt}.json"), record)
            if isinstance(value.get("usage"), dict):
                ledger.update(key, actual=usage_cost(value))
            elif r.status_code in {400, 401, 403, 404, 429}:
                ledger.update(key, actual=0)
            if not r.is_success:
                code = value.get("error", {}).get("code")
                if r.status_code in {429, 500, 502, 503, 504, 529} and code not in {
                    "credit_balance_exhausted", "insufficient_quota",
                } and attempt < 4:
                    await asyncio.sleep(min(8, 2 ** attempt))
                    continue
                raise RuntimeError(f"Provider HTTP {r.status_code}; attempt retained")
            if not isinstance(value.get("usage"), dict) or value.get("service_tier") != "flex":
                raise ValueError("Missing cost accounting or unexpected service tier")
            text = response_text(value)
            result = {"text": text, "response": value, "attempts": attempt,
                      "seconds": time.perf_counter() - started, "request_sha256": sha(body)}
            write_new(path, result)
            return result
    raise RuntimeError("Provider attempts exhausted")


def client_for(key):
    return httpx.AsyncClient(base_url="https://api.openai.com/v1",
                             headers={"Authorization": "Bearer " + key},
                             timeout=httpx.Timeout(900, connect=30))
