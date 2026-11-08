"""Score the graph typed extraction builds against the graph oracle's gold graphs (dwamianm/prism#400).

``DESIGN.md`` says what extraction has to produce; this harness asks extraction to produce it and scores the result
with :func:`benchmarks.graph_oracle.fidelity.fidelity`. For each fixture it stores the corpus's memories, derives
them through ``reingest()`` with ``enable_typed_extraction`` on (the product's batched extraction, its grounding and
its planning), reads the claim nodes and their links back, and writes the extracted graph in the gold file's format:
entities by their names, and each claim that states an admitted relation as a subject, relation and object (or a
literal attribute for ``has``), with its polarity and status.

The model is the one the fast tiers extract with, through the local Ollama server. No paid provider is called.
The report says, per fixture, the entity and relation recall and precision and the polarity and status accuracy,
and how many claims stated a relation and how many kept only their predicate.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import time

from benchmarks.diagnostics.extracted_packs import build_config
from benchmarks.graph_oracle.fidelity import fidelity, fidelity_markdown
from benchmarks.graph_oracle.gold import STATUSES, Gold, load_gold
from prme import MemoryEngine
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.retrieval.relations import RELATIONS
from prme.types import EdgeType, LifecycleState, NodeType

KIND = "prme-graph-oracle-extraction-fidelity"
SCHEMA_VERSION = 1
FIXTURES = ("store-v1", "defects-v1", "chat-v1", "draw2-v1")
MODEL = "deepseek-v4.1-flash:cloud"
ENDPOINT = "http://127.0.0.1:11434/v1"
_ALL = 1_000_000


def _claim(node: MemoryNode, has_subject: bool) -> bool:
    """A fact node: extraction's claims carry ``extraction_kind``, and the oracle's carry a HAS_FACT link."""
    return node.node_type not in (NodeType.ENTITY, NodeType.NOTE) and (
        has_subject or (node.metadata or {}).get("extraction_kind") in ("fact", "relationship"))


def _relation(metadata: dict, link: dict) -> str | None:
    for source in (metadata, link):
        value = source.get("relation")
        if isinstance(value, str) and value.strip().casefold() in RELATIONS:
            # ``has`` holds a literal attribute, which the gold file scores like any other claim.
            return value.strip().casefold()
    return None


def _field(metadata: dict, link: dict, name: str, allowed) -> str | None:
    for source in (metadata, link):
        value = source.get(name)
        if isinstance(value, str) and value.strip() and value.strip().casefold() in allowed:
            return value.strip().casefold()
    return None


def _names(node: MemoryNode) -> list[str]:
    listed = (node.metadata or {}).get("names")
    names = [name for name in listed if isinstance(name, str) and name.strip()] if isinstance(listed, list) else []
    if node.content and node.content.strip() and node.content.strip() not in names:
        names.insert(0, node.content.strip())
    return names


class Candidate:
    """The extracted graph in the gold file's format, plus the counts that say how typed it is."""

    def __init__(self) -> None:
        self.entities: dict[str, list[str]] = {}
        self.relations: list[dict] = []
        self.claims = 0
        self.typed = 0
        self.untyped = 0
        self._by_name: dict[str, str] = {}

    def entity(self, node_id: str, names: list[str]) -> str:
        names = [name for name in names if name.strip()]
        if node_id not in self.entities:
            self.entities[node_id] = names
            for name in names:
                self._by_name.setdefault(name.casefold(), node_id)
        return node_id

    def named(self, text: str) -> str:
        """The candidate entity a literal text names, or a synthetic one that matches no gold entity."""
        found = self._by_name.get(text.strip().casefold())
        if found is not None:
            return found
        synthetic = f"text:{text.strip().casefold()}"
        return self.entity(synthetic, [text.strip()])

    def as_graph(self) -> dict:
        return {"entities": [{"id": node_id, "names": names} for node_id, names in self.entities.items()],
                "relations": self.relations}


def candidate_graph(nodes: list[MemoryNode], edges: list[MemoryEdge], owner: str) -> Candidate:
    """The extracted graph of ``owner`` as a candidate in the gold file's format (``DESIGN.md``)."""
    candidate = Candidate()
    by_id = {str(node.id): node for node in nodes if node.user_id == owner}
    for node in by_id.values():
        if node.node_type == NodeType.ENTITY and node.lifecycle_state not in (
                LifecycleState.SUPERSEDED, LifecycleState.DEPRECATED, LifecycleState.ARCHIVED):
            candidate.entity(str(node.id), _names(node))
    subject_links: dict[str, tuple[str, dict]] = {}
    object_links: dict[str, list[tuple[str, dict]]] = {}
    for edge in edges:
        if edge.user_id != owner:
            continue
        source, target = str(edge.source_id), str(edge.target_id)
        if edge.edge_type == EdgeType.HAS_FACT and target in by_id:
            subject_links[target] = (source, edge.metadata or {})
        elif edge.edge_type == EdgeType.MENTIONS and source in by_id:
            object_links.setdefault(source, []).append((target, edge.metadata or {}))
    for claim_id, node in by_id.items():
        if not _claim(node, claim_id in subject_links):
            continue
        metadata = node.metadata or {}
        link = subject_links.get(claim_id, ("", {}))[1]
        relation = _relation(metadata, link)
        if relation is None:
            candidate.claims += 1
            candidate.untyped += 1
            continue
        candidate.claims += 1
        candidate.typed += 1
        subject_id = subject_links.get(claim_id, (None, {}))[0]
        subject = (candidate.entity(subject_id, _names(by_id[subject_id])) if subject_id in by_id
                   else candidate.named(str(metadata.get("subject", ""))))
        record = {"s": subject, "r": relation,
                  "polarity": _field(metadata, link, "polarity", {"positive", "negative"}) or "positive",
                  "status": _field(metadata, link, "status", set(STATUSES)) or "current"}
        if relation == "has":
            record["attr"] = str(metadata.get("attr") or metadata.get("predicate") or "").strip()
        else:
            object_id = next((target for target, edge_metadata in object_links.get(claim_id, ())
                              if target in by_id and edge_metadata.get("basis") != "text_mention"), None)
            record["o"] = (candidate.entity(object_id, _names(by_id[object_id])) if object_id is not None
                           else candidate.named(str(metadata.get("object", ""))))
        candidate.relations.append(record)
    return candidate


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parents[2]).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def score_fixture(name: str, *, model: str = MODEL, endpoint: str = ENDPOINT,
                        typed: bool = True) -> dict:
    """Store one fixture's memories, derive them with extraction, and score the graph it built."""
    gold: Gold = load_gold(name)
    with tempfile.TemporaryDirectory(prefix=f"extraction-fidelity-{name}-") as work:
        pack = Path(work) / "pack"
        (pack / "lexical_index").mkdir(parents=True)
        config = build_config(pack, {"enable_reingest": True, "enable_typed_extraction": typed,
                                     "enable_pronoun_repair": False}, model=model, endpoint=endpoint)
        async with MemoryEngine.open(config) as engine:
            for memory in gold.memories:
                await engine.store(memory.text, user_id=gold.owner, role=memory.role, speaker=memory.speaker,
                                   node_type=memory.node_type, scope=memory.scope, session_id=memory.session,
                                   event_time=memory.event_time, extract=False)
            reingested = await engine.reingest(gold.owner)
            nodes = await engine.query_nodes(user_id=gold.owner, limit=_ALL)
            edges = await engine._graph_store.get_edges(node_ids=[str(node.id) for node in nodes])
        candidate = candidate_graph(nodes, edges, gold.owner)
    return {"fidelity": fidelity(gold, candidate.as_graph()),
            "claims": {"total": candidate.claims, "typed": candidate.typed, "untyped": candidate.untyped},
            "reingest": {"total": reingested.total, "processed": reingested.processed, "failed": reingested.failed,
                         "model_calls": reingested.model_calls, "source_tokens": reingested.source_tokens}}


async def run(fixtures: tuple[str, ...] = FIXTURES, *, model: str = MODEL, endpoint: str = ENDPOINT,
              typed: bool = True) -> dict:
    started = time.perf_counter()
    fixtures_report: dict[str, dict] = {}
    report = {"kind": KIND, "schema_version": SCHEMA_VERSION, "commit": _commit(),
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model,
              "typed": typed, "fixtures": fixtures_report}
    for name in fixtures:
        began = time.perf_counter()
        fixtures_report[name] = await score_fixture(name, model=model, endpoint=endpoint, typed=typed)
        fixtures_report[name]["seconds"] = round(time.perf_counter() - began, 2)
    report["seconds"] = round(time.perf_counter() - started, 2)
    return report


def report_markdown(report: dict) -> str:
    lines = [f"# Extraction fidelity (commit `{report['commit']}`)", "",
             f"Model `{report['model']}`, typed extraction {'on' if report['typed'] else 'off'}. "
             "The graph extraction built is scored against each fixture's gold graph.", "",
             "## Summary", "",
             "| Fixture | Entity recall | Entity precision | Relation recall | Relation precision | Polarity | Status | "
             "Typed claims | Untyped claims |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, result in report["fixtures"].items():
        measure, claims = result["fidelity"], result["claims"]
        lines.append(f"| {name} | {measure['entity_recall']} | {measure['entity_precision']} | "
                     f"{measure['relation_recall']} | {measure['relation_precision']} | "
                     f"{measure['polarity_accuracy']} | {measure['status_accuracy']} | "
                     f"{claims['typed']} | {claims['untyped']} |")
    lines += ["", f"Seconds: `{json.dumps({name: result['seconds'] for name, result in report['fixtures'].items()})}`.",
              ""]
    for name, result in report["fixtures"].items():
        lines += [f"## {name}", "", fidelity_markdown(result["fidelity"]),
                  f"Reingest: `{json.dumps(result['reingest'])}`.", ""]
    return "\n".join(lines)


async def write(out: Path, fixtures: tuple[str, ...] = FIXTURES, *, model: str = MODEL,
                endpoint: str = ENDPOINT, typed: bool = True) -> dict:
    report = await run(fixtures, model=model, endpoint=endpoint, typed=typed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "report.md").write_text(report_markdown(report))
    return report
