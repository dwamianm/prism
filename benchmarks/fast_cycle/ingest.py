"""Stage "ingest": three scripted chats through ``ingest()`` with model extraction.

``store()`` keeps text as sent; ``ingest()`` is where a model reads each chat
turn and decides the claims, the entities and the links. This stage builds
three small packs with the chat probe's build (``chat_probe.build``), the one
the fast gate for ingest changes uses, and measures them against each chat's
answer key:

- ``defects-v1`` (14 turns): the speaker stays "I", the four cities are not
  linked to Japan, copies of one sentence take the top ranks.
- ``corrections-v1`` (16 turns, owner named as the speaker): what a corrected
  fact leaves behind, measured with the corrections probe's case report.
- ``chat-v1`` (54 turns): repetition, links and probes over a longer chat.

Extraction is by ``deepseek-v4.1-flash:cloud`` through the local Ollama server,
as in the gate. Every request goes through the extraction cache of
``extracted_packs``, which replays a stored model response while the model, the
prompt and the turn are unchanged. The committed cache
(``fixtures/fast-cycle/extraction-cache``) holds the responses of one live run,
so a change to what ingestion does with the model's output (claims, entities,
links, retrieval) is measured on identical model output and takes seconds; a
change to the extraction prompt or the model misses the cache and makes live
calls, whose output varies from run to run. A run works on a copy of the
committed cache, so it never writes into the repository; ``update_cache``
copies the entries a run added back.

The cache keeps responses, not usage, so calls replayed from it report no
tokens. A ``usage.json`` next to the entries keeps each turn's prompt and
completion tokens from the live call that filled it. A replayed turn is the
identical request, so the same count applies; a replay missing its entry
reports no token total instead of a wrong one.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time

from benchmarks.diagnostics import chat_probe, corrections_probe
from benchmarks.diagnostics.product_packing import gate_config
from benchmarks.fast_cycle.store import FIXTURES
from prme import MemoryEngine

LABEL = "cycle"
DEFAULT_MODEL = "deepseek-v4.1-flash:cloud"
CACHE_DIR = FIXTURES / "extraction-cache"
USAGE_FILE = "usage.json"
# The chat, and whether the owner is named as the speaker of their turns: the
# fast gate builds defects-v1 and chat-v1 without it, and the corrections
# probe builds its chat with it.
CONVERSATIONS = (("defects-v1", False), ("corrections-v1", True), ("chat-v1", False))
CONVERSATION_DIR = Path(chat_probe.DEFAULT_CONVERSATION).parent


def default_conversations() -> list[tuple[Path, bool]]:
    return [(CONVERSATION_DIR / f"{name}.json", speaker) for name, speaker in CONVERSATIONS]


# --- The extraction cache --------------------------------------------------------------------------


def cache_size(folder: Path) -> tuple[int, int]:
    """The number of cached responses and the bytes the folder holds."""
    files = [path for path in folder.rglob("*") if path.is_file()] if folder.is_dir() else []
    return sum(path.name != USAGE_FILE for path in files), sum(path.stat().st_size for path in files)


def working_cache(work: Path, committed: Path, *, use_committed: bool) -> Path:
    """A copy of the committed cache for this run to read and add to, empty without ``use_committed``."""
    working = work / "extraction-cache"
    shutil.rmtree(working, ignore_errors=True)
    if use_committed and committed.is_dir():
        shutil.copytree(committed, working)
    else:
        working.mkdir(parents=True)
    return working


def update_cache(working: Path, committed: Path) -> int:
    """Copy the responses a run added to the committed cache and merge its usage; returns the entries added."""
    added = 0
    for path in sorted(working.rglob("*")):
        target = committed / path.relative_to(working)
        if path.is_file() and path.name != USAGE_FILE and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            added += 1
    usage = _read_usage(committed)
    for name, turns in _read_usage(working).items():
        usage.setdefault(name, {}).update(turns)
    _write_usage(committed, usage)
    return added


def _read_usage(folder: Path) -> dict:
    path = folder / USAGE_FILE
    return json.loads(path.read_text()) if path.exists() else {}


def _write_usage(folder: Path, usage: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps({name: dict(sorted(turns.items())) for name, turns in sorted(usage.items())}, indent=1)
    (folder / USAGE_FILE).write_text(text + "\n")


def turn_tokens(rows: list[dict], recorded: dict) -> tuple[int | None, dict]:
    """Extraction tokens of a build, and the live turns' usage to record.

    A turn the model answered counts its own tokens. A turn the cache
    replayed counts the tokens recorded for it; the total is None when a
    replayed turn has no record.
    """
    total, live, known = 0, {}, True
    for row in rows:
        if row["calls"]:
            total += row["prompt_tokens"] + row["completion_tokens"]
            live[row["key"]] = {field: row[field] for field in ("calls", "prompt_tokens", "completion_tokens")}
        elif row["cache_hits"]:
            entry = recorded.get(row["key"])
            known = known and entry is not None
            total += (entry["prompt_tokens"] + entry["completion_tokens"]) if entry else 0
    return (total if known else None), live


# --- Measures ----------------------------------------------------------------------------------------


def correction_measures(cases: list[dict]) -> dict:
    """What corrected facts leave behind, over the cases that changed a value."""
    changed = [case["facts"] for case in cases if "new_value_active" in case["facts"]]
    return {
        "old_value_shown_first": sum(facts["first_context_line_shows"] == "old" for facts in changed),
        "old_only_context_lines": sum(facts["context_lines_with_only_the_old_value"] for facts in changed),
        "old_records_still_active": sum(facts["active_records_with_only_the_old_value"] for facts in changed),
        "old_claims_not_retired": sum(facts["extracted_claims_with_only_the_old_value"] - facts["of_them_superseded"]
                                      for facts in changed),
        "new_value_active": sum(facts["new_value_active"] for facts in changed),
    }


def ingest_measures(report: dict, nodes: int, edges: int, tokens: int | None, corrections: list[dict] | None) -> dict:
    """One chat's measures, by name, from the chat probe's report and the pack's counts."""
    graph, probes, build = report["graph"], report["probes"], report["build"]
    claims, entities = graph["claims"], graph["entities"]
    links = [link["status"] for link in graph["links"]]
    key = [entry["status"] for entry in entities["key"].values()]
    turns = build["turns"]
    measures = {
        "extraction_requests": build["calls"] + build["cache_hits"],
        "extraction_tokens": tokens,
        "claims_per_turn": claims["active"] / turns,
        "claims_repeating_text": claims["extra_copies_of_a_text"],
        "claims_repeating_triple": claims["extra_copies_of_a_claim"],
        "claims_whole_message": claims["text_is_a_whole_message"],
        "nodes_per_turn": nodes / turns,
        "edges_per_turn": edges / turns,
        "user_claims_named_owner": claims["user_claim_subjects"].get("owner_named", 0),
        "user_claims_first_person": claims["user_claim_subjects"].get("first_person_pronoun", 0),
        "pronoun_entities": len(entities["pronouns"]),
        "entities_without_link": len(entities["with_no_link"]),
        "key_entities_found": key.count("found"),
        "key_entities_split": key.count("split"),
        "key_entities_missing": key.count("missing"),
        "key_links_linked": links.count("linked"),
        "key_links_via_pronoun": links.count("via_pronoun"),
        "key_links_not_linked": links.count("not_linked") + links.count("entity_missing"),
        "answers_in_context": sum(probe["answer_in_context"] for probe in probes),
        "answers_in_top3": sum(probe["answer_rank"] is not None and probe["answer_rank"] <= 3 for probe in probes),
        "answers_in_top5": sum(probe["answer_rank"] is not None and probe["answer_rank"] <= 5 for probe in probes),
        "stale_in_context": sum(bool(probe["stale_in_context"]) for probe in probes),
        "repeated_records_in_contexts": sum(probe["repeated_records"] for probe in probes),
        **{f"answer_rank:{probe['id']}": probe["answer_rank"] for probe in probes},
    }
    if corrections is not None:
        measures.update(correction_measures(corrections))
    return measures


async def pack_counts(pack: Path, owner: str, overrides: dict | None) -> tuple[int, int]:
    """The nodes and the edges a finished pack holds for its owner, in every lifecycle state."""
    async with MemoryEngine.open(gate_config(pack, overrides)) as engine:
        nodes, edges, _ = await chat_probe._records(engine, owner)
    return len(nodes), len(edges)


# --- The stage -----------------------------------------------------------------------------------------


async def run(work: Path, *, cache_dir: Path = CACHE_DIR, use_cache: bool = True, overrides: dict | None = None,
              model: str = DEFAULT_MODEL, conversations: list[tuple[Path, bool]] | None = None,
              prepare=None) -> dict:
    """Build and measure each chat; returns the stage's measures by chat and its details.

    ``use_cache=False`` starts from an empty cache, so every request goes to the
    model. ``prepare(engine)``, for tests, runs before each build's first turn.
    """
    working = working_cache(work, cache_dir, use_committed=use_cache)
    usage = _read_usage(working)
    root = work / "builds"
    shutil.rmtree(root, ignore_errors=True)
    chats, measures = {}, {}
    for path, speaker in conversations or default_conversations():
        conversation = chat_probe.load_conversation(path)
        name = conversation["name"]
        (root / name).mkdir(parents=True, exist_ok=True)
        # The chat probe looks for a chat's cache beside its builds; every chat shares this one.
        (root / name / "extraction-cache").symlink_to(working.resolve(), target_is_directory=True)
        started = time.perf_counter()
        report = await chat_probe.build(LABEL, conversation_path=path, root=root, overrides=overrides, model=model,
                                        cloud=model.endswith(":cloud"), speaker=speaker, replace=True,
                                        prepare=prepare)
        folder = root / name / LABEL
        rows = [json.loads(line) for line in (folder / "turns.jsonl").read_text().splitlines()]
        tokens, live = turn_tokens(rows, usage.get(name, {}))
        usage.setdefault(name, {}).update(live)
        nodes, edges = await pack_counts(folder / "pack", conversation["owner"], overrides)
        cases = None
        if conversation["key"].get("corrections"):
            found = await corrections_probe.path_report(
                folder / "pack", conversation, corrections_probe.load_cases(conversation),
                folder / "correction-contexts", overrides)
            cases = found["cases"]
        measures[name] = ingest_measures(report, nodes, edges, tokens, cases)
        build = json.loads((folder / "build.json").read_text())
        chats[name] = {"turns": report["build"]["turns"], "speaker": speaker, "seconds": time.perf_counter() - started,
                       "conversation_sha256": chat_probe.conversation_digest(path),
                       "live_calls": report["build"]["calls"], "cache_hits": report["build"]["cache_hits"],
                       "unreproduced": report["build"]["cache_unreproduced"],
                       "model": build["model"], "server_version": build["server_version"],
                       "questions": {probe["id"]: probe["question"] for probe in report["probes"]},
                       "top_results": {probe["id"]: probe["top_results"][:3] for probe in report["probes"]},
                       **({"corrections": cases} if cases is not None else {})}
    _write_usage(working, usage)
    live_calls = sum(chat["live_calls"] for chat in chats.values())
    return {"mode": "live" if live_calls else "replay", "model": model, "settings": overrides or {},
            "live_calls": live_calls, "cache_hits": sum(chat["cache_hits"] for chat in chats.values()),
            "conversations": chats, "measures": measures, "working_cache": str(working)}
