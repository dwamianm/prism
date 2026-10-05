"""Run a scripted chat through ``ingest()`` and measure how PRME maps it.

The benchmark packs are hundreds of turns each and take most of an hour to
build, and their annotations only name the turns that hold evidence. They
cannot say whether a fact was stored once, attached to the right person or
linked to the right entities. This tool builds a fresh pack from a short
conversation with an answer key (``benchmarks/conversations/``), in a few
minutes, and reports:

- repetition: how many claim records share their text with another record,
  how many state the same subject, predicate and object, and how many records
  hold each fact the conversation repeats;
- the graph: entities with no link, pronoun entities, entities of the key
  found once, split across several nodes or missing, what the owner's own
  claims are attached to, and which links of the key a claim connects;
- retrieval: for each probe question, whether the answer reaches the reader's
  context and how much of that context repeats a record already in it.

Choices, and why:

- Turns go through the public ``ingest()`` one at a time and each waits for
  its extraction, as in a live chat. Assistant turns are ingested too, with
  the assistant role, because a chat stores both sides.
- The configuration, model checks, extraction cache and retries are the
  extracted pack builder's (``extracted_packs``): current defaults isolated
  from the environment, the organizer off, Ollama only, and a cloud model
  only with ``--cloud``. ``--set`` changes settings, ``--speaker`` names the
  owner on their own turns, and ``--organize`` runs the organizer once after
  the last turn.
- Builds of one conversation share an extraction cache, so a rebuild with
  the same settings repeats the same extraction without a model call. A code
  change after extraction is then measured on identical model output.
  ``--no-cache`` asks the model again.
- Measurement needs no model and can be repeated on a finished pack with
  ``measure``. ``compare`` puts the reports of several builds side by side.

Layout under ``<root>/<conversation>/``: ``extraction-cache/`` and, per
label, ``build.json``, ``pack/`` (open it in the memory explorer),
``turns.jsonl``, ``report.json``, ``report.md`` and ``contexts/`` (each probe's
rendered context). Packs hold the conversation's text, so the default root is
the ignored ``data/``.

    uv run python -m benchmarks.diagnostics.chat_probe build --label defaults \\
      --model deepseek-v4.1-flash:cloud --cloud
    uv run python -m benchmarks.diagnostics.chat_probe compare defaults fact-text
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

from benchmarks.diagnostics.extracted_packs import (
    DEFAULT_MODEL, ExtractionCache, _drain_sources, _finish_extractions, _retry_turn, _Usage, build_config,
    model_identity,
)
from benchmarks.diagnostics.product_packing import _quiet_offline_cli, gate_config, parse_overrides
from prme import MemoryEngine
from prme.types import LifecycleState

KIND = "prme-chat-probe"
BUILD_KIND = "prme-chat-probe-build"
SCHEMA_VERSION = 1
DEFAULT_CONVERSATION = Path(__file__).resolve().parents[1] / "conversations" / "chat-v1.json"
DEFAULT_ROOT = Path("data/chat-probe-v1")
# Minutes between turns of a session, so every turn has its own source time.
TURN_MINUTES = 2
CLAIM_TYPES = frozenset({"fact", "preference", "decision", "task"})
ACTIVE = frozenset({"tentative", "stable"})
# Claims that were ever stated: a superseded claim still records a link.
STATED = ACTIVE | {"superseded", "contested"}
FIRST_PERSON = frozenset("i me my mine myself we us our ours ourselves".split())
PRONOUNS = FIRST_PERSON | frozenset("you your yours he him his she her hers they them their theirs it its".split())
# More than any one pack holds; reads stay within one user.
_ALL = 1_000_000


def default_root() -> Path:
    from benchmarks import checkout

    return checkout.main_checkout() / DEFAULT_ROOT


def load_conversation(path: Path = DEFAULT_CONVERSATION) -> dict:
    conversation = json.loads(path.read_text())
    if conversation.get("kind") != KIND or conversation.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a chat probe conversation")
    names = {entity["id"] for entity in conversation["key"]["entities"]}
    owners = [entity["id"] for entity in conversation["key"]["entities"] if entity.get("owner")]
    if len(owners) != 1:
        raise ValueError(f"{path}: the key needs exactly one owner entity")
    for link in conversation["key"]["links"]:
        if not set(link["between"]) <= names:
            raise ValueError(f"{path}: link {link['id']} names an entity the key does not list")
    for probe in conversation["key"]["probes"]:
        if bool(probe.get("all_of")) == bool(probe.get("any_of")):
            raise ValueError(f"{path}: probe {probe['id']} needs all_of or any_of")
    return conversation


def conversation_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chat_turns(conversation: dict, *, speaker: bool = False) -> list[dict]:
    """The conversation as ``ingest()`` arguments, each with its own source time and key."""
    turns = []
    for session in conversation["sessions"]:
        start = datetime.fromisoformat(session["start"])
        for index, turn in enumerate(session["turns"]):
            if turn["role"] not in {"user", "assistant"}:
                raise ValueError(f"{session['id']}#{index}: a chat turn is from the user or the assistant")
            key = f"{session['id']}#{index}"
            turns.append({
                "key": key, "content": turn["text"], "role": turn["role"], "session_id": session["id"],
                "event_time": start + timedelta(minutes=TURN_MINUTES * index),
                "speaker": conversation["user_name"] if speaker and turn["role"] == "user" else None,
                "metadata": {"chat_probe": conversation["name"], "turn_key": key},
            })
    return turns


# --- Measurement over plain records -------------------------------------------------------------


def _name(text: str | None) -> str:
    text = " ".join((text or "").casefold().replace("’", "'").split())
    return text.removeprefix("the ").removesuffix("'s")


def _claim(node: dict) -> dict:
    metadata = node.get("metadata") or {}
    return {key: metadata.get(key) for key in ("subject", "predicate", "object", "polarity")}


def _claim_line(node: dict) -> str:
    claim = _claim(node)
    if not claim["subject"] or not claim["predicate"]:
        return node["content"]
    negated = "NOT " if claim["polarity"] == "negative" else ""
    return f"{claim['subject']} → {negated}{claim['predicate']} → {claim['object']}"


def _is_pronoun(node: dict) -> bool:
    return ((node.get("metadata") or {}).get("identity_status") == "unresolved_reference"
            or _name(node["content"]) in PRONOUNS)


def _copies(texts: list[str]) -> tuple[int, int]:
    """Groups of identical texts, and the copies beyond the first of each."""
    groups = [n for n in Counter(texts).values() if n > 1]
    return len(groups), sum(n - 1 for n in groups)


def graph_report(nodes: list[dict], edges: list[dict], events: dict[str, dict], conversation: dict) -> dict:
    """Repetition and structure of a finished pack against the conversation's key.

    ``nodes`` carry id, type, state, content, metadata and evidence_refs;
    ``edges`` carry source, target and type; ``events`` give each source
    event's role and turn key by event ID.
    """
    key = conversation["key"]
    by_id = {node["id"]: node for node in nodes}
    states = Counter(f"{node['type']}/{node['state']}" for node in nodes)
    claims = [node for node in nodes if node["type"] in CLAIM_TYPES]
    active = [node for node in claims if node["state"] in ACTIVE]
    stated = [node for node in claims if node["state"] in STATED]
    entities = [node for node in nodes if node["type"] == "entity" and node["state"] in ACTIVE]
    notes = {node["content"] for node in nodes if node["type"] == "note"}

    def roles(node: dict) -> set[str]:
        return {events[ref]["role"] for ref in node.get("evidence_refs") or [] if ref in events}

    linked: set[str] = set()
    adjacent: dict[str, set[str]] = defaultdict(set)  # claim -> entities on any edge
    subjects: dict[str, set[str]] = defaultdict(set)  # claim -> entities with has_fact to it
    for edge in edges:
        source, target = by_id.get(edge["source"]), by_id.get(edge["target"])
        if source is None or target is None:
            continue
        linked.update((source["id"], target["id"]))
        for claim, other in ((source, target), (target, source)):
            if claim["type"] in CLAIM_TYPES and other["type"] == "entity":
                adjacent[claim["id"]].add(other["id"])
        if edge["type"] == "has_fact" and source["type"] == "entity":
            subjects[target["id"]].add(source["id"])

    texts = [node["content"] for node in active]
    text_groups, text_copies = _copies(texts)
    triples = [tuple(_name(value) for value in _claim(node).values()) for node in active if _claim(node)["predicate"]]
    triple_groups, triple_copies = _copies([json.dumps(triple) for triple in triples])
    from_user = [node for node in active if "user" in roles(node)]
    from_assistant = [node for node in active if roles(node) == {"assistant"}]

    names = {entity["id"]: {_name(name) for name in entity["names"]} for entity in key["entities"]}
    nodes_for = {entity: {node["id"] for node in entities if _name(node["content"]) in wanted}
                 for entity, wanted in names.items()}
    owner = next(entity["id"] for entity in key["entities"] if entity.get("owner"))
    pronouns = {node["id"] for node in entities if _is_pronoun(node)}
    first_person = {node_id for node_id in pronouns if _name(by_id[node_id]["content"]) in FIRST_PERSON}

    def subject_kind(claim: dict) -> str:
        found = subjects.get(claim["id"], set())
        if found & nodes_for[owner]:
            return "owner_named"
        if found & first_person:
            return "first_person_pronoun"
        if found & pronouns:
            return "other_pronoun"
        return "other_entity" if found else "no_subject_link"

    links = []
    for link in key["links"]:
        a, b = link["between"]
        connecting = [node for node in stated if adjacent[node["id"]] & nodes_for[a] and adjacent[node["id"]] & nodes_for[b]]
        status = "linked" if connecting else None
        if status is None and owner in (a, b):
            other = b if a == owner else a
            connecting = [node for node in stated
                          if adjacent[node["id"]] & nodes_for[other] and adjacent[node["id"]] & first_person]
            status = "via_pronoun" if connecting else None
        if status is None:
            # The owner is present through first-person pronouns even when never named.
            present = all(nodes_for[entity] or (entity == owner and first_person) for entity in (a, b))
            status = "not_linked" if present else "entity_missing"
        links.append({"id": link["id"], "label": link["label"], "status": status,
                      "claims": [{"claim": _claim_line(node), "state": node["state"]} for node in connecting]})

    repeats = []
    for repeat in key["repeats"]:
        terms = [term.casefold() for term in repeat["terms"]]
        stating = [node for node in active if all(term in _claim_line(node).casefold() for term in terms)]
        showing = [node for node in active if all(term in node["content"].casefold() for term in terms)]
        repeats.append({"id": repeat["id"], "label": repeat["label"], "user_mentions": repeat["user_mentions"],
                        "claims_stating_it": len(stating),
                        "from_assistant_only": sum(roles(node) == {"assistant"} for node in stating),
                        "records_showing_its_text": len(showing),
                        "claims": [_claim_line(node) for node in stating]})

    return {
        "records": dict(sorted(states.items())),
        "claims": {
            "active": len(active), "distinct_texts": len(set(texts)),
            "texts_shared_by_several_claims": text_groups, "extra_copies_of_a_text": text_copies,
            "same_claim_groups": triple_groups, "extra_copies_of_a_claim": triple_copies,
            "text_is_a_whole_message": sum(node["content"] in notes for node in active),
            "from_user_turns": len(from_user), "from_assistant_turns_only": len(from_assistant),
            "user_claim_subjects": dict(sorted(Counter(subject_kind(node) for node in from_user).items())),
        },
        "entities": {
            "active": len(entities),
            "with_no_link": sorted(node["content"] for node in entities if node["id"] not in linked),
            "pronouns": sorted(node["content"] for node in entities if node["id"] in pronouns),
            "key": {entity: {"nodes": len(found),
                             "status": "found" if len(found) == 1 else "split" if found else "missing"}
                    for entity, found in nodes_for.items()},
        },
        "links": links,
        "repeats": repeats,
    }


def context_report(probe: dict, included: list[dict], context: str, tokens_used: int, token_budget: int) -> dict:
    """What one probe's context holds: the answer, stale values and repeated records.

    Only record lines count, so the format's own header cannot supply a term.
    """
    lines = [line for line in context.splitlines() if line.startswith("- ")]
    lowered = "\n".join(lines).casefold()
    terms = probe.get("all_of") or probe.get("any_of")
    found = [term for term in terms if term.casefold() in lowered]
    answered = len(found) == len(terms) if probe.get("all_of") else bool(found)
    texts = [record["content"] for record in included]
    _, copies = _copies(texts)
    _, repeated_lines = _copies(lines)
    return {
        "id": probe["id"], "question": probe["question"], "answer_in_context": answered, "terms_found": found,
        "stale_in_context": [term for term in probe.get("stale", []) if term.casefold() in lowered],
        "records": len(included), "record_types": dict(sorted(Counter(record["type"] for record in included).items())),
        "distinct_texts": len(set(texts)), "repeated_records": copies,
        "repeated_share": copies / len(included) if included else 0.0,
        "record_lines": len(lines), "repeated_lines": repeated_lines,
        "tokens_used": tokens_used, "token_budget": token_budget,
    }


def summary(report: dict) -> dict:
    """The headline numbers of a report, for ``compare``."""
    graph, probes, build = report["graph"], report["probes"], report.get("build", {})
    claims, entities = graph["claims"], graph["entities"]
    link_status = Counter(link["status"] for link in graph["links"])
    key_status = Counter(entry["status"] for entry in entities["key"].values())
    records = sum(probe["records"] for probe in probes)
    return {
        "extraction calls": build.get("calls"),
        "active claims": claims["active"],
        "distinct claim texts": claims["distinct_texts"],
        "claims whose text repeats another's": claims["extra_copies_of_a_text"],
        "claims repeating a subject, predicate and object": claims["extra_copies_of_a_claim"],
        "claims whose text is the whole message": claims["text_is_a_whole_message"],
        "claims from assistant turns only": claims["from_assistant_turns_only"],
        "user claims attached to the named owner": claims["user_claim_subjects"].get("owner_named", 0),
        "user claims attached to a first-person pronoun": claims["user_claim_subjects"].get("first_person_pronoun", 0),
        "entities": entities["active"],
        "entities with no link": len(entities["with_no_link"]),
        "pronoun entities": len(entities["pronouns"]),
        "key entities found / split / missing": "/".join(str(key_status.get(name, 0))
                                                         for name in ("found", "split", "missing")),
        "key links linked / via pronoun / not linked / missing": "/".join(
            str(link_status.get(name, 0)) for name in ("linked", "via_pronoun", "not_linked", "entity_missing")),
        "probes with the answer in context": f"{sum(probe['answer_in_context'] for probe in probes)}/{len(probes)}",
        "probes with a stale value in context": sum(bool(probe["stale_in_context"]) for probe in probes),
        "repeated records in contexts": f"{sum(probe['repeated_records'] for probe in probes)}/{records}",
    }


# --- Building and measuring a pack -----------------------------------------------------------------


async def _records(engine, owner: str) -> tuple[list[dict], list[dict], dict[str, dict]]:
    found = await engine.query_nodes(user_id=owner, lifecycle_states=list(LifecycleState), limit=_ALL)
    nodes = [{"id": str(node.id), "type": node.node_type.value, "state": node.lifecycle_state.value,
              "content": node.content, "metadata": node.metadata or {},
              "evidence_refs": [str(ref) for ref in node.evidence_refs]} for node in found]
    edges = await engine._graph_store.get_edges(node_ids=[node["id"] for node in nodes]) if nodes else []
    events = {str(event.id): {"role": event.role, "key": (event.metadata or {}).get("turn_key")}
              for event in await engine.get_events(owner, limit=_ALL)}
    return nodes, [{"source": str(edge.source_id), "target": str(edge.target_id), "type": edge.edge_type.value}
                   for edge in edges if edge.user_id == owner], events


async def measure(folder: Path, conversation: dict) -> dict:
    """Measure a finished pack without any model, and write its report."""
    pack = folder / "pack"
    if not (pack / "memory.duckdb").exists():
        raise ValueError(f"No pack at {pack}")
    owner = conversation["owner"]
    reference_time = datetime.fromisoformat(conversation["reference_time"])
    contexts = folder / "contexts"
    contexts.mkdir(exist_ok=True)
    build_path = folder / "build.json"
    build = json.loads(build_path.read_text()) if build_path.exists() else {}
    probes = []
    # The build's settings, so a retrieval setting it changed is measured too.
    async with MemoryEngine.open(gate_config(pack, build.get("overrides"))) as engine:
        nodes, edges, events = await _records(engine, owner)
        for probe in conversation["key"]["probes"]:
            response = await engine.retrieve(probe["question"], user_id=owner, reference_time=reference_time)
            included = [{"content": candidate.node.content, "type": candidate.node.node_type.value}
                        for section in response.bundle.sections.values() for candidate in section]
            context = response.bundle.render()
            (contexts / f"{probe['id']}.txt").write_text(f"Q: {probe['question']}\n\n{context}\n")
            probes.append(context_report(probe, included, context, response.bundle.tokens_used,
                                         response.bundle.token_budget))
    report = {"kind": "prme-chat-probe-report", "schema_version": SCHEMA_VERSION, "label": folder.name,
              "conversation": conversation["name"], "owner": owner, "build": build.get("totals", {}),
              "settings": {name: build.get(name) for name in ("overrides", "speaker", "organize")},
              "graph": graph_report(nodes, edges, events, conversation), "probes": probes}
    report["summary"] = summary(report)
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (folder / "report.md").write_text(report_markdown(report))
    return report


async def build(label: str, *, conversation_path: Path = DEFAULT_CONVERSATION, root: Path | None = None,
                overrides: dict | None = None, model: str = DEFAULT_MODEL, cloud: bool = False,
                speaker: bool = False, organize: bool = False, cache: bool = True, replace: bool = False,
                prepare=None) -> dict:
    """Ingest the conversation into a fresh pack under ``label``, then measure it.

    ``prepare(engine)``, for tests, runs before the first turn.
    """
    if not label or Path(label).name != label:
        raise ValueError("The label must be a plain folder name")
    conversation = load_conversation(conversation_path)
    root = default_root() if root is None else root
    overrides = overrides or {}
    folder = root / conversation["name"] / label
    if folder.exists():
        if not replace:
            raise ValueError(f"{folder} exists; use another label or --replace")
        if not (folder / "build.json").exists() and any(folder.iterdir()):
            raise ValueError(f"{folder} is not a chat probe build; remove it yourself")
        shutil.rmtree(folder)
    identity = model_identity(model, cloud=cloud)
    pack = folder / "pack"
    (pack / "lexical_index").mkdir(parents=True)
    owner = conversation["owner"]
    record = {"kind": BUILD_KIND, "schema_version": SCHEMA_VERSION, "label": label,
              "conversation": conversation["name"], "conversation_sha256": conversation_digest(conversation_path),
              "overrides": overrides, "speaker": speaker, "organize": organize, "extraction_cache": cache,
              "model": {name: identity.get(name) for name in (
                  "provider", "model", "resolved_model", "manifest_digest_sha256", "remote_host", "remote_model")},
              "server_version": identity.get("server_version"),
              "started_at": datetime.now(timezone.utc).isoformat()}
    config = build_config(pack, overrides, model=model)
    from benchmarks.retrieval_eval import provenance

    record["provenance"] = provenance(config)
    totals = Counter()
    async with MemoryEngine.open(config) as engine:
        if prepare is not None:
            prepare(engine)
        usage = _Usage(engine)
        hits = ExtractionCache(root / conversation["name"] / "extraction-cache")
        if cache:
            hits.wrap(engine._pipeline._extraction_provider)
        event_ids = []
        with (folder / "turns.jsonl").open("w") as log:
            for turn in chat_turns(conversation, speaker=speaker):
                before, hits_before, started, error = usage.snapshot(), hits.hits, time.perf_counter(), None
                try:
                    event_id = await engine.ingest(turn["content"], user_id=owner, role=turn["role"],
                                                   speaker=turn["speaker"], session_id=turn["session_id"],
                                                   metadata=turn["metadata"], event_time=turn["event_time"],
                                                   wait_for_extraction=True)
                except Exception as exc:
                    # The event is saved before extraction runs, so its extraction can be retried.
                    error = f"{type(exc).__name__}: {exc}"
                    stored = {(event.metadata or {}).get("turn_key"): str(event.id)
                              for event in await engine.get_events(owner, limit=_ALL)}
                    event_id = stored.get(turn["key"])
                    if event_id is None:
                        raise
                status = await _retry_turn(engine, owner, event_id) if error else "complete"
                if status != "complete":
                    raise RuntimeError(f"{turn['key']}: extraction did not complete ({error})")
                event_ids.append(event_id)
                calls, prompt_tokens, completion_tokens = (now - then for now, then in zip(usage.snapshot(), before))
                row = {"key": turn["key"], "role": turn["role"], "event_id": event_id, "error": error,
                       "seconds": time.perf_counter() - started, "calls": calls, "prompt_tokens": prompt_tokens,
                       "completion_tokens": completion_tokens, "cache_hits": hits.hits - hits_before}
                totals.update({name: row[name] for name in (
                    "seconds", "calls", "prompt_tokens", "completion_tokens", "cache_hits")})
                log.write(json.dumps(row) + "\n")
                log.flush()
                print(f"{label}: {turn['key']} {turn['role']} {row['seconds']:.1f}s", file=sys.stderr, flush=True)
        statuses = await _finish_extractions(engine, owner, event_ids)
        if set(statuses.values()) != {"complete"}:
            raise RuntimeError(f"Extraction did not complete: {dict(Counter(statuses.values()))}")
        await _drain_sources(engine, owner)
        if organize:
            result = await engine.organize(user_id=owner, budget_ms=600_000)
            record["organize_result"] = result.model_dump(mode="json")
    record.update(totals={"turns": len(event_ids), **dict(totals), "cache_unreproduced": hits.unreproduced},
                  finished_at=datetime.now(timezone.utc).isoformat())
    (folder / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    return await measure(folder, conversation)


# --- Reports -----------------------------------------------------------------------------------------


def _table(header: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |" for row in rows]
    return lines


def report_markdown(report: dict) -> str:
    graph = report["graph"]
    lines = [f"# Chat probe: {report['conversation']} / {report['label']}", "",
             f"Settings: `{json.dumps(report['settings'])}`", ""]
    lines += _table(["Measure", "Value"], [[name, value] for name, value in report["summary"].items()])
    lines += ["", "## Records", ""]
    lines += _table(["Type/state", "Count"], list(graph["records"].items()))
    claims = graph["claims"]
    lines += ["", "## What the owner's claims are attached to", ""]
    lines += _table(["Subject", "Claims from user turns"], list(claims["user_claim_subjects"].items()))
    lines += ["", "## Facts the conversation repeats", ""]
    lines += _table(["Fact", "User mentions", "Claims stating it", "From assistant only", "Records showing its text"],
                    [[repeat["label"], repeat["user_mentions"], repeat["claims_stating_it"],
                      repeat["from_assistant_only"], repeat["records_showing_its_text"]]
                     for repeat in graph["repeats"]])
    lines += ["", "## Links of the answer key", ""]
    lines += _table(["Link", "Status", "Connecting claims"],
                    [[link["label"], link["status"],
                      "; ".join(f"{claim['claim']} ({claim['state']})" for claim in link["claims"][:3])
                      + (f"; and {len(link['claims']) - 3} more" if len(link["claims"]) > 3 else "")]
                     for link in graph["links"]])
    entities = graph["entities"]
    lines += ["", "## Entities of the answer key", ""]
    lines += _table(["Entity", "Nodes", "Status"],
                    [[entity, entry["nodes"], entry["status"]] for entity, entry in entities["key"].items()])
    lines += ["", f"Entities with no link ({len(entities['with_no_link'])}): "
              + (", ".join(entities["with_no_link"]) or "none"),
              "", f"Pronoun entities ({len(entities['pronouns'])}): " + (", ".join(entities["pronouns"]) or "none")]
    lines += ["", "## Probe questions", ""]
    lines += _table(["Probe", "Answer in context", "Stale value", "Records", "Repeated records", "Tokens"],
                    [[probe["question"], "yes" if probe["answer_in_context"] else "no",
                      ", ".join(probe["stale_in_context"]) or "", probe["records"],
                      f"{probe['repeated_records']} ({probe['repeated_share']:.0%})",
                      f"{probe['tokens_used']}/{probe['token_budget']}"] for probe in report["probes"]])
    return "\n".join(lines) + "\n"


def compare_markdown(reports: list[dict]) -> str:
    labels = [report["label"] for report in reports]
    names = list(reports[0]["summary"])
    lines = _table(["Measure", *labels], [[name, *(report["summary"].get(name) for report in reports)]
                                          for name in names])
    lines += [""]
    lines += _table(["Probe", *labels], [
        [probe["question"], *("yes" if by_id[probe["id"]]["answer_in_context"] else "no"
                              for by_id in ({row["id"]: row for row in report["probes"]} for report in reports))]
        for probe in reports[0]["probes"]])
    lines += [""]
    lines += _table(["Link", *labels], [
        [link["label"], *(next(row["status"] for row in report["graph"]["links"] if row["id"] == link["id"])
                          for report in reports)]
        for link in reports[0]["graph"]["links"]])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.chat_probe",
                                     description="Run a scripted chat through ingest() and measure repetition, "
                                                 "graph links and retrieval against its answer key.")
    parser.add_argument("--conversation", type=Path, default=DEFAULT_CONVERSATION)
    parser.add_argument("--root", type=Path, default=None,
                        help="Where builds live; the main checkout's data/chat-probe-v1 by default")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="Build a fresh pack from the conversation and measure it")
    command.add_argument("--label", required=True, help="Folder name for this build, e.g. defaults")
    command.add_argument("--model", default=DEFAULT_MODEL, help="An Ollama model tag")
    command.add_argument("--cloud", action="store_true",
                         help="Accept an Ollama cloud model: Ollama's hosted service receives the turns")
    command.add_argument("--speaker", action="store_true", help="Name the owner as the speaker of their turns")
    command.add_argument("--organize", action="store_true", help="Run the organizer once after the last turn")
    command.add_argument("--no-cache", action="store_true", help="Ask the model again for every turn")
    command.add_argument("--replace", action="store_true", help="Delete an earlier build with this label first")
    command.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                         help="Build setting: a dotted key and a JSON value, as for the gate")
    command = commands.add_parser("measure", help="Measure a finished build again, without any model")
    command.add_argument("--label", required=True)
    command = commands.add_parser("compare", help="Put the reports of several builds side by side")
    command.add_argument("labels", nargs="+")
    args = parser.parse_args(argv)
    _quiet_offline_cli()
    conversation = load_conversation(args.conversation)
    root = default_root() if args.root is None else args.root
    folder = root / conversation["name"]
    if args.command == "build":
        try:
            overrides = parse_overrides(args.overrides)
            build_config(Path("{pack}"), overrides, model=args.model)
        except ValueError as exc:
            parser.error(str(exc))
        report = asyncio.run(build(args.label, conversation_path=args.conversation, root=root, overrides=overrides,
                                   model=args.model, cloud=args.cloud, speaker=args.speaker, organize=args.organize,
                                   cache=not args.no_cache, replace=args.replace))
        print(report_markdown(report))
    elif args.command == "measure":
        print(report_markdown(asyncio.run(measure(folder / args.label, conversation))))
    else:
        reports = [json.loads((folder / label / "report.json").read_text()) for label in args.labels]
        print(compare_markdown(reports))


if __name__ == "__main__":
    main()
