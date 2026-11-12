"""Bar 3 diagnosis: rewrite a pack copy's lexical index as one segment of its live documents, in stored order.

Research harness (research/write-batch-2026-11). The bar 3 comparison found that the batch path's lexical index has
a different segment layout from the current path's (REPORT.md, "Bar 3"). This script copies a pack, reads every live
document of its Tantivy index in segment and document order, and writes them again into one segment with the same
schema (heap 50,000,000 bytes, one thread). compare.py then compares the rewritten copy with the current path pack.

The run in REPORT.md used an inline copy of this code; this file is the same steps with paths as arguments.

    uv run python research/write-batch-2026-11/relex.py --source /tmp/p5/build/batch/4000/pack \
        --dest /tmp/p5/build/batch-relex/4000/pack
"""

from __future__ import annotations

import argparse
import json
import os
import shutil

import tantivy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, help="pack directory to copy (not changed)")
    parser.add_argument("--dest", required=True, help="new pack directory; must not exist")
    arguments = parser.parse_args()
    if os.path.exists(arguments.dest):
        raise SystemExit(f"{arguments.dest} exists; remove it first")
    shutil.copytree(arguments.source, arguments.dest)
    path = os.path.join(arguments.dest, "lexical_index")
    old = tantivy.Index.open(path)
    searcher = old.searcher()
    hits = searcher.search(tantivy.Query.all_query(), limit=max(searcher.num_docs, 1)).hits
    ordered = sorted(hits, key=lambda hit: (hit[1].segment_ord, hit[1].doc))
    documents = [searcher.doc(address).to_dict() for _score, address in ordered]
    schema = old.schema
    del searcher, old
    shutil.rmtree(path)
    os.makedirs(path)
    fresh = tantivy.Index(schema, path=path)
    writer = fresh.writer(heap_size=50_000_000, num_threads=1)
    for fields in documents:
        writer.add_document(tantivy.Document(**fields))
    writer.commit()
    writer.wait_merging_threads()
    with open(os.path.join(path, "meta.json")) as handle:
        segments = json.load(handle)["segments"]
    layout = [(segment["max_doc"], (segment.get("deletes") or {}).get("num_deleted_docs", 0)) for segment in segments]
    print(json.dumps({"documents": len(documents), "segments": layout}))


if __name__ == "__main__":
    main()
