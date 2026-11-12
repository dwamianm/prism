"""Task 21 bar (a): read scratch copies of fixed packs and record every response and receipt.

Run from a worktree root with that worktree's environment, for example:
  cd /tmp/task21/main && PYTHONPATH=. uv run python /tmp/task21/scripts/same_outputs.py --side main
Each pack is copied before it is opened (a read writes a receipt into the pack). Providers are blocked.
The output per pack is a gzipped JSON lines file: one line per question with the normalized response, then one
line per stored receipt. Normalized means: durations dropped, the request id replaced, source file hashes masked.
"""
import argparse, asyncio, gzip, hashlib, json, os, shutil, sys, time
from datetime import datetime, timedelta
from pathlib import Path

os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                   "PRME_BENCH_CACHE_ONLY": "1"})
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.expanduser("~/Sites/prism-write-rate/research/write-rate-2026-11"))

P2 = Path("~/Sites/prism-write-rate/data/write-rate-2026-11").expanduser()
STATED = {400: P2 / "m4/current-a/400/pack", 4000: P2 / "m4/current-a/4000/pack", 20500: P2 / "m3-pack/pack"}
LOCOMO = Path("~/Sites/prism/data/mid-tier-v1/builds/main-0.17.0a6-locomo/locomo").expanduser()
CONVERSATIONS = ("conv-26", "conv-30", "conv-41")


def normalized(value, request_id: str):
    text = json.dumps(value, sort_keys=True, default=str).replace(request_id, "RID")

    def strip(item):
        if isinstance(item, dict):
            return {k: ("MASKED" if k == "source_files_sha256" else strip(v)) for k, v in item.items()
                    if k not in ("timing_ms", "receipt_logging_ms") and not k.endswith("_ms")}
        if isinstance(item, list):
            return [strip(v) for v in item]
        return item
    return strip(json.loads(text))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def receipts(database: Path) -> list[dict]:
    import duckdb
    con = duckdb.connect(str(database), read_only=True)
    try:
        rows = con.execute("SELECT payload::VARCHAR FROM operations WHERE op_type = 'RETRIEVAL_REQUEST' "
                           "ORDER BY created_at").fetchall()
    finally:
        con.close()
    out = []
    for (payload,) in rows:
        data = json.loads(payload)
        rid = data.pop("request_id")
        data.pop("receipt_checksum", None)
        receipt = json.loads(data.pop("receipt"))
        out.append({"query": data.get("query"), "receipt": normalized(receipt, rid), "fields": normalized(data, rid)})
    return out


async def read_pack(engine, questions, user_id, reference_time):
    lines = []
    for question in questions:
        started = time.perf_counter()
        response = await engine.retrieve(question, user_id=user_id, reference_time=reference_time)
        ms = 1000 * (time.perf_counter() - started)
        rid = str(response.metadata.request_id)
        body = normalized(response.model_dump(mode="json"), rid)
        lines.append({"question": question, "ms": round(ms, 1), "response": body,
                      "ranked": [str(c.node.id) for c in response.results],
                      "packed": [str(c.node.id) for s in response.bundle.sections.values() for c in s],
                      "context_sha": hashlib.sha256(response.bundle.render().encode()).hexdigest()})
    return lines


async def stated(side_dir: Path, size: int):
    import common, m4_compare
    from prme.storage.engine import MemoryEngine
    from tests.contract.harness import ExtractionReplay, product_config
    directory = side_dir / f"stated-{size}"
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    os.system(f"cp -cR '{STATED[size]}' '{directory / 'pack'}'")
    config = product_config(str(directory), organizer={"opportunistic_enabled": False})
    engine = await MemoryEngine.create(config)
    guard = ExtractionReplay({"calls": []})
    guard.install(engine._pipeline._extraction_provider)
    reference_time = common.BASE + common.STEP * size + timedelta(minutes=1)
    try:
        lines = await read_pack(engine, [q for q, _ in m4_compare.questions(min(size, 20000))], common.USER,
                                reference_time)
        assert not guard.misses, guard.misses[:3]
    finally:
        await engine.close()
    return lines, receipts(directory / "pack" / "memory.duckdb")


async def locomo(side_dir: Path, conversation: str):
    from benchmarks.diagnostics.product_packing import _harness, gate_config
    from prme import MemoryEngine
    folder = LOCOMO / conversation
    manifest = json.loads((folder / "manifest.json").read_text())
    copy = side_dir / f"locomo-{conversation}"
    shutil.rmtree(copy, ignore_errors=True)
    copy.parent.mkdir(parents=True, exist_ok=True)
    os.system(f"cp -cR '{folder / 'pack'}' '{copy}'")
    rows = [r for r in _harness().question_rows("locomo") if r["conversation_id"] == conversation]
    config = gate_config(copy)
    assert config.organizer.opportunistic_enabled is False
    async with MemoryEngine.open(config) as engine:
        lines = await read_pack(engine, [r["question"] for r in rows], manifest["user_id"],
                                datetime.fromisoformat(manifest["reference_time"]))
    return lines, receipts(copy / "memory.duckdb")


async def main(args):
    import prme
    side_dir = Path(args.root) / args.side
    side_dir.mkdir(parents=True, exist_ok=True)
    print("prme from", prme.__file__, flush=True)
    jobs = [(f"stated-{s}", stated, s) for s in args.sizes] + [(f"locomo-{c}", locomo, c) for c in args.conversations]
    for name, function, value in jobs:
        started = time.perf_counter()
        lines, stored = await function(side_dir, value)
        with gzip.open(side_dir / f"{name}.jsonl.gz", "wt") as handle:
            for line in lines:
                handle.write(json.dumps({"kind": "read", **line}) + "\n")
            for receipt in stored:
                handle.write(json.dumps({"kind": "receipt", **receipt}) + "\n")
        print(name, len(lines), "reads", len(stored), "receipts", round(time.perf_counter() - started, 1), "s",
              flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--side", required=True)
    parser.add_argument("--root", default="/tmp/task21/a")
    parser.add_argument("--sizes", type=int, nargs="*", default=[400, 4000, 20500])
    parser.add_argument("--conversations", nargs="*", default=list(CONVERSATIONS))
    asyncio.run(main(parser.parse_args()))
