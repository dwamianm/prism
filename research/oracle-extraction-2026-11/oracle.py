"""The perfect extraction test's loader and build (GUIDE.md): facts written by hand, loaded with no model.

**The mention rule.** Round 2 (branch ``fact-fields``) moved the rule into the engine: ``ingest(content,
assertions=...)`` accepts a subject or object that the turn mentions, that is the turn's speaker when the turn says
I, me, my, mine, myself, we, us or our, or that names an entity the owner's memory already holds (same name and
type, same scope); a literal value must be in the turn unless it repeats a live claim's value at the same subject
and attribute (a closure). Each assertion records how its ends were found (``found``). Round 1's run-time patch of
the engine's mention check is gone.

**The check** (:func:`check`, run by ``check_facts.py``) walks the turns in order and gives each assertion to the
engine's own ``prepare_assertions``, with the speaker the probe build names (``user_name`` on user turns, only for
a probe built with ``--speaker``) and the memory the earlier accepted assertions of the same level set have built:
their entities (name and type) and their literal values. A build with no model holds nothing else, so the check
predicts the build; the build records whether it agreed.

**The build** (:func:`build`) runs ``benchmarks.diagnostics.chat_probe.build`` unchanged, so turns, event times,
session ids, roles, speaker, the end of the session and every measurement are the model build's. Its ``prepare``
hook (run before the first turn) wraps ``engine.ingest``: each of a turn's assertions is checked alone by the
engine against the pack as it is (``ingest()`` refuses a whole turn when one assertion fails), and the turn is
ingested with the ones it accepts. The hook also replaces the extraction provider's ``extract``,
``extract_batch`` and ``repair_pronouns`` with stubs: ``ingest()`` refuses an empty assertion list, so a turn with
no accepted assertion goes through ``ingest()`` as any turn does and the stub answers its extraction with nothing
(no entities, no facts), which stores the turn exactly as a model build stores a turn the model found nothing in.
A pronoun repair or a single-turn extraction raises. The probe build counts the model client's calls;
:func:`build` requires zero.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prme.ingestion.assertions import (  # noqa: E402
    ASSERTIONS_KEY,
    ATTRIBUTE_RELATION,
    Assertion,
    Held,
    _fold,
    prepare_assertions,
)

HERE = Path(__file__).resolve().parent
LEVELS = ("O1", "O2")
SETS = {"O1": ("O1",), "O1+O2": ("O1", "O2")}
MODEL = "deepseek-v4.1-flash:cloud"
NEEDED = ("text only", "speaker", "memory", "closure")


def turn_index(turns_file: dict) -> dict[str, dict]:
    """Each turn by its probe key, ``<session id>#<index>``, as the probe build keys it, in conversation order."""
    return {f"{session['id']}#{turn['index']}": turn
            for session in turns_file["sessions"] for turn in session["turns"]}


def speaker_of(turns_file: dict, turn: dict) -> str | None:
    """The speaker the probe build passes: the owner's name on user turns of a probe built with --speaker."""
    return turns_file["user_name"] if turns_file.get("speaker_named") and turn["role"] == "user" else None


def _reason(exc: ValueError) -> str:
    return str(exc).removeprefix("assertions[0]: ")


def remember(held: Held, assertion: dict) -> Held:
    """The memory after an accepted assertion: its entities (name, type) and, for ``has``, its value."""
    item = Assertion.model_validate({key: value for key, value in assertion.items() if key != "found"}).normalized()
    entities, values = set(held.entities), set(held.values)
    entities.add((_fold(item.subject), item.subject_type))
    if item.relation == ATTRIBUTE_RELATION:
        values.add((*item.address(), _fold(item.object)))
    else:
        entities.add((_fold(item.object), item.object_type))
    return Held(frozenset(entities), frozenset(values))


def check(facts: dict, turns_file: dict, levels: tuple[str, ...]) -> dict:
    """Every assertion of these levels checked as the build will load it, turn by turn.

    Returns ``{"problems", "turns", "roles"}``. A problem is a malformed entry (no such turn, a repeated or
    out-of-order turn, an assertion without a level). ``turns`` maps each turn key to its rows:
    ``{"position", "level", "assertion", "found", "reason"}``, where ``assertion`` is the engine's form (level
    removed), ``found`` says how the engine found its subject and object, and ``reason`` is None when it loads.
    """
    turns = turn_index(turns_file)
    order = list(turns)
    problems: list[str] = []
    checked: dict[str, list[dict]] = {}
    entries = facts.get("turns")
    if not isinstance(entries, list):
        return {"problems": ["the facts file needs a 'turns' list"], "turns": {}, "roles": {}}
    held, last = Held(), -1
    for number, entry in enumerate(entries):
        key = f"{entry.get('session')}#{entry.get('index')}"
        if key not in turns:
            problems.append(f"turns[{number}]: no turn {key}")
            continue
        if key in checked:
            problems.append(f"turns[{number}]: {key} is listed twice")
            continue
        if order.index(key) < last:
            problems.append(f"turns[{number}]: {key} is out of order")
        last = max(last, order.index(key))
        turn, rows, accepted = turns[key], [], []
        for position, item in enumerate(entry.get("assertions") or []):
            if not isinstance(item, dict):
                problems.append(f"{key} [{position}]: an assertion is an object")
                continue
            if item.get("level") not in LEVELS:
                problems.append(f"{key} [{position}]: level must be O1 or O2, not {item.get('level')!r}")
                continue
            if item["level"] not in levels:
                continue
            raw = {name: value for name, value in item.items() if name != "level"}
            try:
                _, [prepared] = prepare_assertions(turn["text"], [raw], speaker=speaker_of(turns_file, turn),
                                                   held=held)
                rows.append({"position": position, "level": item["level"], "assertion": prepared,
                             "found": prepared["found"], "reason": None})
                accepted.append(prepared)
            except ValueError as exc:
                rows.append({"position": position, "level": item["level"], "assertion": raw, "found": None,
                             "reason": _reason(exc)})
        # The memory grows only after the turn: the engine reads it before the turn's own claims are written.
        for prepared in accepted:
            held = remember(held, prepared)
        checked[key] = rows
    missing = [key for key in order if key not in checked]
    if missing:
        problems.append(f"{len(missing)} turns have no entry: {', '.join(missing)}")
    return {"problems": problems, "turns": checked, "roles": {key: turn["role"] for key, turn in turns.items()}}


def reason_kind(reason: str) -> str:
    if "repeats no live claim's value" in reason:
        return "literal value not in the turn and no closure"
    for field in ("subject", "object"):
        if f"does not mention its {field}" in reason:
            return f"{field} not in the turn, not the speaker, not in memory"
    if "is not in the vocabulary" in reason:
        return "relation not in the vocabulary"
    return reason


def tally(rows: list[dict]) -> dict:
    """Assertions, accepted, refused by reason, and the accepted ones each rule beyond the text was needed for."""
    accepted = [row for row in rows if row["reason"] is None]
    refused = Counter(reason_kind(row["reason"]) for row in rows if row["reason"] is not None)
    needed = Counter()
    for row in accepted:
        used = set(row["found"].values())
        if used == {"text"}:
            needed["text only"] += 1
        for kind in ("speaker", "memory", "closure"):
            if kind in used:
                needed[kind] += 1
    return {"assertions": len(rows), "accepted": len(accepted), "refused": sum(refused.values()),
            "refused_by_reason": dict(refused.most_common()),
            "accepted_needing": {kind: needed[kind] for kind in NEEDED}}


# --- The build ----------------------------------------------------------------------------------------------------


class EmptyExtraction:
    """Stands in for the model on turns with no accepted assertion: every turn extracted to nothing, with no call."""

    def __init__(self) -> None:
        self.turns = 0

    def install(self, provider) -> None:
        provider.extract_batch = self.extract_batch
        provider.extract = self.extract
        provider.repair_pronouns = self.repair_pronouns

    async def extract_batch(self, turns, **_options) -> dict:
        self.turns += len(turns)
        return {"turns": [{"turn": turn.index, "entities": [], "facts": [], "relationships": []} for turn in turns]}

    async def extract(self, *_args, **_options):
        raise RuntimeError("the oracle build expects batched extraction only")

    async def repair_pronouns(self, *_args, **_options):
        raise RuntimeError("the oracle build makes no pronoun repair")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sentence_text(fact) -> str:
    """Diagnostic only (``--sentence-text``): a stated claim's text as its turn's sentences, not its fields.

    The shortest run of the turn's sentences that names the claim's object, or the whole turn when none does: about
    what round 1 stored, so one build separates the rendered text (round 2, change 3) from the new facts.
    """
    from prme.ingestion.grounding import claim_sentences

    turn = fact.evidence_quote
    return claim_sentences(turn, fact.object, fact.object) or turn


def build(label: str, *, name: str, facts_path: Path, levels: tuple[str, ...], overrides: dict | None = None,
          speaker: bool = False, replace: bool = False, sentence_claims: bool = False) -> dict:
    """Build the probe pack ``label`` of conversation ``name`` from a facts file, and measure it with the probe.

    ``sentence_claims`` replaces, for this build only, the engine's rendered claim text with :func:`sentence_text`.
    """
    import asyncio
    import contextlib
    from unittest import mock

    from benchmarks.diagnostics import chat_probe
    from prme.types import Scope

    conversation_path = ROOT / "benchmarks" / "conversations" / f"{name}.json"
    turns_file = json.loads((HERE / "turns" / f"{name}.json").read_text())
    if bool(turns_file.get("speaker_named")) != speaker:
        raise ValueError(f"{name}: the turns file says speaker_named={turns_file.get('speaker_named')}, "
                         f"the build says speaker={speaker}")
    facts = json.loads(facts_path.read_text())
    predicted = check(facts, turns_file, levels)
    if predicted["problems"]:
        raise ValueError(f"{facts_path}: " + "; ".join(predicted["problems"]))
    by_turn = {f"{entry['session']}#{entry['index']}": [
        (position, {key: value for key, value in item.items() if key != "level"})
        for position, item in enumerate(entry.get("assertions") or []) if item.get("level") in levels]
        for entry in facts["turns"]}
    empty, ingested, rows = EmptyExtraction(), Counter(), {}

    def prepare(engine) -> None:
        empty.install(engine._pipeline._extraction_provider)
        original = engine.ingest

        async def ingest(content, **options):
            key = options["metadata"]["turn_key"]
            accepted, turn_rows = [], []
            for position, item in by_turn.get(key, []):
                try:
                    _, metadata = await engine._prepare_assertions(
                        content, [item], None, policy=None, user_id=options["user_id"], scope=Scope.PERSONAL,
                        speaker=options.get("speaker"))
                    prepared = metadata[ASSERTIONS_KEY]["items"][0]
                    accepted.append(item)
                    turn_rows.append({"position": position, "found": prepared["found"], "reason": None})
                except ValueError as exc:
                    turn_rows.append({"position": position, "found": None, "reason": _reason(exc)})
            rows[key] = turn_rows
            ingested["with assertions" if accepted else "without assertions"] += 1
            if accepted:
                return await original(content, assertions=accepted, **options)
            return await original(content, **options)

        engine.ingest = ingest

    # The model build's configuration (extraction through deepseek-v4.1-flash:cloud), so only the facts differ;
    # the stubs answer every extraction call, and the build fails on any model call.
    with (mock.patch("prme.ingestion.pipeline.stated_fact_text", sentence_text) if sentence_claims
          else contextlib.nullcontext()):
        report = asyncio.run(chat_probe.build(label, conversation_path=conversation_path, overrides=overrides,
                                              model=MODEL, cloud=True, speaker=speaker, cache=False,
                                              replace=replace, prepare=prepare))
    folder = chat_probe.default_root() / name / label
    totals = json.loads((folder / "build.json").read_text())["totals"]
    if totals.get("calls"):
        raise RuntimeError(f"{label}: {totals['calls']} model calls; the oracle build allows none")
    built_rows = [row for turn_rows in rows.values() for row in turn_rows]
    disagreements = [f"{key} [{row['position']}]" for key, turn_rows in rows.items() for row in turn_rows
                     if (row["found"], row["reason"]) != next(
                         ((other["found"], other["reason"]) for other in predicted["turns"].get(key, [])
                          if other["position"] == row["position"]), None)]
    record = {"kind": "oracle-extraction-build", "round": 2, "label": label, "conversation": name,
              "claim_text": "turn sentences (diagnostic)" if sentence_claims else "rendered from the fields",
              "levels": list(levels), "facts": str(facts_path.relative_to(ROOT)) if facts_path.is_relative_to(ROOT)
              else str(facts_path), "facts_sha256": digest(facts_path), "overrides": overrides or {},
              "speaker": speaker, "model_calls": totals.get("calls", 0), "turns": dict(ingested),
              "empty_extractions": empty.turns, "tally": tally(built_rows),
              "check_disagrees_with_the_build": disagreements,
              "refusals": [{"turn": key, "position": row["position"], "reason": row["reason"]}
                           for key, turn_rows in rows.items() for row in turn_rows if row["reason"]]}
    (folder / "oracle.json").write_text(json.dumps(record, indent=2) + "\n")
    return {"report": report, "oracle": record, "folder": folder}
