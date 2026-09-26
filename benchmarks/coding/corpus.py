"""Answer-independent documentation corpus used by the coding-memory pilot."""

import hashlib
from pathlib import Path
import subprocess

from prme.integrations.coding import MemoryTools


DOCUMENTS = [
    "AGENTS.md",
    "README.md",
    "docs/METADATA.md",
    "docs/MEMORY-CORRECTIONS.md",
    "docs/VALUE-BINDINGS.md",
    "docs/WORKSPACES.md",
    "docs/LOCAL-RESOURCES.md",
    "docs/REINFORCEMENT.md",
    "docs/ENTITY-IDENTITY.md",
    "docs/LEARNING.md",
    "docs/PACKING.md",
    "docs/RFC-0004-Namespace-and-Scope-Isolation.md",
]


def at_revision(repo: Path, revision: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{revision}:{path}"], text=True
    )


def collect(repo: Path, revision: str) -> tuple[dict[str, str], list[dict]]:
    documents = {path: at_revision(repo, revision, path) for path in DOCUMENTS}
    records = []
    for path, text in documents.items():
        lines = text.splitlines(keepends=True)
        start, pending = 1, []
        for number, line in enumerate(lines, 1):
            pending.append(line)
            if (not line.strip() and sum(map(len, pending)) >= 1000) or number == len(
                lines
            ):
                passage = "".join(pending)
                identity = hashlib.sha256(
                    f"{revision}:{path}:{start}:{number}:{passage}".encode()
                ).hexdigest()
                records.append(
                    {
                        "id": identity,
                        "path": path,
                        "start": start,
                        "end": number,
                        "text": passage,
                        "commit": revision,
                        "file_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    }
                )
                start, pending = number + 1, []
    return documents, records


def seed(memory: MemoryTools, records: list[dict]) -> list[dict]:
    """Import a frozen corpus once into a fresh experiment pack."""
    receipts = []
    for item in records:
        content = f"Repository document {item['path']}:{item['start']}-{item['end']}; commit {item['commit'][:12]}.\n\n{item['text']}"
        receipt = memory.call(
            "memory_store",
            {
                "content": content,
                "node_type": "note",
                "scope": "project",
                "role": "tool",
                "source_type": "external_document",
                "metadata": {
                    "coding_corpus_v1": {
                        key: value for key, value in item.items() if key != "text"
                    }
                },
            },
        )
        if receipt.get("status") not in {None, "complete"}:
            raise RuntimeError("Corpus import did not complete")
        receipts.append({"source_id": item["id"], "receipt": receipt})
    return receipts
