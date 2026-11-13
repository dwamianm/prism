"""Task 26 bar 4 (task 24 script): a receipt made by one tree replays under the other tree's code.

``dump`` (run with tree A first on PYTHONPATH) opens a copy of the m3 pack with ``gate_config`` and
current defaults, reads the 20 questions of ``pairs.py`` and writes each receipt with the ranked ids it
returned. ``check`` (run with tree B first on PYTHONPATH) loads each receipt with tree B's
``RetrievalReceipt``, replays its ranking without a graph or models and compares it with the ids tree A
returned. No model is called.
"""
import asyncio, json, os, shutil, subprocess, sys
from pathlib import Path

os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                   "TOKENIZERS_PARALLELISM": "false"})
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pairs import PACKS, QUESTIONS  # noqa: E402
from reader import REFERENCE_TIME, USER, _find  # noqa: E402


async def dump(tree: str, out: str, copy: str) -> None:
    sys.path.insert(0, tree)
    import prme
    from benchmarks.diagnostics.product_packing import gate_config
    from prme import MemoryEngine
    assert Path(prme.__file__).resolve().is_relative_to(Path(tree).resolve()), prme.__file__
    shutil.rmtree(copy, ignore_errors=True)
    Path(copy).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-cR", PACKS["m3"], copy], check=True)
    rows = []
    try:
        async with MemoryEngine.open(gate_config(Path(copy), {})) as engine:
            for question in QUESTIONS:
                response = await engine.retrieve(question, user_id=USER, reference_time=REFERENCE_TIME)
                receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
                rows.append({"question": question, "ranked": [str(c.node.id) for c in response.results],
                             "receipt": receipt.model_dump_json()})
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    Path(out).write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    print(json.dumps({"tree": tree, "receipts": len(rows)}))


def check(tree: str, path: str) -> None:
    import prme
    from prme.models.relevance import RetrievalReceipt
    assert Path(prme.__file__).resolve().is_relative_to(Path(tree).resolve()), prme.__file__
    rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
    replayed = 0
    for row in rows:
        receipt = RetrievalReceipt.model_validate_json(row["receipt"])
        replayed += [str(i) for i in receipt.replay_ranking()] == row["ranked"]
    sources = {_find(json.loads(row["receipt"]), "source_files_sha256")["candidates"] for row in rows}
    print(json.dumps({"checked_with": tree, "receipts_from": path, "replayed": replayed, "of": len(rows),
                      "candidates_sha256": sorted(sources)}))


if __name__ == "__main__":
    if sys.argv[1] == "dump":
        asyncio.run(dump(sys.argv[2], sys.argv[3], sys.argv[4]))
    else:
        check(sys.argv[2], sys.argv[3])
