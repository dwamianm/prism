"""Apply a declared text normalization equally to both external-study inputs."""

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import re

from benchmarks.diagnostics.hindsight_capture import digest, write

REMOVED = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")
POLICY = "remove-c0-except-tab-lf-cr-plus-del-and-surrogates-v1"


def normalize(inputs):
    result = copy.deepcopy(inputs)
    changes = []
    for case in result["cases"]:
        records = [(case, "question", None)] + [
            (turn, "content", turn["id"]) for turn in case["turns"]
        ]
        for record, field, source in records:
            before = record[field]
            removed = REMOVED.findall(before)
            if removed:
                after = REMOVED.sub("", before)
                record[field] = after
                changes.append(
                    {
                        "case_id": case["case_id"],
                        "source_id": source,
                        "field": field,
                        "original_sha256": digest(
                            before.encode("utf-8", errors="surrogatepass")
                        ),
                        "normalized_sha256": digest(after.encode()),
                        "removed_codepoints": dict(
                            Counter(f"U+{ord(c):04X}" for c in removed)
                        ),
                        "original_characters": len(before),
                        "normalized_characters": len(after),
                    }
                )
    return result, {
        "policy": POLICY,
        "changes": changes,
        "original_hash_encoding": "UTF-8 with surrogatepass",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["input", "output", "audit"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.audit.exists():
        raise ValueError("Refusing to overwrite prior inputs or normalization audit")
    result, audit = normalize(json.loads(args.input.read_bytes()))
    audit["input_file_sha256"] = digest(args.input.read_bytes())
    write(args.output, result)
    audit["output_file_sha256"] = digest(args.output.read_bytes())
    write(args.audit, audit)
    print(
        {
            "changed_records": len(audit["changes"]),
            "removed_characters": sum(
                c["original_characters"] - c["normalized_characters"]
                for c in audit["changes"]
            ),
        }
    )


if __name__ == "__main__":
    main()
