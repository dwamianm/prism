"""Regenerate the LongMemEval-S control captures that the offline evidence gate replays.

The gate's LongMemEval-S half replays the control packs of the 2026-09-22
opt-in study's ``baseline`` arm and reads each question's user and reference
time from that arm's ``capture.json``. Those captures are not on the current
machine. The arm never stored anything itself: it belongs to the study's
historical stratum, so ``opt_in_arm_worker.capture`` passed each question to
``run_opt_in_interactions.capture``. That function checks the question's
2026-09-17 master pack (``data/benchmarks/longmemeval-s-prme-baseline-v1``)
against its saved capture, clones it, requires the clone to equal the master's
saved identity, and runs one cold and one warm ``retrieve()`` before writing
``capture.json``.

``build`` runs that same function over the same master packs, with the
settings the evidence gate retrieves with (the current defaults, organizer
maintenance off), and then writes ``manifest.json`` in the root. The stored
records are byte-identical to the lost controls'; only the receipts of the two
new retrievals differ. ``product_packing gate --lme-captures <root>`` replays
such a root and checks it against that manifest.

No reader, judge, extraction or other model call runs. The build never calls
``store()`` or ``ingest()``, query reformulation and temporal relations are
off, and the worker processes refuse every outbound network connection. Only
the local query embedding runs, from the locally cached model.

    uv run --no-sync python -m benchmarks.diagnostics.lme_control_captures build --jobs 12
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time

from benchmarks import checkout
from benchmarks.integrations import run_longmemeval_s_baseline as lme

ROOT = Path(__file__).resolve().parents[2]
KIND = "prme-lme-control-captures"
SCHEMA_VERSION = 1
# Relative to the main checkout, which holds data/.
MASTER = Path("data/benchmarks/longmemeval-s-prme-baseline-v1")
DATASET = Path("data/benchmarks/longmemeval/longmemeval_s_cleaned.json")
DEFAULT_ROOT = Path("data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline")
REGISTRATION = Path("benchmarks/results/research/2026-09-22/opt-in-successor-v2-registration.json")
CAPTURE_FUNCTION = "benchmarks.diagnostics.run_opt_in_interactions.capture"
NOT_APPLICABLE = (
    "The saved 2026-09-23 contexts' source_capture_sha256 (the digest of the lost capture file) and "
    "artifact_checksum (the lost control pack's identity after the study's own two retrievals wrote their "
    "receipts) cannot match a regenerated capture, so the gate checks instead that each capture's input pack "
    "equals its master pack's saved identity, which the study required of every control clone, and that each "
    "capture and pack equals this manifest.")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative(path: Path, home: Path) -> str:
    """A path relative to the main checkout, so the manifest names no machine."""
    path = path.resolve()
    return str(path.relative_to(home)) if path.is_relative_to(home) else str(path)


def baseline_arm() -> dict:
    """The registered baseline arm, retrieving with the evidence gate's settings instead of its registered ones."""
    from benchmarks.diagnostics.product_packing import gate_config

    registration = json.loads((ROOT / REGISTRATION).read_text())
    [arm] = [arm for arm in registration["arms"] if arm["id"] == "baseline"]
    if arm.get("stratum") != "historical" or any(arm["ingestion_flags"].values()):
        raise ValueError("The registered baseline arm no longer reuses the master packs")
    return {**arm, "config": gate_config(Path("{pack}")).model_dump(mode="json")}


def _refuse_network(*args, **kwargs):
    raise ConnectionRefusedError("The control capture build makes no network calls")


def _refuse_storage(*args, **kwargs):
    raise RuntimeError("The control capture build stores nothing; it only clones master packs and retrieves")


def _worker_init() -> None:
    """Run offline: refuse outbound connections and any store() or ingest(), and log failures."""
    from benchmarks.diagnostics import run_opt_in_interactions as study
    from benchmarks.diagnostics.product_packing import _quiet_offline_cli
    from prme import MemoryEngine

    for name in [name for name in os.environ if name.startswith("PRME_")]:
        del os.environ[name]
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    # The gate's own offline setup: local model files only, and warnings only from structlog.
    _quiet_offline_cli()
    socket.socket.connect = _refuse_network
    socket.socket.connect_ex = _refuse_network
    MemoryEngine.store = _refuse_storage
    MemoryEngine.ingest = _refuse_storage
    # As the study worker did: any logged failure or fallback fails the question.
    logger = logging.getLogger("prme")
    logger.addHandler(study.FailureObserver())
    logger.setLevel(logging.DEBUG)


async def _capture(case: dict, root: Path, master: Path, arm: dict) -> dict:
    from benchmarks.diagnostics import run_opt_in_interactions as study
    from benchmarks.diagnostics.product_packing import gate_config

    folder = root / lme._safe_id(case["question_id"])
    config = study.config_for(arm, folder / "pack", None)
    if config.model_dump(mode="json") != gate_config(folder / "pack").model_dump(mode="json"):
        raise ValueError("The capture would not retrieve with the evidence gate's settings")
    if config.enable_query_reformulation or config.temporal_relation.enabled or config.organizer.opportunistic_enabled:
        raise ValueError("The capture must run without model-backed query features or maintenance")
    folder.mkdir()
    errors: list = []
    token = study.ERRORS.set(errors)
    started = time.perf_counter()
    try:
        captured = await study.capture(case, arm, folder, master, None)
    finally:
        study.ERRORS.reset(token)
    if errors:
        shutil.rmtree(folder)
        raise RuntimeError(f"{case['question_id']}: retrieval logged a failure or fallback: {errors}")
    return {"question_id": case["question_id"], "seconds": time.perf_counter() - started,
            "pack_bytes": captured["final_pack"]["bytes"]}


def _capture_one(case: dict, root: str, master: str, arm: dict) -> dict:
    return asyncio.run(_capture(case, Path(root), Path(master), arm))


def _sources(home: Path) -> tuple[Path, Path, list[dict]]:
    master = home / MASTER
    dataset = home / DATASET
    if lme._sha256_file(dataset) != lme.DATASET_SHA256:
        raise ValueError(f"{dataset} differs from the LongMemEval-S dataset the master packs were built from")
    if not (master / "identity.json").is_file():
        raise ValueError(f"No master packs at {master}")
    return master, dataset, lme._load_dataset(dataset)


def build(root: Path, jobs: int) -> dict:
    """Capture every question not yet captured under ``root``, then write the manifest."""
    home = checkout.main_checkout()
    master, dataset, cases = _sources(home)
    if (root / "manifest.json").exists():
        raise ValueError(f"{root} is already complete; its manifest exists")
    arm = baseline_arm()
    root.mkdir(parents=True, exist_ok=True)
    pending = []
    for case in cases:
        folder = root / lme._safe_id(case["question_id"])
        if (folder / "capture.json").is_file():
            continue
        if folder.exists():
            # A question that stopped part way: its clone is ours and incomplete.
            shutil.rmtree(folder)
        pending.append(case)
    started_at, started = _utc(), time.perf_counter()
    print(json.dumps({"event": "build_started", "at": started_at, "root": str(root), "questions": len(cases),
                      "pending": len(pending), "jobs": jobs}), flush=True)
    pool = ProcessPoolExecutor(jobs, initializer=_worker_init)
    try:
        futures = [pool.submit(_capture_one, case, str(root), str(master), arm) for case in pending]
        for done, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            print(json.dumps({"event": "captured", "done": done, "of": len(pending), **row,
                              "elapsed": round(time.perf_counter() - started, 1)}), flush=True)
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    manifest = write_manifest(root, cases, arm=arm, home=home, master=master, dataset=dataset,
                              timing={"started_at": started_at, "seconds": time.perf_counter() - started,
                                      "jobs": jobs, "questions_captured": len(pending)})
    print(json.dumps({"event": "build_finished", "at": _utc(), "questions": manifest["questions"],
                      "bytes": manifest["bytes"]}), flush=True)
    return manifest


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def _manifest_text(manifest: dict) -> str:
    """Indented JSON with one line per question, so the tracked copy stays compact."""
    head = json.dumps({key: value for key, value in manifest.items() if key != "cases"}, indent=2,
                      ensure_ascii=False, allow_nan=False)
    rows = ",\n".join("    " + json.dumps(row, ensure_ascii=False, allow_nan=False) for row in manifest["cases"])
    return head[:-2] + ',\n  "cases": [\n' + rows + "\n  ]\n}\n"


def write_manifest(root: Path, cases: list[dict], *, arm: dict, home: Path, master: Path, dataset: Path,
                   timing: dict) -> dict:
    """Check every capture and pack once more and record them with the master pack each was cloned from."""
    from benchmarks.diagnostics.product_packing import pack_identity

    rows = []
    for case in cases:
        question_id = case["question_id"]
        folder = root / lme._safe_id(question_id)
        raw = (folder / "capture.json").read_bytes()
        capture = json.loads(raw)
        saved_path = master / "captures" / f"{question_id}.json"
        saved = json.loads(saved_path.read_text())
        master_tree = saved["pack"]["tree_sha256"]
        if capture["input_pack"]["tree_sha256"] != master_tree:
            raise ValueError(f"{question_id}: the clone differs from its master pack's saved identity")
        if lme._tree_identity(master / "packs" / question_id)["tree_sha256"] != master_tree:
            raise ValueError(f"{question_id}: the master pack changed")
        pack_tree = pack_identity(folder / "pack")
        if pack_tree != capture["final_pack"]["tree_sha256"]:
            raise ValueError(f"{question_id}: the pack changed after its capture")
        if capture["ingestion"].get("kind") != "reused_historical" or not capture["cold_warm_context_equal"]:
            raise ValueError(f"{question_id}: the capture is not a reused master pack with matching retrievals")
        rows.append({"question_id": question_id, "capture_sha256": hashlib.sha256(raw).hexdigest(),
                     "capture_bytes": len(raw), "pack_tree_sha256": pack_tree,
                     "pack_bytes": capture["final_pack"]["bytes"], "master_pack_tree_sha256": master_tree,
                     "master_capture_sha256": lme._sha256_file(saved_path)})
    config = arm["config"]
    status = _git("status", "--porcelain", "--untracked-files=all").splitlines()
    pack_bytes = sum(row["pack_bytes"] for row in rows)
    capture_bytes = sum(row["capture_bytes"] for row in rows)
    manifest = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "complete": True, "label": root.parent.name,
        "built_at": _utc(), "build": timing,
        "purpose": ("Regenerated equivalents of the 2026-09-22 opt-in study's LongMemEval-S baseline controls, "
                    "which the offline evidence gate replays: each question's 2026-09-17 master pack cloned by the "
                    "study's own capture function, plus a cold and a warm retrieval with the gate's settings."),
        "not_applicable": NOT_APPLICABLE,
        "root": _relative(root, home),
        "code": {"commit": _git("rev-parse", "HEAD").strip(), "dirty": bool(status),
                 "changed_files": sorted(line[3:] for line in status),
                 "driver_sha256": lme._sha256_file(Path(__file__)), "capture_function": CAPTURE_FUNCTION,
                 "arm": {"registration": str(REGISTRATION),
                         "registration_sha256": lme._sha256_file(ROOT / REGISTRATION),
                         "id": arm["id"], "stratum": arm["stratum"]}},
        "versions": {"python": platform.python_version(), "platform": platform.platform(),
                     **{name: importlib.metadata.version(name)
                        for name in ("prme", "duckdb", "usearch", "tantivy", "fastembed", "onnxruntime", "numpy",
                                     "tiktoken", "dateparser")}},
        "embedding": {key: config["embedding"][key] for key in ("provider", "model_name", "dimension")},
        "retrieval": {"settings": "The evidence gate's settings (product_packing.gate_config): the current "
                                  "defaults, with organizer maintenance off and no environment or .env values.",
                      "retrievals_per_question": 2,
                      "config_sha256": hashlib.sha256(lme._canonical(config)).hexdigest(),
                      "scoring": config["scoring"], "packing": config["packing"]},
        "dataset": {"path": _relative(dataset, home), "sha256": lme.DATASET_SHA256},
        "source": {"root": _relative(master, home),
                   "identity_sha256": lme._sha256_file(master / "identity.json"),
                   "registration_sha256": json.loads((master / "identity.json").read_text())["registration_sha256"],
                   "packs_matching_saved_identity": len(rows)},
        "questions": len(rows),
        "bytes": {"packs": pack_bytes, "captures": capture_bytes, "total": pack_bytes + capture_bytes},
        "cases": rows,
    }
    text = _manifest_text(manifest)
    if json.loads(text) != manifest:
        raise AssertionError("The manifest does not round-trip")
    with (root / "manifest.json").open("x") as handle:
        handle.write(text)
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.lme_control_captures",
                                     description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--root", type=Path,
                        help=f"New capture root (default: the main checkout's {DEFAULT_ROOT})")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 4),
                        help="Worker processes, one question at a time each")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    root = args.root or checkout.main_checkout() / DEFAULT_ROOT
    manifest = build(root.resolve(), args.jobs)
    print(json.dumps({"root": str(root), "questions": manifest["questions"], "bytes": manifest["bytes"]}),
          file=sys.stderr)


if __name__ == "__main__":
    main()
