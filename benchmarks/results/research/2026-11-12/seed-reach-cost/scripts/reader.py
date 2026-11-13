"""Task 26 bars 1 and 2 (task 24's reader; adds the response metadata's graph_seed_reach): one engine on one pack copy, from one code tree, reading questions it is sent.

Run with the tree under test first on the path, so this process imports that tree's ``prme``:

    PYTHONPATH=<tree>/src:<tree> <venv>/bin/python reader.py --tree <tree> --pack <pack> --copy <dir> \
        --set '{"memory_mode": "classic"}'

It copies the pack to ``--copy``, opens it with ``gate_config`` (current defaults, environment and .env ignored,
organizer off) plus ``--set``, reads one warm-up question, prints ``ready`` and then answers one JSON line per
JSON line on stdin: ``{"question": ..., "fingerprint": true}`` gives the read's milliseconds and, when asked, the
fingerprint: ranked ids with scores, packed ids, context digest, receipt digest and the stored receipt row's
digest, each with the request id replaced, and the stored row's digest also with ``source_files_sha256`` masked.
No model is called: provider keys are blank and provider addresses point at a closed port.
"""
import argparse, asyncio, hashlib, importlib.util, json, os, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                   "TOKENIZERS_PARALLELISM": "false"})
USER = "robot-1"
REFERENCE_TIME = datetime.fromisoformat("2026-11-05T00:00:00+00:00")


def _mask(value):
    if isinstance(value, dict):
        return {key: ("MASKED" if key == "source_files_sha256" else _mask(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask(item) for item in value]
    return value


def _find(value, key):
    """The first value under ``key`` anywhere in a JSON value, or None."""
    if isinstance(value, dict):
        if key in value:
            return value[key]
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            found = _find(item, key)
            if found is not None:
                return found
    return None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def main(args) -> None:
    sys.path.insert(0, args.tree)
    from benchmarks.diagnostics.product_packing import _quiet_offline_cli, gate_config
    from prme import MemoryEngine
    import prme

    spec = importlib.util.spec_from_file_location("read_cost", Path(args.tree) / "benchmarks/diagnostics/read_cost.py")
    read_cost = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(read_cost)
    assert Path(prme.__file__).resolve().is_relative_to(Path(args.tree).resolve()), prme.__file__
    _quiet_offline_cli()
    copy = Path(args.copy)
    shutil.rmtree(copy, ignore_errors=True)
    copy.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-cR", args.pack, str(copy)], check=True)
    try:
        async with MemoryEngine.open(gate_config(copy, json.loads(args.set))) as engine:
            await engine.retrieve("warm-up question about the house", user_id=USER, reference_time=REFERENCE_TIME)
            print(json.dumps({"ready": True, "prme": prme.__file__}), flush=True)
            for line in sys.stdin:
                request = json.loads(line)
                started = time.perf_counter()
                response = await engine.retrieve(request["question"], user_id=USER, reference_time=REFERENCE_TIME)
                answer = {"ms": 1000 * (time.perf_counter() - started)}
                if request.get("fingerprint"):
                    rid = str(response.metadata.request_id)
                    receipt = await engine.get_retrieval_receipt(rid, user_id=USER)
                    answer["fingerprint"] = read_cost.fingerprint(response, receipt)
                    row, = engine._conn.execute(
                        "SELECT payload::VARCHAR FROM operations WHERE target_id = ?", [rid]).fetchone()
                    payload = json.loads(row)
                    stored = payload.pop("receipt").replace(rid, "RID")
                    payload.pop("request_id")
                    payload.pop("receipt_checksum", None)
                    answer["stored_receipt_sha256"] = _sha(stored)
                    answer["stored_receipt_masked_sha256"] = _sha(json.dumps(_mask(json.loads(stored)), sort_keys=True))
                    answer["stored_log_fields_sha256"] = _sha(json.dumps(payload, sort_keys=True).replace(rid, "RID"))
                    answer["source_files_sha256"] = _find(json.loads(stored), "source_files_sha256")
                    answer["graph_seed_reach"] = response.metadata.graph_seed_reach
                    answer["replay_ok"] = receipt.replay_ranking() == tuple(c.node.id for c in response.results)
                print(json.dumps(answer), flush=True)
    finally:
        shutil.rmtree(copy, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", required=True)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--copy", required=True)
    parser.add_argument("--set", default="{}")
    asyncio.run(main(parser.parse_args()))
