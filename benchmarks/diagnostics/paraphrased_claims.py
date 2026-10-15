"""Paraphrased duplicate claims in ``ingest()`` packs and the context they take (#255).

Claim merge (#209) retires only exact repeats of a claim, and
``packing.fold_repeated_text`` folds only records of the same source turn. This
diagnostic measures what neither covers: current claims about one subject that
state the same thing in other words, and how much of each packed context they
take. It makes no model calls. Similarities come from the vectors each pack
stored for its claims, which its own embedding model wrote (``vector_metadata``
names it), and the contexts come from the offline evidence gate's own replay
(``product_packing.run_gate``) over the same packs, with the current defaults.

Three subcommands, run in this order:

``pairs``
    Clones each complete pack of a build, checks the clone against its
    manifest, and pairs every two current claims that one subject entity
    holds. A current claim is a fact, decision or preference record in an
    active lifecycle state that is neither superseded nor closed, and its
    subject is the entity of a current HAS_FACT edge to it. Pairs at a cosine
    similarity of 0.90 or more are kept. ``--sheet`` writes a stratified
    sample of them, drawn with a fixed seed, for hand labeling.
``replay``
    Runs the evidence gate over the same packs and keeps, for each question,
    the records its receipt marks as in context and the token cost the packer
    charged for each one.
``report``
    Combines the pairs, the labeled sample and the contexts, and applies the
    decision rule that #255 fixed before measuring.

The decision rule (#255): open a follow-up only if same-fact pairs with both
partners packed take at least 5% of packed tokens on average, and in some
similarity band at least 95% of the labeled pairs are the same fact once pairs
that differ in a number, a date or a negation are set aside. Otherwise the
numbers close the issue. How this module reads the rule is in ``READING``,
which was written before any current pack was measured.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import re
import shutil
import statistics
import sys
import tempfile

import duckdb
import numpy as np

from benchmarks.diagnostics import extracted_packs, product_packing
from prme.ingestion.claim_merge import _normalize
from prme.models.nodes import MemoryNode
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import _render_context_entry
from prme.retrieval.time import as_utc
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.types import ACTIVE_LIFECYCLE_STATES, EdgeType, NodeType, RepresentationLevel

PAIRS_KIND = "prme-paraphrased-claim-pairs"
LABELS_KIND = "prme-paraphrased-claim-labels"
CONTEXTS_KIND = "prme-paraphrased-claim-contexts"
REPORT_KIND = "prme-paraphrased-claim-report"
SCHEMA_VERSION = 1

# What ingest() makes of an extracted claim (pipeline._FACT_TYPE_TO_NODE_TYPE).
CLAIM_TYPES = (NodeType.FACT, NodeType.DECISION, NodeType.PREFERENCE)


@dataclass(frozen=True)
class Band:
    """A sampling band of cosine similarity: ``low`` inclusive, ``high`` exclusive (None: up to 1)."""

    name: str
    low: float
    high: float | None


BANDS = (Band("0.90-0.95", 0.90, 0.95), Band("0.95-0.97", 0.95, 0.97), Band("0.97-1.00", 0.97, None))
# The issue counts pairs at or above each band's lower edge.
THRESHOLDS = tuple(band.low for band in BANDS)
FLOOR = THRESHOLDS[0]
# Similarities are rounded before banding, so a stored value always lands in the band it shows.
SIMILARITY_DIGITS = 6
# Descriptive only: a line this short is a greeting, a thanks or a reply such
# as "Thanks so much, Nate!". Counted after the "(time) Speaker:" label that
# opens a LoCoMo turn.
SHORT_WORDS = 6
_TURN_LABEL = re.compile(r"\s*\([^()]*\)\s*[^:]{1,40}:\s*")
SAMPLE_SIZES = {"0.90-0.95": 17, "0.95-0.97": 17, "0.97-1.00": 16}
SAMPLE_SEED = 255

TOKEN_SHARE_RULE = 0.05
SAME_FACT_RULE = 0.95
LABELS = ("same_fact", "different_facts")
DIFFERENCES = ("number", "date", "negation")

DECISION_RULE = (
    "Open a follow-up only if both of these hold: same-fact pairs with both partners packed take at least 5% "
    "of packed tokens on average; and in some similarity band, at least 95% of the labeled pairs are the same "
    "fact once pairs that differ in a number, a date or a negation are set aside. A follow-up would fold such "
    "pairs at packing time only and keep both claims and their evidence in the store. If either condition "
    "fails, close the issue with the numbers. (#255)"
)
READING = (
    "Packed tokens are the packer's own count of the whole rendered context (MemoryBundle.tokens_used, the "
    "gate's context_tokens) with the configured tokenizer. A packed record's tokens are the token_cost the "
    "packer charged for its rendered line, with the same tokenizer. At a threshold, a packed claim counts when "
    "at least one claim paired with it at or above the threshold is packed in the same context; its share is "
    "taken per question and averaged over every replayed question. The upper bound counts every such pair as "
    "the same fact. The adjusted estimate counts each pair with the labeled same-fact rate of its sampling "
    "band, and a claim with several packed partners with the chance that at least one of them is the same "
    "fact, as if pairs were independent. Condition 1 reads the adjusted estimate for all pairs at or above "
    "0.90. Condition 2 reads each sampling band ([0.90, 0.95), [0.95, 0.97) and [0.97, 1.00]) on its own "
    "labeled pairs, after different-fact pairs that differ only in a number, a date or a negation are set "
    "aside. Pairs whose texts are identical after normalization count like any other pair. The report also "
    "applies the rule under the readings this does not use (the upper bound, pooled tokens, the records' own "
    "tokens as the denominator, the 'at or above' bands weighted by their pair counts, and both conditions "
    "with identical-text pairs set aside), and names any that would change the decision."
)
LABELING_RULE = (
    "Same fact: both records state the same proposition about the same subject: one event or occurrence, "
    "state, attribute, relationship, preference or plan. Wording, word order, a name in place of I, hedges "
    "and filler do not matter, and neither does the date a line shows when both dates refer to the same "
    "occurrence or state. A record that says everything the other says and adds detail is still the same "
    "fact, because the other line adds nothing beside it. Where a line leaves a reference open (it, that, "
    "there), the claim's subject, predicate and object and its source turn decide what it refers to. "
    "Different facts: each record states something the "
    "other does not: another event, or the same kind of event on another occasion; another object, "
    "attribute, person, place, amount, time or polarity; or a detail on each side that the other lacks. A "
    "different-fact pair is also marked with the one thing that alone separates it, if there is one: number "
    "(the same proposition with another count or amount), date (the same kind of event or statement on "
    "another date or occasion) or negation (one record denies what the other asserts)."
)


# Reading packs ---------------------------------------------------------------

@dataclass(frozen=True)
class PackClaims:
    """A pack's current claims that have a subject entity and a stored vector."""

    unit: str
    pack_sha256: str
    ids: tuple[str, ...]
    vectors: np.ndarray  # one unit-length row per claim, in ``ids`` order
    subjects: Mapping[str, frozenset[str]]  # claim -> subject entity IDs
    sources: Mapping[str, frozenset[str]]  # claim -> evidence event IDs
    texts: Mapping[str, str]  # claim -> normalized text
    details: Mapping[str, dict]  # claim -> what a labeling sheet shows
    subject_names: Mapping[str, str]
    counts: Mapping[str, int]
    embedding: Mapping[str, object]


def _slots(values: Sequence) -> str:
    return ", ".join("?" for _ in values)


def _event_details(benchmark: str, rows: Iterable[tuple]) -> dict[str, tuple[str | None, str | None]]:
    """Each event's dataset turn key and speaker."""
    details = {}
    for event_id, raw in rows:
        metadata = json.loads(raw) if isinstance(raw, str) else (raw or {})
        speaker = metadata.get("source_speaker") or metadata.get("source_role")
        details[str(event_id)] = (product_packing._source_key(benchmark, metadata), speaker)
    return details


def _reader_line(node: MemoryNode) -> str:
    """The line the reader format shows for the record at full fidelity, without a citation."""
    candidate = RetrievalCandidate(node=node, representation=RepresentationLevel.FULL, rendered_text=node.content)
    return _render_context_entry(candidate, context_format="reader")


def _stored_vectors(rows: Iterable[tuple], wanted: set[str]) -> tuple[dict[str, np.ndarray], dict, int]:
    """The latest stored vector of each wanted node, the one embedding they share, and how many had several."""
    latest: dict[str, tuple] = {}
    several = set()
    for row in rows:
        node_id = str(row[0])
        if node_id not in wanted:
            continue
        if node_id in latest:
            several.add(node_id)
            if row[1] < latest[node_id][1]:
                continue
        latest[node_id] = row
    identities = {(row[2], row[3], row[4]) for row in latest.values()}
    if len(identities) > 1:
        raise ValueError(f"The claims' vectors come from more than one embedding: {sorted(identities)}")
    vectors = {}
    for node_id, (_, _, _, _, dimension, blob) in latest.items():
        vector = np.frombuffer(blob, dtype="<f4").astype(np.float64)
        norm = float(np.linalg.norm(vector))
        if vector.shape != (dimension,) or not norm:
            raise ValueError(f"The stored vector of {node_id} is malformed")
        vectors[node_id] = vector / norm
    embedding = {}
    if identities:
        model, version, dimension = identities.pop()
        embedding = {"model": model, "version": version, "dimension": dimension}
    return vectors, embedding, len(several)


def read_pack(pack: Path, *, unit: str, user_id: str, benchmark: str, pack_sha256: str) -> PackClaims:
    """Read a pack's current claims, their subjects and their stored vectors, without opening an engine."""
    graph = object.__new__(DuckPGQGraphStore)  # only its row conversion is used
    types = [kind.value for kind in CLAIM_TYPES]
    states = [state.value for state in ACTIVE_LIFECYCLE_STATES]
    with duckdb.connect(str(pack / "memory.duckdb"), read_only=True) as db:
        nodes = [graph._row_to_node(row) for row in db.execute(
            f"SELECT * FROM nodes WHERE user_id = ? AND node_type IN ({_slots(types)}) "
            f"AND lifecycle_state IN ({_slots(states)}) AND valid_to IS NULL AND superseded_by IS NULL ORDER BY id",
            [user_id, *types, *states]).fetchall()]
        edges = db.execute("SELECT source_id, target_id FROM edges WHERE user_id = ? AND edge_type = ? "
                           "AND valid_to IS NULL", [user_id, EdgeType.HAS_FACT.value]).fetchall()
        names = {str(node_id): name for node_id, name in db.execute(
            "SELECT id, content FROM nodes WHERE user_id = ? AND node_type = ?",
            [user_id, NodeType.ENTITY.value]).fetchall()}
        events = _event_details(benchmark, db.execute(
            "SELECT id, metadata FROM events WHERE user_id = ?", [user_id]).fetchall())
        stored = db.execute(
            "SELECT m.node_id, m.vector_key, m.embedding_model, m.embedding_version, m.embedding_dim, "
            "p.vector_data FROM vector_metadata m JOIN vector_payloads p ON p.vector_key = m.vector_key "
            "WHERE m.user_id = ?", [user_id]).fetchall()
    subjects: dict[str, set[str]] = defaultdict(set)
    for source_id, target_id in edges:
        subjects[str(target_id)].add(str(source_id))
    claims = {str(node.id): node for node in nodes}
    vectors, embedding, several = _stored_vectors(stored, set(claims))
    kept = sorted(node_id for node_id in claims if subjects.get(node_id) and node_id in vectors)
    details = {}
    for node_id in kept:
        node = claims[node_id]
        metadata = node.metadata or {}
        turns = [events.get(str(ref), (None, None)) for ref in node.evidence_refs]
        details[node_id] = {
            "node_type": node.node_type.value, "line": _reader_line(node),
            "subjects": sorted(names.get(subject, subject) for subject in subjects[node_id]),
            "predicate": metadata.get("predicate"), "object": metadata.get("object"),
            "polarity": metadata.get("polarity"),
            "event_time": as_utc(node.event_time).isoformat() if node.event_time else None,
            "source_turns": [key for key, _ in turns], "speakers": sorted({speaker for _, speaker in turns if speaker}),
        }
    return PackClaims(
        unit=unit, pack_sha256=pack_sha256, ids=tuple(kept),
        vectors=np.array([vectors[node_id] for node_id in kept]) if kept else np.zeros((0, 0)),
        subjects={node_id: frozenset(subjects[node_id]) for node_id in kept},
        sources={node_id: frozenset(str(ref) for ref in claims[node_id].evidence_refs) for node_id in kept},
        texts={node_id: _normalize(claims[node_id].content) for node_id in kept},
        details=details, subject_names={subject: names.get(subject, subject) for node_id in kept
                                        for subject in subjects[node_id]},
        counts={"current_claims": len(claims),
                "without_subject": sum(not subjects.get(node_id) for node_id in claims),
                "without_vector": sum(node_id not in vectors for node_id in claims),
                "with_several_vectors": several, "paired_claims": len(kept)},
        embedding=embedding)


# Pairs, bands and the sample -------------------------------------------------

def band_of(similarity: float) -> str | None:
    """The sampling band a similarity falls in, or None below the lowest."""
    for band in BANDS:
        if similarity >= band.low and (band.high is None or similarity < band.high):
            return band.name
    return None


def threshold_key(threshold: float) -> str:
    return f"{threshold:.2f}"


def similar_pairs(claims: PackClaims, floor: float = FLOOR) -> tuple[list[dict], int]:
    """Every two claims that share a subject entity at ``floor`` or above, and the count of same-subject pairs.

    A pair that shares two subjects appears once, with both.
    """
    by_subject: dict[str, list[int]] = defaultdict(list)
    for index, node_id in enumerate(claims.ids):
        for subject in claims.subjects[node_id]:
            by_subject[subject].append(index)
    same_subject: set[tuple[int, int]] = set()
    found: dict[tuple[int, int], dict] = {}
    for subject, members in sorted(by_subject.items()):
        if len(members) < 2:
            continue
        block = claims.vectors[members] @ claims.vectors[members].T
        rows, columns = np.triu_indices(len(members), k=1)
        same_subject.update(zip((members[i] for i in rows), (members[j] for j in columns)))
        for i, j in zip(*np.nonzero(np.triu(block, k=1) >= floor - 10.0 ** -SIMILARITY_DIGITS)):
            similarity = round(float(block[i, j]), SIMILARITY_DIGITS)
            if similarity < floor:
                continue
            key = (members[i], members[j])
            pair = found.get(key)
            if pair is None:
                a, b = claims.ids[key[0]], claims.ids[key[1]]
                pair = found[key] = {
                    "unit": claims.unit, "a": a, "b": b, "similarity": similarity, "band": band_of(similarity),
                    "same_source": bool(claims.sources[a] & claims.sources[b]),
                    "identical_text": claims.texts[a] == claims.texts[b], "subjects": []}
            pair["subjects"].append(subject)
    return sorted(found.values(), key=_pair_order), len(same_subject)


def _pair_order(pair: Mapping) -> tuple[str, str, str]:
    return pair["unit"], pair["a"], pair["b"]


def pair_id(pair: Mapping) -> str:
    return f"{pair['unit']}:{pair['a']}:{pair['b']}"


def _words(text: str) -> int:
    """Words in a claim's text after a leading "(time) Speaker:" turn label."""
    label = _TURN_LABEL.match(text)
    return len(text[label.end() if label else 0:].split())


def unit_counts(claims: PackClaims, pairs: Sequence[Mapping], same_subject: int) -> dict:
    """What step 1 reports for one pack: its claims and its pairs at or above each threshold."""
    def at_or_above(selected: Callable[[Mapping], bool]) -> dict[str, int]:
        return {threshold_key(t): sum(pair["similarity"] >= t and selected(pair) for pair in pairs)
                for t in THRESHOLDS}

    def short(pair: Mapping) -> bool:
        return max(_words(claims.texts[pair["a"]]), _words(claims.texts[pair["b"]])) <= SHORT_WORDS

    return {
        "pack_sha256": claims.pack_sha256, **claims.counts, "subjects": len(claims.subject_names),
        "same_subject_pairs": same_subject,
        "pairs_at_or_above": at_or_above(lambda pair: True),
        "identical_text_at_or_above": at_or_above(lambda pair: pair["identical_text"]),
        "same_source_at_or_above": at_or_above(lambda pair: pair["same_source"]),
        # Pairs from different turns whose two lines are both short (SHORT_WORDS).
        "short_other_turns_at_or_above": at_or_above(lambda pair: not pair["same_source"] and short(pair)),
    }


def sample_pairs(pairs: Sequence[Mapping], sizes: Mapping[str, int] = SAMPLE_SIZES,
                 seed: int = SAMPLE_SEED) -> list[Mapping]:
    """A reproducible stratified sample: ``sizes[band]`` pairs from each band.

    A band with fewer pairs than its share gives all it has, and the
    shortfall goes to the other bands in order, so the sample keeps its total
    while any band has pairs to spare. The same pairs, sizes and seed always
    draw the same sample.
    """
    if set(sizes) != {band.name for band in BANDS} or any(size < 0 for size in sizes.values()):
        raise ValueError(f"Give a nonnegative sample size for each band: {', '.join(b.name for b in BANDS)}")
    population = {band.name: sorted((pair for pair in pairs if pair["band"] == band.name), key=_pair_order)
                  for band in BANDS}
    wanted = {name: min(sizes[name], len(members)) for name, members in population.items()}
    shortfall = sum(sizes.values()) - sum(wanted.values())
    for name, members in population.items():
        extra = min(shortfall, len(members) - wanted[name])
        wanted[name] += extra
        shortfall -= extra
    rng = random.Random(seed)
    return [pair for name, members in population.items()
            for pair in sorted(rng.sample(members, wanted[name]), key=_pair_order)]


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def labeling_sheet(pairs_doc: Mapping, packs: Mapping[str, PackClaims], *, sizes: Mapping[str, int] = SAMPLE_SIZES,
                   seed: int = SAMPLE_SEED) -> dict:
    """The sampled pairs with what a labeler needs to read, and empty labels."""
    rows = []
    for pair in sample_pairs(pairs_doc["pairs"], sizes, seed):
        claims = packs[pair["unit"]]
        rows.append({
            "id": pair_id(pair), "unit": pair["unit"], "band": pair["band"], "similarity": pair["similarity"],
            "same_source": pair["same_source"], "identical_text": pair["identical_text"],
            "subjects": sorted(claims.subject_names[subject] for subject in pair["subjects"]),
            "a": {"node_id": pair["a"], **claims.details[pair["a"]]},
            "b": {"node_id": pair["b"], **claims.details[pair["b"]]},
            "label": None, "difference": None, "reason": None,
        })
    return {"kind": LABELS_KIND, "schema_version": SCHEMA_VERSION, "benchmark": pairs_doc["benchmark"],
            "labeling_rule": LABELING_RULE, "labels": list(LABELS), "differences": list(DIFFERENCES),
            "seed": seed, "sizes": dict(sizes), "pairs_sha256": digest(pairs_doc["pairs"]), "pairs": rows}


CODE_KEYS = ("commit", "dirty", "worktree_sha256")


def code_state() -> dict:
    """The commit this step runs and whether the working tree differs from it, as the gate records them."""
    from benchmarks.retrieval_eval import provenance

    state = provenance(product_packing.gate_config(Path("{pack}")))
    return {key: state[key] for key in CODE_KEYS}


def collect_pairs(packs: Path, benchmark: str, *, scratch: Path) -> tuple[dict, dict[str, PackClaims]]:
    """Step 1 over every complete pack of a build; each pack is read from a verified scratch copy."""
    from benchmarks.diagnostics.longmemeval_s_compact import _clone_pack

    built = extracted_packs.built_units(packs / benchmark)
    if not built:
        raise ValueError(f"No complete {benchmark} pack under {packs / benchmark}")
    record = json.loads((packs / "build.json").read_text())
    read: dict[str, PackClaims] = {}
    units, pairs, embeddings = {}, [], set()
    for number, (unit, manifest) in enumerate(sorted(built.items()), start=1):
        copy = scratch / f"pack-{number:05d}"
        try:
            _clone_pack(packs / benchmark / unit / "pack", copy)
            product_packing._verify_copy(copy, manifest["pack_sha256"])
            claims = read_pack(copy, unit=unit, user_id=manifest["user_id"], benchmark=benchmark,
                               pack_sha256=manifest["pack_sha256"])
        finally:
            shutil.rmtree(copy, ignore_errors=True)
        found, same_subject = similar_pairs(claims)
        read[unit], units[unit] = claims, unit_counts(claims, found, same_subject)
        pairs.extend(found)
        if claims.embedding:
            embeddings.add(json.dumps(claims.embedding, sort_keys=True))
    if len(embeddings) != 1:
        raise ValueError(f"The packs' claim vectors do not share one embedding: {sorted(embeddings)}")
    return {
        "kind": PAIRS_KIND, "schema_version": SCHEMA_VERSION, "benchmark": benchmark, "code": code_state(),
        "build": {"path": str(packs), "build_sha256": extracted_packs.build_digest(packs),
                  "label": record.get("label"), "model": record.get("model", {}).get("model")},
        "embedding": {**json.loads(embeddings.pop()), "source": "each pack's stored claim vectors"},
        "floor": FLOOR, "bands": [band.name for band in BANDS], "units": units,
        # Every paired claim, so a context's packed claims can be counted whether or not they have a partner.
        "claims": {unit: list(claims.ids) for unit, claims in read.items()}, "pairs": pairs,
    }, read


# Contexts ------------------------------------------------------------------

def packed_records(receipt: Mapping) -> list[list]:
    """The in-context records of a gate receipt and the token cost the packer charged for each."""
    return [[candidate["node_id"], candidate["token_cost"]]
            for candidate in receipt["candidates"] if candidate["in_context"]]


async def replay_contexts(packs: Path, benchmark: str, *, progress: Callable[[int], None] | None = None,
                          ) -> tuple[dict, dict]:
    """Run the evidence gate over a build and keep each context's packed records (step 3).

    The gate's capture of each question holds its receipt, which the gate
    checked against the packed bundle; the captures stay in a temporary
    directory because they hold benchmark text.
    """
    packing = product_packing.gate_config(Path("{pack}")).packing
    units = {case.question_id: case.pack.parent.name for case in product_packing._built_cases(packs, benchmark)}
    questions = []
    with tempfile.TemporaryDirectory(prefix="prme-paraphrased-claims-") as scratch:
        captures = Path(scratch) / "captures"
        report = await product_packing.run_gate([benchmark], packs=packs, capture_dir=captures, progress=progress)
        for row in report["rows"]:
            raw = (captures / benchmark / f"{row['question_id']}.json").read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["capture_sha256"]:
                raise ValueError(f"The capture of {row['question_id']} differs from the gate's record of it")
            records = packed_records(json.loads(raw)["receipt"])
            if len(records) != row["records"]:
                raise ValueError(f"{row['question_id']}: the receipt holds {len(records)} packed records, "
                                 f"the context {row['records']}")
            questions.append({"question_id": row["question_id"], "unit": units[row["question_id"]],
                              "category": row["category"], "context_tokens": row["context_tokens"],
                              "context_sha256": row["context_sha256"], "records": records})
    contexts = {
        "kind": CONTEXTS_KIND, "schema_version": SCHEMA_VERSION, "benchmark": benchmark,
        "tokenizer": packing.tokenizer, "token_budget": packing.token_budget,
        "overhead_tokens": packing.overhead_tokens,
        "gate": {"provenance": {key: value for key, value in report["provenance"].items() if key != "engine_config"},
                 "summary": report["benchmarks"][benchmark]["summary"]},
        "questions": questions,
    }
    return contexts, report


# Labels, token shares and the decision ---------------------------------------

def _every(pair: Mapping) -> bool:
    return True


def _texts_differ(pair: Mapping) -> bool:
    return not pair["identical_text"]


# Every count is made for the issue's pairs, and for paraphrases alone: the
# pairs whose texts differ after normalization.
PAIR_SETS: dict[str, Callable[[Mapping], bool]] = {"all": _every, "paraphrases": _texts_differ}


def _share(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def check_labels(sheet: Mapping, pairs_doc: Mapping) -> list[Mapping]:
    """The labeled sample, refused unless it is the seed's sample of these pairs and every row is labeled."""
    if sheet.get("kind") != LABELS_KIND or sheet.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Not a paraphrased claim labeling sheet")
    if sheet["pairs_sha256"] != digest(pairs_doc["pairs"]):
        raise ValueError("The sheet was drawn from other pairs")
    expected = [pair_id(pair) for pair in sample_pairs(pairs_doc["pairs"], sheet["sizes"], sheet["seed"])]
    if [row["id"] for row in sheet["pairs"]] != expected:
        raise ValueError("The sheet's pairs are not the sample its seed draws")
    by_id = {pair_id(pair): pair for pair in pairs_doc["pairs"]}
    for row in sheet["pairs"]:
        pair = by_id[row["id"]]
        if (row["band"], row["similarity"]) != (pair["band"], pair["similarity"]):
            raise ValueError(f"{row['id']}: the band or similarity differs from the pairs")
        if row.get("label") not in LABELS:
            raise ValueError(f"{row['id']}: the label must be one of {', '.join(LABELS)}")
        difference = row.get("difference")
        if difference is not None and (row["label"] != "different_facts" or difference not in DIFFERENCES):
            raise ValueError(f"{row['id']}: only a different-facts pair can differ only in "
                             f"{', '.join(DIFFERENCES)}")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip() or "\n" in row["reason"]:
            raise ValueError(f"{row['id']}: give a one-line reason")
    return list(sheet["pairs"])


def _band_labels(rows: Sequence[Mapping], pairs: int) -> dict:
    same = sum(row["label"] == "same_fact" for row in rows)
    set_aside = sum(row.get("difference") is not None for row in rows)
    kept = len(rows) - set_aside
    return {
        "pairs": pairs, "labeled": len(rows), "same_fact": same, "different_facts": len(rows) - same,
        "differ_only_in": {kind: sum(row.get("difference") == kind for row in rows) for kind in DIFFERENCES},
        "same_fact_rate": _share(same, len(rows)),
        "same_fact_share_set_aside": _share(same, kept),
        "meets_rule": bool(kept) and same / kept >= SAME_FACT_RULE,
    }


def _weighted_at_or_above(bands: Mapping[str, Mapping]) -> dict:
    """The same-fact share, number, date and negation pairs set aside, of all pairs at or above each threshold.

    Each band's labels stand for that band's pairs, so the bands are weighted
    by their pair counts. None when a band with pairs has no labels.
    """
    readings = {}
    for threshold in THRESHOLDS:
        chosen = [bands[band.name] for band in BANDS if band.low >= threshold]
        if any(band["pairs"] and not band["labeled"] for band in chosen):
            readings[threshold_key(threshold)] = None
            continue
        labeled = [band for band in chosen if band["labeled"]]
        same = sum(band["pairs"] * band["same_fact"] / band["labeled"] for band in labeled)
        kept = sum(band["pairs"] * (band["labeled"] - sum(band["differ_only_in"].values())) / band["labeled"]
                   for band in labeled)
        readings[threshold_key(threshold)] = _share(same, kept)
    return readings


def label_summary(labels: Sequence[Mapping], pairs: Sequence[Mapping]) -> dict:
    """Each band's labels and whether it meets the 95% rule, for every pair and for paraphrases alone.

    Paraphrases are the pairs whose texts differ after normalization.
    """
    summary = {}
    for name, keep in PAIR_SETS.items():
        population = Counter(pair["band"] for pair in pairs if keep(pair))
        bands = {band.name: _band_labels([row for row in labels if row["band"] == band.name and keep(row)],
                                         population[band.name]) for band in BANDS}
        summary[name] = {"bands": bands, "weighted_at_or_above": _weighted_at_or_above(bands)}
    return summary


def partner_index(pairs: Iterable[Mapping]) -> dict[tuple[str, str], list[tuple[str, float, bool]]]:
    """Each claim's pair partners, their similarities and whether their texts are identical, by (unit, claim)."""
    partners: dict[tuple[str, str], list[tuple[str, float, bool]]] = defaultdict(list)
    for pair in pairs:
        partners[pair["unit"], pair["a"]].append((pair["b"], pair["similarity"], pair["identical_text"]))
        partners[pair["unit"], pair["b"]].append((pair["a"], pair["similarity"], pair["identical_text"]))
    return partners


def _duplicates(question: Mapping, partners: Mapping, threshold: float, rates: Mapping[str, float | None],
                paraphrases: bool) -> dict:
    packed = {node_id for node_id, _ in question["records"]}
    records = tokens = 0
    adjusted: float | None = 0.0
    for node_id, cost in question["records"]:
        similarities = [similarity for other, similarity, identical in partners.get((question["unit"], node_id), ())
                        if similarity >= threshold and other in packed and not (paraphrases and identical)]
        if not similarities:
            continue
        records += 1
        tokens += cost
        chances = [rates[band_of(similarity)] for similarity in similarities]
        if adjusted is not None and None not in chances:
            adjusted += cost * (1 - math.prod(1 - chance for chance in chances))
        else:
            adjusted = None
    return {"records": records, "tokens": tokens, "adjusted_tokens": None if adjusted is None else round(adjusted, 6)}


def question_counts(question: Mapping, partners: Mapping, rates: Mapping[str, Mapping[str, float | None]]) -> dict:
    """One context's packed claims whose pair partner is packed too, at each threshold, and their tokens.

    ``rates`` holds each pair set's labeled same-fact rate by band, for the
    adjusted count, which is None when a band it needs has no labels.
    """
    return {threshold_key(threshold): {name: _duplicates(question, partners, threshold, rates[name],
                                                         paraphrases=name == "paraphrases")
                                       for name in PAIR_SETS}
            for threshold in THRESHOLDS}


def _mean(values: Iterable[float | None]) -> float | None:
    values = list(values)
    return None if None in values else statistics.fmean(values)


def share_summary(rows: Sequence[Mapping], key: str, name: str) -> dict:
    """The token share of packed pairs over every question, at one threshold, for one pair set."""
    counts = [row["thresholds"][key][name] for row in rows]
    context_tokens = sum(row["context_tokens"] for row in rows)
    shares = [count["tokens"] / row["context_tokens"] for count, row in zip(counts, rows)]
    adjusted = [None if count["adjusted_tokens"] is None else count["adjusted_tokens"] / row["context_tokens"]
                for count, row in zip(counts, rows)]
    adjusted_tokens = [count["adjusted_tokens"] for count in counts]
    return {
        "questions": len(rows),
        "questions_with_a_packed_pair": sum(count["records"] > 0 for count in counts),
        "records_mean": statistics.fmean(count["records"] for count in counts),
        "tokens_mean": statistics.fmean(count["tokens"] for count in counts),
        "share_mean": statistics.fmean(shares),
        "share_median": statistics.median(shares),
        "share_max": max(shares),
        "adjusted_share_mean": _mean(adjusted),
        "pooled_share": _share(sum(count["tokens"] for count in counts), context_tokens),
        "adjusted_pooled_share": None if None in adjusted_tokens else _share(sum(adjusted_tokens), context_tokens),
        # The other denominator: the packed records' own line costs, without the context's framing.
        "record_tokens_share_mean": statistics.fmean(
            _share(count["tokens"], row["record_tokens"]) or 0.0 for count, row in zip(counts, rows)),
        "adjusted_record_tokens_share_mean": _mean(
            None if count["adjusted_tokens"] is None else _share(count["adjusted_tokens"], row["record_tokens"]) or 0.0
            for count, row in zip(counts, rows)),
    }


def _at_least(value: float | None, rule: float) -> bool:
    return value is not None and value >= rule


def decide(shares: Mapping[str, Mapping], labels: Mapping) -> dict:
    """Apply #255's rule as ``READING`` reads it, and again under each reading it does not use.

    Every reading gives condition 1 a token share and condition 2 the bands
    that meet the 95% rule.
    """
    floor = shares[threshold_key(FLOOR)]

    def meeting(name: str) -> list[str]:
        return [band for band, values in labels[name]["bands"].items() if values["meets_rule"]]

    def reading(share: float | None, bands: list[str]) -> dict:
        first, second = _at_least(share, TOKEN_SHARE_RULE), bool(bands)
        return {"share": share, "condition_1": first, "bands_meeting_rule": bands, "condition_2": second,
                "follow_up": first and second}

    adjusted = floor["all"]["adjusted_share_mean"]
    primary = reading(adjusted, meeting("all"))
    other = {
        "condition 1 on the upper bound (every pair at or above 0.90 counted as the same fact)":
            reading(floor["all"]["share_mean"], meeting("all")),
        "condition 1 on pooled tokens (all duplicate tokens over all context tokens)":
            reading(floor["all"]["adjusted_pooled_share"], meeting("all")),
        "condition 1 over the packed records' own tokens":
            reading(floor["all"]["adjusted_record_tokens_share_mean"], meeting("all")),
        "condition 2 on the 'at or above' bands, each band weighted by its pair count":
            reading(adjusted, [f"at or above {key}" for key, value in labels["all"]["weighted_at_or_above"].items()
                               if _at_least(value, SAME_FACT_RULE)]),
        "both conditions with identical-text pairs set aside":
            reading(floor["paraphrases"]["adjusted_share_mean"], meeting("paraphrases")),
    }
    return {
        "condition_1": {"holds": primary["condition_1"], "adjusted_share_mean": adjusted, "rule": TOKEN_SHARE_RULE},
        "condition_2": {"holds": primary["condition_2"], "bands_meeting_rule": primary["bands_meeting_rule"],
                        "rule": SAME_FACT_RULE},
        "follow_up": primary["follow_up"],
        "outcome": "open one follow-up to fold such pairs at packing time only" if primary["follow_up"]
        else "close the issue with the numbers",
        "other_readings": other,
        "readings_that_change_the_decision": [name for name, values in other.items()
                                              if values["follow_up"] != primary["follow_up"]],
    }


def build_report(pairs_doc: Mapping, sheet: Mapping, contexts: Mapping, *,
                 inputs: Mapping[str, str] | None = None) -> dict:
    """Steps 1 to 3 and the decision, from the three inputs; ``inputs`` names their files' digests."""
    if pairs_doc.get("kind") != PAIRS_KIND or contexts.get("kind") != CONTEXTS_KIND:
        raise ValueError("Pass a pairs file from the pairs step and a contexts file from the replay step")
    if pairs_doc["benchmark"] != contexts["benchmark"]:
        raise ValueError("The pairs and the contexts come from different benchmarks")
    replayed = contexts["gate"]["provenance"]["packs"]["packs"]
    measured = {unit: counts["pack_sha256"] for unit, counts in pairs_doc["units"].items()}
    if replayed != measured:
        raise ValueError("The contexts were replayed over other packs than the pairs were read from")
    labels = check_labels(sheet, pairs_doc)
    summary = label_summary(labels, pairs_doc["pairs"])
    rates = {name: {band: values["same_fact_rate"] for band, values in summary[name]["bands"].items()}
             for name in PAIR_SETS}
    partners = partner_index(pairs_doc["pairs"])
    claims = {unit: set(ids) for unit, ids in pairs_doc["claims"].items()}
    rows = []
    for question in contexts["questions"]:
        rows.append({
            "question_id": question["question_id"], "unit": question["unit"], "category": question["category"],
            "context_sha256": question["context_sha256"], "context_tokens": question["context_tokens"],
            "record_tokens": sum(cost for _, cost in question["records"]),
            "records": len(question["records"]),
            "claims": sum(node_id in claims[question["unit"]] for node_id, _ in question["records"]),
            "thresholds": question_counts(question, partners, rates),
        })
    shares = {threshold_key(t): {name: share_summary(rows, threshold_key(t), name) for name in PAIR_SETS}
              for t in THRESHOLDS}
    return {
        "kind": REPORT_KIND, "schema_version": SCHEMA_VERSION, "benchmark": pairs_doc["benchmark"], "issue": 255,
        "decision_rule": DECISION_RULE, "reading": READING, "labeling_rule": LABELING_RULE,
        "inputs": dict(inputs or {}),
        "code": {"pairs": pairs_doc["code"],
                 "replay": {key: contexts["gate"]["provenance"][key] for key in CODE_KEYS}},
        "build": pairs_doc["build"], "embedding": pairs_doc["embedding"],
        "contexts": {"tokenizer": contexts["tokenizer"], "token_budget": contexts["token_budget"],
                     "overhead_tokens": contexts["overhead_tokens"], "gate": contexts["gate"],
                     "questions": len(rows),
                     "records_mean": statistics.fmean(row["records"] for row in rows),
                     "claims_mean": statistics.fmean(row["claims"] for row in rows),
                     "context_tokens_mean": statistics.fmean(row["context_tokens"] for row in rows),
                     "record_tokens_mean": statistics.fmean(row["record_tokens"] for row in rows)},
        "pairs": {"units": pairs_doc["units"],
                  "bands": {name: dict(Counter(pair["band"] for pair in pairs_doc["pairs"] if keep(pair)))
                            for name, keep in PAIR_SETS.items()},
                  "at_or_above": {threshold_key(t): sum(pair["similarity"] >= t for pair in pairs_doc["pairs"])
                                  for t in THRESHOLDS}},
        "labels": summary, "shares": shares, "decision": decide(shares, summary), "questions": rows,
    }


# Markdown --------------------------------------------------------------------

def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _label_table(bands: Mapping[str, Mapping]) -> list[str]:
    lines = ["| Band | Pairs | Labeled | Same fact | Different | Only a number | Only a date | Only a negation "
             "| Same-fact rate | Same fact, those set aside | Meets 95% |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for name, band in bands.items():
        only = band["differ_only_in"]
        lines.append(f"| {name} | {band['pairs']} | {band['labeled']} | {band['same_fact']} | "
                     f"{band['different_facts']} | {only['number']} | {only['date']} | {only['negation']} | "
                     f"{_pct(band['same_fact_rate'])} | {_pct(band['same_fact_share_set_aside'])} | "
                     f"{'yes' if band['meets_rule'] else 'no'} |")
    return lines


def report_markdown(report: Mapping) -> str:
    decision, labels, shares, contexts = report["decision"], report["labels"], report["shares"], report["contexts"]
    keys = [threshold_key(t) for t in THRESHOLDS]
    code = ", ".join(f"{step} `{(state['commit'] or '')[:8]}`{' with uncommitted changes' if state['dirty'] else ''}"
                     for step, state in report["code"].items())
    second = decision["condition_2"]
    lines = [
        f"# Paraphrased duplicate claims in `ingest()` packs ({report['benchmark']}, #255)", "",
        f"Build `{report['build']['label']}` ({report['build']['model']}). Code by step: {code}. Similarity: cosine of "
        f"{report['embedding']['source']} ({report['embedding']['model']}, {report['embedding']['dimension']} "
        f"dimensions). Tokens: the packer's own counts with `{contexts['tokenizer']}`, budget "
        f"{contexts['token_budget']} less {contexts['overhead_tokens']} reserved. No model calls.", "",
        f"**Decision: {decision['outcome']}.** Condition 1, at least 5% of packed tokens on average: "
        f"{'holds' if decision['condition_1']['holds'] else 'fails'} at "
        f"{_pct(decision['condition_1']['adjusted_share_mean'])}. Condition 2, a band with at least 95% same-fact "
        f"labels once number, date and negation pairs are set aside: "
        + (f"holds in {', '.join(second['bands_meeting_rule'])}." if second["holds"] else "fails.")
        + " " + ("Readings that would change the decision: " + "; ".join(decision["readings_that_change_the_decision"])
                 + "." if decision["readings_that_change_the_decision"]
                 else "None of the other readings below changes the decision."),
        "",
        f"Contexts: {contexts['questions']} questions, {contexts['records_mean']:.1f} packed records per context, "
        f"{contexts['claims_mean']:.1f} of them current claims with a subject entity, "
        f"{contexts['context_tokens_mean']:.0f} context tokens "
        f"and {contexts['record_tokens_mean']:.0f} record tokens on average.", "",
        "## Pairs of current claims with one subject", "",
        f"The last three columns break down the pairs at or above {keys[0]}. Short lines have {SHORT_WORDS} words "
        "or fewer after the turn's time and speaker.", "",
        "| Pack | Current claims | Paired claims | Same-subject pairs | At or above " + " | ".join(keys)
        + " | From one turn | Identical text | From other turns, both lines short |",
        "|---|---:|---:|---:|" + "---:|" * len(keys) + "---:|---:|---:|",
    ]
    columns = [("current_claims", None), ("paired_claims", None), ("same_subject_pairs", None),
               *(("pairs_at_or_above", key) for key in keys), ("same_source_at_or_above", keys[0]),
               ("identical_text_at_or_above", keys[0]), ("short_other_turns_at_or_above", keys[0])]
    units = report["pairs"]["units"]

    def cell(counts: Mapping, name: str, key: str | None) -> int:
        return counts[name] if key is None else counts[name][key]

    for unit, counts in units.items():
        lines.append(f"| {unit} | " + " | ".join(str(cell(counts, *column)) for column in columns) + " |")
    lines.append("| All | " + " | ".join(str(sum(cell(counts, *column) for counts in units.values()))
                                         for column in columns) + " |")
    lines += ["", "## Labeled sample", "", "Every pair:", "", *_label_table(labels["all"]["bands"]), "",
              "Pairs whose texts differ (identical text set aside):", "",
              *_label_table(labels["paraphrases"]["bands"]), "",
              "## Packed tokens", "",
              "A packed claim counts when a claim paired with it at or above the threshold is packed too. Shares "
              "are of each context's packed tokens, averaged over questions.", "",
              "| Pairs at or above | Questions with a packed pair | Records per question | Tokens per question "
              "| Share, upper bound | Share, adjusted | Pooled, upper / adjusted | Of record tokens, adjusted "
              "| Texts differ: upper / adjusted |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key in keys:
        row, para = shares[key]["all"], shares[key]["paraphrases"]
        lines.append(f"| {key} | {row['questions_with_a_packed_pair']} of {row['questions']} | "
                     f"{row['records_mean']:.2f} | {row['tokens_mean']:.1f} | {_pct(row['share_mean'])} | "
                     f"{_pct(row['adjusted_share_mean'])} | {_pct(row['pooled_share'])} / "
                     f"{_pct(row['adjusted_pooled_share'])} | {_pct(row['adjusted_record_tokens_share_mean'])} | "
                     f"{_pct(para['share_mean'])} / {_pct(para['adjusted_share_mean'])} |")
    lines += ["", "## Other readings of the rule", "",
              "| Reading | Token share | Condition 1 | Bands at 95% or more | Condition 2 | Follow-up |",
              "|---|---:|---|---|---|---|"]
    for name, reading in decision["other_readings"].items():
        lines.append(f"| {name} | {_pct(reading['share'])} | {'holds' if reading['condition_1'] else 'fails'} | "
                     f"{', '.join(reading['bands_meeting_rule']) or 'none'} | "
                     f"{'holds' if reading['condition_2'] else 'fails'} | {'yes' if reading['follow_up'] else 'no'} |")
    lines += ["", "## How the rule is read", "", report["reading"], "", "## Labeling rule", "",
              report["labeling_rule"], ""]
    return "\n".join(lines)


# Command line ----------------------------------------------------------------

def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def pairs_main(args: argparse.Namespace) -> None:
    if args.sheet is not None and args.sheet.exists():
        raise SystemExit(f"{args.sheet} exists; a new sheet would discard its labels")
    with tempfile.TemporaryDirectory(prefix="prme-paraphrased-claims-") as scratch:
        pairs_doc, packs = collect_pairs(args.packs, args.benchmark, scratch=Path(scratch))
    _write_json(args.output, pairs_doc)
    if args.sheet is not None:
        _write_json(args.sheet, labeling_sheet(pairs_doc, packs))
    print(json.dumps({unit: counts["pairs_at_or_above"] for unit, counts in pairs_doc["units"].items()}, indent=2))


def replay_main(args: argparse.Namespace) -> None:
    product_packing._quiet_offline_cli()
    last_done = 0

    def progress(done: int) -> None:
        nonlocal last_done
        if done // 100 > last_done // 100:
            print(f"{done} questions replayed", file=sys.stderr, flush=True)
        last_done = done

    contexts, report = asyncio.run(replay_contexts(args.packs, args.benchmark, progress=progress))
    _write_json(args.output, contexts)
    if args.gate_output is not None:
        product_packing._write_report(args.gate_output, report, product_packing.gate_markdown(report))


def report_main(args: argparse.Namespace) -> None:
    raw = {name: path.read_bytes() for name, path in
           (("pairs", args.pairs), ("labels", args.labels), ("contexts", args.contexts))}
    report = build_report(*(json.loads(data) for data in raw.values()),
                          inputs={f"{name}_sha256": hashlib.sha256(data).hexdigest() for name, data in raw.items()})
    report["code"]["report"] = code_state()
    _write_json(args.output, report)
    markdown = report_markdown(report)
    args.output.with_suffix(".md").write_text(markdown)
    print(markdown)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.paraphrased_claims",
                                     description="Measure how much packed context paraphrased duplicate claims take "
                                                 "in packs built through ingest() (#255). No model calls.")
    commands = parser.add_subparsers(dest="command", required=True)
    pairs = commands.add_parser("pairs", help="Pair current claims of one subject by stored-vector similarity")
    pairs.add_argument("--packs", type=Path, required=True,
                       help="A build folder from benchmarks.diagnostics.extracted_packs")
    pairs.add_argument("--benchmark", choices=extracted_packs.BENCHMARKS, default="locomo")
    pairs.add_argument("--output", type=Path, required=True, help="JSON file of the pairs at 0.90 or above")
    pairs.add_argument("--sheet", type=Path,
                       help="New JSON labeling sheet with the stratified sample (contains benchmark text)")
    replay = commands.add_parser("replay", help="Replay the evidence gate's contexts and keep their packed records")
    replay.add_argument("--packs", type=Path, required=True)
    replay.add_argument("--benchmark", choices=extracted_packs.BENCHMARKS, default="locomo")
    replay.add_argument("--output", type=Path, required=True, help="JSON file of each context's packed records")
    replay.add_argument("--gate-output", type=product_packing._json_output,
                        help="Also write the gate's own report here, with a Markdown summary beside it")
    report = commands.add_parser("report", help="Apply the #255 decision rule to the pairs, labels and contexts")
    report.add_argument("--pairs", type=Path, required=True)
    report.add_argument("--labels", type=Path, required=True)
    report.add_argument("--contexts", type=Path, required=True)
    report.add_argument("--output", type=product_packing._json_output, required=True,
                        help="JSON report; a Markdown summary is written beside it")
    args = parser.parse_args(argv)
    {"pairs": pairs_main, "replay": replay_main, "report": report_main}[args.command](args)


if __name__ == "__main__":
    main()
