"""Padded stores for the scale test (``SCALE.md``): the combined core, ordinary turns, look-alike copies and an embedding cache.

Nothing here calls a model. The look-alikes are made by deterministic substitution
(seed 20261021): every person, place, organization, project, pet and book name of
the core gold graphs is replaced by another name from fixed pools, so the sentence
shapes match the questions' topics and the names do not.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pickle
import random
import re
import time

from benchmarks.graph_oracle.gold import GOLD_NAMES, Claim, Entity, Gold, Memory, _alias_patterns, load_gold

SEED = 20261021
FIXTURE_ORDER = (*GOLD_NAMES, "draw2-v1")  # store-v1, chat-v1 and defects-v1 as GOLD_NAMES orders them, then the new set
DATA = "data/benchmarks"
KEEP_SUBTYPES = frozenset({"tool", "language", "database", "platform", "editor", "service", "partitioning", "index", "drink",
                           "food", "style", "practice", "genre", "format", "transport", "hobby", "habit", "meeting"})

FIRST = """Maya Liam Zoe Omar Hana Felix Nia Theo Ines Kofi Lena Ravi Mira Jonas Ada Emil Yara Hugo Sana Dario Iris Kenji Anya Bruno
Chloe Dev Elsa Farid Greta Hiro Isla Jamal Kira Leo Mei Nico Olga Pablo Quinn Rosa Seth Tara Uma Victor Wren Xavi Yusuf Zara Abel
Bea Cyrus Dina Eli Fay Gael Hope Ivo June Kai Lior Mona Nolan Odette Pia Remy Sofia Tariq Una Vera Wade Ximena Yuri Zeke Amara
Boris Cleo Dmitri Esme Finn Gia Hector Imani Joel Kalani Luca Mateo Nadia Orla Petra Rafi Saskia Tomas Ulla Vince Willa Yael Zane
Anika Bram Cora Dante Edda Fabio Gwen Hassan Ilse Jorge Katya Lars Mina Nils Opal Pavel Ruth Samir Talia Ugo Viola Walt Xenia
Yoel Zelda Arlo Brinn Casia Doran Ember Fenn Galen Hollis Ione Jasper Kestrel Lucan Marlo Neve Orrin Perrin Quill Rhea Sorrel Teague""".split()
LAST = """Lindgren Okonkwo Moreau Tanaka Brandt Castillo Dubois Eriksen Fontaine Gallo Haas Ibrahim Jovanovic Kowalski Lemaire Marchetti
Novak Ortega Petrov Quintero Rasmussen Sandoval Tremblay Underhill Vasquez Whitfield Yamada Zielinski Abboud Bergstrom Costa
Delacroix Engel Farouk Grayson Halvorsen Iyer Janssen Kaplan Lombardi Mbeki Nakamura Oyelaran Pellegrini Rinaldi Soto Thorne
Ueda Vogel Wanjiru Yilmaz Zhao Achebe Baptiste Carvalho Dimitrov Esposito Fischer Gutierrez Hartmann Ivanova Jensen Kruger""".split()
CITY = """Oslo Bergen Gdansk Krakow Seville Valencia Bilbao Turin Verona Naples Genoa Lyon Nantes Bordeaux Ghent Bruges Leiden Utrecht
Graz Salzburg Zagreb Split Tallinn Riga Vilnius Tbilisi Batumi Izmir Antalya Cairo Luxor Fez Marrakech Tunis Accra Kumasi Nairobi
Mombasa Dakar Hanoi Hue Danang Chiangmai Phuket Cebu Davao Busan Daegu Sapporo Nagoya Hakodate Taipei Tainan Cusco Lima Quito
Cuenca Bogota Medellin Cartagena Valparaiso Mendoza Salta Asheville Savannah Charleston Tucson Boulder Duluth Madison Eugene
Spokane Halifax Victoria Quebec Regina Calgary Aarhus Odense Malmo Uppsala Tampere Turku Lodz Wroclaw Poznan Brno Ostrava Kosice
Sibiu Cluj Plovdiv Varna Thessaloniki Patras Cagliari Palermo Catania Siena Pisa Trieste Bologna Parma Lucca Perugia Ancona""".split()
COUNTRY = """Norway Poland Spain Italy France Belgium Netherlands Austria Croatia Estonia Latvia Lithuania Georgia Turkey Egypt Morocco
Tunisia Ghana Kenya Senegal Vietnam Thailand Philippines Korea Taiwan Peru Ecuador Colombia Chile Argentina Sweden Finland
Denmark Czechia Romania Bulgaria Greece Slovakia Hungary Iceland""".split()
WORD = """Aurora Basalt Cobalt Delta Ember Fjord Garnet Harbor Indigo Kestrel Lumina Nimbus Orchid Pioneer Quartz Redwood Sable Tundra
Umber Vertex Willow Zenith Alder Birch Cedar Dune Elm Fern Glade Heron Ivy Jade Kelp Lark Moss Nettle Oak Pine Quail Reed Sage Thistle
Vale Wisp Yarrow Zephyr Atlas Beacon Compass Drift Eddy Flint Grove Haven Isle Journey Knoll Lantern Mesa Nova Orbit Prism Quest Ridge Spire""".split()
SUFFIXES = ("Health", "Logistics", "Studio", "Freight", "Labs", "Systems", "Works", "Group")


def _asset(path: str) -> Path:
    from benchmarks import checkout

    return checkout.main_checkout() / path


# --- The combined core ---------------------------------------------------------------------------


def combine(golds: dict[str, Gold], owner: str = "scale") -> Gold:
    """The fixtures as one gold graph for one owner: every id prefixed with its fixture, names shared as written."""
    entities: dict[str, Entity] = {}
    claims: list[Claim] = []
    memories: list[Memory] = []
    owners: list[str] = []
    for name, gold in golds.items():
        prefix = f"{name}:"
        owners.append(prefix + gold.owner_entity)
        for entity in gold.entities.values():
            entities[prefix + entity.id] = Entity(prefix + entity.id, entity.type, entity.subtype, entity.names, entity.exact_case)
        for claim in gold.claims:
            claims.append(Claim(prefix + claim.id, prefix + claim.s, claim.r, prefix + claim.o if claim.o else None, claim.attr,
                                claim.value, tuple(prefix + memory_id for memory_id in claim.ev), claim.polarity, claim.status,
                                claim.role, claim.time, claim.inferred, prefix + claim.supersedes if claim.supersedes else None,
                                claim.via))
        for memory in gold.memories:
            memories.append(Memory(prefix + memory.id, memory.text, memory.role, memory.speaker, memory.node_type, memory.scope,
                                   prefix + memory.session, memory.event_time))
    combined = Gold("core", Path("."), owner, owners[0], entities, claims, [], memories, "The fixtures combined for one owner.",
                    owner_entities=tuple(owners))
    combined.patterns = _alias_patterns(entities)
    return combined


def load_core() -> tuple[dict[str, Gold], Gold]:
    golds = {name: load_gold(name) for name in FIXTURE_ORDER}
    return golds, combine(golds)


def shared_names(golds: dict[str, Gold]) -> dict[str, list[str]]:
    """The names that more than one fixture uses for an entity: the natural distractors of the combined core."""
    seen: dict[str, set[str]] = {}
    for fixture, gold in golds.items():
        for entity in gold.entities.values():
            for name in entity.names:
                seen.setdefault(name.casefold(), set()).add(fixture)
    return {name: sorted(fixtures) for name, fixtures in sorted(seen.items()) if len(fixtures) > 1}


# --- Ordinary turns -------------------------------------------------------------------------------


def conversation_pad(count: int) -> list[str]:
    """Ordinary turns in a fixed shuffled order: the LoCoMo turns first, then the LongMemEval oracle file's user turns."""
    rng = random.Random(SEED)
    locomo = []
    for conversation in json.loads(_asset(f"{DATA}/locomo/locomo10.json").read_text()):
        for key, value in conversation["conversation"].items():
            if key.startswith("session_") and isinstance(value, list):
                locomo += [turn["text"].strip() for turn in value if turn.get("text", "").strip()]
    rng.shuffle(locomo)
    texts = locomo[:count]
    if len(texts) < count:
        extra = [turn["content"].strip() for question in json.loads(_asset(f"{DATA}/longmemeval/longmemeval_oracle.json").read_text())
                 for session in question["haystack_sessions"] for turn in session if turn["role"] == "user" and turn["content"].strip()]
        rng.shuffle(extra)
        texts += extra[:count - len(texts)]
    if len(texts) < count:
        raise ValueError(f"the benchmark data holds {len(texts)} ordinary turns, not {count}")
    return texts


# --- Look-alikes ----------------------------------------------------------------------------------


def _substituted(entity: Entity) -> bool:
    if entity.type in ("person", "place", "org"):
        return True
    if entity.subtype in KEEP_SUBTYPES or not entity.names[0][:1].isupper():
        return False
    return entity.type in ("event", "thing")


class Substituter:
    """One clone's renaming: a map from every substituted core name to its replacement, applied to text in one pass."""

    def __init__(self, core: Gold, k: int, forbidden: frozenset[str]):
        rng = random.Random(SEED * 1000 + k)
        pools = {key: [name for name in values if name.casefold() not in forbidden] for key, values in
                 (("first", FIRST), ("last", LAST), ("city", CITY), ("country", COUNTRY), ("word", WORD))}
        for pool in pools.values():
            rng.shuffle(pool)
        used: dict[str, int] = {key: 0 for key in pools}

        def take(key: str) -> str:
            pool = pools[key]
            used[key] += 1
            return pool[(used[key] - 1) % len(pool)]

        self.entity_names: dict[str, tuple[str, ...]] = {}
        self.mapping: dict[str, str] = {}
        for entity in core.entities.values():
            if not _substituted(entity):
                self.entity_names[entity.id] = entity.names
                continue
            canonical = max(entity.names, key=len)
            tokens = canonical.split()
            if entity.type == "person":
                new = take("first") + (" " + take("last") if len(tokens) > 1 else "")
            elif entity.type == "place":
                new = take("country") if entity.subtype == "country" else take("city")
            elif entity.type == "org":
                new = f"{take('word')} {tokens[-1] if len(tokens) > 1 and tokens[-1] in SUFFIXES else take('word')}" if len(tokens) > 1 \
                    else take("word")
            else:
                new = " ".join(take("word") if token[:1].isupper() and index < len(tokens) - 1 or len(tokens) == 1 else token
                               for index, token in enumerate(tokens))
            renamed = []
            for name in entity.names:
                if name == canonical:
                    replacement = new
                elif canonical.lower().startswith(name.lower()) and canonical.split()[0].lower() == name.split()[0].lower():
                    replacement = " ".join(new.split()[:len(name.split())])
                elif name.lower() == canonical.lower():
                    replacement = new.lower()
                else:
                    replacement = new.split()[0][:max(3, len(name))]
                self.mapping.setdefault(name.casefold(), replacement)
                renamed.append(replacement)
            self.entity_names[entity.id] = tuple(renamed)
        # A fixed length ordering, longest names first, so "Denver Health" is replaced before "Denver".
        keys = sorted(self.mapping, key=len, reverse=True)
        self._pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(key) for key in keys) + r")(?![\w-])", re.IGNORECASE)

    def text(self, text: str) -> str:
        return self._pattern.sub(lambda match: self.mapping[match.group(1).casefold()], text)


def make_clones(core: Gold, count: int) -> tuple[list[tuple[Memory, int]], list[Gold], int]:
    """Enough look-alike clones of the core to hold ``count`` memories.

    Returns the clone memories in store order with the clone each belongs to, one gold graph per clone, and how many
    clone memories were dropped because substitution left a text identical to one already made (or to a core text).
    """
    forbidden = frozenset(name.casefold() for entity in core.entities.values() for name in entity.names)
    seen_texts = {memory.text for memory in core.memories}
    memories: list[tuple[Memory, int]] = []
    golds: list[Gold] = []
    dropped = 0
    k = 0
    while len(memories) < count:
        sub = Substituter(core, k, forbidden)
        prefix = f"lk{k:03d}:"
        kept: dict[str, Memory] = {}
        for memory in core.memories:
            text = sub.text(memory.text)
            if text in seen_texts:
                dropped += 1
                continue
            seen_texts.add(text)
            speaker = sub.text(memory.speaker) if memory.speaker else None
            kept[memory.id] = Memory(prefix + memory.id, text, memory.role, speaker, memory.node_type, memory.scope,
                                     prefix + memory.session, memory.event_time)
        entities = {f"{prefix}{entity.id}": Entity(f"{prefix}{entity.id}", entity.type, entity.subtype, sub.entity_names[entity.id],
                                                   entity.exact_case) for entity in core.entities.values()}
        claims = []
        for claim in core.claims:
            evidence = tuple(kept[memory_id].id for memory_id in claim.ev if memory_id in kept)
            if not evidence and not claim.inferred:
                continue
            claims.append(Claim(prefix + claim.id, prefix + claim.s, claim.r, prefix + claim.o if claim.o else None, claim.attr,
                                sub.text(claim.value) if claim.value else "", evidence, claim.polarity, claim.status, claim.role,
                                claim.time, claim.inferred, prefix + claim.supersedes if claim.supersedes else None, claim.via))
        clone = Gold(f"lookalike-{k}", Path("."), core.owner, prefix + core.owner_entity, entities, claims, [],
                     list(kept.values()), owner_entities=tuple(prefix + owner for owner in core.owner_entities))
        clone.patterns = _alias_patterns(entities)
        golds.append(clone)
        memories += [(memory, k) for memory in kept.values()]
        k += 1
    return memories[:count], golds, dropped


# --- The embedding cache --------------------------------------------------------------------------


class EmbeddingCache:
    """A provider wrapper that embeds each distinct text once, keeps the vectors in a file and times the model."""

    def __init__(self, provider, path: Path | None = None):
        self._provider = provider
        self._path = path
        self._vectors: dict[str, list[float]] = pickle.loads(path.read_bytes()) if path and path.exists() else {}
        self.seconds = 0.0
        self.computed = 0
        self.hits = 0

    def wrap(self, provider) -> "EmbeddingCache":
        """Use ``provider`` as the model behind the cache (the engine builds the real one) and return the cache."""
        self._provider = provider
        return self

    model_name = property(lambda self: self._provider.model_name)
    model_version = property(lambda self: self._provider.model_version)
    dimension = property(lambda self: self._provider.dimension)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [hashlib.sha1(text.encode()).hexdigest() for text in texts]
        missing = [(key, text) for key, text in dict(zip(keys, texts)).items() if key not in self._vectors]
        self.hits += len(texts) - len(missing)
        if missing:
            started = time.perf_counter()
            vectors = await self._provider.embed([text for _, text in missing])
            self.seconds += time.perf_counter() - started
            self.computed += len(missing)
            self._vectors.update({key: list(vector) for (key, _), vector in zip(missing, vectors)})
        return [self._vectors[key] for key in keys]

    def save(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_bytes(pickle.dumps(self._vectors))

