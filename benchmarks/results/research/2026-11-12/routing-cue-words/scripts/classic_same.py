"""Task 27 bar 4: memory_mode=classic reads the same on ROUTING_VERSION 3 (origin/main) and 4 (branch).

Starts graph-candidate-cut/scripts/reader.py once per tree on a copy of the m3 pack with
{"memory_mode": "classic"}, sends the 20 questions of graph-candidate-cut/scripts/pairs.py and compares ranked ids
with scores, packed ids, context digest and the stored receipt digest with source_files_sha256 masked. No model is
called (reader.py blanks provider keys and points provider addresses at a closed port).
    python classic_same.py <v3 tree> <v4 tree> <reader dir> <out.json>
"""
import json, subprocess, sys
from pathlib import Path

v3, v4, reader_dir, out = sys.argv[1:5]
sys.path.insert(0, reader_dir)
from pairs import PACKS, QUESTIONS  # noqa: E402

PY = str(Path(v4) / ".venv/bin/python")


def read(tree, side):
    env_path = f"{tree}/src:{tree}"
    process = subprocess.Popen(
        [PY, str(Path(reader_dir) / "reader.py"), "--tree", tree, "--pack", PACKS["m3"],
         "--copy", f"/tmp/task27/m3-copy-{side}", "--set", json.dumps({"memory_mode": "classic"})],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        env={**__import__("os").environ, "PYTHONPATH": env_path})
    ready = json.loads(process.stdout.readline())
    rows = []
    for question in QUESTIONS:
        process.stdin.write(json.dumps({"question": question, "fingerprint": True}) + "\n")
        process.stdin.flush()
        rows.append(json.loads(process.stdout.readline()))
    process.stdin.close()
    process.wait()
    return ready, rows


ready3, a = read(v3, "v3")
ready4, b = read(v4, "v4")
keys = {"ranked": lambda r: r["fingerprint"]["ranked"], "packed": lambda r: r["fingerprint"]["packed"],
        "context_sha256": lambda r: r["fingerprint"]["context_sha256"],
        "stored_receipt_masked_sha256": lambda r: r["stored_receipt_masked_sha256"],
        "replay_ok": lambda r: r["replay_ok"]}
same = {name: sum(get(x) == get(y) for x, y in zip(a, b)) for name, get in keys.items()}
result = {"v3_prme": ready3["prme"], "v4_prme": ready4["prme"], "questions": len(QUESTIONS), "identical": same,
          "replay_ok_v4": sum(r["replay_ok"] for r in b),
          "source_files_sha256": [a[0]["source_files_sha256"], b[0]["source_files_sha256"]]}
Path(out).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
